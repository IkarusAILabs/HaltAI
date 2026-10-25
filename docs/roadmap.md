# HaltAI — Mission and Roadmap

**Status:** v0.2 code; direction revised to a break-glass emergency stop
**Version:** 0.4 (supersedes 0.3 of 25 October 2026)
**Last updated:** 25 October 2026
**Next decision review:** 29 January 2027
**Owner:** To be assigned per workstream (section 13)

------------------------------------------------------------------------

## 1. Mission and positioning

**The question HaltAI exists to answer:** how can an authorised operator stop an AI system immediately when it goes out of control, and can that be done with a generic safety valve?

**Positioning:**

> When autonomous AI activity must stop, HaltAI gives infrastructure operators a prepared, auditable way to cut off its ability to act, without requiring agent code integration.

HaltAI is a **small, open-source, self-hosted, break-glass emergency stop for AI workloads**. It uses existing infrastructure controls: network, identity, work intake and workload shutdown. It does not compete with AI governance platforms, and it does not build new gateways, runtimes or a SaaS service.

**The long-term ambition** stays a generic safety-valve *pattern*. The implementation is scoped to boundaries HaltAI can actually reach and verify. A universal pattern is a reasonable ambition; a universal guarantee is not.

**Current product truth:** the v0.2 code is a **proof-tester** for containment. It runs drills, mostly against a simulated deployment. The `aws` backend is tested only with mocks. There is no `prepare`, `halt`, `status` or `release` command yet. This roadmap is the plan to build them.

------------------------------------------------------------------------

## 2. Decision record: what changed in v0.4 and why

Roadmap 0.3 made HaltAI the *proof-tester* first and deferred the *valve itself* to a later stage. Version 0.4 adopts the strategy update of 25 October 2026, which makes the valve the product and keeps proof-testing as its built-in verification.

| Adopted from the strategy | Why |
|---|---|
| Break-glass emergency stop as the product | It answers the mission question directly. A tester alone never stops anything. |
| No agent SDK or code integration | Adoption must not depend on every agent team. Infrastructure administrators can act on their own. |
| Prepare → halt → verify → release | Maps one-to-one onto the valve properties: pre-staged, activated in one action, fail-visible, deliberate reset. |
| Four infrastructure levers | Reuses existing cloud controls and matches how operators already think. |
| Declared environment boundary first; discovery later | The reach is defined by infrastructure, not by developer adoption. No claim of universal discovery. |
| Two profiles: *halt agents*, *emergency isolation* | Makes the blast-radius trade-off an explicit, pre-tested choice. |
| Self-hosted, open source, honest status | Independence from the normal agent stack is the whole point of break-glass. |

**Adjustments made in this roadmap** (gaps in the strategy as written):

1. **Tamper resistance is a core principle.** The strategy lists seven principles but not "out of the agent's reach". An infrastructure stop that the agent's own permissions can undo is not a stop. Claim 3 (escape-path check) becomes a mandatory part of `prepare`.
2. **Verification without agent code needs a design.** Today's effect-level evidence relies on the integration contract: the worker records probe results and the gateway backend writes markers. "No code integration" removes that. HaltAI will instead probe **as the agent's identity and from the agent's network**, using a HaltAI-owned canary task, and read infrastructure evidence. The integration contract stays as *optional deep verification* (section 8).
3. **Production halt has to be earned.** A break-glass tool must eventually work in production, but today's guards forbid production. `halt` stays limited to non-production until the Phase 4 safety gate passes (section 10).
4. **Drill mode and halt mode behave differently.** Drills roll back automatically in `finally`. A real halt must *not* release automatically. Both modes share adapters and verification but have separate lifecycles.
5. **The first boundary should be an AWS account, not a VPC alone.** A dedicated agent account (or organizational unit) gives the cleanest identity boundary: a pre-staged service control policy (SCP) applied from outside the account. It is also the hardest for the agent to reach. The in-account levers still need one HaltAI identity inside the boundary, and section 7.1 sets out its protections.
6. **"No integration" still has a precondition.** Agents must run inside a declared, segmented boundary. That is an infrastructure requirement, not a developer one, and it is stated as a prerequisite rather than hidden.
7. **Gates and dates are kept.** The strategy's lean phases are kept, with explicit gates and the 29 January 2027 decision review.

------------------------------------------------------------------------

## 3. What "stop" means

"Stop the AI" is not one operation:

- Blocking model API traffic does not cancel tool calls already in progress.
- Terminating an agent process does not stop queued jobs, replacement workers or downstream operations already accepted.
- Disabling one identity does not stop an agent that holds another credential.
- Blocking a gateway does not prevent direct access if a bypass exists.
- Broad network or identity controls can disrupt unrelated applications.

HaltAI therefore coordinates several existing controls and reports, **per lever and per effect**, what each one did. It never reports one overall "stopped".

### 3.1 Valve properties

| Property | Meaning | Where it lives |
|---|---|---|
| **Independent** | Activated outside the agent's trust domain | Separate operator identity; out-of-band boundary controls |
| **Tamper-resistant** | The agent cannot disable, bypass or outrun it | `prepare` (Claim 3), `status` re-checks |
| **Pre-staged** | Activation is a small rehearsed toggle, not live reconfiguration | `prepare` |
| **One action** | A single command for a declared scope and profile | `halt` |
| **Latching** | Stays effective across restart, redeploy, retry and autoscaling | `halt` + verification (Claim 2) |
| **Fail-visible** | "Request accepted" is never shown as "effect stopped" | `status` |
| **Bounded blast radius** | The profile's collateral impact is known in advance | Profiles, drills |
| **Deliberate reset** | Release is authorised and followed by recovery checks | `release` |
| **Proof-tested** | Rehearsed in non-production, dated, invalidated by drift | `drill` |

------------------------------------------------------------------------

## 4. Product definition

**HaltAI — Emergency Stop for AI Workloads.** An open-source, self-hosted, break-glass tool that uses infrastructure-level controls to interrupt autonomous AI activity across a declared environment, without modifying agent code.

### Principles

1. **No agent SDK or application-code integration.** HaltAI acts through infrastructure and administrative controls.
2. **Prepared before the emergency.** Permissions, scope, controls and recovery steps are configured and tested ahead of time.
3. **One emergency action.** An authorised operator invokes a prepared procedure for a declared scope.
4. **Reuse existing controls.** Use cloud, IAM, network, queue and runtime APIs; build no gateways or agent platforms.
5. **Self-hosted and open source.** No SaaS or governance-vendor dependency for the emergency command.
6. **Honest status.** "Control request accepted" is distinct from "effect observed stopped".
7. **Controlled recovery.** Containment is never removed just because the command finished.
8. **Out of the agent's reach.** *(added)* The controls, and the identity that operates them, cannot be changed by anything inside the boundary.
9. **Rehearsed before trusted.** *(added)* A prepared halt that has never been drilled is reported as unverified.

### What HaltAI is not

- An AI governance or inventory platform.
- A replacement for IAM, firewalls, cloud-native security or incident response.
- A new LLM gateway or agent runtime.
- A detector of incidents. It acts on an operator's decision.
- A guarantee that all agents can be discovered or stopped, or that completed or downstream-accepted operations can be reversed.

------------------------------------------------------------------------

## 5. Operating model

| Step | Command | What happens | Status |
|---|---|---|---|
| **Prepare** (once, and after changes) | `haltai prepare` | Read the declared boundary; create the pre-staged controls without activating them; check the operator's permissions, the agent's escape paths (Claim 3) and the recovery path; fingerprint the result. | Planned. Static checks exist as `haltai check`. |
| **Rehearse** (regularly, non-production) | `haltai drill` | Halt, verify, release and recover against the prepared controls, with failure scenarios. | Exists for one gateway-deny profile. To be rebased on the prepared controls. |
| **Halt** (emergency) | `haltai halt --profile <halt-agents\|isolate>` | Apply the prepared levers in a defined order; record every action in an audit log. | Planned. |
| **Verify** | `haltai status` | Per lever: `requested`, `accepted`, `effect_verified`, `partial`, `failed`, `unverified`. Lists residual activity. | Planned. |
| **Release** (deliberate) | `haltai release` | Requires explicit authorisation; reverses levers in order; runs recovery checks; reports anything not restored. | Planned. Today `haltai rollback` covers drills only. |

### Profiles

| Profile | Purpose | Trade-off |
|---|---|---|
| **halt-agents** | Stop supported agent workloads in the boundary across their network, identity, work-intake and runtime paths. | Aims to spare unrelated workloads; covers only what was declared. |
| **isolate** | Quarantine the whole declared boundary when stopping matters more than service. | Interrupts everything inside the boundary. The blast radius is documented and drilled in advance. |

Neither profile claims anything outside the declared boundary.

------------------------------------------------------------------------

## 6. The four levers on AWS

Each lever is pre-staged by `prepare` and activated by `halt` with a small toggle. "Verification" lists evidence that needs no agent code.

| Lever | Pre-staged form → halt action | Limitation to report | Verification without agent code |
|---|---|---|---|
| **Identity** | Prepared deny policy or SCP → attach to in-scope roles, or attach the SCP to the agent account/OU. Add a session-revocation deny (`aws:TokenIssueTime`) so credentials issued earlier stop working. | Credentials outside the boundary; service-linked roles and the management account are not affected by SCPs; a shared role disrupts its other users. | Canary task run with the agent's role is denied; IAM policy simulation; CloudTrail shows denied calls. |
| **Network** | Prepared quarantine NACL → swap subnet associations; or prepared DNS Firewall / Network Firewall rule groups → enable. | Security-group changes do **not** cut existing tracked connections, so NACLs or a firewall are needed for a hard cut. Local actions need no network. Paths outside the boundary are not covered. | Canary task in the agent subnets cannot reach model and tool endpoints; VPC Flow Logs show rejects. |
| **Workload** | Recorded desired counts and scaling settings → ECS `desiredCount=0` and stop tasks; suspend Application Auto Scaling; Lambda reserved concurrency `0`; AgentCore runtime session stop where used. | Stopping runtimes doesn't cancel downstream accepted work; other schedulers may start new compute. | ECS running count stays 0 across the hold window; Lambda throttles; no new tasks appear. |
| **Work intake** | Recorded state → disable EventBridge rules and Scheduler schedules; disable Lambda event source mappings; deny `sqs:ReceiveMessage` to consumer roles. | SQS has no pause, and messages keep arriving and stay queued; already-received work may finish. | Rule and mapping state; queue depth grows while no messages are received; no new invocations. |

The default order for `halt` is identity, then network, then intake, then workload: cut the ability to act first, then stop the actors. Each lever reports on its own. A failed lever never stops the others from being applied.

------------------------------------------------------------------------

## 7. Boundary: contain without registering each agent

**Approach A — declared agent environment (first).** Agents run in a boundary administrators control. HaltAI acts on the boundary without knowing each agent's internals.

- **Recommended first boundary:** a dedicated AWS account (or organizational unit) for agent workloads, with the identity lever as a service control policy (SCP). Nothing inside the account can detach an SCP.
- **Within a shared account:** in-scope resources are identified by tag (for example `haltai:scope=agents`) and by subnets. This is weaker, because in-account identities may be able to change tags and policies, and Claim 3 must show they can't.

### 7.1 Two HaltAI identities

Only part of a halt can be done from outside the boundary. Attaching an SCP works through AWS Organizations. Every other lever acts on resources inside the agent account: network ACL associations, ECS services, Lambda concurrency, rules and schedules, and the canary task, which ECS can only run in the account that owns the subnets and the agent role. HaltAI therefore uses two identities:

| Identity | Lives in | Does |
|---|---|---|
| **Operator role** | A security or tooling account, outside the boundary | Authenticates the human operator; attaches and detaches the halt SCP (from the management account or as a delegated administrator); assumes the execution role. |
| **Execution role** | The agent account, **inside** the boundary | Applies and releases the in-account levers; runs the canary; reads state for `status`. |

The execution role is the one HaltAI identity inside the boundary, so it is the most attractive target. `prepare` creates it with these protections and checks each of them, reporting any gap as **blocking**:

1. **Trust:** only the operator role can assume it. The trust policy names that exact role ARN; it has no wildcards and no in-account principals.
2. **Exempt from the halt SCP, and only it:** the halt SCP denies everything except calls by the execution role. Without the exception, a halt would also lock out the release. Being exempt means the execution role is never contained itself, which is why the other protections matter.
3. **A standing protection SCP**, attached at all times, not only during a halt. It denies every principal except the execution role these actions on the execution role and on HaltAI-staged resources (tagged `haltai:managed=true`):
   - modifying, deleting, passing or assuming them;
   - changing their tags;
   - changing their trust policies.
4. **No workload runs as it.** No task definition, function or instance profile uses the execution role. This is checked the same way as today's dedicated-identity check.
5. **Least privilege:** only the lever actions on in-scope resources, plus `ecs:RunTask` and `iam:PassRole` limited to in-scope agent roles for the canary. No broad IAM permissions.
6. **Audited:** every assumption and action is logged to CloudTrail. Any use outside `prepare`, `drill`, `halt`, `status` or `release` is an alert.

Claim 3 is extended to the boundary. No identity inside the boundary may be able to modify, assume or pass the execution role, change the protection SCP's targets, or retag HaltAI-staged resources.

**Shared-account variant:** a halt SCP would hit the whole account, so the identity lever uses deny policies attached to agent roles instead. The execution role is protected only by IAM policies and Claim 3, with no protection SCP. This is weaker, and reports must say so.

**Approach B — discovery of known cloud workloads (later).** Inventory supported runtimes, identities, queues and egress paths. Existing resources can't reliably be classified as "AI agents", so unknown or unsegmented workloads stay a reported coverage gap. No claim of universal discovery.

The current `deployment.yaml` describes one agent. It evolves into an **environment declaration**: account/OU, VPC and subnets, tag selectors, levers enabled per profile, unrelated services used for collateral checks, and recovery owners.

------------------------------------------------------------------------

## 8. Verification

A successful API response is never proof that activity stopped. Verification evidence, from strongest to weakest:

1. **Effect probes as the agent, without agent code.** A HaltAI-owned canary task runs with the agent's role in the agent's subnets and tries the protected effects: a model endpoint, tool endpoints, a synthetic write.
2. **Infrastructure observation.** Running-task counts, throttles, rule and mapping state, queue receive metrics, VPC Flow Logs, CloudTrail denials.
3. **Policy evaluation.** IAM policy simulation for the agent's identities (Claims 1 and 3 at `boundary_assessed`).
4. **Control-plane state.** The policy is attached and the NACL is associated. This shows *configured* only.

**Optional deep verification:** teams that want effect-level evidence from inside the workload can still implement the [integration contract](integration-contract.md). HaltAI never requires it.

### How the existing claims carry over

| Claim | Role in the new model |
|---|---|
| 1. New protected effects are denied | Verified by `status` after `halt` and in every `drill`. Generalised from "gateway writes" to each lever's effect. |
| 2. Restart and retry don't restore access | A `halt` latching check: replacement tasks, autoscaling, retries and schedules stay stopped. |
| 3. The agent can't undo or outrun containment | Mandatory in `prepare`; re-checked by `status`. Extended to the boundary: tags, NACLs, schedules, SCP reach. |

The simulated backend and scenario matrix remain the test foundation. New scenarios cover the levers: established connections surviving a security-group change, autoscaling restarting tasks, a schedule outside the boundary, an untracked credential, a shared NAT.

------------------------------------------------------------------------

## 9. Current state

| Keep | Change | Add |
|---|---|---|
| CLI, configuration, safety guards | `deployment.yaml` → environment declaration | `prepare`, `halt`, `status`, `release` |
| Simulated backend and scenario matrix | Drill rebased on prepared controls; drill rollback ≠ halt release | Four levers on AWS (section 6) |
| Claims 1–3, decision rules, evidence levels | Verification without agent code (canary task) | Audit log for halt and release |
| Gateway-deny and IAM-deny adapters (as drill profiles) | Production allowed only after the Phase 4 gate | Two profiles, with blast radius documented |
| Threat model, baseline checklist | Integration contract becomes optional | Comparison with ServiceNow AI Control Tower, AgentCore and runbooks |

Open good-first issues (#6, #8–#11, #16) stay relevant. #16 (`check --json` backend field and exit codes) is groundwork for the `status` and `halt` output.

------------------------------------------------------------------------

## 10. Roadmap and gates

```
Phase 0  Foundations: claims, simulation, Claim 3, threat model    done
Phase 1  Define the emergency boundary (sim + IaC)          ── to 22 Nov 2026
Phase 2  Build prepare/halt/status/release (sim, then AWS)  ── to 20 Dec 2026
Phase 3  Prove the hard stop in real non-production AWS     ── needs account
Market   Validation interviews and competitor comparison    ── in parallel
         Decision review                                         29 Jan 2027
Phase 4  Operational safety; then production use allowed
Phase 5  Expand only when justified
```

### Phase 0 — Foundations (done)
Claims 1–3, simulated backend with 11 scenarios, static checks with activation guards, threat model, baseline checklist.

### Phase 1 — Define the emergency boundary (to 22 November 2026)
- Environment declaration schema (section 7) with validation and limitations reporting.
- Simulated *environment* backend: account boundary, subnets, ECS service with autoscaling, Lambda with an event source, EventBridge schedule, SQS queue, an unrelated in-boundary service and an out-of-boundary service.
- Reference AWS environment as IaC (CDK, Python): a dedicated agent account layout plus a shared-account variant. `cdk synth` runs in CI.
- Execution-role design (section 7.1): trust policy, halt SCP exception, protection SCP, and the matching Claim 3 checks, designed together with the canary probe.
- Update the threat model for the boundary and the levers.

**Gate:** the boundary is fully declarable; the simulator models every lever and the new failure scenarios.

### Phase 2 — Build the break-glass action (to 20 December 2026)
- `prepare`: create pre-staged controls without activating them; operator-permission check; Claim 3 on the boundary; fingerprint.
- `halt`: the halt-agents profile first, lever order from section 6, an audit record per action, continuing past failed levers.
- `status`: lever states and residual activity; never says "stopped" without effect evidence.
- `release`: explicit authorisation, reverse order, recovery checks.
- Simulated first, then the AWS adapters (moto-tested). Production refused.

**Gate:** in simulation, every failure scenario produces the expected lever states, and nothing reports `effect_verified` without effect evidence.

### Phase 3 — Prove the hard stop (real non-production AWS)
Answer the five validation questions with measured evidence:

1. After halt, can the agent still reach its model or tool endpoints through any known path?
2. Can it act with identities or credentials in scope, including sessions issued before the halt?
3. Can queues, schedulers, replacement workers or autoscaling restart activity?
4. What work accepted before the halt still completes?
5. Can operators verify state and recover without depending on the agent?

Also: time to effective denial per lever (distribution and resolution), whether existing connections survive, collateral impact on in-boundary and out-of-boundary services, and interrupted halt and release.

**Gate:** all five questions answered with evidence; no lever falsely reports `effect_verified`; release and recovery demonstrated; the isolate profile's blast radius measured.

### Market validation (in parallel)
- 10–15 interviews with teams running agents in AWS. Collect concrete emergency procedures, not stated interest.
- Compare against ServiceNow AI Control Tower containment, AWS AgentCore controls, Microsoft agent identity, gateway controls, runbooks and AWS FIS.
- Test the differentiators as hypotheses: no developer adoption needed, framework independence, break-glass independence, simple and auditable, declared-scope isolation.

**Gate:** at least 3 teams share a concrete procedure; at least 2 agree to a supervised drill; at least 1 consequential gap remains after existing tools.

### Decision review — 29 January 2027

| Phase 3 | Market | Decision |
|---|---|---|
| Pass | Pass | Phase 4, then production use. |
| Pass | Fail | Publish the reference break-glass design and benchmark; keep the tool for non-production rehearsal; pause expansion. |
| Fail | — | Fix the levers and verification; no new scope. |

### Phase 4 — Operational safety (then production)
Preflight validation, a protected audit store, partial-failure reporting and safe retry, dual authorisation for the isolate profile, an independent recovery path, and a documented manual fallback. Drift detection invalidates `prepare`.

**Gate:** halt and release behave correctly under partial failure, API errors, propagation delay and loss of the HaltAI process. Only then may `halt` run against production, under an explicit policy.

### Phase 5 — Expand only when justified
More runtimes (EKS, AgentCore at depth), Approach B discovery, other clouds — driven by real tests and demand. No promise of multi-cloud support up front.

------------------------------------------------------------------------

## 11. Non-promises

HaltAI does not claim to:

- stop every AI system in every environment;
- discover every agent, credential, route or delegated task;
- shut down literally instantly;
- stop work already accepted downstream in every case;
- contain a compromised cloud account, host, control plane or operator identity;
- prevent all collateral impact;
- replace cloud security controls, monitoring or incident response.

------------------------------------------------------------------------

## 12. Metrics

- Time to effective denial per lever (measured, with resolution).
- Share of levers reaching `effect_verified` in drills.
- Escape paths found by `prepare`.
- Restart, retry and autoscaling resilience while halted.
- Collateral impact per profile.
- Residual activity surfaced after halt.
- Time and permissions needed to prepare an environment.
- Teams who drill regularly, and gaps found that existing tools missed.

No published response-time or coverage figures before representative testing.

### Stop or redirect if

- existing platform controls already give operators an equivalent prepared, independent stop;
- teams won't place agents in a declared boundary or grant the operator permissions;
- verification can't distinguish "accepted" from "stopped" on real AWS;
- the result is mainly a script plus a runbook.

------------------------------------------------------------------------

## 13. Workstreams and immediate backlog

Workstreams needing an owner: boundary and adapters; verification and canary; operator safety and audit; simulation and tests; market validation; documentation.

1. Environment declaration schema and validator. *(Phase 1)*
2. Simulated environment backend and lever failure scenarios. *(Phase 1)*
3. CDK reference environment (dedicated account + shared-account variant); `cdk synth` in CI. *(Phase 1)*
4. Canary-probe design: a HaltAI-owned task run as the agent role in the agent subnets, launched by the execution role (section 7.1). *(Phase 1)*
5. `prepare` with Claim 3 on the boundary. *(Phase 2)*
6. `halt` (halt-agents), `status`, `release` in simulation; then AWS adapters. *(Phase 2)*
7. Non-production account; first real halt drill. *(Phase 3)*
8. Interviews and competitor comparison. *(Market, from now)*
9. Decision review on 29 January 2027.

------------------------------------------------------------------------

## 14. Sources

Platform behaviour varies by service version and configuration.

- [UK NCSC — Managing the cyber risk of agentic AI](https://www.ncsc.gov.uk/blogs/managing-the-cyber-risk-of-agentic-ai)
- [ServiceNow — Exploring AI agent containment](https://www.servicenow.com/docs/r/zurich/intelligent-experiences/gov-sec-exploring-ai-agent-containment.html)
- [AWS — Stop an AgentCore runtime session](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-stop-session.html)
- [AWS — AgentCore runtime security best practices](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-security-best-practices.html)
- [Microsoft — Agent identity concepts](https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/agent-identity)
- [AWS Fault Injection Service](https://docs.aws.amazon.com/fis/latest/userguide/what-is.html)

------------------------------------------------------------------------

## 15. Decision principle

Keep HaltAI small. Prepare the infrastructure controls in advance, make the emergency action simple, keep it out of the agent's reach, verify what actually stopped, and be honest about what remains outside the boundary.
