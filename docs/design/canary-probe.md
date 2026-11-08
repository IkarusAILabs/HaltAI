# Design: the canary probe

**Status:** proposed, for agreement by 22 November 2026 (issue #20, release plan 0.3.0 step 2). **Design only.** Implementation is in 0.4.0 (simulation) and 0.5.0 (AWS). Statements marked **(V*n*)** are assumptions that the 0.6.0 sandbox must confirm before any result depends on them (see [Validation](#8-validation-in-the-060-sandbox)).

## Summary

HaltAI verifies a halt **without agent code** by running its own canary **as the agent's identity, from the agent's network**, and watching what it can still do.

| Question | Decision |
|---|---|
| What runs | ECS agents: a HaltAI-owned Fargate task, run with the agent's task role in the agent service's subnets and security groups. Lambda agents: a HaltAI-owned function with the agent function's role and VPC configuration. |
| Who launches it | The HaltAI **execution role** inside the agent account ([roadmap 7.1](../roadmap.md#71-two-haltai-identities)). `iam:PassRole` is limited to the declared agent roles and the two compute services. |
| How it reports when cut off | ECS: the **container exit code**, read from the ECS control plane (`DescribeTasks`). One of three states per probe, in a reserved code band. Lambda: the `Invoke` response payload. Neither path needs the canary's own network or credentials. |
| "Could not start" | Never `stopped`. A task that fails to start, an exit code outside the band, or a probe that "could not test" makes that lever's canary evidence `inconclusive`. |
| Working canary | A **positive control** before every halt (an "armed" canary). Under network quarantine, the image is pulled through a dedicated HaltAI endpoint subnet. |
| What it tries | A synthetic write to a HaltAI canary queue (identity); a TCP and TLS connect to each declared model and tool endpoint (network); the same write with a session issued **before** the halt; whether an **established** connection survives. No model calls. No tool calls. |
| Trust | It proves what *that identity on that path* can do, nothing more. `drill` → `enforcement_tested`; `halt`/`status` → `observed_after_activation`. HaltAI identifies canary activity from its own records of task ARNs and network interfaces, not from tags. |

## 1. Launch

### ECS agents

The execution role calls `ecs:RunTask`:

| Field | Value |
|---|---|
| `cluster` | `haltai-canary`, a HaltAI-owned Fargate cluster in the agent account. HaltAI never places tasks in the agents' clusters. |
| `taskDefinition` | `haltai-canary:<revision>`, pinned by full ARN. The container image is pinned **by digest**. |
| `overrides.taskRoleArn` | The agent service's task role, read from the service's current task definition |
| `overrides.containerOverrides[].command` | The probe plan (section 4). The image's entrypoint is the canary binary. The image is distroless and has no shell, so a command override can only pass arguments to the canary. |
| `networkConfiguration` | The agent service's `awsvpcConfiguration` (subnets, security groups, public IP setting), plus the empty marker security group (section 5) |
| `startedBy` | `haltai-canary` |
| `launchType` | `FARGATE` |

The task definition's **task execution role** is `haltai-canary-pull`. It is used only by ECS, to pull the canary image. It may only pull from the `haltai/canary` repository. The task definition has no log configuration, no secrets and no environment taken from the agents, so the task needs nothing from the network except the image.

**Scope for 0.5.0:** agents on Fargate, or on EC2 with `awsvpc` networking. Agents using `bridge` or `host` networking take a different path (the instance's network interface and security groups). For those, the network result is reported as a limitation, never as `stopped`.

### Lambda agents

`prepare` creates `haltai-canary-<function>` for each declared agent function:
- HaltAI's code (a zip, not the agents' code);
- the agent function's **execution role**;
- the same `VpcConfig` (subnets and security groups), plus the marker security group;
- a short timeout.

The execution role invokes it synchronously, and the result comes back in the `Invoke` response. The agent function's own reserved concurrency (the workload lever) doesn't affect the canary function.

### Least privilege for the launcher

The execution role gets only these permissions for the canary:

```json
[
  {"Effect": "Allow", "Action": "iam:PassRole",
   "Resource": ["<identity.roles>", "arn:aws:iam::<acct>:role/haltai/haltai-canary-pull"],
   "Condition": {"StringEquals": {"iam:PassedToService": ["ecs-tasks.amazonaws.com", "lambda.amazonaws.com"]}}},
  {"Effect": "Allow", "Action": "ecs:RunTask",
   "Resource": "arn:aws:ecs:<region>:<acct>:task-definition/haltai-canary:*",
   "Condition": {"ArnEquals": {"ecs:cluster": "arn:aws:ecs:<region>:<acct>:cluster/haltai-canary"}}},
  {"Effect": "Allow", "Action": ["ecs:DescribeTasks", "ecs:StopTask", "ecs:ListTasks"], "Resource": "*",
   "Condition": {"ArnEquals": {"ecs:cluster": "arn:aws:ecs:<region>:<acct>:cluster/haltai-canary"}}},
  {"Effect": "Allow", "Action": "lambda:InvokeFunction",
   "Resource": "arn:aws:lambda:<region>:<acct>:function:haltai-canary-*"}
]
```

Creating the canary function, with `lambda:CreateFunction` and `UpdateFunctionConfiguration`, happens only in `prepare`, under the same `PassRole` limits.

**Does `PassRole` create an escape path?** In the forward direction, no. The permission belongs to the execution role, which agents can't use (Claim 3 already checks that). It doesn't widen anything an agent can do. It does widen what a **stolen execution-role session** could do: run HaltAI's image as an agent role. The image is pinned and has no shell, and `RunTask` is limited to the canary task definition, so that is bounded.

**The other direction matters more.** An agent must not be able to change **what the canary runs or what its results depend on**. A canary that wrongly reports "denied" is a false `stopped`, the worst failure HaltAI can have. These additions to the boundary Claim 3 catalogue ([claims.md](../claims.md#claim-3-on-the-environment-boundary)) are a follow-up (section 10):

| Agent may… | Effect if allowed |
|---|---|
| `ecr:PutImage`, `ecr:BatchDeleteImage` or `ecr:SetRepositoryPolicy` on `haltai/canary` | Replace or remove the canary. The image is pinned by digest, so a replacement fails to start (`inconclusive`), but deletion blocks verification. |
| `ecs:RegisterTaskDefinition` for family `haltai-canary`, or `ecs:DeregisterTaskDefinition` on it | A new revision is ignored, because the revision is pinned. Deregistering blocks verification. |
| `lambda:UpdateFunctionCode`, `UpdateFunctionConfiguration` or `DeleteFunction` on `haltai-canary-*` | Fake canary results for Lambda agents |
| `sqs:SetQueueAttributes`, `sqs:DeleteQueue` or `sqs:RemovePermission` on the canary queue | Remove its own grant, so the identity probe is "denied" for the wrong reason: a **false `stopped`** |
| `ec2:ModifyNetworkInterfaceAttribute`, or use the marker security group on its own resources | Make agent traffic look like canary traffic |
| Pass or modify `haltai-canary-pull` | Pull with HaltAI's pull role. Harmless on its own, but kept under the same protection as the execution role. |

All of these resources are staged by `prepare`, tagged `haltai:managed=true`, and covered by the standing protection SCP (section 6).

### Why not the alternatives

| Alternative | Rejected because |
|---|---|
| Today's fresh-task probe: `haltai-probe` in the **agent's** image (`AwsBackend.probe_write_fresh_task`) | It needs HaltAI installed in every agent image, and the agent's entrypoint and code may run. Break-glass promises no agent code. It stays only for `--legacy` drills. |
| Assuming the agent role from the execution role (`sts:AssumeRole`) | It changes every agent role's trust policy, which is a new way in to the agent role, and it runs from HaltAI's network, not the agent's. |
| A Lambda canary for ECS agents too | The agent's task role would need VPC network-interface permissions, which changes the agent role. The runtime and egress also differ from the agents'. |
| Running identity probes from a separate HaltAI probe subnet | That path isn't one the agents use. Agent policies with network conditions (`aws:SourceVpce`) would break the positive control. |
| A long-running sentinel canary | A standing workload running as the agent role in agent subnets, which the halt must also contain. It costs money all the time, and it is a target. |

## 2. Reporting when the canary is cut off

This is the core problem. Under a network or identity halt, the canary can't reach DynamoDB, S3 or CloudWatch Logs. On Fargate, image pulls, logs and secrets all go through the task's own network interface.

### Decision: ECS exit code, Lambda `Invoke` response

- **ECS.** When the canary's container exits, ECS reports `stopCode`, `stoppedReason` and `containers[].exitCode` through `DescribeTasks`. HaltAI reads that with the execution role from the control plane. The Fargate agent reports task state to ECS over AWS's own network, not the task's network interface **(V1)**. ECS keeps stopped tasks visible only for a limited time (about an hour), so HaltAI reads each result as soon as the task stops and records it in the audit log.
- **Lambda.** A synchronous `Invoke` returns the handler's JSON to the caller. The function's own network and credentials aren't involved in returning it. The handler finishes its probes within its own deadline, which is shorter than the function timeout, so a blocked probe still returns.

### The exit-code encoding

The proposal in #20 was one bit per effect. It is **rejected in that form**, for two reasons:

1. **One bit can't tell "denied" from "could not test".** Question 3 requires exactly that distinction.
2. **Low exit codes collide with runtime failures**: 1 (generic error), 2, 126 and 127 (cannot execute), 128+*n* (killed by signal *n*, including 137 for out-of-memory and 143 for SIGTERM), and 255. A crash must never decode as a result.

**Adopted:** each run carries at most **three probes**. Each probe has three states, encoded in base 3 into a reserved band:

```
exit code = 200 + 9·s1 + 3·s2 + s3        (200..226)

s = 0  blocked       the probe's effect was refused: AccessDenied, or connection timed out or refused
s = 1  reached       the effect worked: the write was accepted, or the TCP and TLS handshake completed
s = 2  could not test  e.g. DNS failure, throttling, an unexpected error, or a denial caused by a resource policy
```

- Unused slots are 0. HaltAI decodes every code **against the probe plan it sent**, so the same code means different things in different plans.
- 200–226 is above every signal code (at most 128+64 = 192) and below 255.
- **Any code outside the band is not a result.** That includes a missing exit code.
- HaltAI launches as many runs as the plan needs: one per three probes.

**Example.** An armed identity canary has the plan `[write before halt, write after halt with the pre-halt session]`. It exits 209 = 200 + 9·1 + 3·0. Decoded: the write worked before the halt (positive control) and was blocked after it.

### Rejected reporting paths

| Path | Rejected because |
|---|---|
| DynamoDB, S3 or SQS results written by the canary | Cut off by the identity lever (the agent role is denied) and by the network lever |
| CloudWatch Logs (`awslogs` driver) | On Fargate, logs leave through the task's network interface, which the network lever cuts |
| Container health-check status | Coarse (healthy or unhealthy only), and timed by ECS rather than by the probe plan |
| Task tags set by the canary | Need the canary's own credentials and network |

**Later, not rejected:** sending ECS task state-change events to an event bus in HaltAI's account. That would save polling, but it isn't needed for correctness.

## 3. "Could not start" is not "denied"

### Outcomes that are never `stopped`

All of these make that lever's canary evidence **`inconclusive`**. In the 0.4.0 lever state model, that lever is then `unverified`, not `effect_verified`:

| What HaltAI sees | Typical cause |
|---|---|
| `RunTask` returns `failures`, or the task stops with `stopCode = TaskFailedToStart` | Capacity, subnet out of IP addresses, the agent role can't be passed |
| `stoppedReason` contains `CannotPullContainerError` or `ResourceInitializationError` | No pull path under quarantine; ECR denied by the SCP |
| No exit code, or one outside 200–226 | Crash, out-of-memory, killed |
| The task is still running at its deadline | HaltAI stops it (`StopTask`) |
| A slot decodes as "could not test" | DNS failure, throttling, a resource-policy denial |
| The positive control didn't succeed | The canary path never worked, so a later "blocked" proves nothing |
| An after-halt probe ran too early | Its scheduled time was before the lever's apply time plus the grace period |
| Lambda: `FunctionError`, a timeout, or the function isn't `Active` | Same as for ECS |

### Demonstrating a working canary

1. **In `prepare`:** a full canary run before anything is applied. Every probe must come back `reached`. If one doesn't, `prepare` records a non-blocking limitation ("canary path for *lever* doesn't work, so this lever can only be `inconclusive`"), with the reason.
2. **In `halt` and `drill`, an armed canary:** before applying the first lever, HaltAI launches the canary with a plan that runs **before** probes at once, waits until a scheduled time, then runs **after** probes and exits. The before probes are the positive control for the after probes in the same task. The task pulled its image, got its credentials and proved its path *before* anything was cut.
   - HaltAI sets the after time to the lever's planned apply time plus `grace_s`.
   - An after result counts only if the lever was actually applied by then. HaltAI knows the apply time, and the task's `stoppedAt` bounds when the probes ran.
3. **Under network quarantine, for later `status` runs:** a fresh task must pull its image while the agent subnets are quarantined. The staged quarantine network ACL therefore allows exactly one path: TCP 443 to, and return traffic from, a **dedicated HaltAI endpoint subnet**. That subnet contains only interface endpoints for ECR (`ecr.api`, `ecr.dkr`), S3 (image layers) and SQS (the canary queue) **(V2)**.
   - **Endpoint policies** allow only pulls from `haltai/canary`, reads of the ECR layer bucket, and `sqs:SendMessage` to the canary queue.
   - **`prepare` checks** that nothing else has a network interface in that subnet. A customer endpoint there, for example for Bedrock, would let agents reach the model through the quarantine, so it is a **blocking** finding.
   - **What remains open to agents** under quarantine: pulling HaltAI's canary image, and sending to the canary queue (which the identity lever also denies). This is reported, never hidden.
   - **Private DNS is VPC-wide** (found while building the testbed, #21). An interface endpoint with private DNS takes over that service's hostname for the whole VPC, so its restrictive policy would also apply to the agents' normal traffic. So:
     - The canary reaches SQS through the endpoint's **own DNS name**, with private DNS off. The agents' SQS traffic keeps using its normal path, which the quarantine cuts.
     - ECR and S3 need private DNS, because ECS pulls the image using the standard hostnames. Where agents also pull private ECR images or use S3, those endpoint policies must allow that too, which leaves it reachable during a quarantine. `prepare` reports that as residual.
   - Name resolution (Route 53 Resolver) and the task credentials endpoint (169.254.170.2) aren't filtered by network ACLs **(V3)**.

For Lambda agents there is no image pull, and staged functions keep their network interfaces, so the pull problem doesn't arise.

### Under the halt SCP (`isolate` profile)

`halt-agents` denies only the declared agent roles, so `haltai-canary-pull` isn't affected. `isolate` denies every principal in the account except the execution role. Image pulls by the pull role are therefore excepted **by action and resource**, not by principal:

```json
{"Sid": "HaltAllButHaltAI", "Effect": "Deny",
 "NotAction": ["ecr:GetAuthorizationToken", "ecr:BatchGetImage",
               "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"],
 "Resource": "*",
 "Condition": {"ArnNotLike": {"aws:PrincipalArn": "arn:aws:iam::<acct>:role/haltai/haltai-execution"}}},
{"Sid": "PullOnlyTheCanary", "Effect": "Deny",
 "Action": ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"],
 "NotResource": "arn:aws:ecr:<region>:<acct>:repository/haltai/canary",
 "Condition": {"ArnNotLike": {"aws:PrincipalArn": "arn:aws:iam::<acct>:role/haltai/haltai-execution"}}}
```

The execution role stays the **only principal** exempt from the halt SCP. During an `isolate` halt, any principal can still pull the canary image. That is harmless and is reported.

## 4. What the canary tries

| Probe | Lever | What it does | blocked / reached / could not test |
|---|---|---|---|
| `write` | Identity | `sqs:SendMessage` to `haltai-canary` with the task's current credentials. The queue policy grants the declared agent roles this one action. In the same account, a resource policy grant is enough, so **agent roles aren't changed**. | `AccessDenied` / accepted / anything else, including a network error |
| `write_presession` | Identity | The same write, using credentials the armed canary read **before** the halt and kept in memory | as `write` |
| `connect:<endpoint>` | Network | A new TCP connection plus a TLS handshake to a declared `model_endpoints` or `tool_endpoints` host. **No HTTP request.** | timeout or refused / handshake complete / DNS failure |
| `established` | Network | Armed canary only: a TLS connection opened before the halt, then one unauthenticated `HEAD /` on it after | no response / any HTTP response / connection never opened |
| Lambda | Identity, network | The same probes, run in the canary function with the agent function's role and VPC configuration. Results come back as JSON in the `Invoke` response. `write_presession` uses credentials kept from an earlier invocation in the same execution environment; if it is a cold start, that probe "could not test". | as above |

**Which denial counts.** `AccessDenied` counts as *blocked* only when it comes from the identity side: an SCP, a permissions boundary or an identity policy. If the error message says the denial came from a **resource-based policy**, the probe "could not test". An agent that removed the queue's grant must never produce a false `stopped`. This relies on services naming the policy type in their denial messages **(V4)**. Where they don't, the queue policy is drift-checked before and after the halt.

**No model calls and no tool calls.**
- The network probes stop after the TLS handshake.
- `HEAD /` carries no credentials, so it can't reach an authenticated action.
- The only write goes to HaltAI's own queue. Its retention is 60 seconds, and nothing reads it.

**Probe plans:**

| Command | Runs |
|---|---|
| `prepare` | Fresh: `[write]`; `[connect…]` (positive control for every path) |
| `halt`, `drill` | Armed identity: `[write before, write_presession after]`. Armed network: `[connect before, connect after, established after]`, for the first declared endpoint. |
| `status` | Fresh: `[write]`; `[connect…]` for every declared endpoint, three per run |

**Workload and work intake have no canary.** They are verified by infrastructure observation, as in the simulator ([levers.md](../levers.md#how-an-observation-is-decided)):
- **Workload:** running-task counts, Application Auto Scaling state, Lambda concurrency and its `Throttles` and `Invocations` metrics.
- **Intake:** rule and schedule state, plus their invocation metrics.

A canary that starts agent work would be running agent code.

## 5. Trust

**What a canary result proves.** That **this identity, on this network path, at this time** can or can't reach the effect. It doesn't prove that no other identity or path exists. Coverage is the declaration's job ([threat model](../threat-model.md#9-assumptions-every-report-inherits), assumptions 1 and 4), and undeclared paths show up as `partial` through infrastructure evidence (the `untracked_credential` and `schedule_outside_boundary` scenarios).

**Evidence levels:**

| When | Level |
|---|---|
| `prepare` positive control | None: it shows the canary works, not that anything is contained |
| `drill` (lever applied for the test, then released) | `enforcement_tested` |
| `halt` and `status` during a real halt | `observed_after_activation` |
| Policy simulation (Claims 1 and 3) | `boundary_assessed` (unchanged) |

**Telling the canary apart from agents.** Otherwise the canary's own attempts would count as agent activity, and a lever would be `partial` because of HaltAI itself.
- **CloudTrail:** an ECS task role session is named after the task ID, and a Lambda session after the function name. HaltAI excludes sessions whose task ARN or function name is in **its own record** of what it launched.
- **VPC Flow Logs:** HaltAI excludes the canary's network interfaces, read from each task's `attachments` in `DescribeTasks`.
- **Lambda:** functions with the same subnets and security groups share network interfaces. The canary function therefore also gets an **empty marker security group**. Security groups only add permissions and this one has no rules, so the effective rules are unchanged. But the set of groups is different, so the canary gets its own interface.
- **Tags, `startedBy` and the marker group are conveniences only.** An agent able to set them could disguise its own activity. That is why exclusion uses HaltAI's records, and why using the marker group is in the Claim 3 catalogue.

## 6. Execution-role policies (roadmap 7.1, sketch)

Release plan 0.3.0 step 2 pairs this design with the execution role's policies. These are sketches. The CDK reference environment (#21) makes them concrete, and `prepare` checks them in 0.5.0.

**Trust policy:** only the exact operator role ARN, with no wildcards and no in-account principals.

```json
{"Effect": "Allow", "Principal": {"AWS": "arn:aws:iam::<operator-acct>:role/haltai-operator"},
 "Action": "sts:AssumeRole"}
```

**Halt SCP:**
- `halt-agents`: deny `*` where `aws:PrincipalArn` matches `identity.roles`.
- `isolate`: deny `*` for every principal except the execution role, with the canary pull exception from section 3.

**Standing protection SCP**, attached at all times. Every statement has the condition `ArnNotLike aws:PrincipalArn <execution role>`:
- deny role changes (`iam:Put/Attach/Detach/DeleteRolePolicy`, `iam:UpdateAssumeRolePolicy`, `iam:Put/DeleteRolePermissionsBoundary`, `iam:DeleteRole`, `iam:Tag/UntagRole`), plus `sts:AssumeRole` and `iam:PassRole`, on `role/haltai/*`;
- deny changes to resources tagged `haltai:managed=true`: network ACLs, staged policies (`policy/haltai/*`), the canary repository, task definition, functions and queue, the marker security group and the endpoint subnet;
- deny adding or removing the `haltai:managed` tag (`aws:TagKeys`);
- deny `organizations:LeaveOrganization`.

**Shared-account variant:** there are no SCPs. The same denies become IAM policies on the agent roles, which Claim 3 checks. This is weaker, and reports say so.

## 7. Cost and blast radius

| | |
|---|---|
| Task size | Fargate 0.25 vCPU, 0.5 GB. Lambda: 128 MB. |
| Run time | Fresh runs: about 30 s plus start-up, with a hard deadline of 120 s. Armed runs: up to `deny_timeout_s` plus `observation_window_s` (default 180 s). |
| Runs | `prepare`: 1 + ⌈endpoints/3⌉. `halt` and `drill`: 2 armed. `status`: 1 + ⌈endpoints/3⌉. `status` reuses results younger than 60 s instead of launching again. |
| Cost | Under a cent per run at Fargate's per-second prices; dominated by the one-minute minimum |
| IP addresses | One per running canary task, in the agent subnets. `prepare` warns if free addresses are fewer than the runs `status` needs. |
| What it touches | No agent code, secrets or environment variables; no model or tool requests; one write to HaltAI's own queue |

**Clean-up after interrupted runs.**
- Every canary has a self-enforced deadline and exits by it.
- At the start of every `prepare`, `halt`, `status` and `release`, HaltAI lists tasks in `haltai-canary` with `startedBy = haltai-canary`, and stops any that are older than their deadline plus a margin.
- `release` stops all canary tasks.
- Staged canary functions and resources are kept until the environment is un-prepared (planned).

## 8. Validation in the 0.6.0 sandbox

Each assumption is tested before any result depends on it. If one fails, the listed fallback applies, and the docs and reports say so.

| | Assumption | Test | Fallback if false |
|---|---|---|---|
| V1 | ECS reports the exit code of a task whose network interface is fully cut | Armed canary; apply a deny-all network ACL; exit with a known code; read `DescribeTasks` | The network lever's canary evidence becomes `inconclusive`; Flow Logs only |
| V2 | Image pull works through the HaltAI endpoint subnet under the quarantine network ACL | `status` canary during a network halt | Network `status` relies on armed canaries plus Flow Logs |
| V3 | DNS and the task credentials endpoint aren't filtered by network ACLs | Armed canary resolving and reading credentials after quarantine | Connect probes report DNS failure as "could not test"; `inconclusive` |
| V4 | SQS `AccessDenied` messages name the denying policy type | Deny the write with an SCP, then with a queue policy; compare messages | Drift-check the queue policy before and after; report it as a limitation |
| V5 | A Lambda canary in agent subnets runs and returns under both the halt SCP and quarantine | Invoke during `drill` | Lambda agents' identity and network become `inconclusive` |

## 9. Worked examples

These use the [example declaration](../../examples/environment.yaml): one ECS service `agents/research-agent`, one Lambda function `summariser-agent`, one model endpoint, one tool endpoint and the `halt-agents` profile. Codes are decoded against their plan.

### Identity

| Result | Evidence |
|---|---|
| **stopped** | Armed `[write before, write_presession after]` exits **209** (1, 0): it worked before and was blocked after. A fresh `status` run with `[write]` exits **200** (blocked). The Lambda canary returns `{"write": "blocked"}`. No allowed CloudTrail calls by agent roles in the window, after excluding the canary. |
| **not_stopped** | Fresh `[write]` exits **209** (reached). For example, the SCP hasn't propagated (`slow_scp_propagation`). |
| **partial** | Fresh `[write]` exits **200**, but CloudTrail shows allowed calls by an agent principal. For example, a credential outside the boundary (`untracked_credential`), or an agent that switched to the execution role (`agent_can_assume_execution_role`). |
| **partial** (presession) | Armed run exits **212** (1, 1): new sessions are denied, but a session issued before the halt still works |
| **inconclusive** | The armed run exits **200** (0, 0): no positive control. Or a run exits **218** (could not test, e.g. a network error), or stops with `TaskFailedToStart`. |

### Network

| Result | Evidence |
|---|---|
| **stopped** | Armed `[connect before, connect after, established after]` exits **209** (1, 0, 0). Fresh `status` with `[connect model, connect tool]` exits **200**. Flow Logs show only rejects from agent interfaces. |
| **partial** | Armed run exits **210** (1, 0, 1): new connections are refused, but the established one still answers. That is a security-group change rather than a network ACL (`sg_change_keeps_connections`). |
| **not_stopped** | Fresh `[connect model, connect tool]` exits **209** (1, 0): the model endpoint is still reachable from an agent subnet |
| **inconclusive** | The fresh run stops with `CannotPullContainerError` (no pull path, V2). Or the armed run's before slot is 2 (DNS failure, so no positive control). |

### Workload (no canary)

| Result | Evidence |
|---|---|
| **stopped** | `research-agent` has desired count 0, no running tasks, and scaling suspended. `summariser-agent` has reserved concurrency 0, with `Throttles` > 0 or `Invocations` = 0 in the window. |
| **not_stopped** | Application Auto Scaling set the desired count back and tasks are running (`autoscaling_restarts_tasks`) |
| **partial** | No declared task is running now, but agent compute ran in the window, for example from an undeclared schedule (`schedule_outside_boundary`) |
| **inconclusive** | No agent compute was seen before the halt, or the metrics can't be read |

### Work intake (no canary)

| Result | Evidence |
|---|---|
| **stopped** | Declared rules and schedules are disabled, and their invocation metrics are 0 in the window. Messages left in SQS are listed as residual. |
| **not_stopped** | A declared rule fired after the grace period |
| **partial** | Only an undeclared trigger fired (a coverage gap) |
| **inconclusive** | No declared trigger fired before the halt (no positive control), so a quiet window proves nothing |

## 10. Follow-up implementation issues (Phase 2)

To open once this design is agreed:

1. **Canary runner and image** (0.5.0): probes, the base-3 band encoding with exhaustive decode tests, deadlines, and the armed mode; a distroless image pinned by digest, built by the release workflow.
2. **ECS canary adapter** (0.5.0): `RunTask` with overrides, decoding from `DescribeTasks`, handling of `stopCode` and `stoppedReason`, clean-up, and the record of canary task ARNs and network interfaces used to exclude canary activity.
3. **Lambda canary adapter** (0.5.0): staged functions, synchronous invoke, decoding the payload.
4. **Staged canary resources and checks in `prepare`** (0.5.0, with #21):
   - the repository, task definition, cluster and pull role;
   - the queue and its policy;
   - the marker security group;
   - the endpoint subnet and endpoint policies, with the blocking "nothing else in the endpoint subnet" check;
   - the quarantine network ACL's single allowed path.
5. **Claim 3 catalogue additions** (0.4.0, simulation first): the canary resources in section 1's table.
6. **Simulator** (0.4.0): canary start failure, a lost exit code and resource-policy denial as scenarios, each expected to give `inconclusive`. Armed canaries in `halt`.
7. **Sandbox validation V1–V5** (0.6.0).
