"""Environment declaration: the boundary the break-glass commands may act on.

Roadmap sections 5–7. Where ``deployment.yaml`` binds one agent deployment for
drills, ``environment.yaml`` declares a whole boundary (an AWS account, an OU or a
tagged slice of a shared account), the identities that operate HaltAI, the
resources each lever acts on, and who owns recovery.

Validation never treats a gap as a pass: every problem becomes an
``AssessmentLimitation``, blocking where acting on the declaration would be unsafe.
"""

from __future__ import annotations

import re
from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator

from haltai.manifest import PRODUCTION_NAMES
from haltai.models import AssessmentLimitation

ACCOUNT_ID = re.compile(r"^\d{12}$")
ARN_ACCOUNT = re.compile(r"^arn:aws[\w-]*:iam::(\d{12}):")


class BoundaryKind(StrEnum):
    ACCOUNT = "account"  # a dedicated agent account (preferred)
    OU = "ou"  # an organizational unit of agent accounts
    TAGGED = "tagged"  # tagged resources and subnets inside a shared account (weaker)


class Lever(StrEnum):
    IDENTITY = "identity"
    NETWORK = "network"
    INTAKE = "intake"
    WORKLOAD = "workload"


class Profile(StrEnum):
    HALT_AGENTS = "halt-agents"
    ISOLATE = "isolate"


# Default halt order (roadmap section 6): cut the ability to act, then stop the actors.
LEVER_ORDER = [Lever.IDENTITY, Lever.NETWORK, Lever.INTAKE, Lever.WORKLOAD]


class Boundary(BaseModel):
    kind: BoundaryKind = BoundaryKind.ACCOUNT
    aws_account_id: str
    organization_unit_id: str | None = None
    regions: list[str] = Field(min_length=1)
    tag_selector: dict[str, str] = Field(default_factory=dict)
    subnets: list[str] = Field(default_factory=list,
                               description="Agent subnets (network lever)")

    @field_validator("aws_account_id")
    @classmethod
    def _account_id(cls, v: str) -> str:
        if not ACCOUNT_ID.match(v):
            raise ValueError("aws_account_id must be 12 digits")
        return v


class Operator(BaseModel):
    role_arn: str = Field(description="Operator role; must live outside the boundary")
    execution_role_arn: str | None = Field(
        default=None,
        description="HaltAI execution role inside the agent account (roadmap 7.1); "
                    "created by `prepare` when omitted")


class IdentityLever(BaseModel):
    roles: list[str] = Field(default_factory=list,
                             description="Agent role ARNs, or a path prefix ending in *")
    revoke_sessions: bool = True


class NetworkLever(BaseModel):
    model_endpoints: list[str] = Field(default_factory=list)
    tool_endpoints: list[str] = Field(default_factory=list)


class WorkloadLever(BaseModel):
    ecs_services: list[str] = Field(default_factory=list, description="cluster/service")
    lambda_functions: list[str] = Field(default_factory=list)


class IntakeLever(BaseModel):
    eventbridge_rules: list[str] = Field(default_factory=list)
    schedules: list[str] = Field(default_factory=list, description="group/name")
    event_source_mappings: list[str] = Field(default_factory=list)
    sqs_queues: list[str] = Field(default_factory=list)


class Service(BaseModel):
    name: str
    url: str | None = None


class Collateral(BaseModel):
    in_boundary: list[Service] = Field(default_factory=list,
                                       description="May be interrupted by `isolate`")
    out_of_boundary: list[Service] = Field(default_factory=list,
                                           description="Must never be interrupted")


class Recovery(BaseModel):
    owners: list[str] = Field(default_factory=list)
    manual_fallback: str | None = None


class EnvironmentAuthorisation(BaseModel):
    isolate_requires_two_approvers: bool = True


class EnvironmentDeclaration(BaseModel):
    name: str
    environment: str = Field(description="Production names are refused until roadmap Phase 4")
    boundary: Boundary
    operator: Operator
    levers: dict[Profile, list[Lever]] = Field(
        default_factory=lambda: {Profile.HALT_AGENTS: list(LEVER_ORDER)})
    identity: IdentityLever = Field(default_factory=IdentityLever)
    network: NetworkLever = Field(default_factory=NetworkLever)
    workload: WorkloadLever = Field(default_factory=WorkloadLever)
    intake: IntakeLever = Field(default_factory=IntakeLever)
    collateral: Collateral = Field(default_factory=Collateral)
    recovery: Recovery = Field(default_factory=Recovery)
    authorisation: EnvironmentAuthorisation = Field(default_factory=EnvironmentAuthorisation)

    def covered(self, lever: Lever) -> bool:
        """Does the declaration name any resource for this lever?"""
        if lever == Lever.IDENTITY:
            return bool(self.identity.roles)
        if lever == Lever.NETWORK:
            return bool(self.boundary.subnets)
        if lever == Lever.WORKLOAD:
            return bool(self.workload.ecs_services or self.workload.lambda_functions)
        i = self.intake
        return bool(i.eventbridge_rules or i.schedules or i.event_source_mappings
                    or i.sqs_queues)

    def lever_coverage(self, profile: Profile) -> dict[Lever, bool]:
        """Levers of a profile in halt order, and whether each is covered."""
        enabled = self.levers.get(profile, [])
        return {lv: self.covered(lv) for lv in LEVER_ORDER if lv in enabled}


def load_environment(path: str | Path) -> EnvironmentDeclaration:
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return EnvironmentDeclaration.model_validate(data)


def _arn_account(arn: str) -> str | None:
    m = ARN_ACCOUNT.match(arn)
    return m.group(1) if m else None


def check_environment(e: EnvironmentDeclaration) -> list[AssessmentLimitation]:
    """Limitations of a declaration. ``blocking`` ones stop `prepare` and `halt`."""
    out: list[AssessmentLimitation] = []

    def add(field: str, reason: str, blocking: bool = False) -> None:
        out.append(AssessmentLimitation(field=field, reason=reason, blocking=blocking))

    b, kind = e.boundary, e.boundary.kind
    if e.environment.lower() in PRODUCTION_NAMES:
        add("environment", "Halting production is refused until the roadmap's Phase 4 "
                           "operational-safety gate has passed.", blocking=True)

    # Boundary shape
    if kind == BoundaryKind.TAGGED and not b.tag_selector:
        add("boundary.tag_selector", "A tagged boundary needs a tag selector; without it "
                                     "nothing identifies what is in scope.", blocking=True)
    if kind == BoundaryKind.OU and not b.organization_unit_id:
        add("boundary.organization_unit_id", "An OU boundary needs the OU ID.", blocking=True)
    network_on = any(Lever.NETWORK in levers for levers in e.levers.values())
    if kind == BoundaryKind.TAGGED and network_on and not b.subnets:
        add("boundary.subnets", "The network lever is enabled but no agent subnets are "
                                "declared for this tagged boundary.", blocking=True)

    # Operator outside, execution role inside (roadmap 7.1)
    op_account = _arn_account(e.operator.role_arn)
    if op_account is None:
        add("operator.role_arn", "Not an IAM role ARN.", blocking=True)
    elif kind == BoundaryKind.ACCOUNT and op_account == b.aws_account_id:
        add("operator.role_arn", "The operator role is inside the boundary account; agents "
                                 "in that account could reach it. Place it in a separate "
                                 "security or tooling account.", blocking=True)
    elif kind == BoundaryKind.OU:
        add("operator.role_arn", "Whether the operator's account is outside the OU can only "
                                 "be checked live; `prepare` will verify it.")
    elif kind == BoundaryKind.TAGGED and op_account == b.aws_account_id:
        add("operator.role_arn", "The operator role shares the account with the agents. "
                                 "`prepare` must show it is not tagged into scope and that "
                                 "no agent identity can modify or assume it.")
    exec_arn = e.operator.execution_role_arn
    if exec_arn is not None:
        exec_account = _arn_account(exec_arn)
        if exec_account is None:
            add("operator.execution_role_arn", "Not an IAM role ARN.", blocking=True)
        elif kind == BoundaryKind.ACCOUNT and exec_account != b.aws_account_id:
            add("operator.execution_role_arn", "The execution role must be in the agent "
                                               "account: in-account levers and the canary "
                                               "can't act across accounts.", blocking=True)
    elif kind == BoundaryKind.ACCOUNT:
        add("operator.execution_role_arn", "Not declared; `prepare` will create it with the "
                                           "protections in roadmap section 7.1.")

    # Identities outside the boundary can't be reached by the identity lever
    if kind == BoundaryKind.ACCOUNT:
        for role in e.identity.roles:
            acct = _arn_account(role)
            if acct is not None and acct != b.aws_account_id:
                add("identity.roles", f"{role} is outside the boundary account, so the "
                                      "identity lever can't reach it.", blocking=True)

    # Profiles and lever coverage
    if not any(e.levers.values()):
        add("levers", "No profile enables any lever.", blocking=True)
    for profile, coverage in ((p, e.lever_coverage(p)) for p in e.levers):
        for lever, ok in coverage.items():
            if not ok:
                add(f"levers.{profile.value}",
                    f"The {lever.value} lever is enabled but no resources are declared for "
                    "it; it will be reported as not_covered.")
    if Profile.ISOLATE in e.levers and not e.collateral.in_boundary:
        add("collateral.in_boundary", "The isolate profile is enabled but no in-boundary "
                                      "services are declared, so its blast radius can't be "
                                      "observed.")

    # Recovery
    if not e.recovery.owners:
        add("recovery.owners", "No recovery owner is named.", blocking=True)
    if not (e.recovery.manual_fallback or "").strip():
        add("recovery.manual_fallback", "No documented manual fallback.", blocking=True)
    return out
