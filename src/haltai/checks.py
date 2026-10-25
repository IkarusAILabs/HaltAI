"""Static checks: evidence levels ``configured`` and ``boundary_assessed``.

Includes Claim 3 (the agent cannot undo or outrun its containment), which needs no
disruptive test.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from haltai.backends.base import Backend
from haltai.escape import EscapeAssessment, escape_catalogue, find_escape_paths
from haltai.manifest import ContainmentMechanism
from haltai.models import AssessmentLimitation, Evidence, EvidenceLevel, EvidenceType


@dataclass
class StaticCheckResult:
    evidence: list[Evidence] = field(default_factory=list)
    limitations: list[AssessmentLimitation] = field(default_factory=list)
    bypass_paths: list[str] = field(default_factory=list)
    shared_identity: list[str] = field(default_factory=list)
    escape: EscapeAssessment = field(default_factory=EscapeAssessment)
    escape_evidence: list[Evidence] = field(default_factory=list)

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
        if self.escape.paths:
            caps.append("Agent role can undo or route around containment: "
                        + "; ".join(self.escape.paths))
        return caps


ACTIVATION_NOTES = {
    ContainmentMechanism.GATEWAY_RESOURCE_POLICY_DENY: (
        "Containment is applied by rewriting the gateway resource policy and redeploying "
        "the whole stage at activation. It is not a pre-staged toggle, and the redeploy "
        "touches every route on the stage."),
    ContainmentMechanism.IAM_INLINE_DENY: (
        "Containment is applied by adding an inline policy to the agent role at "
        "activation. It is not a pre-staged toggle."),
}


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
                                                    reason=detail, blocking=True))
    note = ACTIVATION_NOTES.get(m.containment_policy.mechanism)
    if note:
        out.limitations.append(AssessmentLimitation(field="containment_policy.mechanism",
                                                    reason=note))
    pending = backend.pending_gateway_changes()
    if pending is None:
        out.limitations.append(AssessmentLimitation(
            field="tool_gateway.stage",
            reason="Could not compare the API with the deployed stage. Activation redeploys "
                   "the stage and would publish any undeployed change."))
    elif pending:
        out.limitations.append(AssessmentLimitation(
            field="tool_gateway.stage", blocking=True,
            reason="Activation redeploys the stage and would also publish these undeployed "
                   "changes: " + "; ".join(pending)))

    # 4. Claim 3: the agent cannot undo or outrun containment
    catalogue = escape_catalogue(m, m.region)
    out.escape = find_escape_paths(catalogue, backend.escape_actions_allowed(catalogue))
    if out.escape.evaluated and not out.escape.paths:
        out.escape_evidence.append(Evidence(
            type=EvidenceType.INSPECTED_CONFIGURATION, level=EvidenceLevel.BOUNDARY_ASSESSED,
            description=(f"Agent role cannot perform {out.escape.evaluated} of "
                         f"{len(catalogue)} checked actions that would lift containment, "
                         "escalate its own permissions, switch identity or start compute "
                         "under another role."),
            observed_at=backend.now(), source=f"{backend.name}:policy_simulation",
            data={"actions": [a.label for a in catalogue], "unknown": out.escape.unknown}))
    # 5. Collateral probe must be observable on real backends
    if backend.is_real and not m.collateral_probe.url:
        out.limitations.append(AssessmentLimitation(
            field="collateral_probe.url",
            reason="No collateral URL configured; collateral impact will not be observed."))
    return out
