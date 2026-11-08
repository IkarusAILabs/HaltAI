"""The agent environment: workloads, intake, observability, and HaltAI's in-account pieces.

Used by both variants. In the dedicated variant it is the whole agent account. In the shared
variant the agent resources are tagged ``haltai:scope=agents`` and share the account with an
out-of-boundary service.
"""

from __future__ import annotations

from aws_cdk import CfnOutput, Duration, Fn, RemovalPolicy, SecretValue, Tags
from aws_cdk import aws_cloudtrail as cloudtrail
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_lambda_event_sources as sources
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_scheduler as scheduler
from aws_cdk import aws_secretsmanager as secretsmanager
from aws_cdk import aws_sqs as sqs
from constructs import Construct

from haltai_testbed.code import AGENT_IMAGE, AGENT_LOOP, LAMBDA_AGENT
from haltai_testbed.common import RUNTIME, collateral_service, log_group
from haltai_testbed.config import MANAGED_TAG, SCOPE_TAG, BedConfig

AGENTS = ec2.SubnetSelection(subnet_group_name="agents")
SERVICES = ec2.SubnetSelection(subnet_group_name="services")
ENDPOINTS = "haltai-endpoints"


def _managed(*constructs: Construct) -> None:
    for c in constructs:
        Tags.of(c).add(*MANAGED_TAG)


def _out(scope: Construct, key: str, value: str) -> None:
    CfnOutput(scope, key, key=key, value=value)


class AgentEnvironment(Construct):
    def __init__(self, scope: Construct, id: str, *, cfg: BedConfig, model_url: str,
                 tool_url: str, operator_role_arn: str) -> None:
        super().__init__(scope, id)
        self.cfg = cfg
        self._network()
        self._agents(model_url, tool_url)
        self._intake()
        self._collateral()
        self._observability()
        self._execution_role(operator_role_arn)
        self._canary()
        self._staged_controls()
        self._toggles()
        self._outputs(model_url, tool_url)

    # --- network ---------------------------------------------------------------------

    def _network(self) -> None:
        cfg = self.cfg
        groups = [
            ec2.SubnetConfiguration(name="public", subnet_type=ec2.SubnetType.PUBLIC,
                                    cidr_mask=24),
            ec2.SubnetConfiguration(name="agents",
                                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                                    cidr_mask=24),
            ec2.SubnetConfiguration(name="services",
                                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                                    cidr_mask=24),
        ]
        if cfg.canary_endpoints:
            groups.append(ec2.SubnetConfiguration(
                name=ENDPOINTS, subnet_type=ec2.SubnetType.PRIVATE_ISOLATED, cidr_mask=28))
        self.vpc = ec2.Vpc(
            self, "Vpc", ip_addresses=ec2.IpAddresses.cidr("10.80.0.0/16"),
            availability_zones=[f"{cfg.region}a", f"{cfg.region}b"],
            nat_gateways=1, subnet_configuration=groups)
        self.agent_subnets = self.vpc.select_subnets(subnet_group_name="agents").subnets
        self.agent_sg = ec2.SecurityGroup(self, "AgentSg", vpc=self.vpc,
                                          description="Agent workloads (testbed)",
                                          allow_all_outbound=True)

    # --- agents ----------------------------------------------------------------------

    def _agents(self, model_url: str, tool_url: str) -> None:
        self.results = sqs.Queue(self, "AgentResults", retention_period=Duration.days(1))
        self.jobs = sqs.Queue(self, "AgentJobs", queue_name="agent-jobs",
                              visibility_timeout=Duration.seconds(60),
                              retention_period=Duration.days(1))

        # ECS "research agent"
        self.task_role = iam.Role(self, "ResearchAgentRole", role_name="research-agent",
                                  path="/agents/",
                                  assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"))
        self.results.grant_send_messages(self.task_role)
        self.cluster = ecs.Cluster(self, "AgentsCluster", cluster_name="agents", vpc=self.vpc)
        self.task_def = ecs.FargateTaskDefinition(
            self, "ResearchAgentTask", family="research-agent", cpu=256, memory_limit_mib=512,
            task_role=self.task_role)
        self._agent_secrets: dict[str, ecs.Secret] = {}
        if self.cfg.on("untracked_credential"):
            self._untracked_credential()
        self.task_def.add_container(
            "agent", image=ecs.ContainerImage.from_registry(AGENT_IMAGE),
            entry_point=["/bin/sh", "-c"], command=[AGENT_LOOP],
            environment={"MODEL_URL": model_url, "TOOL_URL": tool_url,
                         "RESULTS_QUEUE_URL": self.results.queue_url,
                         "AWS_REGION": self.cfg.region},
            secrets=self._agent_secrets or None,
            logging=ecs.LogDrivers.aws_logs(stream_prefix="research-agent",
                                            log_group=log_group(self, "ResearchAgentLogs")))
        self.service = ecs.FargateService(
            self, "ResearchAgent", service_name="research-agent", cluster=self.cluster,
            task_definition=self.task_def, desired_count=1, vpc_subnets=AGENTS,
            security_groups=[self.agent_sg], assign_public_ip=False,
            min_healthy_percent=0, max_healthy_percent=200)

        # Lambda "summariser agent", fed by SQS
        self.lambda_role = iam.Role(
            self, "SummariserAgentRole", role_name="summariser-agent", path="/agents/",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[iam.ManagedPolicy.from_aws_managed_policy_name(
                "service-role/AWSLambdaVPCAccessExecutionRole")])
        self.results.grant_send_messages(self.lambda_role)
        self.function = lambda_.Function(
            self, "SummariserAgent", function_name="summariser-agent", runtime=RUNTIME,
            handler="index.handler", code=lambda_.Code.from_inline(LAMBDA_AGENT),
            role=self.lambda_role, vpc=self.vpc, vpc_subnets=AGENTS,
            security_groups=[self.agent_sg], timeout=Duration.seconds(30),
            environment={"MODEL_URL": model_url, "TOOL_URL": tool_url,
                         "RESULTS_QUEUE_URL": self.results.queue_url},
            log_group=log_group(self, "SummariserAgentLogs"))
        self.esm = sources.SqsEventSource(self.jobs, batch_size=1)
        self.function.add_event_source(self.esm)

        if not self.cfg.dedicated:
            for c in (self.task_role, self.lambda_role, self.cluster, self.task_def,
                      self.service, self.function, self.jobs, self.results, self.agent_sg,
                      *self.agent_subnets):
                Tags.of(c).add(*SCOPE_TAG)

    def _untracked_credential(self) -> None:
        user = iam.User(self, "UntrackedUser", user_name="agent-untracked-key")
        self.results.grant_send_messages(user)
        key = iam.AccessKey(self, "UntrackedKey", user=user)
        secret = secretsmanager.Secret(self, "UntrackedKeySecret", secret_object_value={
            "AWS_ACCESS_KEY_ID": SecretValue.unsafe_plain_text(key.access_key_id),
            "AWS_SECRET_ACCESS_KEY": key.secret_access_key,
        }, removal_policy=RemovalPolicy.DESTROY)
        self._agent_secrets = {
            name: ecs.Secret.from_secrets_manager(secret, name)
            for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")
        }

    # --- intake ----------------------------------------------------------------------

    def _intake(self) -> None:
        self.rule = events.Rule(
            self, "AgentRunRule", rule_name="agent-run",
            schedule=events.Schedule.rate(Duration.minutes(15)),
            targets=[targets.SqsQueue(self.jobs,
                                      message=events.RuleTargetInput.from_text("agent-run"))])
        self.scheduler_role = iam.Role(
            self, "SchedulerRole", assumed_by=iam.ServicePrincipal("scheduler.amazonaws.com"))
        self.function.grant_invoke(self.scheduler_role)
        group = scheduler.CfnScheduleGroup(self, "AgentsScheduleGroup", name="agents")
        self.schedule = scheduler.CfnSchedule(
            self, "DigestSchedule", name="digest", group_name="agents",
            schedule_expression="rate(15 minutes)",
            flexible_time_window=scheduler.CfnSchedule.FlexibleTimeWindowProperty(mode="OFF"),
            target=scheduler.CfnSchedule.TargetProperty(
                arn=self.function.function_arn, role_arn=self.scheduler_role.role_arn,
                input='{"source": "digest"}'))
        self.schedule.add_dependency(group)
        if not self.cfg.dedicated:
            for c in (self.rule, group, self.schedule):
                Tags.of(c).add(*SCOPE_TAG)

    # --- collateral ------------------------------------------------------------------

    def _collateral(self) -> None:
        svc_sg = ec2.SecurityGroup(self, "ServiceSg", vpc=self.vpc,
                                   description="In-boundary service (testbed)")
        _, self.in_boundary_url = collateral_service(
            self, "MetricsExporter", "metrics-exporter", path="/services/", vpc=self.vpc,
            subnets=SERVICES, security_groups=[svc_sg])
        self.out_of_boundary_url: str | None = None
        if not self.cfg.dedicated:
            # Shares the account and VPC with the agents but is not tagged into scope.
            # With shared_nat it also shares the agent subnets.
            out_sg = ec2.SecurityGroup(self, "BillingSg", vpc=self.vpc,
                                       description="Out-of-boundary service (testbed)")
            subnets = AGENTS if self.cfg.on("shared_nat") else SERVICES
            _, self.out_of_boundary_url = collateral_service(
                self, "BillingApi", "billing-api", path="/services/", vpc=self.vpc,
                subnets=subnets, security_groups=[out_sg])

    # --- observability ---------------------------------------------------------------

    def _observability(self) -> None:
        self.flow_logs = log_group(self, "FlowLogs")
        ec2.FlowLog(self, "VpcFlowLog", resource_type=ec2.FlowLogResourceType.from_vpc(self.vpc),
                    destination=ec2.FlowLogDestination.to_cloud_watch_logs(self.flow_logs),
                    traffic_type=ec2.FlowLogTrafficType.ALL)
        bucket = s3.Bucket(self, "TrailBucket", enforce_ssl=True,
                           block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
                           encryption=s3.BucketEncryption.S3_MANAGED,
                           removal_policy=RemovalPolicy.DESTROY, auto_delete_objects=True,
                           lifecycle_rules=[s3.LifecycleRule(expiration=Duration.days(14))])
        self.trail = cloudtrail.Trail(self, "Trail", trail_name="haltai-testbed", bucket=bucket,
                                      is_multi_region_trail=False,
                                      include_global_service_events=True)

    # --- HaltAI execution role (roadmap 7.1) -------------------------------------------

    def _execution_role(self, operator_role_arn: str) -> None:
        cfg = self.cfg
        principal: iam.IPrincipal = iam.ArnPrincipal(operator_role_arn)
        if cfg.on("agent_can_assume_execution_role"):
            principal = iam.CompositePrincipal(principal,
                                               iam.ArnPrincipal(self.task_role.role_arn))
        role = iam.Role(self, "ExecutionRole", role_name="haltai-execution", path="/haltai/",
                        assumed_by=principal, max_session_duration=Duration.hours(1),
                        description="HaltAI execution role: in-account levers and the canary. "
                                    "Only the operator role may assume it.")
        self.exec_role = role
        acct, region = cfg.agent_account, cfg.region
        cluster_arn = self.cluster.cluster_arn

        def allow(actions: list[str], resources: list[str],
                  conditions: dict | None = None) -> None:
            role.add_to_policy(iam.PolicyStatement(actions=actions, resources=resources,
                                                   conditions=conditions))

        # Read state for prepare and status
        allow(["ec2:Describe*", "ecs:Describe*", "ecs:List*", "lambda:Get*", "lambda:List*",
               "events:Describe*", "events:List*", "scheduler:Get*", "scheduler:List*",
               "application-autoscaling:Describe*", "cloudwatch:GetMetricData",
               "cloudtrail:LookupEvents", "sqs:GetQueueAttributes", "iam:SimulatePrincipalPolicy",
               "iam:Get*", "iam:List*"], ["*"])
        allow(["logs:FilterLogEvents", "logs:StartQuery", "logs:GetQueryResults"],
              [self.flow_logs.log_group_arn])
        # Network lever
        allow(["ec2:ReplaceNetworkAclAssociation"],
              [f"arn:aws:ec2:{region}:{acct}:network-acl/*", f"arn:aws:ec2:{region}:{acct}:vpc/*"])
        allow(["ec2:ModifyNetworkInterfaceAttribute"],
              [f"arn:aws:ec2:{region}:{acct}:network-interface/*",
               f"arn:aws:ec2:{region}:{acct}:security-group/*"])
        # Workload lever
        allow(["ecs:UpdateService"], [self.service.service_arn])
        allow(["ecs:StopTask"], ["*"], {"ArnEquals": {"ecs:cluster": cluster_arn}})
        allow(["application-autoscaling:RegisterScalableTarget"], ["*"])
        allow(["lambda:PutFunctionConcurrency", "lambda:DeleteFunctionConcurrency"],
              [self.function.function_arn])
        # Work-intake lever
        allow(["events:DisableRule", "events:EnableRule"], [self.rule.rule_arn])
        allow(["scheduler:UpdateSchedule"],
              [f"arn:aws:scheduler:{region}:{acct}:schedule/agents/*"])
        allow(["iam:PassRole"], [self.scheduler_role.role_arn],
              {"StringEquals": {"iam:PassedToService": "scheduler.amazonaws.com"}})
        allow(["lambda:UpdateEventSourceMapping"],
              [f"arn:aws:lambda:{region}:{acct}:event-source-mapping:*"])
        allow(["sqs:SetQueueAttributes"], [self.jobs.queue_arn])
        if not cfg.dedicated:
            # Identity lever in a shared account: attach the staged deny policy to agent roles
            allow(["iam:AttachRolePolicy", "iam:DetachRolePolicy"], [cfg.agent_roles_pattern],
                  {"ArnEquals": {"iam:PolicyARN":
                                 f"arn:aws:iam::{acct}:policy/haltai/haltai-halt-agents"}})
        _managed(role)

    # --- canary (docs/design/canary-probe.md) ------------------------------------------

    def _canary(self) -> None:
        cfg = self.cfg
        acct, region = cfg.agent_account, cfg.region
        self.canary_repo = ecr.Repository(self, "CanaryRepo", repository_name="haltai/canary",
                                          image_scan_on_push=True,
                                          removal_policy=RemovalPolicy.DESTROY,
                                          empty_on_delete=True)
        self.canary_cluster = ecs.Cluster(self, "CanaryCluster", cluster_name="haltai-canary",
                                          vpc=self.vpc)
        pull_role = iam.Role(self, "CanaryPullRole", role_name="haltai-canary-pull",
                             path="/haltai/",
                             assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"))
        self.canary_repo.grant_pull(pull_role)
        # The canary image arrives in 0.5.0. Until then the task definition points at a tag
        # that does not exist, so a run fails to start: `inconclusive`, never `stopped`.
        canary_td = ecs.FargateTaskDefinition(self, "CanaryTask", family="haltai-canary",
                                              cpu=256, memory_limit_mib=512,
                                              execution_role=pull_role,
                                              task_role=self.task_role)
        canary_td.add_container("canary", essential=True,
                                image=ecs.ContainerImage.from_ecr_repository(
                                    self.canary_repo, tag="pending-0.5.0"))
        self.canary_queue = sqs.Queue(self, "CanaryQueue", queue_name="haltai-canary",
                                      retention_period=Duration.seconds(60))
        # Granted by the queue policy, so the agent roles themselves are unchanged.
        self.canary_queue.add_to_resource_policy(iam.PolicyStatement(
            principals=[iam.ArnPrincipal(self.task_role.role_arn),
                        iam.ArnPrincipal(self.lambda_role.role_arn)],
            actions=["sqs:SendMessage"], resources=[self.canary_queue.queue_arn]))
        marker = ec2.SecurityGroup(self, "CanaryMarker", vpc=self.vpc, allow_all_outbound=False,
                                   security_group_name="haltai-canary-marker",
                                   description="Empty: marks HaltAI canary network interfaces")

        role = self.exec_role
        role.add_to_policy(iam.PolicyStatement(
            actions=["iam:PassRole"], resources=[cfg.agent_roles_pattern, pull_role.role_arn],
            conditions={"StringEquals": {"iam:PassedToService": ["ecs-tasks.amazonaws.com",
                                                                 "lambda.amazonaws.com"]}}))
        role.add_to_policy(iam.PolicyStatement(
            actions=["ecs:RunTask"],
            resources=[f"arn:aws:ecs:{region}:{acct}:task-definition/haltai-canary:*"],
            conditions={"ArnEquals": {"ecs:cluster": self.canary_cluster.cluster_arn}}))
        role.add_to_policy(iam.PolicyStatement(
            actions=["ecs:DescribeTasks", "ecs:StopTask", "ecs:ListTasks"], resources=["*"],
            conditions={"ArnEquals": {"ecs:cluster": self.canary_cluster.cluster_arn}}))
        role.add_to_policy(iam.PolicyStatement(
            actions=["lambda:InvokeFunction", "lambda:CreateFunction",
                     "lambda:UpdateFunctionConfiguration", "lambda:DeleteFunction"],
            resources=[f"arn:aws:lambda:{region}:{acct}:function:haltai-canary-*"]))
        _managed(self.canary_repo, self.canary_cluster, pull_role, canary_td, self.canary_queue,
                 marker)

        self.endpoint_subnet: ec2.ISubnet | None = None
        if cfg.canary_endpoints:
            self._endpoint_subnet(pull_role)

    def _endpoint_subnet(self, pull_role: iam.IRole) -> None:
        """The one path the quarantine leaves open: a dedicated subnet of HaltAI endpoints."""
        region = self.cfg.region
        subnet = self.vpc.select_subnets(subnet_group_name=ENDPOINTS).subnets[0]
        self.endpoint_subnet = subnet
        sg = ec2.SecurityGroup(self, "EndpointSg", vpc=self.vpc, allow_all_outbound=False,
                               description="HaltAI canary endpoints")
        for s in self.agent_subnets:
            sg.add_ingress_rule(ec2.Peer.ipv4(s.ipv4_cidr_block), ec2.Port.tcp(443))
        any_principal = [iam.AnyPrincipal()]
        repo_arn = self.canary_repo.repository_arn
        policies = {
            "EcrApi": (ec2.InterfaceVpcEndpointAwsService.ECR, True, [
                iam.PolicyStatement(principals=any_principal,
                                    actions=["ecr:GetAuthorizationToken"], resources=["*"]),
                iam.PolicyStatement(principals=any_principal,
                                    actions=["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer",
                                             "ecr:BatchCheckLayerAvailability"],
                                    resources=[repo_arn])]),
            "EcrDkr": (ec2.InterfaceVpcEndpointAwsService.ECR_DOCKER, True, [
                iam.PolicyStatement(principals=any_principal,
                                    actions=["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer",
                                             "ecr:BatchCheckLayerAvailability"],
                                    resources=[repo_arn])]),
            "S3": (ec2.InterfaceVpcEndpointAwsService.S3, True, [
                iam.PolicyStatement(principals=any_principal, actions=["s3:GetObject"],
                                    resources=[f"arn:aws:s3:::prod-{region}-starport-layer-"
                                               "bucket/*"])]),
            # No private DNS: the canary addresses this endpoint by name, so the agents'
            # own SQS traffic keeps going through the NAT and stays cut by the quarantine.
            "Sqs": (ec2.InterfaceVpcEndpointAwsService.SQS, False, [
                iam.PolicyStatement(principals=any_principal, actions=["sqs:SendMessage"],
                                    resources=[self.canary_queue.queue_arn])]),
        }
        self.endpoints: dict[str, ec2.InterfaceVpcEndpoint] = {}
        for name, (service, private_dns, statements) in policies.items():
            ep = self.vpc.add_interface_endpoint(
                f"Canary{name}Endpoint", service=service, private_dns_enabled=private_dns,
                subnets=ec2.SubnetSelection(subnets=[subnet]), security_groups=[sg])
            for st in statements:
                ep.add_to_policy(st)
            self.endpoints[name] = ep
            _managed(ep)
        _managed(sg, subnet)
        # DNS entries look like "<hosted zone>:<name>"; the canary needs the name.
        first = Fn.select(0, self.endpoints["Sqs"].vpc_endpoint_dns_entries)
        _out(self, "CanarySqsEndpointDns", Fn.select(1, Fn.split(":", first)))

    # --- staged controls (prepared, not active) ------------------------------------------

    def _staged_controls(self) -> None:
        cfg = self.cfg
        if cfg.on("sg_change_keeps_connections"):
            # The weak control: replacing security groups refuses new connections only.
            control: Construct = ec2.SecurityGroup(
                self, "QuarantineSg", vpc=self.vpc, allow_all_outbound=False,
                security_group_name="haltai-quarantine",
                description="Staged quarantine (security group): keeps open connections")
            self.network_control = "security-group"
        else:
            nacl = ec2.NetworkAcl(self, "QuarantineNacl", vpc=self.vpc,
                                  network_acl_name="haltai-quarantine")  # no association
            if self.endpoint_subnet is not None:
                cidr = ec2.AclCidr.ipv4(self.endpoint_subnet.ipv4_cidr_block)
                nacl.add_entry("ToEndpoints", cidr=cidr, rule_number=100,
                               traffic=ec2.AclTraffic.tcp_port(443),
                               direction=ec2.TrafficDirection.EGRESS,
                               rule_action=ec2.Action.ALLOW)
                nacl.add_entry("FromEndpoints", cidr=cidr, rule_number=100,
                               traffic=ec2.AclTraffic.tcp_port_range(1024, 65535),
                               direction=ec2.TrafficDirection.INGRESS,
                               rule_action=ec2.Action.ALLOW)
            control = nacl
            self.network_control = "network-acl"
        _managed(control)
        if not cfg.dedicated:
            # Identity lever in a shared account: a deny policy attached to agent roles.
            iam.ManagedPolicy(self, "HaltAgentsPolicy", managed_policy_name="haltai-halt-agents",
                              path="/haltai/", statements=[iam.PolicyStatement(
                                  effect=iam.Effect.DENY, actions=["*"], resources=["*"])])

    # --- failure toggles ---------------------------------------------------------------

    def _toggles(self) -> None:
        cfg = self.cfg
        if cfg.on("autoscaling_restarts_tasks"):
            scaling = self.service.auto_scale_task_count(min_capacity=1, max_capacity=2)
            scaling.scale_on_cpu_utilization("Cpu", target_utilization_percent=70)
        if cfg.on("schedule_outside_boundary"):
            role = iam.Role(self, "UndeclaredSchedulerRole",
                            assumed_by=iam.ServicePrincipal("scheduler.amazonaws.com"))
            role.add_to_policy(iam.PolicyStatement(
                actions=["ecs:RunTask"], resources=[self.task_def.task_definition_arn]))
            role.add_to_policy(iam.PolicyStatement(
                actions=["iam:PassRole"],
                resources=[self.task_role.role_arn, self.task_def.obtain_execution_role()
                           .role_arn]))
            scheduler.CfnSchedule(
                self, "UndeclaredSchedule", name="agent-kick", group_name="default",
                schedule_expression="rate(5 minutes)",
                flexible_time_window=scheduler.CfnSchedule.FlexibleTimeWindowProperty(
                    mode="OFF"),
                target=scheduler.CfnSchedule.TargetProperty(
                    arn=self.cluster.cluster_arn, role_arn=role.role_arn,
                    ecs_parameters=scheduler.CfnSchedule.EcsParametersProperty(
                        task_definition_arn=self.task_def.task_definition_arn,
                        launch_type="FARGATE",
                        network_configuration=scheduler.CfnSchedule.NetworkConfigurationProperty(
                            awsvpc_configuration=scheduler.CfnSchedule
                            .AwsVpcConfigurationProperty(
                                subnets=[s.subnet_id for s in self.agent_subnets],
                                security_groups=[self.agent_sg.security_group_id],
                                assign_public_ip="DISABLED")))))
        if cfg.on("agent_can_assume_execution_role"):
            self.task_role.add_to_policy(iam.PolicyStatement(
                actions=["sts:AssumeRole"], resources=[self.exec_role.role_arn]))
        if cfg.on("agent_can_modify_execution_role"):
            self.task_role.add_to_policy(iam.PolicyStatement(
                actions=["iam:PutRolePolicy", "iam:AttachRolePolicy",
                         "iam:UpdateAssumeRolePolicy"],
                resources=[self.exec_role.role_arn]))

    # --- outputs (read by haltai_testbed.declaration) -----------------------------------

    def _outputs(self, model_url: str, tool_url: str) -> None:
        cfg = self.cfg
        _out(self, "Variant", cfg.variant)
        _out(self, "EnvironmentName", cfg.environment)
        _out(self, "BoundaryKind", "account" if cfg.dedicated else "tagged")
        if not cfg.dedicated:
            _out(self, "TagSelector", "=".join(SCOPE_TAG))
        _out(self, "AgentAccountId", cfg.agent_account)
        _out(self, "Region", cfg.region)
        _out(self, "AgentSubnets", Fn.join(",", [s.subnet_id for s in self.agent_subnets]))
        _out(self, "ExecutionRoleArn", self.exec_role.role_arn)
        _out(self, "AgentRoles", cfg.agent_roles_pattern)
        _out(self, "ModelEndpoints", model_url)
        _out(self, "ToolEndpoints", tool_url)
        _out(self, "EcsServices", "agents/research-agent")
        _out(self, "LambdaFunctions", "summariser-agent")
        _out(self, "EventBridgeRules", "agent-run")
        _out(self, "Schedules", "agents/digest")
        _out(self, "EventSourceMappings", self.esm.event_source_mapping_id)
        _out(self, "SqsQueues", self.jobs.queue_url)
        _out(self, "InBoundaryCollateralUrl", self.in_boundary_url)
        if self.out_of_boundary_url:
            _out(self, "OutOfBoundaryCollateralUrl", self.out_of_boundary_url)
        _out(self, "RecoveryOwner", cfg.recovery_owner)
        _out(self, "StagedNetworkControl", self.network_control)
        _out(self, "CanaryQueueUrl", self.canary_queue.queue_url)
        _out(self, "FlowLogGroup", self.flow_logs.log_group_name)
        _out(self, "TrailName", "haltai-testbed")
        if cfg.toggles:
            _out(self, "FailureToggles", ",".join(sorted(cfg.toggles)))
