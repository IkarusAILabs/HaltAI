# HaltAI testbed: the CDK reference environment

**Status:** release 0.3.0, issue #21. **CI synthesises it; it has never been deployed.** It is the disposable, non-production AWS environment the 0.6.0 sandbox proof deploys ([release plan](../docs/release-plan.md)). HaltAI can't act on it yet: the AWS lever adapters arrive in 0.5.0.

It is a realistic small agent setup, with switches that turn on the failure conditions HaltAI must detect. Every switch has the same name as a scenario in the [simulated environment](../docs/levers.md#failure-scenarios), so a simulated result and a real one can be compared directly.

> **Safety.**
> - Use disposable, non-production accounts only. Production environment names (`prod`, `production`, `live`) are refused.
> - No real model or third-party calls: agents call **stub endpoints** that record calls and answer with a fixed message.
> - No secrets in the repository. The optional untracked access key is created at deploy time and kept in Secrets Manager.
> - Every resource is tagged `haltai:testbed=true`. `cdk destroy` removes everything.

## Two variants

| | `dedicated` (recommended boundary) | `shared` |
|---|---|---|
| Boundary | The whole agent account (`kind: account`) | Resources tagged `haltai:scope=agents` in one account (`kind: tagged`) |
| Stacks | `HaltAITestbed-Management`, `-Tooling`, `-Agents` (three accounts) | `HaltAITestbed-Shared` (one account) |
| Identity lever | Staged SCPs `haltai-halt-agents` and `haltai-isolate`, created but **not attached** | Staged deny policy `policy/haltai/haltai-halt-agents`, not attached |
| Protecting HaltAI | The standing SCP `haltai-protection` is **attached** to the agent account ([roadmap 7.1](../docs/roadmap.md#71-two-haltai-identities)) | IAM only: weaker, and reported as such |
| Operator role | In the tooling account | In the same account (a non-blocking limitation) |
| Out-of-boundary service | `billing-api` in the tooling account | `billing-api` in the same VPC, untagged |

### What each variant contains

**Agent workloads.** Both variants have:
- a VPC (2 AZs, 1 NAT gateway) with `agents`, `services` and `haltai-endpoints` subnets;
- `research-agent`: an ECS Fargate service. Every 30 s it calls the model stub, the tool stub, and makes one SQS write;
- `summariser-agent`: a Lambda function in the agent subnets. It is started three ways:
  - an SQS queue (`agent-jobs`, through an event source mapping);
  - an EventBridge rule (`agent-run`, every 15 minutes, which feeds the queue);
  - a Scheduler schedule (`agents/digest`, every 15 minutes);
- `metrics-exporter`: an in-boundary collateral service in the `services` subnets.

Agent roles are under `role/agents/`.

**Observability.** VPC Flow Logs (all traffic, to CloudWatch Logs) and a CloudTrail trail, so effects can be checked without agent code.

**HaltAI pieces in the agent account:**
- the **execution role** `role/haltai/haltai-execution`. It trusts only the operator role, and has the lever and canary permissions from the [canary-probe design](../docs/design/canary-probe.md#6-execution-role-policies-roadmap-71-sketch);
- the staged **quarantine network ACL** `haltai-quarantine`, not associated with any subnet. It allows only HTTPS to the HaltAI endpoint subnet;
- the **canary** resources:
  - the `haltai/canary` repository, the `haltai-canary` cluster and task definition, and the `haltai-canary-pull` role;
  - the `haltai-canary` queue, granted to agent roles by the queue policy;
  - the empty `haltai-canary-marker` security group;
  - ECR, S3 and SQS interface endpoints in a single dedicated subnet.

  All of these are tagged `haltai:managed=true`. The canary **image** comes in 0.5.0. Until then a canary run fails to start, which by design gives `inconclusive`, never `stopped`.

**Tooling account (dedicated variant):** the operator role `haltai-operator`, the **model and tool stubs** (Lambda function URLs), and `billing-api`. In the shared variant they live in the one account.

## Failure toggles

Turn them on with `-c toggles=a,b,…`. Each one reproduces the simulated scenario of the same name. The tests check that every toggle name is a simulator scenario.

| Toggle | What it builds | Expected HaltAI result |
|---|---|---|
| `sg_change_keeps_connections` | The network lever is staged as a quarantine **security group** instead of a network ACL | Network `partial`: open connections survive |
| `autoscaling_restarts_tasks` | Application Auto Scaling on `research-agent` with a minimum of 1 task | Workload `not_stopped` unless scaling is suspended |
| `schedule_outside_boundary` | An extra schedule `default/agent-kick`, **not** in `environment.yaml`, that starts agent tasks every 5 minutes | Intake and workload `partial`; identity and network still `stopped` |
| `untracked_credential` | An IAM user with an access key that `research-agent` uses for its AWS calls | Identity `partial` under `halt-agents` |
| `shared_nat` | Shared variant only: `billing-api` runs in the agent subnets | `isolate` interrupts an out-of-boundary service: a violation |
| `agent_can_assume_execution_role` | The execution role also trusts `research-agent` | Claim 3 `failed`. In the dedicated variant the protection SCP should still deny the assumption, which 0.6.0 tests. |
| `agent_can_modify_execution_role` | `research-agent` may change the execution role's policies | Claim 3 `failed`. As above, the protection SCP should still deny it. |

`slow_scp_propagation` and the other Claim 3 scenarios can't be built on purpose; they are properties of AWS or of other accounts.

## Prerequisites

- Python 3.11+, Node.js 18+, and the AWS CDK CLI (`npx aws-cdk@2.160.0`).
- **Dedicated variant:** an AWS Organization with **SCPs enabled**, and three member accounts used for nothing else:
  - the management account, where the SCPs are created;
  - a security or tooling account;
  - a dedicated agent account.

  The management stack also delegates SCP attach and detach (Organizations resource policy) to the tooling account, so the operator role can work there.
- **Shared variant:** one disposable account.
- Each account bootstrapped once: `npx aws-cdk@2.160.0 bootstrap aws://<account>/<region>`.

## Deploy

```bash
cd testbed
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
```

**Shared variant** (one account):

```bash
npx aws-cdk@2.160.0 deploy -c variant=shared -c agent_account=<id> --outputs-file outputs.json
python -m haltai_testbed.declaration outputs.json -o environment.yaml
```

**Dedicated variant.** Deploy in this order, each with that account's credentials. The agent stack needs the stub URLs from the tooling stack.

```bash
CTX="-c agent_account=<agent> -c tooling_account=<tooling> -c management_account=<mgmt>"
npx aws-cdk@2.160.0 deploy HaltAITestbed-Tooling $CTX --profile tooling --outputs-file outputs-tooling.json
npx aws-cdk@2.160.0 deploy HaltAITestbed-Agents $CTX --profile agent --outputs-file outputs-agents.json \
  -c model_stub_url=<ModelStubUrl> -c tool_stub_url=<ToolStubUrl>
npx aws-cdk@2.160.0 deploy HaltAITestbed-Management $CTX --profile management
python -m haltai_testbed.declaration outputs-tooling.json outputs-agents.json -o environment.yaml
```

Then check the generated declaration:

```bash
haltai validate --environment environment.yaml
```

Other context settings:

| Setting | Default |
|---|---|
| `region` | `eu-west-2` |
| `environment` | `sandbox` |
| `recovery_owner` | `platform-oncall@example.com` |
| `canary_endpoints` | `true`; `false` drops the endpoint subnet and its cost |

## Tear down

The testbed has no automatic expiry, so destroy it the same day. For the dedicated variant, destroy in reverse order: the management stack first, so the SCPs are detached and deleted.

```bash
npx aws-cdk@2.160.0 destroy HaltAITestbed-Management $CTX --profile management
npx aws-cdk@2.160.0 destroy HaltAITestbed-Agents $CTX --profile agent
npx aws-cdk@2.160.0 destroy HaltAITestbed-Tooling $CTX --profile tooling
```

For the shared variant, run `npx aws-cdk@2.160.0 destroy -c variant=shared -c agent_account=<id>`.

Every resource, including the trail bucket, log groups and the canary repository, is set to be deleted with its stack. If a destroy fails part-way, re-run it. Then search for leftovers with the tag `haltai:testbed=true` (Resource Groups → Tag Editor).

**Manual fallback.** If HaltAI itself fails during a drill, this is the procedure:
1. Detach `haltai-halt-agents` or `haltai-isolate` from the agent account (Organizations console, from the management account).
2. Re-associate the agent subnets with their original network ACL.
3. Restore `research-agent`'s desired count and the Lambda concurrency.
4. Re-enable `agent-run`, `agents/digest` and the event source mapping.

The generated `environment.yaml` points `recovery.manual_fallback` here.

## Cost

These are rough figures for eu-west-2 at on-demand prices, while deployed. Check them with the AWS pricing calculator.

| Item | Approx. |
|---|---|
| NAT gateway | $0.05/hour, plus data |
| 4 interface endpoints in one AZ (`canary_endpoints`) | $0.045/hour |
| `research-agent` Fargate task (0.25 vCPU, 0.5 GB) | $0.012/hour |
| Lambda, SQS, EventBridge, Scheduler, Flow Logs, CloudTrail (first trail), S3 | Cents per day |
| Secrets Manager (`untracked_credential` only) | $0.40/month |
| **Total** | **About $0.11/hour, about $3/day, about $80/month if left running** |

## Known limits

- **Not deployed yet.** CI synthesises both variants (`cdk synth`) without AWS credentials. The first deployment is a 0.6.0 step.
- `cdk.json` caches the availability zones for the placeholder account `111111111111`, so `cdk synth` runs in CI without credentials. With your own account IDs, the CLI looks the zones up when you deploy.
- The protection SCP exempts the CDK bootstrap roles (`role/cdk-*`), so the testbed can still be updated and destroyed. Claim 3 must show no agent can use those roles.
- Some protection-SCP statements use `aws:ResourceTag`, which not every action supports. Which denials actually apply is checked in 0.6.0.
- The S3 and ECR endpoints use private DNS, so their restrictive policies apply to the whole VPC. The testbed's agents use neither service. In real environments, see the [canary design](../docs/design/canary-probe.md#3-could-not-start-is-not-denied).
- The stubs are public, unauthenticated function URLs that only return a fixed message. The collateral services' URLs are public too.

## Tests

- `tests/test_testbed.py`, in the main suite with no Node.js, covers:
  - the settings: refusing production names, toggle names, account checks;
  - SCP size and contents;
  - the generated `environment.yaml` validating against the schema with no blocking limitation.
- `testbed/tests/test_stacks.py` (needs Node.js; the `testbed` CI job runs it) synthesises both variants and every toggle. It checks:
  - the tags;
  - the execution role's trust;
  - that the SCPs are staged and the protection SCP is attached;
  - that the network ACL isn't associated;
  - the canary resources.
