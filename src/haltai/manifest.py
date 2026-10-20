"""Deployment manifest: the explicit bindings HaltAI assesses (roadmap section 4).

HaltAI does not discover agents. Every assessment starts from a manifest that
names exactly one deployment and its declared paths.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from haltai.models import AssessmentLimitation


class ContainmentMechanism(StrEnum):
    GATEWAY_RESOURCE_POLICY_DENY = "gateway_resource_policy_deny"
    IAM_INLINE_DENY = "iam_inline_deny"
    LOCAL_FLAG = "local_flag"  # anti-pattern, supported only to demonstrate the failure


class Runtime(BaseModel):
    kind: str = Field(default="ecs", description="v1 supports a dedicated ECS worker only")
    cluster: str
    service: str
    task_definition: str
    container: str = "agent"


class Identity(BaseModel):
    role_arn: str
    dedicated: bool = Field(description="Operator declaration: role is not shared with other apps")


class WorkIntake(BaseModel):
    kind: str = "sqs"
    queue_url: str
    visibility_timeout_s: int = 30


class ToolGateway(BaseModel):
    endpoint: str
    auth: str = "iam"
    rest_api_id: str | None = None
    stage: str = "v1"
    write_route: str = "POST /tools/synthetic_write"


class EffectStore(BaseModel):
    kind: str = "dynamodb"
    table: str = "haltai-markers"


class ProtectedEffect(BaseModel):
    name: str
    description: str
    synthetic: bool = True
    probe: str = Field(description="How to attempt the effect and observe success/failure")
    store: EffectStore = Field(default_factory=EffectStore)
    direct_actions: list[str] = Field(
        default_factory=lambda: ["dynamodb:PutItem"],
        description="Actions that would perform the effect without the gateway (bypass check)",
    )


class ContainmentPolicy(BaseModel):
    name: str
    mechanism: ContainmentMechanism = ContainmentMechanism.GATEWAY_RESOURCE_POLICY_DENY
    activation: str
    rollback: str


class CollateralProbe(BaseModel):
    name: str
    check: str
    url: str | None = Field(default=None, description="HTTP URL that must keep returning 200")
    role_arn: str | None = None


class Probes(BaseModel):
    results_table: str = "haltai-probe-results"
    timeout_s: int = 60


class Authorisation(BaseModel):
    approved_by: str
    environment: str = Field(description="Must be non-production for v1")
    disruptive_tests_allowed: bool = False


class DeploymentManifest(BaseModel):
    name: str
    version: str
    aws_account_id: str
    region: str
    runtime: Runtime
    identity: Identity
    work_intake: WorkIntake
    tool_gateway: ToolGateway
    protected_effect: ProtectedEffect
    containment_policy: ContainmentPolicy
    collateral_probe: CollateralProbe
    authorisation: Authorisation
    probes: Probes = Field(default_factory=Probes)
    assumptions: list[str] = Field(default_factory=list)
    unsupported_paths: list[str] = Field(default_factory=list)
    downstream_side_effects: list[str] = Field(default_factory=list)

    @property
    def declared_paths(self) -> list[str]:
        return [
            f"intake: {self.work_intake.kind} {self.work_intake.queue_url}",
            f"identity: {self.identity.role_arn}",
            f"runtime: {self.runtime.kind} {self.runtime.cluster}/{self.runtime.service}",
            f"gateway: {self.tool_gateway.write_route} via {self.tool_gateway.endpoint}",
            f"store: {self.protected_effect.store.kind} {self.protected_effect.store.table}",
        ]


def load_manifest(path: str | Path) -> DeploymentManifest:
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return DeploymentManifest.model_validate(data)


PRODUCTION_NAMES = {"prod", "production", "live"}


def check_prerequisites(m: DeploymentManifest) -> list[AssessmentLimitation]:
    """Return limitations for prerequisites that are not met.

    ``blocking`` limitations stop a drill against a real backend unless the operator
    explicitly accepts them.
    """
    issues: list[AssessmentLimitation] = []
    if m.runtime.kind != "ecs":
        issues.append(
            AssessmentLimitation(field="runtime.kind", reason="v1 supports ECS only", blocking=True)
        )
    if not m.identity.dedicated:
        issues.append(
            AssessmentLimitation(
                field="identity.dedicated",
                reason="Shared identity makes selective enforcement unsafe",
                blocking=True,
            )
        )
    if m.authorisation.environment.lower() in PRODUCTION_NAMES:
        issues.append(
            AssessmentLimitation(
                field="authorisation.environment",
                reason="v1 drills must run in a non-production environment",
                blocking=True,
            )
        )
    if not m.protected_effect.synthetic:
        issues.append(
            AssessmentLimitation(
                field="protected_effect.synthetic",
                reason="v1 requires a test-safe synthetic write",
                blocking=True,
            )
        )
    if not m.containment_policy.rollback.strip():
        issues.append(
            AssessmentLimitation(
                field="containment_policy.rollback",
                reason="A rollback procedure is required",
                blocking=True,
            )
        )
    if m.containment_policy.mechanism == ContainmentMechanism.LOCAL_FLAG:
        issues.append(
            AssessmentLimitation(
                field="containment_policy.mechanism",
                reason="A local flag is not enforced outside the running worker",
            )
        )
    if not m.unsupported_paths:
        issues.append(
            AssessmentLimitation(
                field="unsupported_paths",
                reason="No unsupported paths declared; confirm this is deliberate",
            )
        )
    return issues
