# HaltAI threat model

**Version:** 0.2 · **Last updated:** 25 October 2026 · **Roadmap:** v0.4

This page says what HaltAI is expected to handle, what it trusts, and what it does not claim. It covers both today's drills and the planned break-glass commands (`prepare`, `halt`, `status`, `release`). Claims, adapters and reports must stay inside these boundaries. Change this page before adding anything that crosses one.

## 1. The emergency

Autonomous AI activity inside a **declared environment** is causing harm, or is about to, and an authorised operator decides it must stop. HaltAI does not detect the emergency. It covers what happens **from the operator's decision onwards**:

- cut off the ability to act;
- keep it cut off;
- make sure nothing inside the boundary can undo that;
- show what still runs;
- recover deliberately.

## 2. Scenarios

| | Scenario | In scope as |
|---|---|---|
| **Primary** | Non-adversarial misbehaviour or runaway automation: agents keep acting through the permissions, network paths and work intake they were given (wrong tool calls, loops, bad inputs, prompt injection acting through legitimate access). | `halt` and its verification (Claims 1 and 2), rehearsed by `drill`. |
| **Secondary** | An agent that, through goal pursuit or error, uses access it holds to evade or undo containment: editing policies or tags, switching identity, starting compute, rescheduling work. | `prepare` must find this **exposure** (Claim 3) and report it as blocking. Containment against an agent that actively exploits an unreported path is not claimed. |
| **Out of scope** | Compromise of the cloud account that hosts HaltAI's operator identity, of the control plane, or of the operator. Host or hypervisor compromise. Model-weight exfiltration. Lab-level containment of frontier models. Anything outside the declared boundary. | Not assessed. Reports must not imply otherwise. |

## 3. The boundary

HaltAI acts on an infrastructure boundary, not on each agent's code.

- **Preferred:** a dedicated AWS account or organizational unit (OU) for agent workloads. HaltAI's operator identity sits outside it, and the identity lever is a service control policy (SCP) that nothing inside the account can detach.
- **Accepted, weaker:** tagged resources and subnets inside a shared account. In-account identities may be able to change tags, policies or routes, so `prepare` must show they cannot.

**Precondition:** agents run inside the declared boundary. Agents outside it, and unsegmented workloads, are a reported coverage gap, never silently included.

## 4. Trust boundaries

**Trusted** (a failure here invalidates results):

- The HaltAI operator identity and the account it lives in. It is separate from every identity inside the boundary.
- The cloud control plane (IAM, Organizations, VPC, ECS, Lambda, EventBridge, SQS, API Gateway) behaving as documented, including eventual consistency.
- The pre-staged controls created by `prepare`, and the integrity of HaltAI's own configuration.
- HaltAI's canary probe, which runs with an agent identity in agent subnets using HaltAI's own image. It is trusted to report what that identity and network path can still do.
- The audit store for halt, release, drill reports and raw evidence.

**Untrusted or unknown:**

- Model output, agent decisions and agent code.
- Queued inputs and job payloads.
- Any resource, path or credential not covered by the declaration.
- Anything an agent says about its own state. HaltAI never relies on it.
- Today's drills only: probe results and markers written by the workload under the optional [integration contract](integration-contract.md). Evidence written by the workload is weaker than evidence HaltAI observes itself.

## 5. Effects

| Effect | Status |
|---|---|
| New actions through in-scope identities (including sessions issued before the halt) | **Must stop.** Identity lever; Claim 1. |
| Reaching model and tool endpoints from agent subnets | **Must stop.** Network lever; Claim 1. *(planned)* |
| Activity coming back through restart, redeploy, autoscaling, retry or schedules | **Must stay stopped.** Workload and intake levers; Claim 2. |
| An in-boundary identity lifting a control, escalating itself, switching identity or starting compute under another role | **Must be impossible.** Checked by `prepare`; Claim 3. |
| Connections already open when a security-group rule changes | **May persist.** Reported. A hard cut needs a network ACL or a firewall. |
| Messages queued before or during the halt | **May remain.** Reported as residual, not cancelled. |
| Work accepted before the halt and completed after it | **May complete.** Reported as residual. |
| Effects already accepted by downstream systems | **Not reversed.** Out of scope; declared. |
| Unrelated services | **halt-agents:** must keep working, observed. **isolate:** may be interrupted inside the boundary; the blast radius is documented and drilled. |

## 6. Timing targets

Targets are thresholds for tests, not promises. Results always report the measured interval and its polling resolution, per lever.

| Measure | Target | Source |
|---|---|---|
| Decision to denial, per lever | Within `deny_timeout_s` (default 120 s) | Drill; `status` after halt |
| Denial held | Whole `observation_window_s` (default 60 s), and across restart and retry | Claims 1 and 2 |
| Recovery after release | Within `deny_timeout_s` | Recovery checks |

## 7. Authority

| Action | Who and when |
|---|---|
| `drill` | Non-production only, `disruptive_tests_allowed: true`, matching account, `--confirm`. Rolls back automatically. |
| `halt` (planned) | Any authorised operator. Non-production only until the roadmap's Phase 4 gate; after that, production under an explicit policy. The `isolate` profile requires dual authorisation. |
| `release` (planned) | Explicit authorisation, never automatic. Followed by recovery checks; anything not restored is reported. |
| Incidents today | HaltAI cannot halt yet. Activation and recovery follow the operator's own incident procedure. |

## 8. Prerequisites

- A declared boundary with a named owner, environment and account.
- A HaltAI operator identity outside the boundary, with only the permissions `prepare`, `halt` and `release` need, protected by strong authentication.
- Dedicated identities for agent workloads, not shared with unrelated services. A shared identity is a blocking limitation for halt-agents.
- No permission inside the boundary to change containment controls, tags used for scoping, or the operator identity (Claim 3).
- A documented manual fallback and an out-of-band recovery path that does not run inside the boundary.
- Logs that HaltAI can read to verify effects (VPC Flow Logs, CloudTrail).

## 9. Assumptions every report inherits

1. The declaration lists every identity, subnet, runtime and intake path the agents use.
2. The pre-staged controls are unchanged since `prepare`, or drift is detected.
3. Policy simulation reflects effective permissions for the actions and resources simulated.
4. The canary probe uses the same identity and network path as the agents.
5. Results apply to the fingerprinted configuration at the time of the test.

When an assumption is known not to hold, HaltAI reports a limitation, never a pass.
