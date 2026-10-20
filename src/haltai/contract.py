"""Agent integration contract for the ``aws`` backend.

HaltAI observes a real deployment through two DynamoDB tables and one queue message
type. See docs/integration-contract.md for the full description.

* **Probe message** on the work-intake queue::

      {"haltai_probe": {"probe_id": "haltai-probe-1a2b", "action": "write"}}

  The worker calls the protected write route exactly as it would for real work and
  records the outcome with :func:`record_probe_result`.

* **Probe results table** (``probes.results_table``), partition key ``probe_id`` (S):
  ``status`` = allowed | denied | error, ``task_id``, ``at`` (ISO-8601 UTC).

* **Marker table** (``protected_effect.store.table``), partition key ``marker_id`` (S):
  written by the gateway backend whenever the protected effect completes, with
  ``job_id``, ``principal``, ``source``, ``initiated_at`` and ``written_at``.

``haltai-probe`` is a ready-made entry point for the fresh-task probe: run it as a
command override in a one-off task from the agent's task definition.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

_ASSUMED = re.compile(r"^arn:(aws[\w-]*):sts::(\d{12}):assumed-role/([^/]+)/.+$")


def principal_role_arn(arn: str) -> str:
    """Map an assumed-role session ARN to its IAM role ARN; other ARNs unchanged."""
    m = _ASSUMED.match(arn)
    if not m:
        return arn
    partition, account, role = m.groups()
    return f"arn:{partition}:iam::{account}:role/{role}"


def utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


def probe_request(body: str | dict[str, Any]) -> dict[str, Any] | None:
    """Return the probe request if a queue message is a HaltAI probe, else None."""
    data = json.loads(body) if isinstance(body, str) else body
    probe = data.get("haltai_probe") if isinstance(data, dict) else None
    return probe if isinstance(probe, dict) and "probe_id" in probe else None


def status_from_http(code: int) -> str:
    if 200 <= code < 300:
        return "allowed"
    if code in (401, 403):
        return "denied"
    return "error"


def record_probe_result(dynamodb: Any, table: str, probe_id: str, status: str,
                        task_id: str | None = None, detail: str = "") -> None:
    item = {"probe_id": {"S": probe_id}, "status": {"S": status}, "at": {"S": utcnow_iso()}}
    if task_id:
        item["task_id"] = {"S": task_id}
    if detail:
        item["detail"] = {"S": detail[:1000]}
    dynamodb.put_item(TableName=table, Item=item)


def record_marker(dynamodb: Any, table: str, *, principal: str, source: str,
                  initiated_at: str, job_id: str | None = None,
                  marker_id: str | None = None) -> str:
    """For the gateway backend (e.g. the Lambda behind the write route)."""
    marker_id = marker_id or f"marker-{uuid.uuid4().hex}"
    item = {"marker_id": {"S": marker_id}, "principal": {"S": principal_role_arn(principal)},
            "source": {"S": source}, "initiated_at": {"S": initiated_at},
            "written_at": {"S": utcnow_iso()}}
    if job_id:
        item["job_id"] = {"S": job_id}
    dynamodb.put_item(TableName=table, Item=item)
    return marker_id


def handle_probe(body: str | dict[str, Any], call_write: Callable[[str], int],
                 dynamodb: Any, results_table: str, task_id: str | None = None) -> bool:
    """Worker-side helper. Returns True if the message was a probe (and was handled).

    ``call_write(probe_id)`` must call the protected write route the same way real work
    does (same credentials, same client) and return the HTTP status code.
    """
    req = probe_request(body)
    if req is None:
        return False
    try:
        status = status_from_http(call_write(req["probe_id"]))
        detail = ""
    except Exception as exc:  # noqa: BLE001 - outcome must be recorded
        status, detail = "error", repr(exc)
    record_probe_result(dynamodb, results_table, req["probe_id"], status, task_id, detail)
    return True


def signed_post(url: str, payload: dict[str, Any], region: str) -> int:
    """SigV4-signed POST using the task's own credentials (IAM-auth API Gateway)."""
    import boto3
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest

    data = json.dumps(payload).encode()
    req = AWSRequest(method="POST", url=url, data=data,
                     headers={"Content-Type": "application/json"})
    session_creds = boto3.Session().get_credentials()
    if session_creds is None:
        raise RuntimeError("No AWS credentials available in the task")
    creds = session_creds.get_frozen_credentials()
    SigV4Auth(creds, "execute-api", region).add_auth(req)
    http = urllib.request.Request(url, data=data, headers=dict(req.headers), method="POST")
    try:
        with urllib.request.urlopen(http, timeout=20) as resp:  # noqa: S310
            return resp.status
    except urllib.error.HTTPError as err:
        return err.code


def probe_main(argv: list[str] | None = None) -> int:
    """``haltai-probe``: fresh-task probe entry point.

    Environment: HALTAI_WRITE_URL, HALTAI_RESULTS_TABLE, AWS_REGION.
    """
    ap = argparse.ArgumentParser(prog="haltai-probe")
    ap.add_argument("--probe-id", required=True)
    ap.add_argument("--url", default=os.environ.get("HALTAI_WRITE_URL"))
    ap.add_argument("--results-table", default=os.environ.get("HALTAI_RESULTS_TABLE"))
    ap.add_argument("--region", default=os.environ.get("AWS_REGION"))
    a = ap.parse_args(argv)
    if not (a.url and a.results_table and a.region):
        print("haltai-probe: url, results table and region are required", file=sys.stderr)
        return 2
    import boto3

    task_id = os.environ.get("ECS_CONTAINER_METADATA_URI_V4", "").rsplit("/", 1)[-1] or None
    try:
        status = status_from_http(signed_post(a.url, {"haltai_probe_id": a.probe_id},
                                              a.region))
        detail = ""
    except Exception as exc:  # noqa: BLE001
        status, detail = "error", repr(exc)
    record_probe_result(boto3.client("dynamodb", region_name=a.region), a.results_table,
                        a.probe_id, status, task_id, detail)
    print(json.dumps({"probe_id": a.probe_id, "status": status}))
    return 0
