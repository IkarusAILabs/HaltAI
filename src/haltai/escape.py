"""Claim 3 catalogue: actions that let the agent undo or route around its containment.

A containment control the agent can lift is not a control. This module lists the
permissions that would let the agent identity:

* lift or overwrite the containment itself (depends on the mechanism);
* grant itself new permissions, for example a direct write that skips the gateway;
* act as an identity the containment does not cover;
* start new compute under another role.

Backends only answer "can the agent role perform this action on this resource?".
``find_escape_paths`` turns those answers into escape paths.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from haltai.manifest import ContainmentMechanism, DeploymentManifest

WILDCARD = "*"


class EscapeKind(StrEnum):
    LIFT_CONTAINMENT = "lift_containment"
    SELF_ESCALATION = "self_escalation"
    OTHER_IDENTITY = "other_identity"
    NEW_COMPUTE = "new_compute"


@dataclass(frozen=True)
class EscapeAction:
    action: str
    resource: str
    kind: EscapeKind
    why: str

    @property
    def label(self) -> str:
        return f"{self.action} on {self.resource}"


# Creating compute is only an escape when the agent can also hand it another role.
PASS_ROLE = "iam:PassRole"
COMPUTE_ACTIONS = ("ecs:RunTask", "ecs:RegisterTaskDefinition", "lambda:CreateFunction",
                   "ec2:RunInstances")


def escape_catalogue(m: DeploymentManifest, region: str) -> list[EscapeAction]:
    role = m.identity.role_arn
    out: list[EscapeAction] = []
    mech = m.containment_policy.mechanism
    if mech == ContainmentMechanism.GATEWAY_RESOURCE_POLICY_DENY:
        api = f"arn:aws:apigateway:{region}::/restapis/{m.tool_gateway.rest_api_id or WILDCARD}"
        for action, resource in (("apigateway:UpdateRestApiPolicy", api),
                                 ("apigateway:PATCH", api),
                                 ("apigateway:POST", f"{api}/deployments")):
            out.append(EscapeAction(action, resource, EscapeKind.LIFT_CONTAINMENT,
                                    "could remove the gateway deny or redeploy without it"))
    elif mech == ContainmentMechanism.IAM_INLINE_DENY:
        out.append(EscapeAction("iam:DeleteRolePolicy", role, EscapeKind.LIFT_CONTAINMENT,
                                "could delete the containment inline policy"))
    for action in ("iam:PutRolePolicy", "iam:AttachRolePolicy"):
        out.append(EscapeAction(action, role, EscapeKind.SELF_ESCALATION,
                                "could grant itself new permissions or overwrite the deny"))
    for action, why in (("sts:AssumeRole", "could act as a role the containment does not cover"),
                        ("iam:CreateAccessKey", "could mint long-lived keys for another user")):
        out.append(EscapeAction(action, WILDCARD, EscapeKind.OTHER_IDENTITY, why))
    for action in (PASS_ROLE, *COMPUTE_ACTIONS):
        out.append(EscapeAction(action, WILDCARD, EscapeKind.NEW_COMPUTE,
                                "could start compute that runs under another role"))
    return out


@dataclass
class EscapeAssessment:
    paths: list[str] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)
    evaluated: int = 0


def find_escape_paths(catalogue: list[EscapeAction],
                      decisions: dict[str, bool | None]) -> EscapeAssessment:
    """``decisions`` maps ``EscapeAction.label`` to allowed (True), denied (False) or
    not evaluated (None, for example when policy simulation failed)."""
    out = EscapeAssessment()
    compute: list[EscapeAction] = []
    pass_role: bool | None = False
    for a in catalogue:
        d = decisions.get(a.label)
        if d is None:
            out.unknown.append(a.label)
        else:
            out.evaluated += 1
        if a.kind == EscapeKind.NEW_COMPUTE:
            if a.action == PASS_ROLE:
                pass_role = d
            elif d:
                compute.append(a)
        elif d:
            out.paths.append(f"{a.label}: {a.why}")
    if pass_role and compute:
        out.paths.append(f"{PASS_ROLE} + {', '.join(c.action for c in compute)}: "
                         f"{compute[0].why}")
    return out
