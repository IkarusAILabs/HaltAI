"""Starter files written by ``haltai init``."""

CONFIG_TEMPLATE = """\
# HaltAI run configuration.
# backend: simulated runs everything against an in-memory model of the deployment
# (no AWS calls). Change to `aws` to drill a real non-production deployment.
backend: simulated
manifest: deployment.yaml

drill:
  repetitions: 5            # Claim 1 repetitions
  poll_interval_s: 2        # how often to probe while waiting for denial
  deny_timeout_s: 120       # give up waiting for denial (or recovery) after this
  observation_window_s: 60  # keep probing after denial to catch flapping
  max_drill_duration_s: 1800
  reports_dir: reports

# Only used when backend: aws
aws:
  profile: null             # e.g. haltai-nonprod
  region: null              # defaults to the manifest region

# Only used when backend: simulated
simulation:
  seed: 7
  propagation_delay_s: 8    # how long a deny takes to take effect
  jitter_s: 3
  scenarios: []             # e.g. [bypass_direct_write]; see `haltai scenarios`
"""

MANIFEST_TEMPLATE = """\
# HaltAI deployment manifest: the one deployment being assessed.
# Replace the example values with your own non-production resources.
name: demo-agent
version: "1"
aws_account_id: "123456789012"
region: eu-west-2

runtime:
  kind: ecs
  cluster: haltai-demo
  service: demo-agent-worker
  task_definition: demo-agent-worker
  container: agent

identity:
  role_arn: arn:aws:iam::123456789012:role/demo-agent-task
  dedicated: true

work_intake:
  kind: sqs
  queue_url: https://sqs.eu-west-2.amazonaws.com/123456789012/demo-agent-jobs
  visibility_timeout_s: 30

tool_gateway:
  endpoint: https://abc123.execute-api.eu-west-2.amazonaws.com/v1
  auth: iam
  rest_api_id: abc123
  stage: v1
  write_route: POST /tools/synthetic_write

protected_effect:
  name: synthetic_write
  description: Writes a marker item to a test DynamoDB table via the tool gateway
  synthetic: true
  probe: Probe job asks the worker to call the write route; result recorded in probes table
  store:
    kind: dynamodb
    table: haltai-markers
  direct_actions: [dynamodb:PutItem]

containment_policy:
  name: deny-writes-demo-agent
  mechanism: gateway_resource_policy_deny   # or iam_inline_deny
  activation: Add a Deny for the agent role on the write route and redeploy the stage
  rollback: Remove the HaltAI Deny statement, redeploy, confirm a probe write succeeds

collateral_probe:
  name: unrelated-app
  check: GET health URL returns 200 throughout the drill
  url: null                 # e.g. https://app.nonprod.example.com/health
  role_arn: arn:aws:iam::123456789012:role/unrelated-app

probes:
  results_table: haltai-probe-results
  timeout_s: 60

authorisation:
  approved_by: owner@example.com
  environment: staging
  disruptive_tests_allowed: true

assumptions:
  - Tool gateway and its policy store are not compromised
  - Agent cannot obtain credentials outside its task role

unsupported_paths:
  - Direct DynamoDB access using credentials not issued to the task role
  - Other AWS regions

downstream_side_effects:
  - None (synthetic table only)
"""
