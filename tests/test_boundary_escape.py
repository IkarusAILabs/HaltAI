"""Claim 3 on the environment boundary (roadmap 7.1, release plan 0.3.0 step 5)."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from haltai.backends.sim_environment import (
    ASSUME_EXEC,
    EDIT_STAGED,
    LEAVE_ORG,
    MODIFY_EXEC,
    PASS_EXEC,
    RETAG_SCOPE,
    SimEnvSettings,
    SimulatedEnvironment,
)
from haltai.boundary_escape import (
    EXECUTION_ROLE_PATH,
    assess_boundary,
    boundary_catalogue,
    execution_role_arn,
)
from haltai.cli import app
from haltai.environment import BoundaryKind, Lever, Profile, load_environment
from haltai.escape import EscapeKind
from haltai.models import ClaimResult

EXAMPLE = Path(__file__).parent.parent / "examples" / "environment.yaml"
AGENT = "arn:aws:iam::123456789012:role/agents/research-agent"
runner = CliRunner()


@pytest.fixture
def env():
    return load_environment(EXAMPLE)


def tagged(env):
    env.boundary.kind = BoundaryKind.TAGGED
    env.boundary.tag_selector = {"haltai:scope": "agents"}
    return env


def sim(env, *flags):
    return SimulatedEnvironment(env, SimEnvSettings(scenarios=list(flags)))


# --- catalogue -------------------------------------------------------------------------

def test_catalogue_targets_the_execution_role_and_staged_controls(env):
    cat = boundary_catalogue(env, AGENT)
    exec_role = execution_role_arn(env)
    on_exec = {a.action for a in cat if a.resource == exec_role}
    assert {"sts:AssumeRole", "iam:PassRole", "iam:UpdateAssumeRolePolicy",
            "iam:AttachRolePolicy", "iam:DeleteRole", "iam:UntagRole"} <= on_exec
    actions = {a.action for a in cat}
    assert {"organizations:DetachPolicy", "organizations:LeaveOrganization",
            "ec2:ReplaceNetworkAclAssociation", "iam:CreatePolicyVersion",
            "tag:UntagResources"} <= actions
    assert not any(a.kind == EscapeKind.SCOPE for a in cat)  # account boundary: no scope tags


def test_labels_are_unique(env):
    for e in (env, tagged(load_environment(EXAMPLE))):
        labels = [a.label for a in boundary_catalogue(e, AGENT)]
        assert len(labels) == len(set(labels))


def test_staged_resources_carry_the_managed_tag_condition(env):
    nacl = [a for a in boundary_catalogue(env, AGENT) if a.action.startswith("ec2:")
            and "NetworkAcl" in a.action]
    assert nacl and all(a.condition == "tag haltai:managed=true" for a in nacl)
    assert all("[tag haltai:managed=true]" in a.label for a in nacl)


def test_undeclared_execution_role_uses_the_path_prepare_creates(env):
    env.operator.execution_role_arn = None
    assert execution_role_arn(env) == f"arn:aws:iam::123456789012:{EXECUTION_ROLE_PATH}"


def test_tagged_boundary_adds_scope_and_own_deny_policy_actions(env):
    cat = boundary_catalogue(tagged(env), AGENT)
    scope = [a for a in cat if a.kind == EscapeKind.SCOPE]
    assert {a.action for a in scope} == {"iam:UntagRole", "tag:UntagResources", "ec2:DeleteTags"}
    assert all(a.condition == "tag haltai:scope=agents" for a in scope)
    own = {a.action for a in cat if a.resource == AGENT}
    assert {"iam:DetachRolePolicy", "iam:DeleteRolePolicy", "iam:UntagRole"} <= own
    assert not any(a.action.startswith("organizations:") for a in cat)  # no SCP to retarget


# --- assessment ------------------------------------------------------------------------

def test_clean_boundary_is_verified_for_every_agent_identity(env):
    r = assess_boundary(sim(env))
    assert r.result == ClaimResult.VERIFIED
    assert len(r.principals) == 2
    assert r.assessment.paths == [] and r.assessment.unknown == []


@pytest.mark.parametrize("flag,needle", [
    (ASSUME_EXEC, "sts:AssumeRole on arn:aws:iam::123456789012:role/haltai/haltai-execution"),
    (PASS_EXEC, "iam:PassRole on arn:aws:iam::123456789012:role/haltai/haltai-execution"),
    (MODIFY_EXEC, "iam:UpdateAssumeRolePolicy on"),
    (LEAVE_ORG, "organizations:LeaveOrganization"),
    (EDIT_STAGED, "ec2:ReplaceNetworkAclAssociation"),
])
def test_each_escape_fails_claim3_and_is_named(env, flag, needle):
    r = assess_boundary(sim(env, flag))
    assert r.result == ClaimResult.FAILED
    assert any(needle in p for p in r.assessment.paths), r.assessment.paths
    assert all(p.startswith("role/agents/") for p in r.assessment.paths)  # names the identity


def test_retag_scope_fails_only_in_a_tagged_boundary(env):
    assert assess_boundary(sim(tagged(env), RETAG_SCOPE)).result == ClaimResult.FAILED
    # an account boundary has no scope tags to remove
    assert assess_boundary(sim(load_environment(EXAMPLE), RETAG_SCOPE)).result \
        == ClaimResult.VERIFIED


class _Partial(SimulatedEnvironment):
    """Policy simulation fails for some actions: not evaluated, never denied."""

    def boundary_escape_allowed(self, principal, actions):
        return {a.label: (None if a.action.startswith("organizations:") else False)
                for a in actions}


class _Nobody(SimulatedEnvironment):
    def agent_principals(self):
        return []


def test_not_evaluated_is_partial_not_verified(env):
    r = assess_boundary(_Partial(env))
    assert r.result == ClaimResult.PARTIAL
    assert r.assessment.unknown and all("organizations:" in u for u in r.assessment.unknown)


def test_no_agent_identities_is_unknown(env):
    r = assess_boundary(_Nobody(env))
    assert r.result == ClaimResult.UNKNOWN and r.assessment.evaluated == 0


def test_assuming_the_execution_role_gets_past_the_halt(env):
    s = sim(env, ASSUME_EXEC)
    s.sleep(60)
    s.stage(Lever.IDENTITY, Profile.HALT_AGENTS)
    s.apply(Lever.IDENTITY)
    s.sleep(60)
    obs = s.observe(Lever.IDENTITY)
    assert obs.outcome.value == "partial"
    assert any("haltai-execution" in e for e in obs.evidence)


# --- CLI -------------------------------------------------------------------------------

def test_cli_check_environment_clean_exits_0():
    res = runner.invoke(app, ["check", "--environment", str(EXAMPLE)])
    assert res.exit_code == 0, res.output
    assert "Claim 3 on the boundary: verified" in res.output


def test_cli_check_environment_escape_exits_1_with_json():
    res = runner.invoke(app, ["check", "-e", str(EXAMPLE), "-s", ASSUME_EXEC, "--json"])
    assert res.exit_code == 1
    data = json.loads(res.stdout)
    c3 = data["claim3_boundary"]
    assert data["backend"] == "simulated-environment" and data["scenarios"] == [ASSUME_EXEC]
    assert c3["result"] == "failed" and c3["evidence_level"] == "boundary_assessed"
    assert any("sts:AssumeRole" in p for p in c3["escape_paths"])


def test_cli_check_environment_bad_input_exits_2(tmp_path):
    assert runner.invoke(app, ["check", "-e", str(tmp_path / "nope.yaml")]).exit_code == 2
    res = runner.invoke(app, ["check", "-e", str(EXAMPLE), "-s", "nope"])
    assert res.exit_code == 2
