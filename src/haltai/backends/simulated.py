"""A deterministic, in-memory model of the v1 AWS deployment.

Nothing here touches AWS. The world models the pieces the claims depend on:

* an API Gateway write route whose deny for the agent principal takes effect after a
  propagation delay (eventual consistency), and lifts after a delay on rollback;
* an ECS service whose tasks can be restarted or redeployed, plus one-off tasks;
* an SQS queue with visibility timeout, retries and a DLQ;
* a DynamoDB marker table recording every completed protected write;
* an unrelated application that uses the same gateway with its own role.

Time is virtual: ``sleep`` advances the clock and runs the world, so a full drill
finishes in milliseconds and results are reproducible from ``seed``.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from haltai.backends.base import Backend
from haltai.config import SimulationSettings
from haltai.manifest import ContainmentMechanism, DeploymentManifest
from haltai.models import Marker, ProbeOutcome, ProbeStatus, ProbeVia
from haltai.scenarios import validate_scenarios

TICK_S = 1.0
JOB_INTERVAL_S = 2.0
PROBE_LATENCY_S = 1.0
FRESH_TASK_START_S = 3.0
MAX_RECEIVES = 3
PRELOAD_JOBS = 3
SLOW_PROPAGATION_S = 3600.0


@dataclass
class _Task:
    task_id: str
    started_at: datetime
    revision: int
    local_flag: bool = False


@dataclass
class _Job:
    job_id: str
    visible_at: datetime
    receives: int = 0


@dataclass
class _PolicyChange:
    deny: bool
    requested_at: datetime
    effective_at: datetime


@dataclass
class _PendingWrite:
    due_at: datetime
    marker: Marker


@dataclass
class _World:
    tasks: list[_Task] = field(default_factory=list)
    pending_task_at: datetime | None = None
    queue: list[_Job] = field(default_factory=list)
    dlq: list[_Job] = field(default_factory=list)
    markers: list[Marker] = field(default_factory=list)
    pending_writes: list[_PendingWrite] = field(default_factory=list)
    policy_changes: list[_PolicyChange] = field(default_factory=list)
    revision: int = 1
    next_job_at: datetime | None = None


class SimulatedBackend(Backend):
    name = "simulated"
    is_real = False

    def __init__(self, manifest: DeploymentManifest, settings: SimulationSettings):
        super().__init__(manifest)
        self.settings = settings
        self.scenarios = set(validate_scenarios(settings.scenarios))
        self.rng = random.Random(settings.seed)
        self._now = datetime.now(UTC).replace(microsecond=0)
        self._seq = 0
        self.agent_role = manifest.identity.role_arn
        acct = manifest.aws_account_id
        self.collateral_role = (
            self.agent_role
            if "shared_identity" in self.scenarios
            else manifest.collateral_probe.role_arn or f"arn:aws:iam::{acct}:role/unrelated-app"
        )
        self.mechanism = (
            ContainmentMechanism.LOCAL_FLAG
            if "local_flag_containment" in self.scenarios
            else manifest.containment_policy.mechanism
        )
        self.w = _World()
        self.w.tasks.append(self._new_task())

    # ------------------------------------------------------------------ helpers
    def _id(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}-{self._seq:04d}"

    def _new_task(self) -> _Task:
        return _Task(task_id=self._id("task"), started_at=self._now, revision=self.w.revision)

    def _running_task(self) -> _Task | None:
        return self.w.tasks[-1] if self.w.tasks else None

    def _delay(self) -> float:
        if "slow_propagation" in self.scenarios:
            return SLOW_PROPAGATION_S
        return self.settings.propagation_delay_s + self.rng.uniform(0, self.settings.jitter_s)

    def _deny_effective(self, at: datetime) -> bool:
        """Latest-requested change whose effect has propagated wins."""
        state = False
        for change in self.w.policy_changes:  # request order
            if change.effective_at <= at:
                state = change.deny
        return state

    def _gateway_write(self, principal: str, source: str, task: _Task | None,
                       job_id: str | None = None) -> ProbeStatus:
        if "broken_gateway" in self.scenarios:
            return ProbeStatus.ERROR
        if task is not None and task.local_flag:
            return ProbeStatus.DENIED  # the worker refuses on its own
        if (self.mechanism != ContainmentMechanism.LOCAL_FLAG
                and principal == self.agent_role and self._deny_effective(self._now)):
            return ProbeStatus.DENIED
        marker = Marker(marker_id=self._id("marker"), job_id=job_id, principal=principal,
                        source=source, initiated_at=self._now, written_at=self._now)
        if "async_downstream" in self.scenarios and source != "collateral":
            due = self._now + timedelta(seconds=self.settings.async_write_delay_s)
            marker.written_at = due
            self.w.pending_writes.append(_PendingWrite(due_at=due, marker=marker))
        else:
            self.w.markers.append(marker)
        return ProbeStatus.ALLOWED

    def _tick(self) -> None:
        w = self.w
        # pending replacement task
        if w.pending_task_at and self._now >= w.pending_task_at:
            w.tasks.append(self._new_task())
            w.pending_task_at = None
        # async downstream completions
        for pw in [p for p in w.pending_writes if p.due_at <= self._now]:
            w.markers.append(pw.marker)
            w.pending_writes.remove(pw)
        # worker consumes one visible job per JOB_INTERVAL_S
        task = self._running_task()
        if task is None or (w.next_job_at and self._now < w.next_job_at):
            return
        visible = [j for j in w.queue if j.visible_at <= self._now]
        if not visible:
            return
        job = visible[0]
        job.receives += 1
        w.next_job_at = self._now + timedelta(seconds=JOB_INTERVAL_S)
        status = self._gateway_write(self.agent_role, "worker", task, job.job_id)
        if status == ProbeStatus.ALLOWED:
            w.queue.remove(job)
        elif job.receives >= MAX_RECEIVES:
            w.queue.remove(job)
            w.dlq.append(job)
        else:
            vt = self.manifest.work_intake.visibility_timeout_s
            job.visible_at = self._now + timedelta(seconds=vt)

    # ------------------------------------------------------------------ clock
    def now(self) -> datetime:
        return self._now

    def sleep(self, seconds: float) -> None:
        remaining = seconds
        while remaining > 0:
            step = min(TICK_S, remaining)
            self._now += timedelta(seconds=step)
            remaining -= step
            self._tick()

    # ------------------------------------------------------------------ inspection
    def caller_account(self) -> str:
        return self.manifest.aws_account_id

    def snapshot(self) -> dict[str, Any]:
        m = self.manifest
        statements: list[dict[str, Any]] = [{"Effect": "Allow", "Action": "execute-api:Invoke",
                       "Resource": f"{m.tool_gateway.rest_api_id or 'api'}/*"}]
        if "bypass_direct_write" in self.scenarios:
            statements.append({"Effect": "Allow", "Action": m.protected_effect.direct_actions,
                               "Resource": m.protected_effect.store.table})
        return {
            "backend": self.name,
            "ecs": {"service": m.runtime.service, "task_definition_revision": self.w.revision,
                    "task_role": self.agent_role},
            "iam": {"role": self.agent_role, "statements": statements},
            "gateway": {"write_route": m.tool_gateway.write_route,
                        "deny_active": self._deny_effective(self._now)},
            "sqs": {"visibility_timeout_s": m.work_intake.visibility_timeout_s,
                    "max_receives": MAX_RECEIVES},
            "store": m.protected_effect.store.model_dump(),
            "collateral_role": self.collateral_role,
            "scenarios": sorted(self.scenarios),
        }

    def role_consumers(self) -> list[str]:
        r = self.manifest.runtime
        consumers = [f"ecs-service:{r.cluster}/{r.service}"]
        if self.collateral_role == self.agent_role:
            consumers.append(f"app:{self.manifest.collateral_probe.name}")
        return consumers

    def direct_effect_allowed(self) -> dict[str, bool]:
        bypass = "bypass_direct_write" in self.scenarios
        return {a: bypass for a in self.manifest.protected_effect.direct_actions}

    def containment_prestaged(self) -> tuple[bool, str]:
        if self.mechanism == ContainmentMechanism.LOCAL_FLAG:
            return False, "A worker-local flag is not an enforcement point outside the process."
        return True, (f"{self.mechanism.value} for {self.agent_role} on "
                      f"{self.manifest.tool_gateway.write_route} is pre-staged.")

    # ------------------------------------------------------------------ containment
    def _request_policy(self, deny: bool) -> None:
        delay = self._delay()
        if not deny and "no_recovery" in self.scenarios:
            delay = 10 * 365 * 24 * 3600.0
        self.w.policy_changes.append(_PolicyChange(
            deny=deny, requested_at=self._now,
            effective_at=self._now + timedelta(seconds=delay)))

    def activate_containment(self) -> None:
        if "preload_queue" in self.scenarios:
            for _ in range(PRELOAD_JOBS):
                self.enqueue_job(self._id("preload"))
        if self.mechanism == ContainmentMechanism.LOCAL_FLAG:
            for t in self.w.tasks:
                t.local_flag = True
        else:
            self._request_policy(deny=True)

    def rollback_containment(self) -> None:
        if self.mechanism == ContainmentMechanism.LOCAL_FLAG:
            for t in self.w.tasks:
                t.local_flag = False
        else:
            self._request_policy(deny=False)

    def containment_active(self) -> bool:
        if self.mechanism == ContainmentMechanism.LOCAL_FLAG:
            return any(t.local_flag for t in self.w.tasks)
        return bool(self.w.policy_changes) and self.w.policy_changes[-1].deny

    # ------------------------------------------------------------------ probes
    def _wait_for_task(self) -> _Task | None:
        waited = 0.0
        while self._running_task() is None and waited < self.manifest.probes.timeout_s:
            self.sleep(TICK_S)
            waited += TICK_S
        return self._running_task()

    def probe_write_via_worker(self) -> ProbeOutcome:
        pid = self._id("probe")
        task = self._wait_for_task()
        if task is None:
            return ProbeOutcome(probe_id=pid, via=ProbeVia.WORKER, status=ProbeStatus.TIMEOUT,
                                at=self._now, detail="no running worker")
        self.sleep(PROBE_LATENCY_S)
        status = self._gateway_write(self.agent_role, "probe", task, pid)
        return ProbeOutcome(probe_id=pid, via=ProbeVia.WORKER, status=status, at=self._now,
                            task_id=task.task_id)

    def probe_write_fresh_task(self) -> ProbeOutcome:
        pid = self._id("probe")
        task = self._new_task()
        self.sleep(FRESH_TASK_START_S)
        status = self._gateway_write(self.agent_role, "probe", task, pid)
        return ProbeOutcome(probe_id=pid, via=ProbeVia.FRESH_TASK, status=status, at=self._now,
                            task_id=task.task_id, detail="one-off task from agent task definition")

    def probe_collateral(self) -> ProbeOutcome:
        pid = self._id("collateral")
        status = self._gateway_write(self.collateral_role, "collateral", None)
        return ProbeOutcome(probe_id=pid, via=ProbeVia.COLLATERAL, status=status, at=self._now,
                            detail=self.manifest.collateral_probe.name)

    # ------------------------------------------------------------------ runtime
    def _replace_tasks(self) -> None:
        self.w.tasks.clear()
        self.w.pending_task_at = self._now + timedelta(seconds=self.settings.worker_restart_s)
        self.sleep(self.settings.worker_restart_s + TICK_S)

    def restart_worker(self) -> None:
        self._replace_tasks()

    def force_redeploy(self) -> None:
        self.w.revision += 1
        self._replace_tasks()

    def enqueue_job(self, job_id: str) -> None:
        self.w.queue.append(_Job(job_id=job_id, visible_at=self._now))

    def queue_depth(self) -> int:
        return len(self.w.queue)

    def list_markers(self, since: datetime) -> list[Marker]:
        return [m for m in self.w.markers if m.written_at >= since]
