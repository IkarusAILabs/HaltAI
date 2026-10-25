# Using the aws backend

> The aws backend is implemented and tested with moto and stubs only. It has not yet been run against a real account. Start in a disposable non-production account.

> **Scope (roadmap v0.4).** This page describes today's **drill** backend: one registered deployment, a gateway or IAM deny, and probes that rely on the [integration contract](integration-contract.md). The planned break-glass commands (`prepare`, `halt`, `status`, `release`) will use new AWS adapters for the four levers (identity, network, workload, work intake), act on a declared boundary, and need no agent code. They are not implemented yet. See [roadmap section 6](roadmap.md#6-the-four-levers-on-aws).

## Switch

```yaml
# haltai.yaml
backend: aws
aws:
  profile: haltai-nonprod   # a profile for the HaltAI operator role
  region: null              # defaults to the manifest region
```

Fill `deployment.yaml` with the real values: `aws_account_id`, `runtime.cluster/service/container`, `identity.role_arn`, `work_intake.queue_url`, `tool_gateway.endpoint/rest_api_id/stage/write_route`, `protected_effect.store.table`, `probes.results_table`, and `collateral_probe.url`.

Then:

```bash
haltai validate
haltai check                  # read-only
haltai snapshot               # read-only; writes snapshot.json
haltai drill --confirm        # disruptive, non-production only
haltai rollback --confirm     # if a drill was interrupted
```

## Containment mechanisms

| `containment_policy.mechanism` | What HaltAI changes | Rollback |
|---|---|---|
| `gateway_resource_policy_deny` (default) | Adds a `Deny` statement with Sid `HaltAIContainment` for the agent role on the write route to the REST API resource policy, then redeploys the stage | Removes the statement and redeploys |
| `iam_inline_deny` | Puts inline policy `HaltAIContainment` on the agent role, denying `execute-api:Invoke` on the write route | Deletes the inline policy |

Both changes are eventually consistent, which is why HaltAI measures the time to denial instead of assuming it.

Neither is a pre-staged toggle: each reconfigures the deployment at activation, and `haltai check` says so. The planned `haltai prepare` replaces this with pre-staged controls (roadmap v0.4, section 6).

`haltai check` also guards activation:

- **Leftover containment.** If the `HaltAIContainment` statement or inline policy already exists, the check is blocking. Containment may be active for a real reason, and a drill would remove it in its rollback. Check first, then run `haltai rollback --confirm`.
- **Undeployed gateway changes.** The gateway mechanism redeploys the stage, which publishes every undeployed change on the API. HaltAI compares routes, methods and authorization types between the API and the deployed stage, and blocks if they differ. Integration changes are not compared. If the comparison fails, it is reported as a limitation.
- **Claim 3.** Policy simulation of the escape catalogue in [`claims.md`](claims.md) for the agent role. Needs `iam:SimulatePrincipalPolicy`, already in the read-only list below.

## Operator role permissions

Keep the operator role separate from the agent role (roadmap section 5).

Read-only (check, snapshot):

```
sts:GetCallerIdentity
ecs:DescribeServices, ecs:DescribeTaskDefinition, ecs:ListServices, ecs:ListTasks
iam:GetRole, iam:ListRolePolicies, iam:GetRolePolicy, iam:ListAttachedRolePolicies,
iam:SimulatePrincipalPolicy
lambda:ListFunctions
apigateway:GET                     (includes stage, deployment and resource reads)
sqs:GetQueueAttributes
dynamodb:DescribeTable, dynamodb:Scan (marker table), dynamodb:GetItem (probe results table)
```

Drill (in addition), scoped to the bound resources:

```
apigateway:PATCH, apigateway:POST        (resource policy + deployment)   # gateway mechanism
iam:PutRolePolicy, iam:DeleteRolePolicy  (agent role only)               # iam mechanism
sqs:SendMessage                           (work-intake queue)
ecs:RunTask, ecs:StopTask, ecs:UpdateService (agent service only)
iam:PassRole                              (agent task role, for RunTask)
```

## Before the first real drill

1. Implement the [integration contract](integration-contract.md) in the worker and the gateway backend.
2. Run `haltai check` and fix every blocking limitation.
3. Run one drill with `drill.repetitions: 1` while watching the deployment.
4. Compare the report with the baseline checklist (plan step M9).
