"""Claim and evidence model (roadmap section 6).

HaltAI never reports a single "containable" badge. Each claim is bound to a
deployment fingerprint and carries its evidence level, assumptions, measured
interval and residual exposure.
"""

from __future__ import annotations

import statistics
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class EvidenceLevel(StrEnum):
    CONFIGURED = "configured"
    ENFORCEMENT_TESTED = "enforcement_tested"
    BOUNDARY_ASSESSED = "boundary_assessed"
    OBSERVED_AFTER_ACTIVATION = "observed_after_activation"


class EvidenceType(StrEnum):
    INSPECTED_CONFIGURATION = "inspected_configuration"
    ACTIVE_TEST = "active_test"
    RUNTIME_OBSERVATION = "runtime_observation"
    OPERATOR_DECLARATION = "operator_declaration"


class ClaimResult(StrEnum):
    VERIFIED = "verified"  # verified within the tested scope only
    FAILED = "failed"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


class Evidence(BaseModel):
    type: EvidenceType
    level: EvidenceLevel
    description: str
    observed_at: datetime
    source: str = Field(description="Where the evidence came from, e.g. an API call or log query")
    data: dict = Field(default_factory=dict)


class ClaimId(StrEnum):
    NEW_WRITES_DENIED = "new_protected_writes_denied"
    RESTART_RETRY_NO_RESTORE = "restart_retry_do_not_restore_access"


CLAIM_STATEMENTS: dict[ClaimId, str] = {
    ClaimId.NEW_WRITES_DENIED: (
        "After containment is activated, new writes through the registered tool gateway "
        "are rejected within a measured interval."
    ),
    ClaimId.RESTART_RETRY_NO_RESTORE: (
        "When a worker restarts or a queued job is retried, the protected write remains "
        "denied while containment remains active."
    ),
}


# --------------------------------------------------------------------------- probes


class ProbeVia(StrEnum):
    WORKER = "worker"  # a probe job handled by the running agent worker
    FRESH_TASK = "fresh_task"  # a one-off task from the agent's own task definition
    COLLATERAL = "collateral"  # the unrelated application's normal operation


class ProbeStatus(StrEnum):
    ALLOWED = "allowed"
    DENIED = "denied"
    ERROR = "error"
    TIMEOUT = "timeout"
    SKIPPED = "skipped"  # probe not configured; never counted as success or impact


class ProbeOutcome(BaseModel):
    probe_id: str
    via: ProbeVia
    status: ProbeStatus
    at: datetime
    task_id: str | None = None
    detail: str = ""

    @property
    def allowed(self) -> bool:
        return self.status == ProbeStatus.ALLOWED

    @property
    def denied(self) -> bool:
        return self.status == ProbeStatus.DENIED


class Marker(BaseModel):
    """A record of the protected effect actually happening (a synthetic write)."""

    marker_id: str
    job_id: str | None = None
    principal: str
    source: str  # worker | probe | collateral | direct
    initiated_at: datetime
    written_at: datetime


class TimelineEvent(BaseModel):
    at: datetime
    kind: str
    detail: str = ""


# --------------------------------------------------------------------------- drill runs


class Claim1Repetition(BaseModel):
    index: int
    positive_control: ProbeOutcome | None = None
    activation_ok: bool = False
    requested_at: datetime | None = None
    first_denied_at: datetime | None = None
    interval_upper_bound_s: float | None = None
    window_probes: list[ProbeOutcome] = Field(default_factory=list)
    rollback_at: datetime | None = None
    rollback_ok: bool = False
    recovery: ProbeOutcome | None = None
    post_containment_writes: list[Marker] = Field(default_factory=list)
    residual_writes: list[Marker] = Field(default_factory=list)

    @property
    def inconclusive(self) -> bool:
        return self.positive_control is None or not self.positive_control.allowed

    @property
    def denied_in_time(self) -> bool:
        return self.first_denied_at is not None

    @property
    def flapped(self) -> bool:
        return any(p.allowed for p in self.window_probes) or bool(self.post_containment_writes)

    @property
    def recovered(self) -> bool:
        return self.recovery is not None and self.recovery.allowed


class Claim2Check(BaseModel):
    name: str  # restart_then_probe | fresh_task_probe | redeploy_then_probe | job_retry
    outcome: ProbeOutcome | None = None
    passed: bool = False
    detail: str = ""


class Claim2Run(BaseModel):
    positive_control: ProbeOutcome | None = None
    containment_effective: bool = False
    requested_at: datetime | None = None
    effective_at: datetime | None = None
    checks: list[Claim2Check] = Field(default_factory=list)
    post_containment_writes: list[Marker] = Field(default_factory=list)
    residual_writes: list[Marker] = Field(default_factory=list)
    queued_at_activation: int = 0
    rollback_ok: bool = False
    recovery: ProbeOutcome | None = None
    timeline: list[TimelineEvent] = Field(default_factory=list)


class CollateralObservation(BaseModel):
    probes: list[ProbeOutcome] = Field(default_factory=list)

    @property
    def impacted(self) -> bool:
        return any(p.status not in (ProbeStatus.ALLOWED, ProbeStatus.SKIPPED)
                   for p in self.probes)


# --------------------------------------------------------------------------- claims & reports


class Claim(BaseModel):
    id: ClaimId
    statement: str
    deployment_fingerprint: str
    protected_effect: str
    declared_paths: list[str]
    assumptions: list[str] = Field(default_factory=list)
    containment_requested_at: datetime | None = None
    containment_effective_at: datetime | None = None
    measured_intervals_s: list[float] = Field(default_factory=list)
    poll_interval_s: float | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    result: ClaimResult = ClaimResult.UNKNOWN
    rationale: list[str] = Field(default_factory=list)
    residual_exposure: list[str] = Field(default_factory=list)
    residual_work_items: int = Field(
        default=0, description="Queued jobs or accepted writes that completed after containment")
    revalidation_triggers: list[str] = Field(default_factory=list)

    @property
    def measured_interval_seconds(self) -> float | None:
        """Observed time to denial during this test. Not a universal guarantee."""
        if self.containment_requested_at and self.containment_effective_at:
            delta = self.containment_effective_at - self.containment_requested_at
            return delta.total_seconds()
        return None

    @property
    def interval_stats(self) -> dict[str, float] | None:
        if not self.measured_intervals_s:
            return None
        xs = self.measured_intervals_s
        return {"min": min(xs), "median": statistics.median(xs), "max": max(xs), "n": len(xs)}

    @property
    def highest_evidence_level(self) -> EvidenceLevel | None:
        order = list(EvidenceLevel)
        levels = [e.level for e in self.evidence]
        return max(levels, key=order.index) if levels else None


class AssessmentLimitation(BaseModel):
    """A prerequisite or binding that was missing. Never silently treated as a pass."""

    field: str
    reason: str
    blocking: bool = False


class AssessmentReport(BaseModel):
    haltai_version: str
    backend: str
    scenarios: list[str] = Field(default_factory=list)
    deployment_name: str
    deployment_fingerprint: str
    generated_at: datetime
    claims: list[Claim]
    static_evidence: list[Evidence] = Field(default_factory=list)
    limitations: list[AssessmentLimitation] = Field(default_factory=list)
    collateral_impact: list[str] = Field(default_factory=list)
    recovery_verified: bool | None = None
    timeline: list[TimelineEvent] = Field(default_factory=list)
