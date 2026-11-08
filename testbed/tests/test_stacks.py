"""Synthesise the testbed stacks and check them. Needs Node.js (the CDK runs on jsii)."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import aws_cdk as cdk  # noqa: E402
from aws_cdk.assertions import Match, Template  # noqa: E402
from haltai_testbed.config import TOGGLES, BedConfig  # noqa: E402
from haltai_testbed.stacks import build  # noqa: E402


def synth(**kw):
    app = cdk.App()
    stacks = build(app, BedConfig(**kw))
    return {s.stack_name: Template.from_stack(s) for s in stacks}


def agents(**kw):
    t = synth(**kw)
    return t["HaltAITestbed-Agents"] if "HaltAITestbed-Agents" in t else t["HaltAITestbed-Shared"]


@pytest.fixture(scope="module")
def dedicated():
    return synth()


@pytest.fixture(scope="module")
def shared():
    return synth(variant="shared")


# --- both variants -------------------------------------------------------------------

def test_stacks_per_variant(dedicated, shared):
    assert set(dedicated) == {"HaltAITestbed-Management", "HaltAITestbed-Tooling",
                              "HaltAITestbed-Agents"}
    assert set(shared) == {"HaltAITestbed-Shared"}


@pytest.mark.parametrize("variant", ["dedicated", "shared"])
def test_every_taggable_resource_is_tagged_testbed(variant):
    for name, t in synth(variant=variant).items():
        for logical_id, res in t.to_json()["Resources"].items():
            tags = res.get("Properties", {}).get("Tags")
            if tags is None:
                continue
            keys = {x["Key"] for x in tags} if isinstance(tags, list) else set(tags)
            assert "haltai:testbed" in keys, (name, logical_id, res["Type"])


def test_workloads_intake_and_observability(dedicated):
    t = dedicated["HaltAITestbed-Agents"]
    t.has_resource_properties("AWS::ECS::Service", {"ServiceName": "research-agent"})
    t.has_resource_properties("AWS::Lambda::Function", {"FunctionName": "summariser-agent"})
    t.resource_count_is("AWS::Lambda::EventSourceMapping", 1)
    t.has_resource_properties("AWS::Events::Rule", {"Name": "agent-run"})
    t.has_resource_properties("AWS::Scheduler::Schedule",
                              {"Name": "digest", "GroupName": "agents"})
    t.resource_count_is("AWS::EC2::FlowLog", 1)
    t.resource_count_is("AWS::CloudTrail::Trail", 1)
    t.has_resource_properties("AWS::IAM::Role", {"RoleName": "research-agent",
                                                 "Path": "/agents/"})


def test_agents_call_only_stub_endpoints(dedicated):
    tooling = dedicated["HaltAITestbed-Tooling"].to_json()
    stub_envs = [r["Properties"]["Environment"]["Variables"]
                 for r in tooling["Resources"].values()
                 if r["Type"] == "AWS::Lambda::Function"
                 and "STUB_NAME" in r["Properties"].get("Environment", {}).get("Variables", {})]
    assert sorted(v["STUB_NAME"] for v in stub_envs) == ["model", "tool"]
    body = json.dumps(dedicated["HaltAITestbed-Agents"].to_json())
    assert "bedrock" not in body and "openai" not in body.lower()


# --- HaltAI pieces -------------------------------------------------------------------

def test_execution_role_trusts_only_the_operator(dedicated):
    t = dedicated["HaltAITestbed-Agents"]
    t.has_resource_properties("AWS::IAM::Role", {
        "RoleName": "haltai-execution", "Path": "/haltai/",
        "AssumeRolePolicyDocument": {"Statement": [{
            "Action": "sts:AssumeRole", "Effect": "Allow",
            "Principal": {"AWS": "arn:aws:iam::222222222222:role/haltai-operator"}}]},
    })


def test_scps_staged_and_protection_attached(dedicated):
    t = dedicated["HaltAITestbed-Management"]
    t.resource_count_is("AWS::Organizations::Policy", 3)
    t.has_resource_properties("AWS::Organizations::Policy", {
        "Name": "haltai-halt-agents", "TargetIds": Match.absent()})
    t.has_resource_properties("AWS::Organizations::Policy", {
        "Name": "haltai-isolate", "TargetIds": Match.absent()})
    t.has_resource_properties("AWS::Organizations::Policy", {
        "Name": "haltai-protection", "TargetIds": ["111111111111"]})


def test_quarantine_nacl_is_staged_not_associated(dedicated):
    t = dedicated["HaltAITestbed-Agents"]
    t.has_resource_properties("AWS::EC2::NetworkAcl", {
        "Tags": Match.array_with([{"Key": "haltai:managed", "Value": "true"}])})
    t.resource_count_is("AWS::EC2::SubnetNetworkAclAssociation", 0)
    # the single open path: 443 to the HaltAI endpoint subnet, and its return traffic
    t.resource_count_is("AWS::EC2::NetworkAclEntry", 2)


def test_canary_resources(dedicated):
    t = dedicated["HaltAITestbed-Agents"]
    t.has_resource_properties("AWS::ECR::Repository", {"RepositoryName": "haltai/canary"})
    t.has_resource_properties("AWS::ECS::Cluster", {"ClusterName": "haltai-canary"})
    t.has_resource_properties("AWS::ECS::TaskDefinition", {"Family": "haltai-canary"})
    t.has_resource_properties("AWS::SQS::Queue", {"QueueName": "haltai-canary",
                                                  "MessageRetentionPeriod": 60})
    t.has_resource_properties("AWS::EC2::SecurityGroup",
                              {"GroupName": "haltai-canary-marker"})
    t.resource_count_is("AWS::EC2::VPCEndpoint", 4)
    t.has_resource_properties("AWS::EC2::VPCEndpoint", {
        "ServiceName": Match.string_like_regexp("sqs"), "PrivateDnsEnabled": False})


def test_canary_endpoints_can_be_left_out():
    t = agents(canary_endpoints=False)
    t.resource_count_is("AWS::EC2::VPCEndpoint", 0)
    t.resource_count_is("AWS::EC2::NetworkAclEntry", 0)


def test_shared_variant_scopes_by_tag_and_stages_a_deny_policy(shared):
    t = shared["HaltAITestbed-Shared"]
    t.has_resource_properties("AWS::IAM::Role", {
        "RoleName": "research-agent",
        "Tags": Match.array_with([{"Key": "haltai:scope", "Value": "agents"}])})
    t.has_resource_properties("AWS::IAM::ManagedPolicy", {
        "ManagedPolicyName": "haltai-halt-agents", "Path": "/haltai/"})
    t.resource_count_is("AWS::Organizations::Policy", 0)
    billing = t.find_resources("AWS::IAM::Role", {"Properties": {"RoleName": "billing-api"}})
    assert billing and all("haltai:scope" not in json.dumps(r) for r in billing.values())


# --- failure toggles ------------------------------------------------------------------

def test_sg_toggle_stages_a_security_group_instead_of_a_nacl():
    t = agents(toggles=frozenset({"sg_change_keeps_connections"}))
    t.resource_count_is("AWS::EC2::NetworkAcl", 0)
    t.has_resource_properties("AWS::EC2::SecurityGroup", {"GroupName": "haltai-quarantine"})


def test_autoscaling_toggle():
    agents().resource_count_is("AWS::ApplicationAutoScaling::ScalableTarget", 0)
    t = agents(toggles=frozenset({"autoscaling_restarts_tasks"}))
    t.has_resource_properties("AWS::ApplicationAutoScaling::ScalableTarget", {"MinCapacity": 1})


def test_undeclared_schedule_toggle():
    t = agents(toggles=frozenset({"schedule_outside_boundary"}))
    t.has_resource_properties("AWS::Scheduler::Schedule", {
        "Name": "agent-kick", "Target": {"EcsParameters": Match.object_like(
            {"LaunchType": "FARGATE"})}})


def test_untracked_credential_toggle():
    t = agents(toggles=frozenset({"untracked_credential"}))
    t.resource_count_is("AWS::IAM::AccessKey", 1)
    t.has_resource_properties("AWS::ECS::TaskDefinition", {
        "Family": "research-agent", "ContainerDefinitions": [Match.object_like({
            "Secrets": Match.array_with([Match.object_like({"Name": "AWS_ACCESS_KEY_ID"})])})]})


def test_shared_nat_puts_the_outside_service_in_agent_subnets():
    plain = synth(variant="shared")["HaltAITestbed-Shared"]
    nat = synth(variant="shared", toggles=frozenset({"shared_nat"}))["HaltAITestbed-Shared"]

    def billing_subnets(t):
        fns = t.find_resources("AWS::Lambda::Function", {"Properties": {
            "Environment": {"Variables": {"SERVICE_NAME": "billing-api"}}}})
        (fn,) = fns.values()
        return json.dumps(fn["Properties"]["VpcConfig"]["SubnetIds"])

    # subnet logical IDs carry the subnet group name: AgentsVpc<group>Subnet1...
    assert "Vpcservices" in billing_subnets(plain) and "Vpcagents" not in billing_subnets(plain)
    assert "Vpcagents" in billing_subnets(nat)


def test_assume_execution_role_toggle():
    t = agents(toggles=frozenset({"agent_can_assume_execution_role"}))
    (role,) = t.find_resources("AWS::IAM::Role", {
        "Properties": {"RoleName": "haltai-execution"}}).values()
    trust = json.dumps(role["Properties"]["AssumeRolePolicyDocument"])
    assert "arn:aws:iam::222222222222:role/haltai-operator" in trust
    assert "ResearchAgentRole" in trust  # the agent task role is trusted too


def test_modify_execution_role_toggle():
    t = agents(toggles=frozenset({"agent_can_modify_execution_role"}))
    body = json.dumps(t.find_resources("AWS::IAM::Policy"))
    assert "iam:UpdateAssumeRolePolicy" in body


@pytest.mark.parametrize("variant", ["dedicated", "shared"])
def test_all_toggles_together_synthesise(variant):
    toggles = set(TOGGLES) if variant == "shared" else set(TOGGLES) - {"shared_nat"}
    assert synth(variant=variant, toggles=frozenset(toggles))
