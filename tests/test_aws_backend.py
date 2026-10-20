"""AWS backend tests. Everything runs against moto; no real AWS calls are made."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import boto3
import pytest
from moto import mock_aws

from haltai.backends.aws import SID, AwsBackend
from haltai.config import DrillSettings
from haltai.contract import (
    handle_probe,
    principal_role_arn,
    probe_request,
    record_marker,
    record_probe_result,
    status_from_http,
)
from haltai.manifest import ContainmentMechanism, load_manifest
from haltai.models import ProbeStatus

EXAMPLE = Path(__file__).parent.parent / "examples" / "deployment.yaml"
REGION = "eu-west-2"
ROLE = "arn:aws:iam::123456789012:role/demo-agent-task"


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    with mock_aws():
        s = boto3.Session(region_name=REGION)
        iam = s.client("iam")
        trust = json.dumps({"Version": "2012-10-17", "Statement": [{
            "Effect": "Allow", "Principal": {"Service": "ecs-tasks.amazonaws.com"},
            "Action": "sts:AssumeRole"}]})
        iam.create_role(RoleName="demo-agent-task", AssumeRolePolicyDocument=trust)
        q = s.client("sqs").create_queue(QueueName="demo-agent-jobs",
                                         Attributes={"VisibilityTimeout": "30"})
        ddb = s.client("dynamodb")
        for t, k in (("haltai-markers", "marker_id"), ("haltai-probe-results", "probe_id")):
            ddb.create_table(TableName=t, KeySchema=[{"AttributeName": k, "KeyType": "HASH"}],
                             AttributeDefinitions=[{"AttributeName": k, "AttributeType": "S"}],
                             BillingMode="PAY_PER_REQUEST")
        api = s.client("apigateway").create_rest_api(
            name="tools", policy=json.dumps({"Version": "2012-10-17", "Statement": [{
                "Sid": "AllowAccount", "Effect": "Allow", "Principal": {"AWS": "*"},
                "Action": "execute-api:Invoke", "Resource": "*"}]}))
        apigw = s.client("apigateway")
        root = apigw.get_resources(restApiId=api["id"])["items"][0]["id"]
        tools = apigw.create_resource(restApiId=api["id"], parentId=root, pathPart="tools")
        write = apigw.create_resource(restApiId=api["id"], parentId=tools["id"],
                                      pathPart="synthetic_write")
        apigw.put_method(restApiId=api["id"], resourceId=write["id"], httpMethod="POST",
                         authorizationType="AWS_IAM")
        apigw.put_integration(restApiId=api["id"], resourceId=write["id"], httpMethod="POST",
                              type="MOCK")
        ecs = s.client("ecs")
        ecs.create_cluster(clusterName="haltai-demo")
        td = ecs.register_task_definition(
            family="demo-agent-worker", taskRoleArn=ROLE,
            containerDefinitions=[{"name": "agent", "image": "busybox", "memory": 128}])
        ecs.create_service(cluster="haltai-demo", serviceName="demo-agent-worker",
                           taskDefinition=td["taskDefinition"]["taskDefinitionArn"],
                           desiredCount=0)

        m = load_manifest(EXAMPLE)
        m.work_intake.queue_url = q["QueueUrl"]
        m.tool_gateway.rest_api_id = api["id"]
        m.collateral_probe.url = None
        b = AwsBackend(m, s, DrillSettings(poll_interval_s=0.01), sleep_fn=lambda _s: None)
        yield b, s


def test_contract_helpers():
    assert principal_role_arn("arn:aws:sts::123456789012:assumed-role/demo-agent-task/abc") \
        == ROLE
    assert principal_role_arn(ROLE) == ROLE
    assert probe_request('{"haltai_probe": {"probe_id": "x"}}') == {"probe_id": "x"}
    assert probe_request({"job_id": "j"}) is None
    assert [status_from_http(c) for c in (200, 403, 500)] == ["allowed", "denied", "error"]


def test_identity_snapshot_and_static_checks(world):
    b, _ = world
    assert b.caller_account() == "123456789012"
    snap = b.snapshot()
    assert snap["ecs"]["task_role"] == ROLE
    assert snap["gateway"]["policy"]["Statement"][0]["Sid"] == "AllowAccount"
    assert b.role_consumers() == ["ecs-service:haltai-demo/demo-agent-worker"]
    ok, detail = b.containment_prestaged()
    assert ok, detail


def test_direct_effect_uses_policy_simulation(world):
    b, _ = world
    iam = MagicMock()
    iam.simulate_principal_policy.return_value = {"EvaluationResults": [
        {"EvalActionName": "dynamodb:PutItem", "EvalDecision": "allowed"}]}
    b._clients["iam"] = iam
    assert b.direct_effect_allowed() == {"dynamodb:PutItem": True}
    kwargs = iam.simulate_principal_policy.call_args.kwargs
    assert kwargs["PolicySourceArn"] == ROLE
    assert kwargs["ResourceArns"][0].endswith(":table/haltai-markers")


def test_gateway_deny_activation_and_rollback(world):
    b, s = world
    apigw = s.client("apigateway")
    rest_id = b.manifest.tool_gateway.rest_api_id
    assert not b.containment_active()
    b.activate_containment()
    assert b.containment_active()
    policy = json.loads(apigw.get_rest_api(restApiId=rest_id)["policy"])
    deny = [st for st in policy["Statement"] if st.get("Sid") == SID][0]
    assert deny["Effect"] == "Deny" and deny["Principal"] == {"AWS": ROLE}
    assert deny["Resource"].endswith(f"{rest_id}/v1/POST/tools/synthetic_write")
    assert any(st["Sid"] == "AllowAccount" for st in policy["Statement"])
    b.activate_containment()  # idempotent
    policy = json.loads(apigw.get_rest_api(restApiId=rest_id)["policy"])
    assert sum(st.get("Sid") == SID for st in policy["Statement"]) == 1
    b.rollback_containment()
    assert not b.containment_active()
    assert len(apigw.get_deployments(restApiId=rest_id)["items"]) == 3


def test_iam_inline_deny_activation_and_rollback(world):
    b, s = world
    b.manifest.containment_policy.mechanism = ContainmentMechanism.IAM_INLINE_DENY
    b.activate_containment()
    assert b.containment_active()
    doc = s.client("iam").get_role_policy(RoleName="demo-agent-task",
                                          PolicyName="HaltAIContainment")["PolicyDocument"]
    assert doc["Statement"][0]["Effect"] == "Deny"
    b.rollback_containment()
    b.rollback_containment()  # idempotent
    assert not b.containment_active()


def test_probe_via_worker_reads_result_table(world):
    b, s = world
    sqs, ddb = s.client("sqs"), s.client("dynamodb")

    def fake_worker(_seconds):
        # Plays the agent worker: receive the probe and record a denial.
        for msg in sqs.receive_message(QueueUrl=b.manifest.work_intake.queue_url,
                                       MaxNumberOfMessages=10).get("Messages", []):
            handle_probe(msg["Body"], lambda _pid: 403, ddb, "haltai-probe-results",
                         task_id="task-1")

    b._sleep = fake_worker
    out = b.probe_write_via_worker()
    assert out.status == ProbeStatus.DENIED and out.task_id == "task-1"


def test_probe_times_out_without_worker(world):
    b, _ = world
    b.manifest.probes.timeout_s = 0
    assert b.probe_write_via_worker().status == ProbeStatus.TIMEOUT


def test_queue_depth_and_markers(world):
    b, s = world
    b.enqueue_job("job-1")
    b.enqueue_job("job-2")
    assert b.queue_depth() == 2
    ddb = s.client("dynamodb")
    t0 = datetime.now(UTC) - timedelta(seconds=5)
    record_marker(ddb, "haltai-markers", job_id="job-1", source="worker",
                  principal="arn:aws:sts::123456789012:assumed-role/demo-agent-task/s1",
                  initiated_at=t0.isoformat())
    markers = b.list_markers(t0)
    assert len(markers) == 1 and markers[0].principal == ROLE and markers[0].job_id == "job-1"
    assert b.list_markers(datetime.now(UTC) + timedelta(hours=1)) == []


def test_collateral_probe_skipped_or_http(world):
    b, _ = world
    assert b.probe_collateral().status == ProbeStatus.SKIPPED
    b.manifest.collateral_probe.url = "https://app.example/health"
    b._http_status = lambda _u: 200
    assert b.probe_collateral().status == ProbeStatus.ALLOWED
    b._http_status = lambda _u: 503
    assert b.probe_collateral().status == ProbeStatus.ERROR


def test_record_probe_result_shape(world):
    _, s = world
    ddb = s.client("dynamodb")
    record_probe_result(ddb, "haltai-probe-results", "p1", "allowed", "t1")
    item = ddb.get_item(TableName="haltai-probe-results", Key={"probe_id": {"S": "p1"}})["Item"]
    assert item["status"]["S"] == "allowed" and item["task_id"]["S"] == "t1"
