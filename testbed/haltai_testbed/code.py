"""Inline code for the testbed's stub endpoints and simulated agents.

None of it calls a real model or a third party. The agents only call the stub endpoints and
write to a testbed queue.
"""

# Public image, pinned. Its shell has curl and the AWS CLI.
AGENT_IMAGE = "public.ecr.aws/aws-cli/aws-cli:2.17.0"

# The ECS "research agent": every 30 s it calls the model stub, the tool stub and makes one
# AWS write. The output shows what still works during a halt.
AGENT_LOOP = """\
while true; do
  curl -s -m 5 -o /dev/null -w "model %{http_code}\\n" "$MODEL_URL" || echo "model unreachable"
  curl -s -m 5 -o /dev/null -w "tool %{http_code}\\n" "$TOOL_URL" || echo "tool unreachable"
  aws sqs send-message --queue-url "$RESULTS_QUEUE_URL" --message-body research-agent \\
    --query MessageId --output text || echo "write failed"
  sleep 30
done
"""

# The Lambda "summariser agent", started by SQS, EventBridge and Scheduler.
LAMBDA_AGENT = """\
import json, os, urllib.request
import boto3

sqs = boto3.client("sqs")


def _get(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status
    except Exception as e:
        return repr(e)[:120]


def handler(event, context):
    out = {"model": _get(os.environ["MODEL_URL"]), "tool": _get(os.environ["TOOL_URL"])}
    try:
        sqs.send_message(QueueUrl=os.environ["RESULTS_QUEUE_URL"], MessageBody="summariser-agent")
        out["write"] = "ok"
    except Exception as e:
        out["write"] = repr(e)[:120]
    print(json.dumps(out))
    return out
"""

# Stub model and tool endpoints: they record every call and never forward it anywhere.
STUB = """\
import json, os


def handler(event, context):
    http = event.get("requestContext", {}).get("http", {})
    print(json.dumps({"stub": os.environ["STUB_NAME"], "method": http.get("method"),
                      "path": http.get("path"), "source_ip": http.get("sourceIp")}))
    return {"statusCode": 200, "headers": {"content-type": "application/json"},
            "body": json.dumps({"stub": os.environ["STUB_NAME"],
                                "note": "HaltAI testbed stub; not a real model or tool"})}
"""

# Collateral services. Healthy only while their own role can make an AWS call, so an
# identity or network halt that reaches them shows up as unhealthy.
COLLATERAL = """\
import json, os
import boto3

cw = boto3.client("cloudwatch")


def handler(event, context):
    try:
        cw.put_metric_data(Namespace="HaltAITestbed", MetricData=[
            {"MetricName": "Heartbeat", "Value": 1,
             "Dimensions": [{"Name": "Service", "Value": os.environ["SERVICE_NAME"]}]}])
        return {"statusCode": 200, "body": json.dumps({"healthy": True})}
    except Exception as e:
        return {"statusCode": 503, "body": json.dumps({"healthy": False, "error": repr(e)[:120]})}
"""
