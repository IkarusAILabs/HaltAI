"""Real AWS backend (boto3).

Status: implemented against the AWS APIs and tested only with mocks (moto and stubs).
It has not yet been run against a real account. Use a non-production account, and
read docs/integration-contract.md first: the worker and the gateway backend must
record probe results and markers for HaltAI to observe outcomes.

Required permissions for the HaltAI operator role are listed in docs/aws-backend.md.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from haltai.backends.base import Backend, BackendError
from haltai.config import AwsSettings, DrillSettings
from haltai.contract import principal_role_arn
from haltai.manifest import ContainmentMechanism, DeploymentManifest
from haltai.models import Marker, ProbeOutcome, ProbeStatus, ProbeVia

SID = "HaltAIContainment"
INLINE_POLICY = "HaltAIContainment"
EMPTY_POLICY = {"Version": "2012-10-17", "Statement": []}


def _parse_policy(raw: str | None) -> dict[str, Any]:
    """API Gateway returns the resource policy as a (sometimes backslash-escaped) string."""
    if not raw:
        return json.loads(json.dumps(EMPTY_POLICY))
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return json.loads(raw.replace('\\"', '"'))


def _http_status(url: str) -> int:
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:  # noqa: S310
            return resp.status
    except urllib.error.HTTPError as err:
        return err.code


class AwsBackend(Backend):
    name = "aws"
    is_real = True

    def __init__(self, manifest: DeploymentManifest, session: Any,
                 drill: DrillSettings | None = None,
                 sleep_fn: Callable[[float], None] = time.sleep,
                 http_status: Callable[[str], int] = _http_status):
        super().__init__(manifest)
        self.session = session
        self.drill = drill or DrillSettings()
        self._sleep = sleep_fn
        self._http_status = http_status
        self._clients: dict[str, Any] = {}

    @classmethod
    def from_config(cls, manifest: DeploymentManifest, aws: AwsSettings,
                    drill: DrillSettings) -> AwsBackend:
        import boto3

        session = boto3.Session(profile_name=aws.profile,
                                region_name=aws.region or manifest.region)
        return cls(manifest, session, drill)

    def client(self, name: str) -> Any:
        if name not in self._clients:
            self._clients[name] = self.session.client(name)
        return self._clients[name]

    # ------------------------------------------------------------------ helpers
    @property
    def region(self) -> str:
        return self.session.region_name or self.manifest.region

    @property
    def role_name(self) -> str:
        return self.manifest.identity.role_arn.rsplit("/", 1)[-1]

    def _route(self) -> tuple[str, str]:
        method, path = self.manifest.tool_gateway.write_route.split(" ", 1)
        return method.upper(), path if path.startswith("/") else "/" + path

    def _execute_api_arn(self) -> str:
        gw = self.manifest.tool_gateway
        if not gw.rest_api_id:
            raise BackendError("tool_gateway.rest_api_id is required for the aws backend")
        method, path = self._route()
        return (f"arn:aws:execute-api:{self.region}:{self.manifest.aws_account_id}:"
                f"{gw.rest_api_id}/{gw.stage}/{method}{path}")

    def _deny_statement(self, principal: bool) -> dict[str, Any]:
        st: dict[str, Any] = {"Sid": SID, "Effect": "Deny", "Action": "execute-api:Invoke",
                              "Resource": self._execute_api_arn()}
        if principal:
            st["Principal"] = {"AWS": self.manifest.identity.role_arn}
        return st

    def _service(self) -> dict[str, Any]:
        r = self.manifest.runtime
        resp = self.client("ecs").describe_services(cluster=r.cluster, services=[r.service])
        if not resp.get("services"):
            raise BackendError(f"ECS service {r.cluster}/{r.service} not found")
        return resp["services"][0]

    def _table_arn(self) -> str:
        t = self.manifest.protected_effect.store.table
        return self.client("dynamodb").describe_table(TableName=t)["Table"]["TableArn"]

    def _wait(self, predicate: Callable[[], bool], timeout_s: float, what: str) -> None:
        start = time.monotonic()
        while not predicate():
            if time.monotonic() - start > timeout_s:
                raise BackendError(f"Timed out after {timeout_s}s waiting for {what}")
            self._sleep(self.drill.poll_interval_s)

    # ------------------------------------------------------------------ clock
    def now(self) -> datetime:
        return datetime.now(UTC)

    def sleep(self, seconds: float) -> None:
        self._sleep(seconds)

    # ------------------------------------------------------------------ inspection
    def caller_account(self) -> str:
        return self.client("sts").get_caller_identity()["Account"]

    def snapshot(self) -> dict[str, Any]:
        m = self.manifest
        iam = self.client("iam")
        svc = self._service()
        td = self.client("ecs").describe_task_definition(
            taskDefinition=svc["taskDefinition"])["taskDefinition"]
        inline = {}
        for name in iam.list_role_policies(RoleName=self.role_name)["PolicyNames"]:
            inline[name] = iam.get_role_policy(RoleName=self.role_name,
                                               PolicyName=name)["PolicyDocument"]
        attached = [p["PolicyArn"] for p in
                    iam.list_attached_role_policies(RoleName=self.role_name)["AttachedPolicies"]]
        gw: dict[str, Any] = {}
        if m.tool_gateway.rest_api_id:
            api = self.client("apigateway").get_rest_api(restApiId=m.tool_gateway.rest_api_id)
            gw = {"id": api["id"], "policy": _parse_policy(api.get("policy"))}
        q = self.client("sqs").get_queue_attributes(
            QueueUrl=m.work_intake.queue_url,
            AttributeNames=["VisibilityTimeout", "RedrivePolicy", "Policy"])["Attributes"]
        return {
            "backend": self.name,
            "ecs": {"task_definition": td["taskDefinitionArn"],
                    "task_role": td.get("taskRoleArn"),
                    "containers": sorted(c["name"] for c in td.get("containerDefinitions", []))},
            "iam": {"role": m.identity.role_arn, "inline": inline, "attached": sorted(attached)},
            "gateway": gw,
            "sqs": q,
            "store": {"table_arn": self._table_arn()},
        }

    def role_consumers(self) -> list[str]:
        r = self.manifest.runtime
        role = self.manifest.identity.role_arn
        ecs = self.client("ecs")
        found: list[str] = []
        arns: list[str] = []
        for page in ecs.get_paginator("list_services").paginate(cluster=r.cluster):
            arns += page["serviceArns"]
        for i in range(0, len(arns), 10):
            for s in ecs.describe_services(cluster=r.cluster, services=arns[i:i + 10])["services"]:
                td = ecs.describe_task_definition(taskDefinition=s["taskDefinition"])
                if td["taskDefinition"].get("taskRoleArn") == role:
                    found.append(f"ecs-service:{r.cluster}/{s['serviceName']}")
        for page in self.client("lambda").get_paginator("list_functions").paginate():
            found += [f"lambda:{f['FunctionName']}" for f in page["Functions"]
                      if f.get("Role") == role]
        own = f"ecs-service:{r.cluster}/{r.service}"
        return ([own] if own in found else []) + sorted(x for x in found if x != own)

    def direct_effect_allowed(self) -> dict[str, bool]:
        actions = self.manifest.protected_effect.direct_actions
        if not actions:
            return {}
        resp = self.client("iam").simulate_principal_policy(
            PolicySourceArn=self.manifest.identity.role_arn, ActionNames=actions,
            ResourceArns=[self._table_arn()])
        return {r["EvalActionName"]: r["EvalDecision"] == "allowed"
                for r in resp["EvaluationResults"]}

    def containment_prestaged(self) -> tuple[bool, str]:
        mech = self.manifest.containment_policy.mechanism
        try:
            if mech == ContainmentMechanism.GATEWAY_RESOURCE_POLICY_DENY:
                rest_id = self.manifest.tool_gateway.rest_api_id
                if not rest_id:
                    return False, "tool_gateway.rest_api_id is not set."
                self.client("apigateway").get_rest_api(restApiId=rest_id)
                return True, (f"Resource-policy Deny for {self.manifest.identity.role_arn} on "
                              f"{self._execute_api_arn()} can be applied (API found).")
            if mech == ContainmentMechanism.IAM_INLINE_DENY:
                self.client("iam").get_role(RoleName=self.role_name)
                return True, (f"Inline Deny {INLINE_POLICY} on role {self.role_name} for "
                              f"{self._execute_api_arn()} can be applied (role found).")
        except Exception as exc:  # noqa: BLE001
            return False, f"Containment target not reachable: {exc!r}"
        return False, "A worker-local flag cannot be enforced or verified by HaltAI on AWS."

    # ------------------------------------------------------------------ containment
    def _set_gateway_deny(self, on: bool) -> None:
        apigw = self.client("apigateway")
        rest_id = self.manifest.tool_gateway.rest_api_id
        policy = _parse_policy(apigw.get_rest_api(restApiId=rest_id).get("policy"))
        stmts = [s for s in policy.get("Statement", []) if s.get("Sid") != SID]
        if on:
            stmts.append(self._deny_statement(principal=True))
        policy["Statement"] = stmts
        apigw.update_rest_api(restApiId=rest_id, patchOperations=[
            {"op": "replace", "path": "/policy", "value": json.dumps(policy)}])
        # Resource policy changes only take effect after a stage deployment.
        apigw.create_deployment(restApiId=rest_id, stageName=self.manifest.tool_gateway.stage,
                                description=f"HaltAI containment {'on' if on else 'off'}")

    def activate_containment(self) -> None:
        mech = self.manifest.containment_policy.mechanism
        if mech == ContainmentMechanism.GATEWAY_RESOURCE_POLICY_DENY:
            self._set_gateway_deny(True)
        elif mech == ContainmentMechanism.IAM_INLINE_DENY:
            doc = {"Version": "2012-10-17", "Statement": [self._deny_statement(principal=False)]}
            self.client("iam").put_role_policy(RoleName=self.role_name,
                                               PolicyName=INLINE_POLICY,
                                               PolicyDocument=json.dumps(doc))
        else:
            raise BackendError("local_flag containment is not supported on the aws backend")

    def rollback_containment(self) -> None:
        mech = self.manifest.containment_policy.mechanism
        if mech == ContainmentMechanism.GATEWAY_RESOURCE_POLICY_DENY:
            self._set_gateway_deny(False)
        elif mech == ContainmentMechanism.IAM_INLINE_DENY:
            iam = self.client("iam")
            try:
                iam.delete_role_policy(RoleName=self.role_name, PolicyName=INLINE_POLICY)
            except iam.exceptions.NoSuchEntityException:
                pass

    def containment_active(self) -> bool:
        mech = self.manifest.containment_policy.mechanism
        if mech == ContainmentMechanism.GATEWAY_RESOURCE_POLICY_DENY:
            raw = self.client("apigateway").get_rest_api(
                restApiId=self.manifest.tool_gateway.rest_api_id).get("policy")
            return any(s.get("Sid") == SID for s in _parse_policy(raw).get("Statement", []))
        if mech == ContainmentMechanism.IAM_INLINE_DENY:
            names = self.client("iam").list_role_policies(RoleName=self.role_name)["PolicyNames"]
            return INLINE_POLICY in names
        return False

    # ------------------------------------------------------------------ probes
    def _await_probe(self, probe_id: str, via: ProbeVia, timeout_s: float) -> ProbeOutcome:
        ddb = self.client("dynamodb")
        table = self.manifest.probes.results_table
        start = time.monotonic()
        while True:
            item = ddb.get_item(TableName=table, Key={"probe_id": {"S": probe_id}},
                                ConsistentRead=True).get("Item")
            if item:
                raw = item.get("status", {}).get("S", "error")
                status = ProbeStatus(raw) if raw in ("allowed", "denied") else ProbeStatus.ERROR
                return ProbeOutcome(probe_id=probe_id, via=via, status=status, at=self.now(),
                                    task_id=item.get("task_id", {}).get("S"),
                                    detail=item.get("detail", {}).get("S", ""))
            if time.monotonic() - start > timeout_s:
                return ProbeOutcome(probe_id=probe_id, via=via, status=ProbeStatus.TIMEOUT,
                                    at=self.now(), detail=f"no result in {table}")
            self._sleep(1.0)

    def probe_write_via_worker(self) -> ProbeOutcome:
        pid = f"haltai-probe-{uuid.uuid4().hex[:12]}"
        self.client("sqs").send_message(
            QueueUrl=self.manifest.work_intake.queue_url,
            MessageBody=json.dumps({"haltai_probe": {"probe_id": pid, "action": "write"}}))
        return self._await_probe(pid, ProbeVia.WORKER, self.manifest.probes.timeout_s)

    def probe_write_fresh_task(self) -> ProbeOutcome:
        m = self.manifest
        pid = f"haltai-probe-{uuid.uuid4().hex[:12]}"
        svc = self._service()
        method, path = self._route()
        kwargs: dict[str, Any] = {
            "cluster": m.runtime.cluster, "taskDefinition": svc["taskDefinition"],
            "startedBy": "haltai",
            "overrides": {"containerOverrides": [{
                "name": m.runtime.container,
                "command": ["haltai-probe", "--probe-id", pid],
                "environment": [
                    {"name": "HALTAI_WRITE_URL",
                     "value": m.tool_gateway.endpoint.rstrip("/") + path},
                    {"name": "HALTAI_RESULTS_TABLE", "value": m.probes.results_table},
                    {"name": "AWS_REGION", "value": self.region},
                ]}]},
        }
        if svc.get("networkConfiguration"):
            kwargs["networkConfiguration"] = svc["networkConfiguration"]
        if svc.get("capacityProviderStrategy"):
            kwargs["capacityProviderStrategy"] = svc["capacityProviderStrategy"]
        elif svc.get("launchType"):
            kwargs["launchType"] = svc["launchType"]
        resp = self.client("ecs").run_task(**kwargs)
        if resp.get("failures") or not resp.get("tasks"):
            return ProbeOutcome(probe_id=pid, via=ProbeVia.FRESH_TASK, status=ProbeStatus.ERROR,
                                at=self.now(), detail=f"run_task failed: {resp.get('failures')}")
        out = self._await_probe(pid, ProbeVia.FRESH_TASK,
                                m.probes.timeout_s + self.drill.worker_ready_timeout_s)
        out.task_id = out.task_id or resp["tasks"][0]["taskArn"]
        return out

    def probe_collateral(self) -> ProbeOutcome:
        cp = self.manifest.collateral_probe
        pid = f"collateral-{uuid.uuid4().hex[:8]}"
        if not cp.url:
            return ProbeOutcome(probe_id=pid, via=ProbeVia.COLLATERAL, status=ProbeStatus.SKIPPED,
                                at=self.now(), detail="collateral_probe.url not configured")
        try:
            code = self._http_status(cp.url)
            status = ProbeStatus.ALLOWED if code == 200 else ProbeStatus.ERROR
            detail = f"{cp.name}: HTTP {code}"
        except Exception as exc:  # noqa: BLE001
            status, detail = ProbeStatus.ERROR, f"{cp.name}: {exc!r}"
        return ProbeOutcome(probe_id=pid, via=ProbeVia.COLLATERAL, status=status, at=self.now(),
                            detail=detail)

    # ------------------------------------------------------------------ runtime
    def _running_tasks(self) -> set[str]:
        r = self.manifest.runtime
        return set(self.client("ecs").list_tasks(cluster=r.cluster, serviceName=r.service,
                                                 desiredStatus="RUNNING")["taskArns"])

    def _service_steady(self) -> bool:
        svc = self._service()
        deps = svc.get("deployments", [])
        return (len(deps) == 1 and svc.get("runningCount") == svc.get("desiredCount")
                and deps[0].get("rolloutState", "COMPLETED") == "COMPLETED")

    def restart_worker(self) -> None:
        r = self.manifest.runtime
        ecs = self.client("ecs")
        old = self._running_tasks()
        for arn in old:
            ecs.stop_task(cluster=r.cluster, task=arn, reason="HaltAI drill: worker restart")
        desired = self._service().get("desiredCount", 1)

        def replaced() -> bool:
            now = self._running_tasks()
            return len(now - old) >= desired and not (now & old)

        self._wait(replaced, self.drill.worker_ready_timeout_s, "replacement worker tasks")

    def force_redeploy(self) -> None:
        r = self.manifest.runtime
        self.client("ecs").update_service(cluster=r.cluster, service=r.service,
                                          forceNewDeployment=True)
        self._sleep(self.drill.poll_interval_s)
        self._wait(self._service_steady, self.drill.worker_ready_timeout_s,
                   "service redeployment to complete")

    def enqueue_job(self, job_id: str) -> None:
        self.client("sqs").send_message(
            QueueUrl=self.manifest.work_intake.queue_url,
            MessageBody=json.dumps({"job_id": job_id, "haltai": True,
                                    "action": self.manifest.protected_effect.name}))

    def queue_depth(self) -> int:
        attrs = self.client("sqs").get_queue_attributes(
            QueueUrl=self.manifest.work_intake.queue_url,
            AttributeNames=["ApproximateNumberOfMessages",
                            "ApproximateNumberOfMessagesNotVisible"])["Attributes"]
        return (int(attrs.get("ApproximateNumberOfMessages", 0))
                + int(attrs.get("ApproximateNumberOfMessagesNotVisible", 0)))

    def list_markers(self, since: datetime) -> list[Marker]:
        ddb = self.client("dynamodb")
        out: list[Marker] = []
        kwargs: dict[str, Any] = {
            "TableName": self.manifest.protected_effect.store.table,
            "FilterExpression": "written_at >= :s",
            "ExpressionAttributeValues": {":s": {"S": since.isoformat()}},
        }
        while True:
            page = ddb.scan(**kwargs)
            for it in page.get("Items", []):
                def s(key: str, item: dict[str, Any] = it) -> str | None:
                    return item.get(key, {}).get("S")
                written = s("written_at")
                if not written:
                    continue  # not a HaltAI marker
                out.append(Marker(
                    marker_id=s("marker_id") or "?", job_id=s("job_id"),
                    principal=principal_role_arn(s("principal") or ""),
                    source=s("source") or "worker",
                    initiated_at=datetime.fromisoformat(s("initiated_at") or written),
                    written_at=datetime.fromisoformat(written)))
            if "LastEvaluatedKey" not in page:
                return out
            kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
