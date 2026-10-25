# HaltAI — Mission and Roadmap

**Status:** v0.2, validation stage
**Version:** 0.3 (supersedes 0.1 of 20 October 2026)
**Last updated:** 25 October 2026
**Next decision review:** 29 January 2027 (end of Stages 1 and 2)
**Owner:** To be assigned per workstream (section 11)

------------------------------------------------------------------------

## 1. Mission

**Question HaltAI exists to answer:** how can an authorised operator stop an AI system immediately when it goes out of control, and can that be done with a generic safety valve?

**Long-term mission:** a dependable, independently controlled emergency containment layer for AI-enabled workloads, together with a way to prove that it works before it is needed.

**Short answer from the work so far:** there is no single switch that stops "the AI". There *is* a generic **valve pattern**: a pre-staged, independent control at an effect boundary the AI cannot reach, which stays shut across restarts, reports honestly whether it shut, and reopens only by deliberate reset. What can be generic is the pattern, the activation contract, the evidence format and the adapter architecture. The enforcement points are always specific to an environment.

**Current product truth:** HaltAI v0.2 is a **valve proof-tester**, not yet a valve. It drills whether a registered deployment *can* be contained and reports claim-level evidence. The drill engine and evidence model run against simulation; the `aws` backend is implemented but tested only with mocks. HaltAI has not yet stopped anything in a real deployment, and it has no command for activating containment outside a drill.

The mission stays ambitious. Public claims stay bounded by evidence.

### 1.1 Why "proof-tester first" is the right order

Industrial safety reached the same conclusion long ago. Machinery emergency stops (ISO 13850) and safety instrumented functions in process plants (IEC 61508/61511) are not trusted because a valve was installed. They are trusted because the stop function is independent of the control logic it overrides, latches until deliberately reset, and is **proof-tested** at intervals with recorded results. A valve that has never been proof-tested is an assumption.

HaltAI's drills are proof tests for an AI stop function. Building the tester first produces the evidence that tells us what a generic valve must look like. Building the valve first would encode guesses.

------------------------------------------------------------------------

## 2. What "stop" means: effect-specific containment

"Stop the AI" is not one operation. It is a set of effects stopped at different boundaries:

1. Stop new model/API requests.
2. Deny new tool operations or writes.
3. Stop accepting new work, and stop queued work from restarting execution.
4. Terminate or isolate the agent runtime.
5. Prevent the agent from reacquiring credentials, using alternate identities or creating new compute.
6. Cancel or contain delegated and asynchronous operations.
7. Keep unrelated workloads running and keep the recovery path available.

No single lever covers all seven. A model-call block does not cancel an accepted tool request. Terminating a worker does not cancel an accepted downstream job. A gateway deny does not help if the agent can reach the backing resource directly. HaltAI therefore defines "stop" as a set of **effect-specific containment claims**, never as a claim that an agent has been "switched off".

### 2.1 Properties of a valve (what every adapter and claim is measured against)

| Property | Meaning | Industrial analogue | Tested today? |
|---|---|---|---|
| **Effect-scoped** | Names the effect it stops and the paths it covers | Stop category per hazard | Yes (manifest, Claim 1) |
| **Independent** | Activated and enforced outside the agent's trust domain | Hard-wired e-stop, not PLC logic | Partly (separate operator role) |
| **Tamper-resistant** | The agent cannot disable, bypass or outrun it | Guard interlock cannot be defeated by the machine | **No** (proposed Claim 3) |
| **Latching** | Stays effective across restart, redeploy and retry | E-stop stays engaged until reset | Yes (Claim 2) |
| **Pre-staged** | Activation is a small, rehearsed toggle, not a live reconfiguration | Valve fitted and tested, not installed during the fire | **No** (see 4.3) |
| **Fail-visible** | Reports partial or failed activation; never "success" because an API call returned 200 | Valve position feedback | Yes in drills; not for real activation |
| **Bounded blast radius** | Leaves unrelated workloads running | Zone isolation | Yes (collateral probe) |
| **Deliberate reset** | Reopening is authorised and followed by a recovery probe; rollback ≠ recovery | Reset after cause cleared | Yes in drills |
| **Proof-tested** | Evidence is dated, bound to a configuration and invalidated by drift | Proof-test interval and records | Yes (fingerprint, reports) |

------------------------------------------------------------------------

## 3. Architecture: three planes

Keep these modular. The verifier must never trust the containment plane's own success response.

### A. Containment plane — performs the emergency action

An operator, or an authorised upstream system, requests a named containment policy. Adapters invoke existing enforcement mechanisms: gateway deny rules, narrowly scoped IAM denies, work-intake controls, runtime isolation or termination.

- Out-of-band from the agent; separate operator identity with strong authentication and narrow privileges.
- Explicit scope, preconditions, idempotent activation, timeout and recovery path.
- Prefer effect-denial controls that persist across restart and retry over in-process flags.
- Order actions by objective and blast radius; never block shared infrastructure by default.
- Fail visibly when an action cannot be applied or independently verified.

**Today:** two adapters (`gateway_resource_policy_deny`, `iam_inline_deny`) used only inside drills, plus `haltai rollback`.

### B. Verification plane — establishes what happened

Observes the protected effect independently of the control command: request time, first observed denial, time-to-denial distribution, restart/retry results, collateral observations, recovery and residual in-flight or delegated work. Configuration evidence, enforcement tests, runtime observation and operator declarations stay separate.

**Today:** probes run as the agent (worker and fresh task), positive control before activation, marker scanning, collateral probe. This is the most mature plane.

### C. Readiness / evidence plane — can this deployment be contained?

Binds manifest, live configuration snapshot, identity, intake path, enforcement point and tested effects to a versioned fingerprint. Evidence is scoped to declared paths, dated, reproducible and invalidated by drift.

**Today:** manifest, snapshot, fingerprint, static checks (dedicated identity, gateway bypass, containment target reachable), claim-level reports.

------------------------------------------------------------------------

## 4. Current state versus mission

### 4.1 Built and useful

- CLI with explicit deployment and run configuration (`init`, `validate`, `check`, `snapshot`, `drill`, `matrix`, `rollback`).
- Simulated backend with nine failure scenarios, run as a matrix in CI.
- Claim 1 (new protected writes denied) and Claim 2 (restart/retry do not restore access) with decision rules covered one-to-one by tests.
- Four result states and four evidence levels; residual exposure reported separately.
- `aws` backend on boto3, moto-tested.
- Safety guards: `--confirm`, non-production only, account match, rollback in `finally`, recovery probe, drill stops if recovery fails.

### 4.2 Not yet demonstrated

- A real non-production AWS drill, end to end.
- Measured activation-to-denial behaviour in a real account.
- Value beyond a competent engineer with a runbook (no baseline checklist yet).
- Demand from real teams (Phase 0 discovery from roadmap 0.1 has not run; implementation got ahead of it).

### 4.3 Gaps found in this review

| # | Gap | Why it matters for the mission | Resolution |
|---|---|---|---|
| G1 | **No tamper/escape check.** Nothing tests whether the agent's own permissions let it remove the deny (`iam:DeleteRolePolicy` on its role, `apigateway:PATCH` on the API), assume another role, pass a role to new compute, register task definitions or create credentials. | "Out of control" is exactly when an agent may route around a control. A valve the agent can open is not a valve. | Add **Claim 3** (section 6) as a static, policy-simulated check first. Add simulated scenarios `agent_can_lift_deny` and `agent_can_spawn_compute`. No AWS account needed. |
| G2 | **Activation is a live reconfiguration, not a toggle.** The gateway adapter rewrites the REST API resource policy and calls `create_deployment` on the whole stage. That deployment also publishes *any other undeployed change* on the API, and it touches every route on the stage, including the collateral route. | Slow, error-prone and wide blast radius at the worst moment. `containment_prestaged` only checks that the API or role exists. | In Stage 1, measure a third, genuinely pre-staged mechanism (for example a deny already attached and conditioned on a role tag or flag, or a gateway authorizer reading a flag) against the two existing ones. Make the pre-staging check verify the actual staged statement. Warn when the stage has undeployed changes. |
| G3 | **No way to use HaltAI as a valve.** Only drills activate containment. | The mission question cannot be answered by a tool that can only rehearse. | Stage 3 adds `haltai contain` / `haltai release` with lifecycle states, non-production first. |
| G4 | **Already-issued sessions are not modelled.** | Credential reacquisition (effect 5) depends on whether live sessions are denied. | Threat model states the assumption; Stage 4 adds an identity adapter that denies sessions issued before activation (`aws:TokenIssueTime`) and a probe that uses a session obtained before containment. |
| G5 | **Detection is out of scope but the hand-off is undefined.** | A valve no one can pull in time is useless. | HaltAI does not detect incidents. It measures *decision-to-denial* time, and Stage 3 exposes a stable activation interface that external detectors and incident tooling can call under customer policy. |

### 4.4 Public claim for now

> For explicitly registered deployments and declared paths, HaltAI tests selected containment claims and reports evidence, measured outcomes and residual exposure.

Do not claim "instant shutdown", "all agents", "universal kill switch", "complete bypass prevention" or "guaranteed containment of a compromised runtime".

------------------------------------------------------------------------

## 5. Roadmap and gates

Timeboxes are targets for planning, not promises. If a gate fails, fix the cause or stop; do not widen scope to compensate.

```
Stage 0  Threat model + baseline + Claim 3 (sim)     ── 2 weeks ──┐
Stage 1  Real AWS proof                               ── 6 weeks ──┼─► Decision review 29 Jan 2027
Stage 2  Differentiated value (parallel with 1)       ── 8 weeks ──┘
Stage 3  Valve contract: contain/release + lifecycle  (after gate)
Stage 4  Evidence-backed adapters, by effect
Stage 5  Generalise the control model
Stage 6  Research and external validation (continuous; publish at each gate)
```

### Stage 0 — Threat model, baseline and tamper check (now → 8 November 2026)

No AWS account needed. Unblocks everything else.

1. `docs/threat-model.md` (one page):
   - **Primary scenario:** non-adversarial misbehaviour or runaway automation using the agent's registered permissions.
   - **Secondary scenario:** an agent that, through goal pursuit or error, uses permissions it holds to evade or undo containment (self-preservation, spawning work elsewhere). In scope for *detection of the exposure* (Claim 3), not for guaranteed containment.
   - **Out of scope:** compromised cloud account, host, control plane or operator identity; model-weight exfiltration; containment of frontier models at the lab level.
   - Trusted components: operator identity, cloud control plane, containment adapter, policy boundary, observer, audit store.
   - Untrusted: model output, agent decisions, queued inputs, undeclared paths.
   - Effects to stop, effects allowed to continue, maximum acceptable measured delay per effect (a test target, not a promise), who may activate and who authorises recovery.
2. `docs/baseline-checklist.md`, then score every simulated scenario with both the checklist and HaltAI.
3. Claim 3 static check and two simulated scenarios (G1).
4. Decide the IaC tool. Recommendation: **AWS CDK (Python)**, one language with the rest of the code. Revisit only if Stage 2 interviews show target teams standardise on Terraform.
5. Write the testbed IaC and the stub worker; `cdk synth` in CI. Deployment waits for the account.

**Gate:** threat model and claim list agreed; baseline checklist scored against the simulated matrix; Claim 3 in the matrix.

### Stage 1 — Real AWS proof (highest priority; 6 weeks once a non-production account exists)

Keep the narrow profile: one ECS worker and task role, one SQS intake queue, one IAM-authenticated API Gateway write route, one synthetic marker write, one unrelated application, one separate HaltAI operator role.

1. Deploy the testbed; implement the integration contract in worker and gateway backend.
2. First authorised drill with `repetitions: 1`, while watching; prove rollback, recovery and `haltai rollback` after a killed CLI.
3. Run every failure scenario that is safe to run for real; keep reports and redacted raw evidence.
4. Measure time-to-denial distributions for **three** mechanisms: gateway resource-policy deny, IAM inline deny, and one pre-staged toggle (G2). Report polling resolution.
5. Test restart, fresh task, redeploy, retry, pre-queued work, downstream-accepted work, and Claim 3 escape paths with real policy simulation.
6. Run the baseline checklist against the same conditions.

**Gate:** real execution is repeatable and safe; reports match observed markers; no failure scenario falsely passes; collateral effects are observed; rollback and recovery are demonstrated; one mechanism is recommended with measured evidence.

### Stage 2 — Prove differentiated value (in parallel with Stage 1)

- Interview 10–15 teams running agentic workloads. Use the simulated matrix and the Stage 1 testbed as the demo.
- Collect concrete deployments and current shutdown procedures, not stated interest.
- Compare against native AWS controls, gateway tests, runbooks and fault-injection practice.

**Gate (kept from roadmap 0.1):** at least 3 teams provide a concrete deployment and shutdown procedure; at least 2 agree to a supervised non-production drill; at least 1 consequential gap remains after existing practice; participants accept the binding and permission requirements.

**Decision on 29 January 2027:**

| Stage 1 | Stage 2 | Decision |
|---|---|---|
| Pass | Pass | Productise: Stage 3, then 4. |
| Pass | Fail | Research track: publish the methodology, benchmark and reference design (Stage 6); build only the Stage 3 contract spec; pause adapters. |
| Fail | — | Fix the testbed and semantics; no new scope. |

### Stage 3 — The valve contract

Starts after the Stage 1 gate. Specification can start regardless of Stage 2; implementation beyond non-production needs both gates.

- Versioned containment-policy schema: target deployment, effects, adapters, authorisation, ordering, timeout, verification probes, residual-risk notes, rollback and recovery.
- `haltai contain <policy>` and `haltai release <policy>`: the first time HaltAI acts as a valve. Non-production first, same guards as drills.
- Lifecycle states: `requested` → `activating` → `active_unverified` → `effect_denied` | `partial` | `failed` → `recovery_pending` → `recovered`.
- Verification runs after activation, not only in drills: a contain command finishes in `effect_denied` only when an agent-identity probe is denied.
- Idempotent, auditable activation; safe retry after partial failure; alert if the effect is still possible.
- Test operator or adapter failure, control-plane API errors, partial activation, propagation delay and loss of the HaltAI process mid-activation.
- A stable activation interface (CLI exit codes and JSON output first; an API later) so external detectors and incident tooling can call it. Automatic triggering stays the customer's policy decision, with approval proportional to blast radius.

**Gate:** every activation outcome is reported honestly, including partial containment; nothing reports success because an API call succeeded.

### Stage 4 — Evidence-backed adapters, by effect

Build by effect and real users' architecture, not by provider checklist. Likely order:

1. Tool/API gateway deny for a specified write effect (exists; harden).
2. Work-intake pause with restart/retry-safe enforcement.
3. Identity: deny sessions issued before activation; block role assumption and credential creation (G4).
4. Runtime isolation or termination, as a complement, never as the definition of success.
5. Model-provider request blocking, only where it prevents a defined harmful effect, never implying accepted work is cancelled.
6. Delegated and asynchronous workflow controls, only with explicit downstream cancellation semantics.

Each adapter ships with a test contract, failure model, blast-radius description, least-privilege permissions, rollback and recovery, measurable verification and documented unsupported cases.

### Stage 5 — Generalise the control model

Only after at least two real deployment profiles work.

- One policy and evidence schema with provider- and runtime-specific adapters.
- Profiles for further runtimes and clouds (for example EKS, Lambda, another cloud), chosen by user evidence.
- Drift-triggered reassessment and proof-test intervals.
- Export to incident-response and governance tooling.
- SafeAI source-derived capability evidence as an input to readiness, kept distinct from live enforcement evidence.
- OpenPulse dependency findings may inform exposure assessment; they must not trigger shutdown automatically.

### Stage 6 — Research and external validation (continuous)

Publish results at each gate, separating:

- declared versus discovered coverage;
- configuration evidence versus active test evidence;
- activation latency versus effect-denial latency;
- fresh actions versus in-flight and delegated residual actions;
- local failure detection versus resistance to an evasive agent;
- containment effectiveness versus collateral impact and recovery.

A reproducible proof-testing methodology and benchmark for AI stop functions is a valid outcome even if a standalone product is not justified.

------------------------------------------------------------------------

## 6. Claims

Every claim reports one result per effect (`verified`, `failed`, `partial`, `unknown`). Never compress claims into one overall pass. Decision rules live in [`claims.md`](claims.md).

| Claim | Statement | Status |
|---|---|---|
| **1. New protected writes are denied** | After activation, new writes through the registered gateway are rejected within a measured interval. | Built; simulated |
| **2. Restart and retry do not restore access** | While containment is active, worker restart, fresh task, redeploy and job retry stay denied. | Built; simulated |
| **3. The agent cannot undo or outrun containment** *(proposed)* | The agent identity cannot remove or modify the containment control, assume an uncontained identity, create credentials, or start compute that performs the effect outside the control. | Stage 0 (static, `boundary_assessed`); Stage 1 with real policy simulation |

Claim 3 starts as a static check because it is cheap and needs no disruptive test. Any escape path found caps Claims 1 and 2 at `partial`, the same way a gateway bypass does today.

Each claim answers:

- Which exact deployment and configuration snapshot?
- Which effect must stop, and which paths are in scope or explicitly unsupported?
- What is the trusted control boundary?
- When was containment requested, and when was denial first independently observed?
- What continued after activation (queued, in-flight, delegated, accepted downstream)?
- Did restart, retry, alternate paths or the agent's own permissions restore the effect?
- What happened to unrelated workloads?
- Were rollback and recovery observed?
- Which changes invalidate the evidence?

### Evidence levels

1. **Configured:** the intended control appears in inspected configuration.
2. **Enforcement tested:** an authorised negative test, run as the agent, was rejected.
3. **Boundary assessed:** policy simulation shows the agent cannot perform the effect, or undo the control, without passing the enforcement point.
4. **Observed after activation:** monitored paths showed no relevant activity during a defined window.

These are not interchangeable. A successful gateway probe does not show the agent cannot bypass the gateway.

------------------------------------------------------------------------

## 7. Prerequisites for a meaningful assessment

### Minimum deployment bindings

One deployment version; one runtime (initially a dedicated ECS service); one authenticated execution identity; one agent-specific intake path; one protected effect (initially a synthetic write); one enforcement point; one containment policy; one unrelated application for collateral probes; declared assumptions, unsupported paths and downstream side effects.

### Operational prerequisites

- Non-production AWS environment with authorisation for controlled tests; allowlisted account and resource IDs; automatic drill timeout.
- Dedicated agent identity, not shared with unrelated workloads.
- Test-safe synthetic write with observable success and failure.
- Separate, strongly authenticated, least-privilege operator role.
- Pre-tested rollback, recovery probe and documented manual emergency procedure.
- Logs sufficient to observe the tested paths; protected audit records of requester, approver, scope, actions, timestamps, verification and recovery.
- A declared policy for queued, in-flight and delegated work and for irreversible effects.
- An owner who approves scope, interprets results and remediates findings.

Missing prerequisites are reported as limitations or blockers, never as passes.

------------------------------------------------------------------------

## 8. Recommendations for making agents containable

These are engineering practices. HaltAI must distinguish recommendations from what it has verified.

**Identity and permissions.** One role per agent or tightly scoped agent class. No shared roles, provider keys or tool credentials with ordinary services. Least privilege on high-impact actions. **No permission for the agent to modify its own policies, the gateway, or containment resources, or to pass roles or create compute.** Short-lived credentials, with already-issued sessions considered separately. Pre-stage and test the exact deny mechanism.

**Runtime and work intake.** Dedicated ECS services or equivalent. Agent-specific queues, schedulers and consumers. Restart and autoscaling must not restore access while containment is active. Defined cancellation semantics for queued, executing and delegated jobs. Idempotent retries that recheck containment before acting.

**Model and tool access.** Mandatory gateway for protected effects where feasible, with direct access blocked by identity and network controls and the bypass tested, not assumed absent. Separate credentials per agent or capability class. Explicit policy checks, and human approval where appropriate, for high-impact tools. Blocking a model endpoint does not cancel tool calls already made.

**Network and application boundaries.** Dedicated, enforceable egress where provider access must be restricted. No shared NAT, broad security-group changes or provider-domain blocks as the default selective mechanism. Ingress blocking alone does not stop outbound execution. Document routes outside the tested boundary.

**Governance.** Agent inventory with owner, purpose, runtime, identity, providers, tools and intake paths. Versioned configuration. Named containment policies with scope, authorisation, activation, verification and recovery. Two-person authorisation for high-impact actions where appropriate. Non-production drills first, repeated after meaningful changes. An out-of-band recovery path independent of the contained workloads.

------------------------------------------------------------------------

## 9. Non-promises

HaltAI does not guarantee:

- literally instantaneous response or a universal latency bound;
- stopping every AI system or unregistered execution path;
- discovery of every agent, credential, region, proxy or delegated activity;
- containment after the cloud account, host, control plane or operator identity is compromised;
- cancellation or reversal of effects already accepted downstream;
- zero collateral impact in all architectures;
- permanent validity after configuration changes;
- detection of incidents, or replacement of IAM, cloud security monitoring, runbooks or incident response;
- a general-purpose LLM gateway or a single "safe" score.

The goal is a generic *architecture* and *proof-testing method* for emergency containment, not one switch that bypasses a system's real trust and execution boundaries.

------------------------------------------------------------------------

## 10. Metrics

Measure outcomes, not integration counts.

- Material failures found that the baseline checklist missed, and their severity.
- Share of declared claims with enough evidence to report a result.
- Decision-to-denial time per mechanism (distribution and resolution).
- Restart/retry resilience while containment is active.
- Escape paths (Claim 3) found per deployment.
- Collateral impact on the unrelated application.
- Unsupported or unknown paths explicitly surfaced.
- Effort to bind a deployment and run an assessment.
- Repeat-test demand after meaningful configuration changes.

No published response-time or coverage figures before representative testing.

### Stop or redirect if

- the result is mainly a checklist plus shutdown scripts;
- existing gateway tests and runbooks establish the same claims;
- teams will not provide the required bindings or test permissions;
- the value depends on bespoke architecture consulting;
- the evidence never changes an operational or deployment decision.

------------------------------------------------------------------------

## 11. Workstreams and immediate backlog

Workstreams needing an owner: customer discovery; threat model and claims; AWS testbed and baseline; drill engine and verifier; valve contract (Stage 3); research write-up.

Immediate backlog, in order:

1. `docs/threat-model.md`. *(Stage 0)*
2. `docs/baseline-checklist.md`; score the simulated matrix with it. *(Stage 0)*
3. Claim 3 static check, decision rules and scenarios `agent_can_lift_deny` and `agent_can_spawn_compute`. *(Stage 0)*
4. Fix `containment_prestaged` to verify a real staged control; warn on undeployed API changes before a gateway activation. *(Stage 0)*
5. CDK testbed and stub worker; `cdk synth` in CI. *(Stage 0)*
6. Obtain the non-production account (`eu-west-2` assumed); first one-repetition drill with rollback and recovery. *(Stage 1)*
7. Full real failure matrix; three-mechanism latency comparison; baseline comparison. *(Stage 1)*
8. Interviews and supervised drills; record against the Stage 2 gate. *(Stage 2, from now)*
9. Decision review on 29 January 2027.
10. If the gates pass: containment-policy schema, `contain`/`release`, lifecycle states. *(Stage 3)*

------------------------------------------------------------------------

## 12. Decision principle

Do not choose between "generic safety valve" and "narrow assurance tool" too early. The narrow, real-world proof test is the experiment that decides whether the broader mission is achievable and valuable.

HaltAI's first milestone is not to stop every AI system. It is to show, in a real controlled deployment, that an independent emergency action can deny one consequential effect, keep it denied across restart and retry, cannot be undone by the agent it contains, exposes its own limits, and recovers safely. Then generalise the contract, not the promise.
