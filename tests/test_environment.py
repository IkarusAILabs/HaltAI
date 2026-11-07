"""Environment declaration (issue #18): one test per validation rule, plus the CLI."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from haltai.cli import app
from haltai.environment import (
    LEVER_ORDER,
    BoundaryKind,
    EnvironmentDeclaration,
    Lever,
    Profile,
    check_environment,
    load_environment,
)
from haltai.templates import ENVIRONMENT_TEMPLATE

EXAMPLE = Path(__file__).parent.parent / "examples" / "environment.yaml"
AGENT = "123456789012"
runner = CliRunner()


@pytest.fixture
def env() -> EnvironmentDeclaration:
    return load_environment(EXAMPLE)


def lims(e: EnvironmentDeclaration) -> dict[str, list]:
    out: dict[str, list] = {}
    for i in check_environment(e):
        out.setdefault(i.field, []).append(i)
    return out


def blocking(e: EnvironmentDeclaration, field: str) -> bool:
    return any(i.blocking for i in lims(e).get(field, []))


def test_example_is_clean_and_matches_init_template(env):
    assert check_environment(env) == []
    assert EXAMPLE.read_text(encoding="utf-8") == ENVIRONMENT_TEMPLATE


def test_coverage_follows_halt_order(env):
    assert list(env.lever_coverage(Profile.HALT_AGENTS)) == LEVER_ORDER
    env.levers[Profile.HALT_AGENTS] = [Lever.WORKLOAD, Lever.IDENTITY]
    assert list(env.lever_coverage(Profile.HALT_AGENTS)) == [Lever.IDENTITY, Lever.WORKLOAD]


# --- schema ------------------------------------------------------------------------

def test_account_id_must_be_twelve_digits():
    data = {"name": "x", "environment": "sandbox", "operator": {"role_arn": "r"},
            "boundary": {"aws_account_id": "1234", "regions": ["eu-west-2"]}}
    with pytest.raises(ValidationError):
        EnvironmentDeclaration.model_validate(data)


def test_regions_are_required():
    data = {"name": "x", "environment": "sandbox", "operator": {"role_arn": "r"},
            "boundary": {"aws_account_id": AGENT, "regions": []}}
    with pytest.raises(ValidationError):
        EnvironmentDeclaration.model_validate(data)


# --- validation rules ----------------------------------------------------------------

@pytest.mark.parametrize("name", ["prod", "Production", "LIVE"])
def test_production_is_blocking(env, name):
    env.environment = name
    assert blocking(env, "environment")


def test_operator_inside_account_boundary_is_blocking(env):
    env.operator.role_arn = f"arn:aws:iam::{AGENT}:role/haltai-operator"
    assert blocking(env, "operator.role_arn")


def test_operator_must_be_a_role_arn(env):
    env.operator.role_arn = "haltai-operator"
    assert blocking(env, "operator.role_arn")


def test_ou_operator_location_is_checked_live_not_passed(env):
    env.boundary.kind = BoundaryKind.OU
    env.boundary.organization_unit_id = "ou-abcd-12345678"
    found = lims(env)["operator.role_arn"]
    assert found and not any(i.blocking for i in found)


def test_ou_without_id_is_blocking(env):
    env.boundary.kind = BoundaryKind.OU
    assert blocking(env, "boundary.organization_unit_id")


def test_tagged_without_selector_is_blocking(env):
    env.boundary.kind = BoundaryKind.TAGGED
    assert blocking(env, "boundary.tag_selector")


def test_tagged_with_network_lever_needs_subnets(env):
    env.boundary.kind = BoundaryKind.TAGGED
    env.boundary.tag_selector = {"haltai:scope": "agents"}
    env.boundary.subnets = []
    assert blocking(env, "boundary.subnets")
    env.levers = {Profile.HALT_AGENTS: [Lever.IDENTITY, Lever.WORKLOAD]}
    assert "boundary.subnets" not in lims(env)


def test_tagged_operator_in_same_account_is_flagged_not_blocking(env):
    env.boundary.kind = BoundaryKind.TAGGED
    env.boundary.tag_selector = {"haltai:scope": "agents"}
    env.operator.role_arn = f"arn:aws:iam::{AGENT}:role/haltai-operator"
    found = lims(env)["operator.role_arn"]
    assert found and not any(i.blocking for i in found)


def test_execution_role_must_be_in_agent_account(env):
    env.operator.execution_role_arn = "arn:aws:iam::999999999999:role/haltai-execution"
    assert blocking(env, "operator.execution_role_arn")


def test_missing_execution_role_is_noted_not_blocking(env):
    env.operator.execution_role_arn = None
    found = lims(env)["operator.execution_role_arn"]
    assert found and not any(i.blocking for i in found)


def test_agent_role_outside_account_boundary_is_blocking(env):
    env.identity.roles.append("arn:aws:iam::555555555555:role/stray-agent")
    assert blocking(env, "identity.roles")


def test_no_profile_with_levers_is_blocking(env):
    env.levers = {Profile.HALT_AGENTS: []}
    assert blocking(env, "levers")


@pytest.mark.parametrize("lever,clear", [
    (Lever.IDENTITY, lambda e: e.identity.roles.clear()),
    (Lever.NETWORK, lambda e: e.boundary.subnets.clear()),
    (Lever.WORKLOAD, lambda e: (e.workload.ecs_services.clear(),
                                e.workload.lambda_functions.clear())),
    (Lever.INTAKE, lambda e: (e.intake.eventbridge_rules.clear(), e.intake.schedules.clear(),
                              e.intake.event_source_mappings.clear(),
                              e.intake.sqs_queues.clear())),
])
def test_uncovered_lever_is_not_covered_not_blocking(env, lever, clear):
    clear(env)
    assert env.lever_coverage(Profile.HALT_AGENTS)[lever] is False
    found = lims(env)["levers.halt-agents"]
    assert any(lever.value in i.reason for i in found)
    assert not any(i.blocking for i in found)


def test_isolate_without_in_boundary_collateral_is_flagged(env):
    env.collateral.in_boundary = []
    found = lims(env)["collateral.in_boundary"]
    assert found and not any(i.blocking for i in found)
    del env.levers[Profile.ISOLATE]
    assert "collateral.in_boundary" not in lims(env)


def test_recovery_owner_and_fallback_are_blocking(env):
    env.recovery.owners = []
    env.recovery.manual_fallback = "  "
    assert blocking(env, "recovery.owners")
    assert blocking(env, "recovery.manual_fallback")


# --- CLI -----------------------------------------------------------------------------

def test_cli_clean_example_exits_0():
    res = runner.invoke(app, ["validate", "--environment", str(EXAMPLE)])
    assert res.exit_code == 0, res.output
    assert "no limitations" in res.output


def test_cli_blocking_exits_1_and_json_parses(tmp_path):
    p = tmp_path / "environment.yaml"
    p.write_text(EXAMPLE.read_text(encoding="utf-8").replace(
        "environment: sandbox", "environment: production"), encoding="utf-8")
    res = runner.invoke(app, ["validate", "-e", str(p), "--json"])
    assert res.exit_code == 1
    data = json.loads(res.stdout)
    assert data["blocking"] is True
    assert any(i["field"] == "environment" and i["blocking"] for i in data["limitations"])
    assert data["profiles"]["halt-agents"]["identity"] == "covered"


def test_cli_missing_or_invalid_file_exits_2(tmp_path):
    assert runner.invoke(app, ["validate", "-e", str(tmp_path / "nope.yaml")]).exit_code == 2
    bad = tmp_path / "bad.yaml"
    bad.write_text("name: x\nboundary: {aws_account_id: '1'}\n", encoding="utf-8")
    res = runner.invoke(app, ["validate", "-e", str(bad), "--json"])
    assert res.exit_code == 2
    assert res.stdout == ""  # errors go to stderr; stdout stays JSON or empty
    broken = tmp_path / "broken.yaml"
    broken.write_text("name: [unclosed\n", encoding="utf-8")
    assert runner.invoke(app, ["validate", "-e", str(broken)]).exit_code == 2
    empty = tmp_path / "empty.yaml"
    empty.write_text("", encoding="utf-8")
    assert runner.invoke(app, ["validate", "-e", str(empty)]).exit_code == 2


def test_init_writes_environment_yaml(tmp_path):
    assert runner.invoke(app, ["init", str(tmp_path)]).exit_code == 0
    written = tmp_path / "environment.yaml"
    assert written.read_text(encoding="utf-8") == ENVIRONMENT_TEMPLATE
    assert check_environment(load_environment(written)) == []
