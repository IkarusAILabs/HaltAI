# Using the aws backend

> The aws backend is implemented and tested with moto and stubs only. It has not yet been run against a real account. Start in a disposable non-production account.

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

## Operator role permissions

Keep the operator role separate from the agent role (roadmap section 5).

Read-only (check, snapshot):

```
sts:GetCallerIdentity
ecs:DescribeServices, ecs:DescribeTaskDefinition, ecs:ListServices, ecs:ListTasks
iam:GetRole, iam:ListRolePolicies, iam:GetRolePolicy, iam:ListAttachedRolePolicies,
iam:SimulatePrincipalPolicy
lambda:ListFunctions
apigateway:GET
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
