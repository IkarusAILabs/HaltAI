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

ENVIRONMENT_TEMPLATE = """\
# HaltAI environment declaration: the boundary `prepare`, `halt`, `status` and
# `release` may act on (roadmap sections 5-7). Field reference: docs/environment.md.
# Replace the example values with your own non-production resources.
name: agents-sandbox
environment: sandbox                 # production names are refused until roadmap Phase 4

boundary:
  kind: account                      # account (preferred) | ou | tagged
  aws_account_id: "123456789012"     # the dedicated agent account
  regions: [eu-west-2]
  subnets: [subnet-0a1b2c3d4e5f60001, subnet-0a1b2c3d4e5f60002]

operator:
  # Outside the boundary: a security or tooling account.
  role_arn: arn:aws:iam::999999999999:role/haltai-operator
  # Inside the agent account; omit it and `prepare` creates it (roadmap 7.1).
  execution_role_arn: arn:aws:iam::123456789012:role/haltai/haltai-execution

levers:                              # levers per profile, applied in halt order
  halt-agents: [identity, network, intake, workload]
  isolate: [identity, network, intake, workload]

identity:
  roles: [arn:aws:iam::123456789012:role/agents/*]
  revoke_sessions: true              # also deny sessions issued before the halt

network:
  model_endpoints: [bedrock-runtime.eu-west-2.amazonaws.com]
  tool_endpoints: [https://tools.sandbox.example.internal/]

workload:
  ecs_services: [agents/research-agent]
  lambda_functions: [summariser-agent]

intake:
  eventbridge_rules: [nightly-agent-run]
  schedules: [agents/hourly-digest]
  event_source_mappings: [9f8e7d6c-0000-4000-8000-000000000001]
  sqs_queues: [https://sqs.eu-west-2.amazonaws.com/123456789012/agent-jobs]

collateral:
  in_boundary:                       # may be interrupted by the isolate profile
    - name: metrics-exporter
      url: null                      # e.g. https://metrics.sandbox.example.internal/health
  out_of_boundary:                   # must never be interrupted
    - name: billing-api
      url: null

recovery:
  owners: [platform-oncall@example.com]
  manual_fallback: docs/runbooks/agents-break-glass.md

authorisation:
  isolate_requires_two_approvers: true
"""
