"""Static checks: evidence levels ``configured`` and ``boundary_assessed``."""

from __future__ import annotations

from dataclasses import dataclass, field

from haltai.backends.base import Backend
from haltai.models import AssessmentLimitation, Evidence, EvidenceLevel, EvidenceType


@dataclass
class StaticCheckResult:
    evidence: list[Evidence] = field(default_factory=list)
    limitations: list[AssessmentLimitation] = field(default_factory=list)
    bypass_paths: list[str] = field(default_factory=list)
    shared_identity: list[str] = field(default_factory=list)

    @property
    def caps(self) -> list[str]:
        """Reasons that cap every claim at ``partial``."""
        caps = []
        if self.bypass_paths:
            caps.append("Bypass path(s) let the agent perform the effect without the gateway: "
                        + "; ".join(self.bypass_paths))
        if self.shared_identity:
            caps.append("Agent role is shared with other workloads, so selective enforcement "
                        "is unsafe: " + ", ".join(self.shared_identity))
        return caps


def run_static_checks(backend: Backend) -> StaticCheckResult:
    m = backend.manifest
    out = StaticCheckResult()

    # 1. Dedicated identity
    consumers = backend.role_consumers()
    if len(consumers) <= 1:
        out.evidence.append(Evidence(
            type=EvidenceType.INSPECTED_CONFIGURATION, level=EvidenceLevel.CONFIGURED,
            description=f"Agent role is used only by {', '.join(consumers) or 'nothing'}.",
            observed_at=backend.now(), source=f"{backend.name}:role_consumers",
            data={"consumers": consumers}))
    else:
        others = consumers[1:]
        out.shared_identity = others
        reason = "Agent role is also used by: " + ", ".join(others)
        if m.identity.dedicated:
            reason += " (contradicts manifest declaration identity.dedicated=true)"
        out.limitations.append(AssessmentLimitation(field="identity.role_arn", reason=reason,
                                                    blocking=True))

    # 2. Gateway bypass (boundary)
    direct = backend.direct_effect_allowed()
    table = m.protected_effect.store.table
    allowed = [a for a, ok in direct.items() if ok]
    if allowed:
        out.bypass_paths = [f"{a} on {table} as {m.identity.role_arn}" for a in allowed]
    else:
        out.evidence.append(Evidence(
            type=EvidenceType.INSPECTED_CONFIGURATION, level=EvidenceLevel.BOUNDARY_ASSESSED,
            description=(f"Agent role cannot perform {', '.join(direct) or 'any direct action'} "
                         f"on {table} without the gateway."),
            observed_at=backend.now(), source=f"{backend.name}:policy_simulation",
            data={"actions": direct}))

    # 3. Containment pre-staged
    ok, detail = backend.containment_prestaged()
    if ok:
        out.evidence.append(Evidence(
            type=EvidenceType.INSPECTED_CONFIGURATION, level=EvidenceLevel.CONFIGURED,
            description=detail, observed_at=backend.now(),
            source=f"{backend.name}:containment_prestaged"))
    else:
        out.limitations.append(AssessmentLimitation(field="containment_policy.mechanism",
                                                    reason=detail))
    # 4. Collateral probe must be observable on real backends
    if backend.is_real and not m.collateral_probe.url:
        out.limitations.append(AssessmentLimitation(
            field="collateral_probe.url",
            reason="No collateral URL configured; collateral impact will not be observed."))
    return out
