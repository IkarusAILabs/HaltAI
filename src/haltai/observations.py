"""What a lever actually achieved, as observed from effects (roadmap section 8).

An observation is built only from evidence of effects: canary probes made as the
agent's identity and from the agent's network, and infrastructure activity (the
CloudTrail and VPC Flow Logs equivalents). A lever's own control state ("SCP
attached", "desired count 0") is never enough to report an effect as stopped.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from haltai.environment import Lever


class ObservedOutcome(StrEnum):
    STOPPED = "stopped"  # canary denied and no agent activity got through in the window
    PARTIAL = "partial"  # canary denied, but some agent activity still got through
    NOT_STOPPED = "not_stopped"  # the canary or the agents' main path still works
    INCONCLUSIVE = "inconclusive"  # the evidence needed to decide could not be gathered
    NOT_COVERED = "not_covered"  # the declaration names nothing for this lever


class LeverObservation(BaseModel):
    lever: Lever
    outcome: ObservedOutcome
    observed_at: datetime
    window_start: datetime | None = Field(
        default=None, description="Activity before this is ignored (propagation grace)")
    evidence: list[str] = Field(default_factory=list)
    residual: list[str] = Field(default_factory=list,
                                description="Activity that may continue: queued or accepted work")


class CollateralObservation(BaseModel):
    service: str
    in_boundary: bool
    healthy: bool
    impact_allowed: bool = Field(
        description="True only for in-boundary services under the isolate profile")
    detail: str = ""

    @property
    def violation(self) -> bool:
        """A service that must keep working was interrupted."""
        return not self.healthy and not self.impact_allowed
