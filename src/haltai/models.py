"""Claim and evidence model (roadmap section 6).

HaltAI never reports a single "containable" badge. Each claim is bound to a
deployment fingerprint and carries its evidence level, assumptions, measured
interval and residual exposure.
"""

from __future__ import annotations

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


class Claim(BaseModel):
    id: ClaimId
    statement: str
    deployment_fingerprint: str
    protected_effect: str
    declared_paths: list[str]
    assumptions: list[str] = Field(default_factory=list)
    containment_requested_at: datetime | None = None
    containment_effective_at: datetime | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    result: ClaimResult = ClaimResult.UNKNOWN
    residual_exposure: list[str] = Field(default_factory=list)
    revalidation_triggers: list[str] = Field(default_factory=list)

    @property
    def measured_interval_seconds(self) -> float | None:
        """Observed time to denial during this test. Not a universal guarantee."""
        if self.containment_requested_at and self.containment_effective_at:
            delta = self.containment_effective_at - self.containment_requested_at
            return delta.total_seconds()
        return None

    @property
    def highest_evidence_level(self) -> EvidenceLevel | None:
        order = list(EvidenceLevel)
        levels = [e.level for e in self.evidence]
        return max(levels, key=order.index) if levels else None


class AssessmentLimitation(BaseModel):
    """A prerequisite or binding that was missing. Never silently treated as a pass."""

    field: str
    reason: str


class AssessmentReport(BaseModel):
    deployment_name: str
    deployment_fingerprint: str
    generated_at: datetime
    claims: list[Claim]
    limitations: list[AssessmentLimitation] = Field(default_factory=list)
    collateral_impact: list[str] = Field(default_factory=list)
