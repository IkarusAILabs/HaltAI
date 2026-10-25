import json

import boto3
from moto import mock_aws
from typer.testing import CliRunner

from haltai.cli import app

runner = CliRunner()


def test_init_check_drill_matrix(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["init"]).exit_code == 0
    assert (tmp_path / "haltai.yaml").exists() and (tmp_path / "deployment.yaml").exists()

    res = runner.invoke(app, ["check"])
    assert res.exit_code == 0, res.output
    assert "boundary_assessed" in res.output

    res = runner.invoke(app, ["drill", "-q", "--out", "out"])
    assert res.exit_code == 0, res.output
    report = json.loads((tmp_path / "out" / "report.json").read_text())
    assert [c["result"] for c in report["claims"]] == ["verified", "verified", "verified"]

    res = runner.invoke(app, ["matrix"])
    assert res.exit_code == 0, res.output
    assert "MISMATCH" not in res.output


def test_check_reports_bypass_and_shared_identity(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init"])
    res = runner.invoke(app, ["check", "-s", "bypass_direct_write", "-s", "shared_identity"])
    assert res.exit_code == 1
    assert "[bypass]" in res.output and "[blocking]" in res.output


def test_check_reports_escape_paths(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init"])
    res = runner.invoke(app, ["check", "-s", "agent_can_lift_deny"])
    assert res.exit_code == 1
    assert "[escape] apigateway:UpdateRestApiPolicy" in res.output


def test_unknown_scenario_is_rejected(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init"])
    res = runner.invoke(app, ["drill", "-s", "nope"])
    assert res.exit_code != 0


@mock_aws
def test_aws_drill_refuses_without_confirm(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-2")
    runner.invoke(app, ["init"])
    monkeypatch.setenv("HALTAI_BACKEND", "aws")
    assert boto3.client("sts").get_caller_identity()["Account"] == "123456789012"
    res = runner.invoke(app, ["drill"])
    assert res.exit_code == 3
    assert "--confirm" in res.output


@mock_aws
def test_aws_drill_refuses_wrong_account(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-2")
    runner.invoke(app, ["init"])
    text = (tmp_path / "deployment.yaml").read_text().replace(
        'aws_account_id: "123456789012"', 'aws_account_id: "999999999999"')
    (tmp_path / "deployment.yaml").write_text(text)
    monkeypatch.setenv("HALTAI_BACKEND", "aws")
    res = runner.invoke(app, ["drill", "--confirm"])
    assert res.exit_code == 3
    assert "does not match" in res.output


def test_check_json_output_clean_run(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init"])
    res = runner.invoke(app, ["check", "--json"])
    assert res.exit_code == 0, res.output
    data = json.loads(res.output)
    assert "evidence" in data
    assert "limitations" in data
    assert "bypass_paths" in data
    assert "escape_paths" in data
    assert "escape_not_evaluated" in data
    assert len(data["evidence"]) > 0
    assert data["bypass_paths"] == []
    assert data["escape_paths"] == []


def test_check_json_output_with_escape_scenario(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init"])
    res = runner.invoke(app, ["check", "--json", "-s", "agent_can_lift_deny"])
    assert res.exit_code == 1
    data = json.loads(res.output)
    assert any("UpdateRestApiPolicy" in p for p in data["escape_paths"])
    assert "blocking" in data["limitations"][0]


def test_check_json_output_with_bypass_and_blocking_limitation(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init"])
    res = runner.invoke(
        app, ["check", "--json", "-s", "bypass_direct_write", "-s", "shared_identity"]
    )
    assert res.exit_code == 1
    data = json.loads(res.output)
    assert any(lim["blocking"] for lim in data["limitations"])
    assert len(data["bypass_paths"]) > 0
