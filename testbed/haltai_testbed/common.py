"""Constructs shared by both variants: stub endpoints, collateral services, the operator role."""

from __future__ import annotations

from aws_cdk import CfnOutput, Duration, RemovalPolicy
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from constructs import Construct

from haltai_testbed.code import COLLATERAL, STUB
from haltai_testbed.config import BedConfig

RUNTIME = lambda_.Runtime.PYTHON_3_12


def log_group(scope: Construct, id: str) -> logs.LogGroup:
    return logs.LogGroup(scope, id, retention=logs.RetentionDays.ONE_WEEK,
                         removal_policy=RemovalPolicy.DESTROY)


def url_function(scope: Construct, id: str, *, code: str, env: dict[str, str],
                 role: iam.IRole | None = None, vpc: ec2.IVpc | None = None,
                 subnets: ec2.SubnetSelection | None = None,
                 security_groups: list[ec2.ISecurityGroup] | None = None,
                 ) -> tuple[lambda_.Function, str]:
    """A small Python function with a public function URL. Returns it and the URL."""
    fn = lambda_.Function(
        scope, id, runtime=RUNTIME, handler="index.handler", code=lambda_.Code.from_inline(code),
        environment=env, timeout=Duration.seconds(10), role=role, vpc=vpc, vpc_subnets=subnets,
        security_groups=security_groups, log_group=log_group(scope, f"{id}Logs"))
    url = fn.add_function_url(auth_type=lambda_.FunctionUrlAuthType.NONE)
    return fn, url.url


class Stubs(Construct):
    """Stub model and tool endpoints. They record calls and never forward them."""

    def __init__(self, scope: Construct, id: str) -> None:
        super().__init__(scope, id)
        _, self.model_url = url_function(self, "ModelStub", code=STUB,
                                         env={"STUB_NAME": "model"})
        _, self.tool_url = url_function(self, "ToolStub", code=STUB, env={"STUB_NAME": "tool"})
        CfnOutput(self, "ModelStubUrl", key="ModelStubUrl", value=self.model_url)
        CfnOutput(self, "ToolStubUrl", key="ToolStubUrl", value=self.tool_url)


def collateral_service(scope: Construct, id: str, name: str, *, path: str,
                       vpc: ec2.IVpc | None = None, subnets: ec2.SubnetSelection | None = None,
                       security_groups: list[ec2.ISecurityGroup] | None = None,
                       ) -> tuple[lambda_.Function, str]:
    managed = ["service-role/AWSLambdaBasicExecutionRole"]
    if vpc is not None:
        managed.append("service-role/AWSLambdaVPCAccessExecutionRole")
    role = iam.Role(scope, f"{id}Role", role_name=name, path=path,
                    assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
                    managed_policies=[iam.ManagedPolicy.from_aws_managed_policy_name(m)
                                      for m in managed])
    role.add_to_policy(iam.PolicyStatement(
        actions=["cloudwatch:PutMetricData"], resources=["*"],
        conditions={"StringEquals": {"cloudwatch:namespace": "HaltAITestbed"}}))
    return url_function(scope, id, code=COLLATERAL, env={"SERVICE_NAME": name}, role=role,
                        vpc=vpc, subnets=subnets, security_groups=security_groups)


def operator_role(scope: Construct, cfg: BedConfig, trusted_account: str) -> iam.Role:
    """The HaltAI operator role (roadmap 7.1). Outside the boundary in the dedicated variant."""
    role = iam.Role(scope, "OperatorRole", role_name="haltai-operator",
                    assumed_by=iam.AccountPrincipal(trusted_account),
                    description="HaltAI operator: attaches the halt SCP and assumes the "
                                "execution role. Testbed only.",
                    max_session_duration=Duration.hours(1))
    role.add_to_policy(iam.PolicyStatement(actions=["sts:AssumeRole"],
                                           resources=[cfg.execution_role_arn]))
    if cfg.dedicated:
        mgmt = cfg.management_account
        # Delegated SCP management (see the Organizations resource policy in the
        # management stack): attach and detach SCPs on the agent account only.
        role.add_to_policy(iam.PolicyStatement(
            actions=["organizations:AttachPolicy", "organizations:DetachPolicy"],
            resources=[f"arn:aws:organizations::{mgmt}:policy/o-*/service_control_policy/p-*",
                       f"arn:aws:organizations::{mgmt}:account/o-*/{cfg.agent_account}"]))
        role.add_to_policy(iam.PolicyStatement(
            actions=["organizations:Describe*", "organizations:List*"], resources=["*"]))
    CfnOutput(scope, "OperatorRoleArn", key="OperatorRoleArn", value=role.role_arn)
    return role
