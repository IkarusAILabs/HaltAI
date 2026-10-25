# HaltAI threat model

**Version:** 0.1 · **Last updated:** 25 October 2026 · **Roadmap:** Stage 0

This page says what HaltAI v1 is expected to handle, what it trusts, and what it does not claim. Claims and reports must stay inside these boundaries. Change this page before adding a claim, adapter or deployment profile that crosses one.

## 1. The emergency

An AI agent running in a registered AWS deployment is producing a harmful effect, or is about to, and an authorised operator decides to stop it. HaltAI does not detect the emergency. It is concerned with what happens **from the operator's decision onwards**: can the effect be denied, does it stay denied, can the agent undo that, what else breaks, and can the deployment be recovered.

## 2. Scenarios

| | Scenario | In scope as |
|---|---|---|
| **Primary** | Non-adversarial misbehaviour or runaway automation: the agent keeps performing an effect using the permissions it was given (wrong tool calls, loops, bad inputs, prompt injection acting through legitimate permissions). | Claims 1 and 2: tested, with measured results. |
| **Secondary** | An agent that, through goal pursuit or error, uses permissions it holds to evade or undo containment: editing policies, switching identity, starting new compute. | Claim 3: the **exposure** is assessed by policy simulation. Containment against an agent that actively exploits it is not claimed. |
| **Out of scope** | Compromise of the cloud account, host, container runtime, control plane or operator identity. Model-weight exfiltration. Containment of frontier models at the lab level. Agents and paths that are not registered in the manifest. | Not assessed. Reports must not imply otherwise. |

## 3. Trust boundaries

**Trusted** (a failure here invalidates results):

- The operator identity and the HaltAI operator role, which are separate from the agent role.
- The AWS control plane (IAM, API Gateway, ECS, SQS, DynamoDB) behaving as documented, including eventual consistency.
- The containment adapter and the policy boundary it changes.
- The observer: probe results and marker tables are written by the deployment as a side effect of real work (see the [integration contract](integration-contract.md)), and HaltAI reads them with its own identity.
- The audit store for reports and raw evidence.

**Untrusted or unknown:**

- Model output and agent decisions.
- Queued inputs and job payloads.
- Any path not declared in the manifest.
- Anything the agent says about its own state. HaltAI never relies on it.

## 4. Effects

| Effect | v1 status |
|---|---|
| New writes through the registered tool gateway | **Must stop.** Claim 1. |
| Writes after worker restart, fresh task, redeploy or job retry | **Must stop.** Claim 2. |
| Agent lifting the control, escalating itself, switching identity or starting compute under another role | **Must be impossible by policy.** Claim 3 (simulation). |
| Jobs queued before containment | **May remain.** Reported as residual exposure with counts. |
| Writes accepted before containment and completed after it | **May complete.** Reported as residual exposure with job IDs. |
| Effects already accepted by downstream systems | **Not reversed.** Out of scope; declared in the manifest. |
| Model/API calls that do not reach the protected effect | **Not stopped in v1.** No claim. |
| Unrelated applications | **Must keep working.** Observed by the collateral probe. |

## 5. Timing targets

Targets are test thresholds, not promises. Results always report the measured interval and its polling resolution.

| Measure | v1 target | Source |
|---|---|---|
| Decision-to-denial for new writes | Within `deny_timeout_s` (default 120 s) | Claim 1, repeated 5 times |
| Denial held after first observation | Whole `observation_window_s` (default 60 s) | Claim 1 |
| Recovery after rollback | Within `deny_timeout_s` | Recovery probe |

## 6. Authority

- **Activation in drills:** non-production only, `authorisation.disruptive_tests_allowed: true`, matching account, `--confirm`.
- **Recovery:** every activation is paired with a rollback and a recovery probe. If recovery cannot be verified, the drill stops and says so. `haltai rollback --confirm` is the documented single command after an interrupted drill.
- **Real incidents:** HaltAI has no activation command outside drills yet (roadmap Stage 3). Until then, who may activate containment in an incident, and who authorises recovery, is set by the operator's own incident procedure.

## 7. Assumptions every report inherits

1. The agent role in the manifest is the only identity the agent's code runs as.
2. The protected effect has one declared enforcement point (the tool gateway), and declared bypasses are checked by policy simulation.
3. Probe and marker records are written by the deployment, not by the agent's reasoning.
4. Policy simulation reflects effective permissions for the actions and resources simulated.
5. Results apply to the fingerprinted configuration at the time of the test.

When an assumption is known not to hold, HaltAI reports a limitation rather than a pass.
