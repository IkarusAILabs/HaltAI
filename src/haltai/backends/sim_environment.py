"""A deterministic, in-memory model of a declared environment and its four levers.

Nothing here touches AWS. Time is virtual: ``sleep`` advances the clock and runs the
world, so a full halt finishes in milliseconds and results are reproducible from
``seed``. The world models what each lever depends on:

* **identity:** agent roles calling AWS APIs; a halt SCP (``account``/``ou``) or
  per-role deny (``tagged``) that takes effect after a propagation delay; principals
  outside the boundary account are out of its reach;
* **network:** agent subnets with established connections to model and tool
  endpoints. A quarantine NACL drops everything; a security-group change only
  refuses *new* connections (tracked connections survive);
* **workload:** ECS services with desired counts and Application Auto Scaling;
  Lambda functions with reserved concurrency;
* **work intake:** an SQS queue fed by an outside producer (SQS has no pause, so
  messages accumulate), event source mappings, EventBridge rules and schedules;
* **collateral:** the declared in-boundary and out-of-boundary services.

Observations come from the activity log and canary probes only (``observations.py``).
Failure scenarios are listed in ``haltai.env_scenarios``.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from fnmatch import fnmatch

from haltai.backends.env_base import EnvironmentBackend, LeverError
from haltai.environment import BoundaryKind, EnvironmentDeclaration, Lever, Profile
from haltai.escape import EscapeAction, EscapeKind
from haltai.observations import CollateralObservation, LeverObservation, ObservedOutcome

TICK_S = 1.0
LAMBDA_RUN_S = 3.0
STANDALONE_RUN_S = 20.0
UNDECLARED_SCHEDULE_EVERY_S = 45.0
ESM_EVERY_S = 5.0
OUTSIDE_ACCOUNT = "555555555555"
COLLATERAL_OUTSIDE_ACCOUNT = "111111111111"
SERVICE_SUBNET = "subnet-sim-services"
OUTSIDE_SUBNET = "subnet-sim-outside"
SLOW_SCP_DELAY_S = 3600.0

# Failure-scenario flags (expected outcomes live in haltai.env_scenarios).
SG_NETWORK = "sg_change_keeps_connections"
AUTOSCALE = "autoscaling_restarts_tasks"
UNDECLARED_SCHEDULE = "schedule_outside_boundary"
UNTRACKED_KEY = "untracked_credential"
SHARED_NAT = "shared_nat"
SLOW_SCP = "slow_scp_propagation"
# Boundary escape flags (Claim 3 on the boundary, haltai.boundary_escape). Only
# ASSUME_EXEC also changes behaviour during a halt; the others are permissions only.
ASSUME_EXEC = "agent_can_assume_execution_role"
MODIFY_EXEC = "agent_can_modify_execution_role"
PASS_EXEC = "agent_can_pass_execution_role"
LEAVE_ORG = "agent_can_leave_organization"
EDIT_STAGED = "agent_can_edit_staged_controls"
RETAG_SCOPE = "agent_can_retag_scope"  # tagged boundaries only
ESCAPE_FLAGS = {ASSUME_EXEC, MODIFY_EXEC, PASS_EXEC, LEAVE_ORG, EDIT_STAGED, RETAG_SCOPE}
FLAGS = {SG_NETWORK, AUTOSCALE, UNDECLARED_SCHEDULE, UNTRACKED_KEY, SHARED_NAT, SLOW_SCP,
         *ESCAPE_FLAGS}


@dataclass
class SimEnvSettings:
    seed: int = 7
    scp_delay_s: float = 5.0
    jitter_s: float = 2.0
    network_delay_s: float = 1.0
    task_stop_s: float = 8.0
    autoscale_interval_s: float = 30.0
    agent_call_interval_s: float = 5.0
    producer_interval_s: float = 10.0
    trigger_interval_s: float = 30.0
    grace_s: float = 20.0  # observations ignore activity this soon after apply
    scenarios: list[str] = field(default_factory=list)


@dataclass
class _Actor:
    """One unit of running agent compute: an ECS task or a Lambda invocation."""
    actor_id: str
    kind: str  # ecs_task | lambda | standalone_task
    source: str
    principal: str
    subnet: str
    started_at: datetime
    ends_at: datetime | None = None
    connected: bool = False  # holds an established connection to the model endpoint
    declared: bool = True
    next_call_at: datetime | None = None


@dataclass
class _Activity:
    at: datetime
    action: str  # aws_call | egress | trigger | invoke | task_running
    actor_id: str
    principal: str
    target: str
    allowed: bool
    declared: bool = True
    source: str = ""


@dataclass
class _Control:
    profile: Profile | None = None
    staged: bool = False
    active: bool = False
    requested_at: datetime | None = None
    effective_at: datetime | None = None
    prior: dict = field(default_factory=dict)


@dataclass
class _Service:
    name: str
    desired: int = 1
    scaling_suspended: bool = False
    next_scale_check: datetime | None = None
    tasks: list[_Actor] = field(default_factory=list)


class SimulatedEnvironment(EnvironmentBackend):
    name = "simulated-environment"
    is_real = False

    def __init__(self, env: EnvironmentDeclaration, settings: SimEnvSettings | None = None):
        super().__init__(env)
        self.s = settings or SimEnvSettings()
        unknown = set(self.s.scenarios) - FLAGS
        if unknown:
            raise ValueError(f"Unknown environment scenario(s): {', '.join(sorted(unknown))}")
        self.flags = set(self.s.scenarios)
        self.rng = random.Random(self.s.seed)
        self._now = datetime(2026, 1, 1, tzinfo=UTC)
        self._seq = 0
        self.log: list[_Activity] = []
        self.controls: dict[Lever, _Control] = {lv: _Control() for lv in Lever}
        self.account = env.boundary.aws_account_id
        self.exec_role = (env.operator.execution_role_arn
                          or f"arn:aws:iam::{self.account}:role/haltai/haltai-execution")
        self.queue_depth = 0
        self.esm_enabled = {m: True for m in env.intake.event_source_mappings}
        self.rules_enabled = {r: True for r in env.intake.eventbridge_rules}
        self.schedules_enabled = {sc: True for sc in env.intake.schedules}
        self.concurrency: dict[str, int | None] = {f: None for f in env.workload.lambda_functions}
        self.services = {name: _Service(name) for name in env.workload.ecs_services}
        self.lambdas: list[_Actor] = []
        self.standalone: list[_Actor] = []
        self._next_produce = self._now
        self._next_trigger = self._now
        self._next_esm = self._now
        self._next_undeclared = self._now
        for svc in self.services.values():
            self._start_task(svc)

    # ------------------------------------------------------------------ helpers
    def _id(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}-{self._seq:04d}"

    def _role_for(self, workload: str) -> str:
        """The agent role a workload runs as, derived from the declared role patterns."""
        short = workload.rsplit("/", 1)[-1]
        if not self.env.identity.roles:
            return f"arn:aws:iam::{self.account}:role/{short}"
        pattern = self.env.identity.roles[0]
        return pattern[:-1] + short if pattern.endswith("*") else pattern

    def _agent_subnet(self) -> str:
        return self.env.boundary.subnets[0] if self.env.boundary.subnets else "subnet-sim-agents"

    def _start_task(self, svc: _Service) -> None:
        actor = _Actor(self._id("task"), "ecs_task", svc.name, self._role_for(svc.name),
                       self._agent_subnet(), self._now, next_call_at=self._now)
        svc.tasks.append(actor)

    def _on(self, lever: Lever, at: datetime | None = None) -> bool:
        c = self.controls[lever]
        return bool(c.active and c.effective_at is not None and c.effective_at <= (at or self._now))

    def _profile(self, lever: Lever) -> Profile:
        return self.controls[lever].profile or Profile.HALT_AGENTS

    def _account_of(self, principal: str) -> str:
        parts = principal.split(":")
        return parts[4] if len(parts) > 4 else ""

    # ------------------------------------------------------------------ lever effects
    def _identity_denies(self, principal: str) -> bool:
        if not self._on(Lever.IDENTITY):
            return False
        if principal == self.exec_role:
            return False  # the halt SCP's only exemption (roadmap 7.1)
        if self._account_of(principal) != self.account:
            return False  # outside the boundary: no SCP or role policy reaches it
        isolate = self._profile(Lever.IDENTITY) == Profile.ISOLATE
        if isolate and self.env.boundary.kind != BoundaryKind.TAGGED:
            return True
        return any(fnmatch(principal, pat) for pat in self.env.identity.roles)

    def _quarantined_subnets(self) -> set[str]:
        subnets = set(self.env.boundary.subnets) or {self._agent_subnet()}
        if self._profile(Lever.NETWORK) == Profile.ISOLATE:
            subnets.add(SERVICE_SUBNET)
        return subnets

    def _nat_cut(self) -> bool:
        return (self._on(Lever.NETWORK) and SHARED_NAT in self.flags
                and self._profile(Lever.NETWORK) == Profile.ISOLATE)

    def _egress_allowed(self, subnet: str, new: bool) -> bool:
        if subnet == OUTSIDE_SUBNET:
            return not self._nat_cut()
        if not self._on(Lever.NETWORK) or subnet not in self._quarantined_subnets():
            return True
        if SG_NETWORK in self.flags:
            return not new  # tracked connections survive a security-group change
        return False

    def _record(self, action: str, actor: _Actor, target: str, allowed: bool) -> None:
        self.log.append(_Activity(self._now, action, actor.actor_id, actor.principal, target,
                                  allowed, actor.declared, actor.source))

    def _act(self, actor: _Actor) -> None:
        """One round of agent behaviour: an AWS call and a model request."""
        endpoint = (self.env.network.model_endpoints or ["model-endpoint"])[0]
        api_ok = (self._egress_allowed(actor.subnet, new=True)
                  and not self._identity_denies(actor.principal))
        self._record("aws_call", actor, "aws-api", api_ok)
        if (not api_ok and ASSUME_EXEC in self.flags
                and self._egress_allowed(actor.subnet, new=True)):
            as_exec = _Actor(actor.actor_id, actor.kind, actor.source, self.exec_role,
                             actor.subnet, actor.started_at, declared=actor.declared)
            self._record("aws_call", as_exec, "aws-api (assumed execution role)",
                         not self._identity_denies(self.exec_role))
        if UNTRACKED_KEY in self.flags and actor.kind == "ecs_task":
            key = _Actor(actor.actor_id, actor.kind, actor.source,
                         f"arn:aws:iam::{OUTSIDE_ACCOUNT}:user/legacy-agent", actor.subnet,
                         actor.started_at, declared=False)
            ok = self._egress_allowed(actor.subnet, new=True)
            self._record("aws_call", key, "aws-api (untracked key)", ok)
        if actor.connected and self._egress_allowed(actor.subnet, new=False):
            self._record("egress", actor, endpoint, True)
        else:
            actor.connected = self._egress_allowed(actor.subnet, new=True)
            self._record("egress", actor, endpoint, actor.connected)

    def _invoke(self, function: str | None, source: str, declared: bool) -> None:
        """A trigger fired; the invocation runs unless concurrency is zero."""
        trig = _Actor(self._id("trigger"), "trigger", source, "", "", self._now,
                      declared=declared)
        self._record("trigger", trig, function or "-", True)
        if function is None:
            return
        actor = _Actor(self._id("lambda"), "lambda", function, self._role_for(function),
                       self._agent_subnet(), self._now,
                       self._now + timedelta(seconds=LAMBDA_RUN_S), declared=declared)
        runs = self.concurrency.get(function) != 0
        self._record("invoke", actor, function, runs)
        if runs:
            self._act(actor)
            self.lambdas.append(actor)

    # ------------------------------------------------------------------ world loop
    def _tick(self) -> None:
        now = self._now
        fn = self.env.workload.lambda_functions[0] if self.env.workload.lambda_functions else None
        if now >= self._next_produce:  # outside producer: SQS keeps accepting messages
            self.queue_depth += 1
            self._next_produce = now + timedelta(seconds=self.s.producer_interval_s)
        if now >= self._next_esm:
            for m, on in self.esm_enabled.items():
                if on and self.queue_depth > 0:
                    self.queue_depth -= 1
                    self._invoke(fn, f"esm:{m}", True)
            self._next_esm = now + timedelta(seconds=ESM_EVERY_S)
        if now >= self._next_trigger:
            for r, on in self.rules_enabled.items():
                if on:
                    self._invoke(fn, f"rule:{r}", True)
            for sc, on in self.schedules_enabled.items():
                if on:
                    self._invoke(fn, f"schedule:{sc}", True)
            self._next_trigger = now + timedelta(seconds=self.s.trigger_interval_s)
        if UNDECLARED_SCHEDULE in self.flags and now >= self._next_undeclared:
            t = _Actor(self._id("task"), "standalone_task", "schedule:undeclared",
                       self._role_for("undeclared-agent"), self._agent_subnet(), now,
                       now + timedelta(seconds=STANDALONE_RUN_S), declared=False,
                       next_call_at=now)
            self._record("trigger", t, "ecs:RunTask", True)
            self.standalone.append(t)
            self._next_undeclared = now + timedelta(seconds=UNDECLARED_SCHEDULE_EVERY_S)

        for svc in self.services.values():
            # Application Auto Scaling restores the minimum unless scaling is suspended.
            if svc.next_scale_check is None or now >= svc.next_scale_check:
                if not svc.scaling_suspended and svc.desired < 1:
                    svc.desired = 1
                svc.next_scale_check = now + timedelta(seconds=self.s.autoscale_interval_s)
            for t in list(svc.tasks):
                if t.ends_at is not None and now >= t.ends_at:
                    svc.tasks.remove(t)
            live = [t for t in svc.tasks if t.ends_at is None]
            if len(live) > svc.desired:
                for t in live[svc.desired:]:
                    t.ends_at = now + timedelta(seconds=self.s.task_stop_s)
            elif len(live) < svc.desired:
                self._start_task(svc)
        self.lambdas = [a for a in self.lambdas if a.ends_at is None or a.ends_at > now]
        self.standalone = [a for a in self.standalone if a.ends_at is None or a.ends_at > now]

        for actor in self._running():
            self._record("task_running", actor, actor.source, True)
            if actor.next_call_at is not None and now >= actor.next_call_at:
                self._act(actor)
                actor.next_call_at = now + timedelta(seconds=self.s.agent_call_interval_s)

    def _running(self) -> list[_Actor]:
        tasks = [t for svc in self.services.values() for t in svc.tasks]
        return tasks + self.standalone

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

    # ------------------------------------------------------------------ levers
    def stage(self, lever: Lever, profile: Profile) -> None:
        c = self.controls[lever]
        c.profile, c.staged = profile, True
        if lever == Lever.WORKLOAD:
            c.prior = {"desired": {n: s.desired for n, s in self.services.items()},
                       "suspended": {n: s.scaling_suspended for n, s in self.services.items()},
                       "concurrency": dict(self.concurrency)}
        elif lever == Lever.INTAKE:
            c.prior = {"esm": dict(self.esm_enabled), "rules": dict(self.rules_enabled),
                       "schedules": dict(self.schedules_enabled)}

    def apply(self, lever: Lever) -> None:
        c = self.controls[lever]
        if not c.staged:
            raise LeverError(f"{lever.value} lever was not staged; run prepare first")
        if not self.env.covered(lever):
            return
        c.active, c.requested_at = True, self._now
        if lever == Lever.IDENTITY:
            delay = SLOW_SCP_DELAY_S if SLOW_SCP in self.flags else (
                self.s.scp_delay_s + self.rng.uniform(0, self.s.jitter_s))
        elif lever == Lever.NETWORK:
            delay = self.s.network_delay_s
        else:
            delay = 0.0
        c.effective_at = self._now + timedelta(seconds=delay)
        if lever == Lever.WORKLOAD:
            for svc in self.services.values():
                svc.desired = 0
                # The scenario models an adapter that forgets to suspend scaling.
                svc.scaling_suspended = AUTOSCALE not in self.flags
            self.concurrency = {f: 0 for f in self.concurrency}
        elif lever == Lever.INTAKE:
            self.esm_enabled = {m: False for m in self.esm_enabled}
            self.rules_enabled = {r: False for r in self.rules_enabled}
            self.schedules_enabled = {sc: False for sc in self.schedules_enabled}

    def release(self, lever: Lever) -> None:
        c = self.controls[lever]
        if lever == Lever.WORKLOAD and c.prior:
            for n, svc in self.services.items():
                svc.desired = c.prior["desired"][n]
                svc.scaling_suspended = c.prior["suspended"][n]
            self.concurrency = dict(c.prior["concurrency"])
        elif lever == Lever.INTAKE and c.prior:
            self.esm_enabled = dict(c.prior["esm"])
            self.rules_enabled = dict(c.prior["rules"])
            self.schedules_enabled = dict(c.prior["schedules"])
        c.active = False

    # ------------------------------------------------------------------ observations
    def _window(self, lever: Lever) -> tuple[datetime, list[_Activity]]:
        c = self.controls[lever]
        if c.requested_at is None:
            raise LeverError(f"{lever.value} lever has not been applied")
        start = c.requested_at + timedelta(seconds=self.s.grace_s)
        return start, [a for a in self.log if a.at >= start]

    def _obs(self, lever: Lever, outcome: ObservedOutcome, start: datetime | None,
             evidence: list[str], residual: list[str] | None = None) -> LeverObservation:
        return LeverObservation(lever=lever, outcome=outcome, observed_at=self._now,
                                window_start=start, evidence=evidence, residual=residual or [])

    # Activity that shows a lever's effect was possible before it was applied.
    POSITIVE_CONTROL = {
        Lever.IDENTITY: {"aws_call"},
        Lever.NETWORK: {"egress"},
        Lever.INTAKE: {"trigger"},
        Lever.WORKLOAD: {"task_running", "invoke"},
    }

    def observe(self, lever: Lever) -> LeverObservation:
        if not self.env.covered(lever):
            return self._obs(lever, ObservedOutcome.NOT_COVERED, None,
                             ["The declaration names no resources for this lever."])
        start, acts = self._window(lever)
        requested = self.controls[lever].requested_at
        before = [a for a in self.log if requested is not None and a.at < requested
                  and a.action in self.POSITIVE_CONTROL[lever] and a.allowed]
        if not before:
            return self._obs(lever, ObservedOutcome.INCONCLUSIVE, start, [
                "No positive control: the effect was never observed working before the "
                "lever was applied, so a denial can't be attributed to it."])
        if lever == Lever.IDENTITY:
            return self._observe_identity(start, acts)
        if lever == Lever.NETWORK:
            return self._observe_network(start, acts)
        if lever == Lever.INTAKE:
            return self._observe_intake(start, acts)
        return self._observe_workload(start, acts)

    def _canary_principals(self) -> list[str]:
        names = list(self.services) + list(self.concurrency)
        return sorted({self._role_for(n) for n in names}) or [self._role_for("canary")]

    def _observe_identity(self, start: datetime, acts: list[_Activity]) -> LeverObservation:
        canary = {p: not self._identity_denies(p) for p in self._canary_principals()}
        ev = [f"canary as {p} (fresh and pre-halt session): "
              f"{'ALLOWED' if ok else 'denied'}" for p, ok in canary.items()]
        through = sorted({a.principal for a in acts if a.action == "aws_call" and a.allowed})
        ev += [f"activity log: AWS call allowed for {p}" for p in through]
        if any(canary.values()):
            outcome = ObservedOutcome.NOT_STOPPED
        elif through:
            outcome = ObservedOutcome.PARTIAL
        else:
            outcome = ObservedOutcome.STOPPED
        return self._obs(Lever.IDENTITY, outcome, start, ev)

    def _observe_network(self, start: datetime, acts: list[_Activity]) -> LeverObservation:
        endpoints = self.env.network.model_endpoints + self.env.network.tool_endpoints
        endpoints = endpoints or ["model-endpoint"]
        reach = self._egress_allowed(self._agent_subnet(), new=True)
        ev = [f"canary from {self._agent_subnet()} to {e}: "
              f"{'REACHED' if reach else 'refused'}" for e in endpoints]
        flows = [a for a in acts if a.action == "egress" and a.allowed]
        if flows:
            ev.append(f"flow log: {len(flows)} accepted flow(s) from agent subnets "
                      "(established connections)")
        if reach:
            outcome = ObservedOutcome.NOT_STOPPED
        elif flows:
            outcome = ObservedOutcome.PARTIAL
        else:
            outcome = ObservedOutcome.STOPPED
        return self._obs(Lever.NETWORK, outcome, start, ev)

    def _observe_intake(self, start: datetime, acts: list[_Activity]) -> LeverObservation:
        fired = [a for a in acts if a.action == "trigger"]
        declared = sorted({a.source for a in fired if a.declared})
        undeclared = sorted({a.source for a in fired if not a.declared})
        ev = [f"trigger fired: {s}" for s in declared]
        ev += [f"undeclared trigger fired (coverage gap): {s}" for s in undeclared]
        residual = [f"{self.queue_depth} message(s) queued in SQS; SQS has no pause, so "
                    "they stay queued and are not cancelled"] if self.queue_depth else []
        if declared:
            outcome = ObservedOutcome.NOT_STOPPED
        elif undeclared:
            outcome = ObservedOutcome.PARTIAL
        else:
            outcome = ObservedOutcome.STOPPED
            ev.append("no trigger fired in the window")
        return self._obs(Lever.INTAKE, outcome, start, ev, residual)

    def _observe_workload(self, start: datetime, acts: list[_Activity]) -> LeverObservation:
        ran = [a for a in acts if a.action == "task_running"]
        ran += [a for a in acts if a.action == "invoke" and a.allowed]
        ev = []
        still = [t for svc in self.services.values() for t in svc.tasks]
        if still:
            ev.append(f"{len(still)} declared ECS task(s) running now")
        for src in sorted({a.source for a in ran}):
            ev.append(f"agent compute ran in the window: {src}")
        throttled = [a for a in acts if a.action == "invoke" and not a.allowed]
        if throttled:
            ev.append(f"{len(throttled)} Lambda invocation(s) throttled")
        if still:
            outcome = ObservedOutcome.NOT_STOPPED
        elif ran:
            outcome = ObservedOutcome.PARTIAL
        else:
            outcome = ObservedOutcome.STOPPED
            ev.append("no agent compute ran in the window")
        return self._obs(Lever.WORKLOAD, outcome, start, ev)

    # ------------------------------------------------------------------ Claim 3 on the boundary
    def agent_principals(self) -> list[str]:
        return self._canary_principals()

    def boundary_escape_allowed(self, principal: str,
                                actions: list[EscapeAction]) -> dict[str, bool | None]:
        return {a.label: self._escape_granted(a) for a in actions}

    def _escape_granted(self, a: EscapeAction) -> bool:
        on_exec = a.resource == self.exec_role
        if a.action == "sts:AssumeRole" and on_exec:
            return ASSUME_EXEC in self.flags
        if a.action == "iam:PassRole" and on_exec:
            return PASS_EXEC in self.flags
        if a.kind == EscapeKind.HALTAI_IDENTITY and on_exec:
            return MODIFY_EXEC in self.flags
        if a.action == "organizations:LeaveOrganization":
            return LEAVE_ORG in self.flags
        if a.kind == EscapeKind.HALTAI_CONTROL and not a.action.startswith("organizations:"):
            return EDIT_STAGED in self.flags
        if a.kind == EscapeKind.SCOPE:
            return RETAG_SCOPE in self.flags
        return False

    def observe_collateral(self) -> list[CollateralObservation]:
        isolate = any(c.active and c.profile == Profile.ISOLATE for c in self.controls.values())
        out = []
        for svc in self.env.collateral.in_boundary:
            principal = f"arn:aws:iam::{self.account}:role/{svc.name}"
            ok = (not self._identity_denies(principal)
                  and self._egress_allowed(SERVICE_SUBNET, new=True))
            out.append(CollateralObservation(service=svc.name, in_boundary=True, healthy=ok,
                                             impact_allowed=isolate))
        for svc in self.env.collateral.out_of_boundary:
            ok = self._egress_allowed(OUTSIDE_SUBNET, new=True)
            out.append(CollateralObservation(
                service=svc.name, in_boundary=False, healthy=ok, impact_allowed=False,
                detail="" if ok else "egress lost: shares the boundary's NAT gateway"))
        return out
