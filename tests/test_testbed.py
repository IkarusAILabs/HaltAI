"""The CDK testbed's pure-Python parts (issue #21). The CDK stacks are tested in testbed/tests,
which needs Node.js; these run everywhere."""

import json
import sys
from pathlib import Path

import pytest

TESTBED = Path(__file__).parent.parent / "testbed"
sys.path.insert(0, str(TESTBED))

from haltai_testbed.config import TOGGLES, BedConfig  # noqa: E402
from haltai_testbed.declaration import build_declaration, main, merge_outputs  # noqa: E402
from haltai_testbed.scps import (  # noqa: E402
    halt_agents_scp,
    isolate_scp,
    protection_scp,
)

from haltai.backends.sim_environment import FLAGS  # noqa: E402
from haltai.environment import check_environment, load_environment  # noqa: E402

AGENT_STACK = {
    "Variant": "dedicated", "EnvironmentName": "sandbox", "BoundaryKind": "account",
    "AgentAccountId": "111111111111", "Region": "eu-west-2",
    "AgentSubnets": "subnet-0aaa,subnet-0bbb",
    "ExecutionRoleArn": "arn:aws:iam::111111111111:role/haltai/haltai-execution",
    "AgentRoles": "arn:aws:iam::111111111111:role/agents/*",
    "ModelEndpoints": "https://m.lambda-url.eu-west-2.on.aws/",
    "ToolEndpoints": "https://t.lambda-url.eu-west-2.on.aws/",
    "EcsServices": "agents/research-agent", "LambdaFunctions": "summariser-agent",
    "EventBridgeRules": "agent-run", "Schedules": "agents/digest",
    "EventSourceMappings": "9f8e7d6c-0000-4000-8000-000000000001",
    "SqsQueues": "https://sqs.eu-west-2.amazonaws.com/111111111111/agent-jobs",
    "InBoundaryCollateralUrl": "https://x.lambda-url.eu-west-2.on.aws/",
    "RecoveryOwner": "platform-oncall@example.com",
}
TOOLING_STACK = {
    "OperatorRoleArn": "arn:aws:iam::222222222222:role/haltai-operator",
    "OutOfBoundaryCollateralUrl": "https://b.lambda-url.eu-west-2.on.aws/",
}


def shared_outputs():
    o = {**AGENT_STACK, "Variant": "shared", "BoundaryKind": "tagged",
         "TagSelector": "haltai:scope=agents",
         "OperatorRoleArn": "arn:aws:iam::111111111111:role/haltai-operator",
         "OutOfBoundaryCollateralUrl": "https://b.lambda-url.eu-west-2.on.aws/"}
    return {"HaltAITestbed-Shared": o}


def dedicated_outputs():
    return [{"HaltAITestbed-Agents": AGENT_STACK}, {"HaltAITestbed-Tooling": TOOLING_STACK}]


# --- settings ------------------------------------------------------------------------

def test_every_toggle_is_a_simulated_scenario():
    assert set(TOGGLES) <= set(FLAGS)


def test_defaults_are_a_sandbox():
    cfg = BedConfig()
    assert cfg.environment == "sandbox" and cfg.dedicated


@pytest.mark.parametrize("kw,needle", [
    ({"environment": "Production"}, "production name"),
    ({"variant": "hybrid"}, "variant"),
    ({"toggles": frozenset({"nope"})}, "unknown toggles"),
    ({"toggles": frozenset({"shared_nat"})}, "shared variant only"),
    ({"tooling_account": "111111111111"}, "three different accounts"),
    ({"agent_account": "12345"}, "12-digit"),
])
def test_bad_settings_are_refused(kw, needle):
    with pytest.raises(ValueError, match=needle):
        BedConfig(**kw)


def test_context_parsing():
    ctx = {"variant": "shared", "toggles": "shared_nat, sg_change_keeps_connections",
           "canary_endpoints": "false"}
    cfg = BedConfig.from_context(ctx.get)
    assert cfg.toggles == {"shared_nat", "sg_change_keeps_connections"}
    assert not cfg.canary_endpoints and cfg.operator_role_arn.endswith(
        "111111111111:role/haltai-operator")


# --- service control policies ----------------------------------------------------------

def test_scps_fit_the_organizations_size_limit():
    cfg = BedConfig()
    for doc in (halt_agents_scp(cfg), isolate_scp(cfg), protection_scp(cfg)):
        assert len(json.dumps(doc, separators=(",", ":"))) <= 5120


def test_isolate_exempts_only_the_execution_role_as_a_principal():
    cfg = BedConfig()
    for st in isolate_scp(cfg)["Statement"]:
        assert st["Condition"] == {"ArnNotLike": {"aws:PrincipalArn": cfg.execution_role_arn}}
    pull = isolate_scp(cfg)["Statement"][1]
    assert pull["NotResource"].endswith(":repository/haltai/canary")


def test_protection_scp_guards_the_execution_role_and_staged_resources():
    sids = {s["Sid"]: s for s in protection_scp(BedConfig())["Statement"]}
    roles = sids["ProtectHaltAIRoles"]
    assert {"sts:AssumeRole", "iam:PassRole", "iam:UpdateAssumeRolePolicy"} <= set(roles["Action"])
    assert roles["Resource"] == "arn:aws:iam::*:role/haltai/*"
    assert "sqs:SetQueueAttributes" in sids["ProtectStagedResources"]["Action"]
    assert sids["StayInTheOrganization"]["Action"] == "organizations:LeaveOrganization"


# --- generated environment.yaml ----------------------------------------------------------

@pytest.mark.parametrize("variant", ["dedicated", "shared"])
def test_generated_declaration_validates_without_blocking_limitations(tmp_path, variant):
    files = dedicated_outputs() if variant == "dedicated" else [shared_outputs()]
    paths = []
    for i, content in enumerate(files):
        p = tmp_path / f"outputs{i}.json"
        p.write_text(json.dumps(content), encoding="utf-8")
        paths.append(str(p))
    out = tmp_path / "environment.yaml"
    assert main([*paths, "-o", str(out)]) == 0
    env = load_environment(out)
    assert env.environment == "sandbox"
    assert not [lim for lim in check_environment(env) if lim.blocking]
    assert env.workload.ecs_services == ["agents/research-agent"]
    if variant == "shared":
        assert env.boundary.tag_selector == {"haltai:scope": "agents"}
        assert [s.name for s in env.collateral.out_of_boundary] == ["billing-api"]


def test_missing_output_is_reported(capsys, tmp_path):
    p = tmp_path / "agents.json"
    p.write_text(json.dumps({"HaltAITestbed-Agents": AGENT_STACK}), encoding="utf-8")
    assert main([str(p)]) == 2  # tooling outputs (OperatorRoleArn) not passed
    assert "OperatorRoleArn" in capsys.readouterr().err


def test_outputs_from_several_files_are_merged():
    merged = merge_outputs(dedicated_outputs())
    decl = build_declaration(merged)
    assert decl["operator"]["role_arn"].startswith("arn:aws:iam::222222222222:")
    assert decl["boundary"]["subnets"] == ["subnet-0aaa", "subnet-0bbb"]
