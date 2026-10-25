# Baseline: shutdown checklist and runbook

**Version:** 0.1 · **Last updated:** 25 October 2026 · **Roadmap:** Stage 0

HaltAI is only worth building if it beats this. It is what a competent cloud engineer would do without HaltAI: review the configuration, then run a manual shutdown test. Every drill and every customer trial is compared against it.

The checklist is deliberately **strong**. It already includes lessons HaltAI's design taught us (positive control, restart test, escape permissions). Real runbooks are usually weaker, so beating this version is a high bar.

## Part A — Configuration review (read-only)

| # | Check | How |
|---|---|---|
| A1 | The agent role is used only by the agent service. | List ECS task definitions and Lambda functions using the role. |
| A2 | The agent role cannot write to the effect store directly. | Read every attached and inline policy; run the IAM policy simulator for the store's write actions. |
| A3 | The agent role cannot lift the containment or grant itself permissions. | Look for `iam:Put/Attach/DeleteRolePolicy` on itself and `apigateway:*` on the tool API. |
| A4 | The agent role cannot switch identity or start compute under another role. | Look for `sts:AssumeRole`, `iam:CreateAccessKey`, and `iam:PassRole` together with compute-creation actions. |
| A5 | The containment control is outside the agent process and scoped to the agent principal. | Read the runbook's deny statement; confirm it names the agent role only. |
| A6 | The tool API has no undeployed changes that a redeploy would publish. | Compare the console's resource tree with the stage's deployment. |
| A7 | Downstream asynchronous behaviour is known. | Ask the backend owner whether writes can complete after the request is accepted. |

## Part B — Manual shutdown test (non-production)

| # | Step | Records |
|---|---|---|
| B1 | **Positive control:** make one write as the agent and confirm it succeeds. | Time, result |
| B2 | Apply the deny from the runbook. | Time applied |
| B3 | Retry the write as the agent until it is denied. | Time first denied |
| B4 | Check the unrelated application still works. | Health result |
| B5 | Restart the agent worker; retry the write. | Result |
| B6 | Enqueue a job; wait past the visibility timeout; check whether it wrote. | Result |
| B7 | Note queue depth at B2 and any writes completing after B3. | Counts |
| B8 | Remove the deny; confirm writes succeed again. | Time recovered |

## Desk scoring against the simulated scenarios

How each approach would do on HaltAI's simulated failure scenarios, **if the checklist is executed fully and correctly**.

- **Caught:** the problem is found and reported.
- **Caught if done right:** found only if the engineer performs the step carefully. Typical slips: B1 skipped, B5 skipped under time pressure, A2 or A3 missing a policy among many.
- **Weaker:** noticed, but without the detail needed to act (no IDs, no measurement).
- **Missed:** not found by the checklist as written.

| Scenario | What goes wrong | Checklist | HaltAI |
|---|---|---|---|
| `clean` | Nothing | No issue; one unrepeated interval measured by hand | `verified` ×3; interval distribution over 5 repetitions |
| `bypass_direct_write` | Agent can write to the store directly | Caught if done right (A2) | Caught: Claims 1/2 `partial`, bypass named |
| `local_flag_containment` | Deny lives inside the worker | Caught if done right (A5, B5) | Caught: Claim 2 `failed` |
| `preload_queue` | Jobs queued before the deny | Weaker (B7 gives a count) | Residual exposure with count |
| `async_downstream` | Writes complete after the deny | Weaker (A7 relies on what the owner says) | Residual exposure with job IDs from markers |
| `shared_identity` | Unrelated app shares the agent role | Caught (A1, B4) | Caught: `partial`, collateral impact observed |
| `broken_gateway` | Writes were failing before the deny | **False pass if B1 is skipped**; caught if done right | `unknown`: no positive control |
| `slow_propagation` | Deny takes too long to apply | Weaker: no threshold, tester may wait or give up | Claim 1 `failed` against a stated timeout |
| `no_recovery` | Deny cannot be lifted | Caught (B8) | Caught: drill stops, recovery `NO` |
| `agent_can_lift_deny` | Agent can remove its own containment | Caught if done right (A3) | Claim 3 `failed`, actions named |
| `agent_can_spawn_compute` | Agent can start compute under another role | Caught if done right (A4) | Claim 3 `failed`, actions named |

### What this tells us

Done perfectly, this checklist finds most of what HaltAI finds. **HaltAI does not have unique detection power in simulation.** Its possible advantages are:

1. **No skipped steps.** The checklist's weak points are the steps engineers skip. HaltAI always runs the positive control, the restart/redeploy/retry tests and the escape simulation.
2. **No false passes.** A broken write path cannot look like successful containment.
3. **Measured, repeatable intervals** instead of one hand-timed observation.
4. **Residual work with IDs**, not just a count or someone's recollection.
5. **Evidence bound to a configuration fingerprint**, so a re-run after a change is cheap and comparable.

Whether these matter enough to adopt a tool is the Stage 2 question. Gate evidence must come from real teams: how often B1, B5 and A2–A4 are skipped or wrong in practice, and whether HaltAI found something their own process missed.

### Other baselines (roadmap v0.4)

Since HaltAI is now aimed at a break-glass emergency stop, the comparison during market validation also covers existing tools:

| Baseline | What to compare |
|---|---|
| ServiceNow AI Control Tower containment | Preparation effort; whether agents need connectors or integration; status and audit detail; independence from the governance platform |
| AWS platform controls (AgentCore session stop, IAM, network security) done by hand | Time and steps to halt; whether restart, retry and schedules are covered; verification of effect |
| AWS Fault Injection Service | Whether it can rehearse the same levers and verify their effect |
| The team's own incident runbook | Everything in parts A and B above |

The same rule applies to every baseline: score what each one actually catches and verifies, not what it claims to cover.

### Caveats on this scoring

- It was scored by HaltAI's authors, which biases it. In Stage 2, have engineers from participating teams run the checklist on the same testbed and score it independently.
- It assumes the strong checklist above. Each team's actual runbook should be scored too; that is the realistic baseline.
- Simulation shows the method works. It is not evidence about real AWS behaviour (roadmap Phase 3).
