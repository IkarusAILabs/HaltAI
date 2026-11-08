"""The testbed's stacks, per variant."""

from __future__ import annotations

from aws_cdk import App, CfnOutput, Environment, Stack, Tags
from aws_cdk import aws_organizations as organizations
from constructs import Construct

from haltai_testbed.agents import AgentEnvironment
from haltai_testbed.common import Stubs, collateral_service, operator_role
from haltai_testbed.config import TESTBED_TAG, BedConfig
from haltai_testbed.scps import delegation_policy, halt_agents_scp, isolate_scp, protection_scp


class ManagementStack(Stack):
    """Management account: the staged halt SCPs and the standing protection SCP."""

    def __init__(self, scope: Construct, id: str, *, cfg: BedConfig, **kw: object) -> None:
        super().__init__(scope, id, **kw)  # type: ignore[arg-type]
        policies = {
            "HaltAgentsScp": ("haltai-halt-agents", halt_agents_scp(cfg), [],
                              "HaltAI halt-agents: staged, attached only by `halt`"),
            "IsolateScp": ("haltai-isolate", isolate_scp(cfg), [],
                           "HaltAI isolate: staged, attached only by `halt`"),
            "ProtectionScp": ("haltai-protection", protection_scp(cfg), [cfg.agent_account],
                              "HaltAI protection: attached at all times (roadmap 7.1)"),
        }
        for logical_id, (name, content, targets, description) in policies.items():
            policy = organizations.CfnPolicy(
                self, logical_id, name=name, type="SERVICE_CONTROL_POLICY", content=content,
                description=description, target_ids=targets or None)
            CfnOutput(self, f"{logical_id}Id", key=f"{logical_id}Id", value=policy.attr_id)
        organizations.CfnResourcePolicy(self, "DelegateToTooling",
                                        content=delegation_policy(cfg))


class ToolingStack(Stack):
    """Security or tooling account: operator role, stub endpoints, an outside service."""

    def __init__(self, scope: Construct, id: str, *, cfg: BedConfig, **kw: object) -> None:
        super().__init__(scope, id, **kw)  # type: ignore[arg-type]
        operator_role(self, cfg, trusted_account=cfg.tooling_account)
        Stubs(self, "Stubs")
        _, url = collateral_service(self, "BillingApi", "billing-api", path="/services/")
        CfnOutput(self, "OutOfBoundaryCollateralUrl", key="OutOfBoundaryCollateralUrl",
                  value=url)


class AgentsStack(Stack):
    """Dedicated agent account: the whole account is the boundary."""

    def __init__(self, scope: Construct, id: str, *, cfg: BedConfig, **kw: object) -> None:
        super().__init__(scope, id, **kw)  # type: ignore[arg-type]
        self.env_construct = AgentEnvironment(
            self, "Agents", cfg=cfg, model_url=cfg.model_stub_url, tool_url=cfg.tool_stub_url,
            operator_role_arn=cfg.operator_role_arn)


class SharedStack(Stack):
    """Shared account: tagged agents, an out-of-boundary service, HaltAI and the stubs."""

    def __init__(self, scope: Construct, id: str, *, cfg: BedConfig, **kw: object) -> None:
        super().__init__(scope, id, **kw)  # type: ignore[arg-type]
        stubs = Stubs(self, "Stubs")
        op = operator_role(self, cfg, trusted_account=cfg.agent_account)
        self.env_construct = AgentEnvironment(
            self, "Agents", cfg=cfg, model_url=stubs.model_url, tool_url=stubs.tool_url,
            operator_role_arn=op.role_arn)


def build(app: App, cfg: BedConfig) -> list[Stack]:
    def env(account: str) -> Environment:
        return Environment(account=account, region=cfg.region)

    Tags.of(app).add(*TESTBED_TAG)
    Tags.of(app).add("haltai:environment", cfg.environment)
    if cfg.dedicated:
        return [
            ManagementStack(app, "HaltAITestbed-Management", cfg=cfg,
                            env=env(cfg.management_account)),
            ToolingStack(app, "HaltAITestbed-Tooling", cfg=cfg, env=env(cfg.tooling_account)),
            AgentsStack(app, "HaltAITestbed-Agents", cfg=cfg, env=env(cfg.agent_account)),
        ]
    return [SharedStack(app, "HaltAITestbed-Shared", cfg=cfg, env=env(cfg.agent_account))]
