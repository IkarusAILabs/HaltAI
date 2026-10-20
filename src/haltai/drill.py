"""Drill engine: runs Claim 1 and Claim 2 procedures against any backend.

Every activation is paired with a rollback in ``finally``. If recovery cannot be
verified, the drill stops instead of disrupting the deployment further.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime

from haltai import __version__
from haltai.backends.base import Backend
from haltai.checks import StaticCheckResult, run_static_checks
from haltai.config import DrillSettings
from haltai.evaluate import evaluate_claim1, evaluate_claim2
from haltai.fingerprint import REVALIDATION_TRIGGERS, fingerprint
from haltai.manifest import check_prerequisites
from haltai.models import (
    CLAIM_STATEMENTS,
    AssessmentLimitation,
    AssessmentReport,
    Claim,
    Claim1Repetition,
    Claim2Check,
    Claim2Run,
    ClaimId,
    CollateralObservation,
    Evidence,
    EvidenceLevel,
    EvidenceType,
    Marker,
    ProbeOutcome,
    ProbeStatus,
    ProbeVia,
    TimelineEvent,
)


class DrillAborted(RuntimeError):
    pass


class DrillEngine:
    def __init__(self, backend: Backend, settings: DrillSettings,
                 log: Callable[[str], None] | None = None):
        self.b = backend
        self.m = backend.manifest
        self.s = settings
        self.log = log or (lambda _msg: None)
        self.timeline: list[TimelineEvent] = []
        self.collateral = CollateralObservation()
        self.recoveries: list[bool] = []
        self.started_at = backend.now()
        self.aborted: str | None = None

    # ------------------------------------------------------------------ utilities
    def _event(self, kind: str, detail: str = "") -> TimelineEvent:
        ev = TimelineEvent(at=self.b.now(), kind=kind, detail=detail)
        self.timeline.append(ev)
        self.log(f"[{ev.at.strftime('%H:%M:%S')}] {kind} {detail}".rstrip())
        return ev

    def _check_budget(self) -> None:
        elapsed = (self.b.now() - self.started_at).total_seconds()
        if elapsed > self.s.max_drill_duration_s:
            raise DrillAborted(f"max_drill_duration_s ({self.s.max_drill_duration_s}s) exceeded")

    def _collateral(self) -> None:
        p = self.b.probe_collateral()
        self.collateral.probes.append(p)
        if p.status not in (ProbeStatus.ALLOWED, ProbeStatus.SKIPPED):
            self._event("collateral_impact", f"{p.detail}: {p.status.value}")

    def _poll(self, want: ProbeStatus, timeout_s: float,
              collateral: bool = False) -> tuple[ProbeOutcome | None, list[ProbeOutcome]]:
        """Probe via the worker until ``want`` is observed or the timeout passes."""
        start = self.b.now()
        seen: list[ProbeOutcome] = []
        while True:
            p = self.b.probe_write_via_worker()
            seen.append(p)
            if collateral:
                self._collateral()
            if p.status == want:
                return p, seen
            if (self.b.now() - start).total_seconds() >= timeout_s:
                return None, seen
            self.b.sleep(self.s.poll_interval_s)

    def _rollback_and_recover(self) -> ProbeOutcome | None:
        try:
            self.b.rollback_containment()
            self._event("rollback_requested")
        except Exception as exc:  # noqa: BLE001 - must report, never mask
            self._event("rollback_error", repr(exc))
            self.recoveries.append(False)
            return None
        rec, _ = self._poll(ProbeStatus.ALLOWED, self.s.deny_timeout_s)
        self.recoveries.append(rec is not None)
        if rec is None:
            self._event("recovery_not_verified",
                        f"write still not allowed {self.s.deny_timeout_s}s after rollback")
        else:
            self._event("recovery_verified")
        return rec

    def _classify_markers(self, since: datetime, effective_at: datetime,
                          until: datetime | None = None) -> tuple[list[Marker], list[Marker]]:
        """Agent writes initiated in [effective_at, until) are failures; writes initiated
        before effective_at but completed after it are residual exposure."""
        post, residual = [], []
        for mk in self.b.list_markers(since):
            if mk.principal != self.m.identity.role_arn or mk.source == "collateral":
                continue
            if mk.initiated_at >= effective_at and (until is None or mk.initiated_at < until):
                post.append(mk)
            elif mk.initiated_at < effective_at and mk.written_at >= effective_at:
                residual.append(mk)
        return post, residual

    # ------------------------------------------------------------------ claim 1
    def run_claim1(self) -> list[Claim1Repetition]:
        reps: list[Claim1Repetition] = []
        for i in range(1, self.s.repetitions + 1):
            self._check_budget()
            rep = Claim1Repetition(index=i)
            reps.append(rep)
            self._event("claim1_repetition", f"{i}/{self.s.repetitions}")
            rep.positive_control = self.b.probe_write_via_worker()
            self._event("positive_control", rep.positive_control.status.value)
            if not rep.positive_control.allowed:
                continue
            activated = False
            try:
                rep.requested_at = self.b.now()
                self.b.activate_containment()
                activated = True
                rep.activation_ok = True
                self._event("containment_requested", self.m.containment_policy.name)
                denied, _ = self._poll(ProbeStatus.DENIED, self.s.deny_timeout_s,
                                       collateral=True)
                if denied is None:
                    self._event("deny_not_observed", f"within {self.s.deny_timeout_s}s")
                    continue
                rep.first_denied_at = denied.at
                rep.interval_upper_bound_s = (denied.at - rep.requested_at).total_seconds()
                self._event("deny_observed", f"after <= {rep.interval_upper_bound_s:.0f}s")
                waited = 0.0
                while waited < self.s.observation_window_s:
                    self.b.sleep(self.s.poll_interval_s)
                    waited += self.s.poll_interval_s
                    p = self.b.probe_write_via_worker()
                    rep.window_probes.append(p)
                    self._collateral()
                    if p.allowed:
                        self._event("write_allowed_in_window", p.probe_id)
            except Exception as exc:  # noqa: BLE001
                self._event("activation_error", repr(exc))
            finally:
                if activated:
                    rep.rollback_at = self.b.now()
                    rep.recovery = self._rollback_and_recover()
                    rep.rollback_ok = rep.recovered
                    if rep.first_denied_at and rep.requested_at:
                        rep.post_containment_writes, rep.residual_writes = (
                            self._classify_markers(rep.requested_at, rep.first_denied_at,
                                                   rep.rollback_at))
            if activated and not rep.recovered:
                self.aborted = "Recovery not verified after rollback; drill stopped."
                break
        return reps

    # ------------------------------------------------------------------ claim 2
    def run_claim2(self) -> Claim2Run:
        run = Claim2Run()
        self._check_budget()
        self._event("claim2_start")
        run.positive_control = self.b.probe_write_via_worker()
        self._event("positive_control", run.positive_control.status.value)
        if not run.positive_control.allowed:
            return run
        activated = False
        try:
            run.requested_at = self.b.now()
            self.b.activate_containment()
            activated = True
            run.queued_at_activation = self.b.queue_depth()
            self._event("containment_requested",
                        f"{run.queued_at_activation} job(s) queued at activation")
            denied, _ = self._poll(ProbeStatus.DENIED, self.s.deny_timeout_s, collateral=True)
            if denied is None:
                self._event("deny_not_observed", f"within {self.s.deny_timeout_s}s")
                return run
            run.containment_effective = True
            run.effective_at = denied.at

            self.b.restart_worker()
            self._event("worker_restarted")
            run.checks.append(self._check("restart_then_probe", self.b.probe_write_via_worker()))
            run.checks.append(self._check("fresh_task_probe", self.b.probe_write_fresh_task()))
            self.b.force_redeploy()
            self._event("service_redeployed")
            run.checks.append(self._check("redeploy_then_probe",
                                          self.b.probe_write_via_worker()))
            run.checks.append(self._job_retry_check(run.effective_at))
            self._collateral()
        except Exception as exc:  # noqa: BLE001
            self._event("claim2_error", repr(exc))
        finally:
            if activated:
                rollback_at = self.b.now()
                if run.effective_at and run.requested_at:
                    run.post_containment_writes, run.residual_writes = self._classify_markers(
                        run.requested_at, run.effective_at, rollback_at)
                run.recovery = self._rollback_and_recover()
                run.rollback_ok = run.recovery is not None
        return run

    def _check(self, name: str, outcome: ProbeOutcome) -> Claim2Check:
        self._event(name, outcome.status.value)
        self._collateral()
        return Claim2Check(name=name, outcome=outcome, passed=outcome.denied,
                           detail=outcome.detail)

    def _job_retry_check(self, effective_at: datetime) -> Claim2Check:
        job_id = f"haltai-retry-{uuid.uuid4().hex[:8]}"
        self.b.enqueue_job(job_id)
        vt = self.m.work_intake.visibility_timeout_s
        self.b.sleep(2 * vt + 5)  # allow the first attempt and at least one retry
        written = [mk for mk in self.b.list_markers(effective_at) if mk.job_id == job_id]
        status = ProbeStatus.ALLOWED if written else ProbeStatus.DENIED
        outcome = ProbeOutcome(probe_id=job_id, via=ProbeVia.WORKER, status=status, at=self.b.now(),
                               detail="job enqueued after containment, retried past "
                                      "visibility timeout")
        self._event("job_retry", status.value)
        return Claim2Check(name="job_retry", outcome=outcome, passed=not written,
                           detail=outcome.detail)


# ---------------------------------------------------------------------- assembly


def _claim1(reps: list[Claim1Repetition], static: StaticCheckResult, fp: str,
            drill: DrillEngine) -> Claim:
    m = drill.m
    result, why = evaluate_claim1(reps, static.caps, drill.s.repetitions)
    intervals = [r.interval_upper_bound_s for r in reps if r.interval_upper_bound_s is not None]
    first = next((r for r in reps if r.first_denied_at), None)
    evidence = [e for e in static.evidence]
    now = drill.b.now()
    if intervals:
        evidence.append(Evidence(
            type=EvidenceType.ACTIVE_TEST, level=EvidenceLevel.ENFORCEMENT_TESTED,
            description=f"Agent-identity write probes were denied after activation in "
                        f"{len(intervals)} repetition(s).",
            observed_at=now, source=f"{drill.b.name}:probe_write_via_worker",
            data={"intervals_s": intervals}))
    residual = [mk for r in reps for mk in r.residual_writes]
    clean_window = intervals and all(not r.flapped for r in reps)
    if clean_window and drill.s.observation_window_s > 0:
        evidence.append(Evidence(
            type=EvidenceType.RUNTIME_OBSERVATION, level=EvidenceLevel.OBSERVED_AFTER_ACTIVATION,
            description=f"No agent write was initiated after denial during the "
                        f"{drill.s.observation_window_s:.0f}s observation window(s).",
            observed_at=now, source=f"{drill.b.name}:list_markers"))
    return Claim(
        id=ClaimId.NEW_WRITES_DENIED, statement=CLAIM_STATEMENTS[ClaimId.NEW_WRITES_DENIED],
        deployment_fingerprint=fp, protected_effect=m.protected_effect.name,
        declared_paths=m.declared_paths, assumptions=m.assumptions,
        containment_requested_at=first.requested_at if first else None,
        containment_effective_at=first.first_denied_at if first else None,
        measured_intervals_s=intervals, poll_interval_s=drill.s.poll_interval_s,
        evidence=evidence, result=result, rationale=why,
        residual_exposure=_residual(m.unsupported_paths, residual, 0, m.downstream_side_effects),
        residual_work_items=len(residual),
        revalidation_triggers=REVALIDATION_TRIGGERS)


def _claim2(run: Claim2Run, static: StaticCheckResult, fp: str, drill: DrillEngine) -> Claim:
    m = drill.m
    result, why = evaluate_claim2(run, static.caps)
    evidence = [e for e in static.evidence]
    if run.containment_effective and run.checks:
        evidence.append(Evidence(
            type=EvidenceType.ACTIVE_TEST, level=EvidenceLevel.ENFORCEMENT_TESTED,
            description="Restart, fresh-task, redeploy and retry probes: " + ", ".join(
                f"{c.name}={c.outcome.status.value if c.outcome else 'n/a'}" for c in run.checks),
            observed_at=drill.b.now(), source=f"{drill.b.name}:claim2_checks"))
    if run.containment_effective and not run.post_containment_writes:
        evidence.append(Evidence(
            type=EvidenceType.RUNTIME_OBSERVATION, level=EvidenceLevel.OBSERVED_AFTER_ACTIVATION,
            description="No agent write was initiated after containment became effective.",
            observed_at=drill.b.now(), source=f"{drill.b.name}:list_markers"))
    return Claim(
        id=ClaimId.RESTART_RETRY_NO_RESTORE,
        statement=CLAIM_STATEMENTS[ClaimId.RESTART_RETRY_NO_RESTORE],
        deployment_fingerprint=fp, protected_effect=m.protected_effect.name,
        declared_paths=m.declared_paths, assumptions=m.assumptions,
        containment_requested_at=run.requested_at, containment_effective_at=run.effective_at,
        measured_intervals_s=[(run.effective_at - run.requested_at).total_seconds()]
        if run.effective_at and run.requested_at else [],
        poll_interval_s=drill.s.poll_interval_s, evidence=evidence, result=result,
        rationale=why,
        residual_exposure=_residual(m.unsupported_paths, run.residual_writes,
                                    run.queued_at_activation, m.downstream_side_effects),
        residual_work_items=len(run.residual_writes) + run.queued_at_activation,
        revalidation_triggers=REVALIDATION_TRIGGERS)


def _residual(unsupported: list[str], residual: list[Marker], queued: int,
              downstream: list[str]) -> list[str]:
    out = []
    if residual:
        out.append(f"{len(residual)} write(s) accepted before containment completed after it "
                   "became effective: " + ", ".join(mk.job_id or mk.marker_id for mk in residual))
    if queued:
        out.append(f"{queued} job(s) were already queued when containment was requested.")
    out += [f"Unsupported path (not tested): {p}" for p in unsupported]
    out += [f"Downstream side effect (not reversible by HaltAI): {d}" for d in downstream
            if d.lower() not in {"none", "none (synthetic table only)"}]
    return out


def assess(backend: Backend, settings: DrillSettings, claims: set[str],
           scenarios: list[str] | None = None,
           log: Callable[[str], None] | None = None) -> AssessmentReport:
    """Static checks + requested drills -> AssessmentReport."""
    m = backend.manifest
    drill = DrillEngine(backend, settings, log)
    limitations = check_prerequisites(m)
    static = run_static_checks(backend)
    limitations += static.limitations
    snap = backend.snapshot()
    fp = fingerprint(m, snap)
    drill._event("assessment_start", f"backend={backend.name} fingerprint={fp[:19]}")

    reps: list[Claim1Repetition] = []
    run2: Claim2Run | None = None
    out_claims: list[Claim] = []
    try:
        if "1" in claims:
            reps = drill.run_claim1()
            out_claims.append(_claim1(reps, static, fp, drill))
        if "2" in claims:
            if drill.aborted:
                run2 = Claim2Run()
                c2 = _claim2(run2, static, fp, drill)
                c2.rationale = [f"Not run: {drill.aborted}"]
                out_claims.append(c2)
            else:
                run2 = drill.run_claim2()
                out_claims.append(_claim2(run2, static, fp, drill))
    except DrillAborted as exc:
        drill.aborted = str(exc)
        drill._event("drill_aborted", str(exc))

    if drill.aborted:
        limitations.append(AssessmentLimitation(field="drill", reason=drill.aborted,
                                                blocking=True))
    impacted = [p for p in drill.collateral.probes
                if p.status not in (ProbeStatus.ALLOWED, ProbeStatus.SKIPPED)]
    collateral = []
    if impacted:
        collateral.append(f"{len(impacted)} of {len(drill.collateral.probes)} collateral probes "
                          f"({m.collateral_probe.name}) failed while containment was active.")
    recovery = all(drill.recoveries) if drill.recoveries else None
    if recovery is False:
        limitations.append(AssessmentLimitation(
            field="recovery", reason="Protected write was not restored after rollback.",
            blocking=True))
    return AssessmentReport(
        haltai_version=__version__, backend=backend.name, scenarios=scenarios or [],
        deployment_name=m.name, deployment_fingerprint=fp, generated_at=backend.now(),
        claims=out_claims, static_evidence=static.evidence, limitations=limitations,
        collateral_impact=collateral, recovery_verified=recovery, timeline=drill.timeline)
