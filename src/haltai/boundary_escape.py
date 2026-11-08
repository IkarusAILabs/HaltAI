"""Claim 3 on the environment boundary: agents cannot take over HaltAI itself.

Roadmap section 7.1. In a dedicated agent account, HaltAI's **execution role** is the
one identity inside the boundary, and the only one the halt SCP exempts. An agent
that can assume, pass or modify it walks straight past the halt. The same goes for
an agent that can retarget the halt or protection SCPs, leave the organization
(which drops every SCP), edit HaltAI-staged controls before a halt, or, in a tagged
boundary, untag itself out of scope.

For every agent identity in the boundary, this module asks the backend whether it
may perform each action in the boundary catalogue, then reuses the Claim 3 rules
(``find_escape_paths`` and ``evaluate_claim3``). The result is policy evaluation,
not an attempted escape, so it is evidence at ``boundary_assessed`` level only.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from haltai.backends.env_base import EnvironmentBackend
from haltai.environment import BoundaryKind, EnvironmentDeclaration
from haltai.escape import EscapeAction, EscapeAssessment, EscapeKind, find_escape_paths
from haltai.evaluate import evaluate_claim3
from haltai.models import ClaimResult

EXECUTION_ROLE_PATH = "role/haltai/haltai-execution"  # what `prepare` creates if undeclared
MANAGED = "tag haltai:managed=true"
ALL = "*"


def execution_role_arn(env: EnvironmentDeclaration) -> str:
    return (env.operator.execution_role_arn
            or f"arn:aws:iam::{env.boundary.aws_account_id}:{EXECUTION_ROLE_PATH}")


def boundary_catalogue(env: EnvironmentDeclaration, principal: str) -> list[EscapeAction]:
    """Actions that would let ``principal`` (an agent identity) defeat HaltAI itself."""
    acct, region = env.boundary.aws_account_id, env.boundary.regions[0]
    kind = env.boundary.kind
    exec_role = execution_role_arn(env)
    out: list[EscapeAction] = []

    def add(action: str, resource: str, k: EscapeKind, why: str, condition: str = "") -> None:
        out.append(EscapeAction(action, resource, k, why, condition))

    # HaltAI's own identities
    add("sts:AssumeRole", exec_role, EscapeKind.HALTAI_IDENTITY,
        "could act as HaltAI's execution role, which the halt SCP exempts")
    add("iam:PassRole", exec_role, EscapeKind.HALTAI_IDENTITY,
        "could hand HaltAI's execution role to compute it controls")
    add("iam:UpdateAssumeRolePolicy", exec_role, EscapeKind.HALTAI_IDENTITY,
        "could let itself, or anyone, assume HaltAI's execution role")
    for action in ("iam:PutRolePolicy", "iam:AttachRolePolicy", "iam:DeleteRolePolicy",
                   "iam:DetachRolePolicy", "iam:PutRolePermissionsBoundary",
                   "iam:DeleteRolePermissionsBoundary", "iam:DeleteRole", "iam:TagRole",
                   "iam:UntagRole"):
        add(action, exec_role, EscapeKind.HALTAI_IDENTITY,
            "could modify, weaken, retag or delete HaltAI's execution role")
    add("sts:AssumeRole", env.operator.role_arn, EscapeKind.HALTAI_IDENTITY,
        "could act as the HaltAI operator role")

    # The halt and protection SCPs (dedicated account or OU)
    if kind in (BoundaryKind.ACCOUNT, BoundaryKind.OU):
        for action in ("organizations:DetachPolicy", "organizations:UpdatePolicy",
                       "organizations:DeletePolicy"):
            add(action, "arn:aws:organizations::*:policy/*", EscapeKind.HALTAI_CONTROL,
                "could detach or rewrite the halt or protection SCP")
        add("organizations:LeaveOrganization", ALL, EscapeKind.HALTAI_CONTROL,
            "leaving the organization removes every SCP, including the halt SCP")

    # HaltAI-staged controls, prepared before any halt
    nacl = f"arn:aws:ec2:{region}:{acct}:network-acl/*"
    for action in ("ec2:ReplaceNetworkAclAssociation", "ec2:CreateNetworkAclEntry",
                   "ec2:ReplaceNetworkAclEntry", "ec2:DeleteNetworkAclEntry",
                   "ec2:DeleteNetworkAcl"):
        add(action, nacl, EscapeKind.HALTAI_CONTROL,
            "could weaken or swap back the quarantine network ACL", MANAGED)
    staged_policies = f"arn:aws:iam::{acct}:policy/haltai/*"
    for action in ("iam:CreatePolicyVersion", "iam:SetDefaultPolicyVersion",
                   "iam:DeletePolicy"):
        add(action, staged_policies, EscapeKind.HALTAI_CONTROL,
            "could rewrite a staged HaltAI deny policy")
    for action in ("tag:TagResources", "tag:UntagResources", "ec2:CreateTags",
                   "ec2:DeleteTags"):
        add(action, ALL, EscapeKind.HALTAI_CONTROL,
            "could retag HaltAI-staged resources so the halt no longer targets them", MANAGED)

    # Tagged boundary: the identity lever is a deny policy on each agent role, and
    # scope is defined by tags the agent might remove.
    if kind == BoundaryKind.TAGGED:
        for action in ("iam:DetachRolePolicy", "iam:DeleteRolePolicy"):
            add(action, principal, EscapeKind.HALTAI_CONTROL,
                "could remove the halt deny policy from its own role")
        scope = ", ".join(f"{k}={v}" for k, v in env.boundary.tag_selector.items())
        add("iam:UntagRole", principal, EscapeKind.SCOPE,
            "could untag its own role out of the declared scope", f"tag {scope}")
        for action in ("tag:UntagResources", "ec2:DeleteTags"):
            add(action, ALL, EscapeKind.SCOPE,
                "could untag agent resources out of the declared scope", f"tag {scope}")
    return out


@dataclass
class BoundaryEscape:
    principals: list[str] = field(default_factory=list)
    assessment: EscapeAssessment = field(default_factory=EscapeAssessment)
    result: ClaimResult = ClaimResult.UNKNOWN
    rationale: list[str] = field(default_factory=list)


def assess_boundary(backend: EnvironmentBackend) -> BoundaryEscape:
    """Claim 3 on the boundary, for every agent identity the backend knows about."""
    out = BoundaryEscape(principals=backend.agent_principals())
    merged = out.assessment
    for principal in out.principals:
        catalogue = boundary_catalogue(backend.env, principal)
        res = find_escape_paths(catalogue, backend.boundary_escape_allowed(principal, catalogue))
        short = principal.rsplit(":", 1)[-1]
        merged.paths += [f"{short}: {p}" for p in res.paths]
        merged.unknown += [f"{short}: {u}" for u in res.unknown]
        merged.evaluated += res.evaluated
    if not out.principals:
        out.rationale = ["No agent identities are known in the boundary, so nothing could be "
                         "evaluated."]
        return out
    out.result, out.rationale = evaluate_claim3(merged)
    return out
