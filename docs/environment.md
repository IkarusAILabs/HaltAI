# Environment declaration (`environment.yaml`)

**Status:** schema and validation exist (release 0.3.0, issue #18). The commands that act on it (`prepare`, `halt`, `status` and `release`) are planned for 0.4.0. See the [release plan](release-plan.md).

`deployment.yaml` binds one agent deployment for today's drills. `environment.yaml` declares the **boundary** the break-glass commands may act on: which account, OU or tagged slice; who operates HaltAI; what each lever acts on; and who owns recovery. HaltAI never discovers resources to fill gaps. Anything not declared is out of scope and is reported, never silently included (roadmap section 7).

```bash
haltai validate --environment environment.yaml          # human output
haltai validate --environment environment.yaml --json   # machine output
```

`haltai init` writes a starter `environment.yaml`, identical to [`examples/environment.yaml`](../examples/environment.yaml).

**Exit codes:**

| Code | Meaning |
|---|---|
| 0 | No blocking limitations (non-blocking ones may still be listed) |
| 1 | At least one blocking limitation |
| 2 | File not found, or not a valid declaration (YAML or schema error). The message goes to stderr; with `--json`, stdout stays empty. |

## Fields

| Field | Required | Meaning |
|---|---|---|
| `name` | yes | Name of this environment |
| `environment` | yes | Stage name. `prod`, `production` and `live` are refused until the roadmap's Phase 4 gate. |
| `boundary.kind` | no (`account`) | `account` (a dedicated agent account, preferred), `ou` (an organizational unit), or `tagged` (tagged resources in a shared account, which is weaker) |
| `boundary.aws_account_id` | yes | 12-digit agent account ID (for `ou`, the account HaltAI acts in first) |
| `boundary.organization_unit_id` | for `ou` | The OU ID |
| `boundary.regions` | yes | At least one region |
| `boundary.tag_selector` | for `tagged` | Tags that mark resources in scope, for example `{haltai:scope: agents}` |
| `boundary.subnets` | for the network lever | Agent subnets |
| `operator.role_arn` | yes | Operator role, **outside** the boundary (roadmap 7.1) |
| `operator.execution_role_arn` | no | HaltAI execution role **inside** the agent account. Omit it and `prepare` creates it with the roadmap 7.1 protections. |
| `levers` | no (`halt-agents`: all four) | Levers per profile (`halt-agents`, `isolate`). They are always applied in the order identity → network → intake → workload, whatever order you list them in. |
| `identity.roles` | for the identity lever | Agent role ARNs, or a path prefix ending in `*` |
| `identity.revoke_sessions` | no (`true`) | Also deny sessions issued before the halt (`aws:TokenIssueTime`) |
| `network.model_endpoints`, `network.tool_endpoints` | no | Endpoints the canary checks are unreachable after a halt |
| `workload.ecs_services` | for the workload lever | `cluster/service` |
| `workload.lambda_functions` | for the workload lever | Function names |
| `intake.eventbridge_rules`, `intake.schedules`, `intake.event_source_mappings`, `intake.sqs_queues` | for the intake lever | Work-intake paths. Schedules are `group/name`. |
| `collateral.in_boundary` | for `isolate` | Services `isolate` may interrupt; observed to measure blast radius |
| `collateral.out_of_boundary` | no | Services that must never be interrupted |
| `recovery.owners` | yes | Who owns recovery |
| `recovery.manual_fallback` | yes | Where the documented manual procedure lives |
| `authorisation.isolate_requires_two_approvers` | no (`true`) | Dual authorisation for `isolate` (enforced from 0.7.0) |

A lever is **covered** when the declaration names at least one resource for it:
- **identity:** roles;
- **network:** subnets;
- **workload:** ECS services or Lambda functions;
- **intake:** any intake path.

## Validation rules

| Rule | Blocking |
|---|---|
| `environment` is a production name | yes |
| `tagged` boundary without `tag_selector` | yes |
| `ou` boundary without `organization_unit_id` | yes |
| `tagged` boundary with the network lever on but no `subnets` | yes |
| `operator.role_arn` is not an IAM role ARN | yes |
| `account` boundary: operator role in the agent account | yes |
| `account` boundary: execution role outside the agent account | yes |
| `account` boundary: an `identity.roles` entry in another account (the identity lever can't reach it) | yes |
| No profile enables any lever | yes |
| No `recovery.owners`, or an empty `recovery.manual_fallback` | yes |
| `ou` boundary: whether the operator is outside the OU can only be checked live | no; `prepare` verifies it |
| `tagged` boundary: operator role in the shared account | no; `prepare` must show it isn't in scope and can't be modified by agents |
| `account` boundary: no execution role declared | no; `prepare` creates it |
| A lever is enabled but not covered | no; reported as `not_covered` |
| `isolate` is enabled but there are no `collateral.in_boundary` services | no; its blast radius can't be observed |

Some checks need live AWS state, so the validator can only flag them. Examples: whether an account belongs to an OU, and which roles carry a tag. These are reported as non-blocking limitations here and become checks in `prepare`.

`haltai check --environment environment.yaml` runs the validation above **plus Claim 3 on the boundary**: can any agent identity take over HaltAI's execution role, retarget its SCPs, leave the organization, edit staged controls, or untag itself out of scope ([claims.md](claims.md#claim-3-on-the-environment-boundary))? Until the AWS adapters exist (0.5.0), the permission decisions come from the simulated environment.
