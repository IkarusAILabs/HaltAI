"""Deployment manifest: the explicit bindings HaltAI assesses (roadmap section 4).

HaltAI does not discover agents. Every assessment starts from a manifest that
names exactly one deployment and its declared paths.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from haltai.models import AssessmentLimitation


class Runtime(BaseModel):
    kind: str = Field(default="ecs", description="v1 supports a dedicated ECS worker only")
    cluster: str
    service: str
    task_definition: str


class Identity(BaseModel):
    role_arn: str
    dedicated: bool = Field(description="Operator declaration: role is not shared with other apps")


class WorkIntake(BaseModel):
    kind: str = "sqs"
    queue_url: str


class ToolGateway(BaseModel):
    endpoint: str
    auth: str = "iam"


class ProtectedEffect(BaseModel):
    name: str
    description: str
    synthetic: bool = True
    probe: str = Field(description="How to attempt the effect and observe success/failure")


class ContainmentPolicy(BaseModel):
    name: str
    activation: str
    rollback: str


class CollateralProbe(BaseModel):
    name: str
    check: str


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
    assumptions: list[str] = Field(default_factory=list)
    unsupported_paths: list[str] = Field(default_factory=list)
    downstream_side_effects: list[str] = Field(default_factory=list)


def load_manifest(path: str | Path) -> DeploymentManifest:
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return DeploymentManifest.model_validate(data)


def check_prerequisites(m: DeploymentManifest) -> list[AssessmentLimitation]:
    """Return limitations for prerequisites that are not met."""
    issues: list[AssessmentLimitation] = []
    if m.runtime.kind != "ecs":
        issues.append(AssessmentLimitation(field="runtime.kind", reason="v1 supports ECS only"))
    if not m.identity.dedicated:
        issues.append(
            AssessmentLimitation(
                field="identity.dedicated",
                reason="Shared identity makes selective enforcement unsafe",
            )
        )
    if m.authorisation.environment.lower() in {"prod", "production"}:
        issues.append(
            AssessmentLimitation(
                field="authorisation.environment",
                reason="v1 drills must run in a non-production environment",
            )
        )
    if not m.protected_effect.synthetic:
        issues.append(
            AssessmentLimitation(
                field="protected_effect.synthetic",
                reason="v1 requires a test-safe synthetic write",
            )
        )
    if not m.containment_policy.rollback.strip():
        issues.append(
            AssessmentLimitation(
                field="containment_policy.rollback", reason="A rollback procedure is required"
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
