# HaltAI — Containability Assurance for AI Agents

## Product Roadmap, Prerequisites, Limitations and Operating Guidance

**Status:** Validation-stage proposal\
**Scope:** Explicitly registered AWS agent deployments\
**Owner:** To be assigned\
**Decision posture:** Validate before committing to a maintained
product\
**Version:** 0.1\
**Last updated:** 20 October 2026

------------------------------------------------------------------------

## 1. Executive summary

HaltAI (formerly "SafeAI Containability Assurance") is a proposed SafeAI-ecosystem capability for
assessing and verifying whether a registered AI-agent deployment can be
selectively prevented from performing specified effects without
unnecessarily disrupting unrelated applications.

The proposed differentiator is **deployment-bound, claim-level
evidence** connecting an agent's declared capabilities, runtime
identity, protected effect paths, tested enforcement and known
limitations.

The initial effort should not be a general-purpose emergency
orchestration platform. It should be a narrowly scoped assessment and
verification workflow, using existing AWS and gateway controls as
enforcement mechanisms.

### Central hypothesis

For explicitly registered AWS agent deployments, we can test a small set
of containment claims, identify supported classes of isolation failure,
and produce deployment-bound evidence that improves on existing
checklists and shutdown drills.

This hypothesis must be tested against customer evidence and a credible
baseline. It is not yet validated.

------------------------------------------------------------------------

## 2. Product goals

1.  Assess whether a specified agent effect can be selectively
    contained.
2.  Identify supported classes of shared identity, execution-boundary
    and configured bypass exposure.
3.  Test whether selected controls reject prohibited actions.
4.  State what the tests do and do not establish.
5.  Bind results to the deployment and relevant configuration snapshot.
6.  Provide practical remediation guidance for making agents more
    containable.
7.  Enable repeatable, authorised verification after meaningful changes.

## 3. Non-goals: what we are not promising

The product must not imply any of the following:

-   **Universal discovery:** It will not discover every AI agent, hidden
    credential, undocumented path or delegated task in an AWS estate.
-   **Guaranteed global shutdown:** It will not guarantee that every
    possible autonomous action stops instantly.
-   **Containment of arbitrary compromised runtimes:** The initial scope
    does not claim that controls remain trustworthy after a runtime or
    its host is compromised.
-   **Reversal of completed actions:** It cannot undo committed
    transactions, sent emails, completed payments, external API side
    effects or already accepted downstream jobs unless the downstream
    system provides and verifies suitable cancellation or compensation.
-   **Complete bypass prevention:** Passing supported checks does not
    prove that no unknown or untested path exists.
-   **A permanent assurance status:** A successful drill applies to the
    tested deployment and configuration at a point in time. Material
    changes may invalidate it.
-   **A replacement for AWS security or incident response:** It will not
    replace IAM, cloud logging, GuardDuty, Security Hub,
    incident-response procedures, gateway policy engines or existing
    chaos/operational testing platforms.
-   **A generic LLM gateway:** The project is not intended to compete by
    implementing another general-purpose model proxy.
-   **A complete cloud compromise-remediation product:** Investigation,
    persistence removal, account-wide remediation and recovery from a
    compromised AWS account remain outside the initial scope.
-   **Automatic global shutdown from dependency alerts:** OpenPulse
    findings may inform risk assessment, but should not trigger
    disruptive containment automatically in the initial scope.
-   **A single universal "safe" score:** Results should be expressed as
    specific claims with evidence, assumptions, failures and unknowns.

## 4. Prerequisites for a meaningful assessment

The tool should require explicit bindings for the deployment being
assessed. The initial prototype should not attempt to discover arbitrary
agents throughout an entire cloud estate.

### Minimum deployment bindings

-   One deployment artifact or version.
-   One runtime deployment (initially a dedicated ECS worker).
-   One authenticated execution identity.
-   One agent-specific work-intake path.
-   One protected tool capability and its effect (initially a synthetic
    write).
-   One enforcement point (initially an authenticated tool gateway).
-   One containment policy and activation procedure.
-   One unrelated application or service path for collateral-impact
    probes.
-   A declared set of assumptions, unsupported paths and downstream side
    effects.

### Operational prerequisites

-   A non-production AWS environment with permission to run controlled
    tests.
-   A dedicated agent identity; do not share the identity with unrelated
    applications.
-   A test-safe synthetic write operation with observable
    success/failure.
-   A documented baseline runbook and configuration-review process.
-   Explicit authorisation and a rollback/recovery procedure for every
    disruptive test.
-   Logs and telemetry sufficient to observe the tested effect paths.
-   An owner who can approve the scope, interpret results and remediate
    findings.
-   A versioned deployment/configuration snapshot against which evidence
    can be bound.

If these prerequisites cannot be met, the tool should report an
assessment limitation rather than silently treating missing information
as a pass.

------------------------------------------------------------------------

## 5. Suggestions to isolate and govern agents

These are recommended engineering practices to improve containability.
They are not all prerequisites for the first prototype, and the tool
must distinguish recommendations from controls it has actually verified.

### Identity and permissions

-   Give each agent, or each tightly scoped agent class, a dedicated IAM
    role and runtime identity.
-   Avoid shared IAM roles, provider API keys, secrets and tool
    credentials between agents and ordinary application services.
-   Apply least privilege and explicit boundaries to high-impact
    actions.
-   Use short-lived credentials where possible, while recognising that
    already issued sessions and alternate credentials require separate
    consideration.
-   Keep the emergency control identity separate from agent identities
    and protect it with narrowly scoped permissions and strong operator
    authentication.
-   Pre-stage and test the exact deny/revocation mechanism; do not
    assume a tag or policy change will cover every action or take effect
    instantly.

### Runtime and work intake

-   Prefer dedicated ECS services/tasks or similarly isolated agent
    workloads for the initial supported profile.
-   Separate agent-specific queues, schedulers, consumers and execution
    pools from critical application workloads.
-   Ensure workload restart or autoscaling cannot silently restore
    access while containment remains active.
-   Define cancellation semantics for jobs already queued, executing or
    delegated.
-   Make retries idempotent and ensure a retried job rechecks the active
    containment policy before performing a protected effect.

### Model and tool access

-   Route model and tool calls through a mandatory gateway where
    feasible.
-   Prevent direct SDK/API access that bypasses the declared gateway,
    using supported identity and network controls.
-   Use separate credentials and policies for different agents or
    capability classes.
-   Make high-impact tool actions subject to explicit policy checks and,
    where appropriate, human approval.
-   Define which operations are read-only, which can mutate state, and
    which require independent authorisation.
-   Do not assume that blocking a model endpoint cancels tool calls or
    downstream operations already initiated.

### Network and application boundaries

-   Use dedicated, enforceable egress paths if direct provider access
    must be restricted.
-   Do not use shared NAT, broad security-group changes or
    provider-domain blocks as the default selective-containment
    mechanism.
-   Separate agent-specific ingress routes where agents expose
    endpoints; ingress blocking alone does not stop internal or outbound
    execution.
-   Apply network policies only where their enforcement implementation
    is confirmed.
-   Document alternate regions, endpoints, proxies and other routes that
    are outside the tested boundary.

### Governance and operational readiness

-   Maintain an agent inventory with owner, purpose, runtime, identity,
    model providers, tools, work-intake paths and environment.
-   Version agent configuration and record changes to identities,
    gateway routes, tool credentials, queues and delegation behaviour.
-   Define named containment policies with explicit scope,
    authorisation, activation, verification and recovery steps.
-   Require approval proportional to blast radius; use two-person
    authorisation for high-impact global or destructive actions where
    appropriate.
-   Run drills in non-production first and schedule repeat tests after
    meaningful changes.
-   Preserve incident evidence in a protected store and log who
    activated, approved and reversed each action.
-   Maintain an out-of-band recovery path independent of the agent
    workloads being contained.

------------------------------------------------------------------------

## 6. Claim model and evidence standard

Avoid a single "containable" badge or overall pass. Each claim should
specify:

-   **Deployment:** the exact deployment and configuration snapshot.
-   **Protected effect:** the action that must be prevented, such as a
    new write through a tool gateway.
-   **Declared paths:** the registered intake, identity, gateway,
    runtime and downstream path.
-   **Assumptions:** trusted components, credentials and policies
    required for the claim.
-   **Activation time:** when containment was requested and when the
    control became effective.
-   **Measured interval:** the observed time to denial, not a universal
    guarantee.
-   **Evidence type:** inspected configuration, active test, runtime
    observation or operator declaration.
-   **Result:** verified in the tested scope, failed, partial, or
    unknown.
-   **Residual exposure:** in-flight work, delegated jobs, unsupported
    paths and untested assumptions.
-   **Validity boundary:** configuration/version fingerprint and changes
    that should trigger revalidation.

### Example claim

> For deployment D, after an authorised containment request, new write
> operations through the registered tool gateway were denied within the
> measured interval during the test. Previously accepted downstream jobs
> and unsupported execution paths were not covered.

### Evidence levels

1.  **Configured:** the intended control appears in inspected
    configuration.
2.  **Enforcement tested:** an authorised negative test was rejected by
    the effective control.
3.  **Boundary assessed:** supported configuration checks and authorised
    tests assessed whether the declared protected effect is required to
    pass through the enforcement point.
4.  **Observed after activation:** monitored paths showed no relevant
    activity during a defined observation window.

These levels are not interchangeable. A successful gateway probe alone
does not establish that the agent cannot bypass the gateway. No result
should imply complete discovery of unknown execution paths.

------------------------------------------------------------------------

## 7. Initial proof of concept

### Environment

Use one dedicated ECS worker deployment with:

-   One dedicated execution identity.
-   One agent-specific work-intake path.
-   One authenticated tool gateway.
-   One synthetic write operation.
-   One unrelated application used for collateral-impact probes.

Use a non-production environment and explicit test authorisation.

### Two primary claims

**Claim 1 --- New protected writes are denied.**\
After containment is activated, new writes through the registered tool
gateway are rejected within a measured interval.

**Claim 2 --- Restart and retry do not restore write access.**\
When a worker restarts or a queued job is retried, the protected write
remains denied while containment remains active.

Runtime termination is a supporting action, not the definition of
success.

### Deliberate failure conditions

-   A declared direct-access path bypasses the gateway.
-   A replacement worker ignores a local containment flag.
-   A job was queued before containment.
-   A delegated operation was accepted downstream before denial.
-   A shared identity makes selective enforcement unsafe.

The prototype earns further investment only if it exposes or explains
consequential failures more effectively than a competent engineer using
the baseline checklist and runbook.

------------------------------------------------------------------------

## 8. Roadmap and decision gates

### Phase 0 --- Problem and competitive validation

**Objective:** Establish whether the gap is important and underserved.

Tasks: - Interview 10--15 teams operating production agents. - Obtain
concrete deployment and shutdown examples. - Compare native AWS
controls, gateways, runbooks and existing testing platforms. - Identify
which claims teams cannot currently demonstrate. - Confirm access to
non-production drill participants.

**Gate to proceed:** - At least 3 relevant teams provide a concrete
deployment and current shutdown procedure for review. - At least 2 agree
to a supervised non-production drill. - At least 1 consequential gap
remains after reviewing existing practices. - Participants accept the
minimum identity, deployment-binding and test-permission requirements.

### Phase 1 --- Claim model and experiment design

**Objective:** Define exactly what the prototype will test.

Tasks: - Specify the two primary claims and assumptions. - Define result
states and evidence requirements. - Design the deployment manifest and
configuration fingerprint. - Define safe activation, rollback and
collateral-impact probes. - Agree on a baseline checklist/runbook for
comparison.

**Exit criteria:** Another engineer can execute the test and interpret
the result without relying on undocumented assumptions.

### Phase 2 --- Minimal AWS prototype

**Objective:** Test the two primary claims on one registered ECS
deployment.

Tasks: - Bind the deployment, identity, intake path, gateway and
protected effect. - Inspect supported configuration and resource
evidence. - Execute authorised negative tests. - Test worker restart and
job retry while containment remains active. - Introduce deliberate
bypass and residual-work conditions. - Observe the unrelated
application. - Produce a claim-level evidence report.

**Exit criteria:** The prototype identifies the test outcomes, failure
modes, unknowns and collateral impact, and can be compared with the
baseline.

### Phase 3 --- Evaluate differentiated value

**Objective:** Decide whether this should become maintained software.

Tasks: - Compare findings with the baseline process. - Check whether the
tool exposes a material failure the baseline missed. - Ask participants
whether the evidence changes a deployment or operational decision. -
Measure integration effort and repeatability. - Test whether users want
to rerun verification after meaningful changes.

**Gate to proceed:** - At least one material failure is exposed that the
existing process missed. - Evidence changes an operational or deployment
decision. - At least one team wants repeat verification after a
meaningful change. - Integration effort is acceptable and results
compare favourably with the baseline.

### Phase 4 --- Harden the evidence workflow

**Objective:** Improve reliability without expanding into a general
security platform.

Potential tasks, only if Phase 3 justifies them: - Add further supported
deployment profiles. - Add more claim types and adapters. - Improve
drift-triggered revalidation for relevant configuration changes. -
Improve evidence provenance and report reproducibility. - Add recurring
drills using existing testing platforms where suitable. - Add optional,
narrowly scoped containment levers where user evidence justifies them.

### Phase 5 --- Ecosystem integration

**Objective:** Connect assurance evidence to SafeAI and, optionally,
OpenPulse.

Potential tasks: - Import SafeAI source-derived capability evidence. -
Connect source, deployment and live-test evidence without conflating
their assurance levels. - Allow OpenPulse findings to inform exposure
assessment. - Keep human authorisation in the loop for disruptive
actions. - Export evidence for existing security and governance
workflows.

### Stop or redirect criteria

Stop or redirect if: - The result is mainly a checklist plus shutdown
scripts. - Existing gateway tests and runbooks establish the same
claims. - Customers will not provide required deployment bindings or
test permissions. - The primary value requires bespoke architecture
consulting. - The reference architecture is useful but repeatable
verification adds little. - The product cannot produce evidence that
changes an operational or deployment decision.

A reference design, benchmark or assessment methodology remains a valid
research outcome even if a maintained product is not justified.

------------------------------------------------------------------------

## 9. Metrics

Measure outcomes rather than integration counts.

-   Number and severity of material failures found that the baseline
    missed.
-   Percentage of declared claims with sufficient evidence to report a
    result.
-   Time to deny the protected effect during a test.
-   Restart/retry resilience while containment remains active.
-   Collateral impact observed on the unrelated application.
-   Number of unsupported or unknown paths explicitly surfaced.
-   Time and effort required to bind a deployment and run the
    assessment.
-   Repeat-test demand after meaningful configuration changes.

Do not publish universal response-time or coverage promises before
representative testing.

------------------------------------------------------------------------

## 10. Ownership and immediate actions

Assign an owner for each workstream before implementation. Suggested
workstreams:

-   Customer discovery and competitive review.
-   Threat model and claim definitions.
-   AWS test environment and baseline runbook.
-   Prototype and verifier.
-   Evidence schema and report.
-   Research framing and evaluation.

### Immediate next actions

1.  Draft the interview script and target profile.
2.  Identify candidate teams and request concrete shutdown examples.
3.  Select the single ECS test deployment and synthetic write effect.
4.  Write the two primary claims and baseline checklist.
5.  Agree on safe test boundaries and rollback steps.
6.  Do not expand the implementation scope until the Phase 0 gate is
    met.

------------------------------------------------------------------------

## 11. Final decision principle

The product should proceed only if it demonstrates a repeatable
advantage over existing configuration reviews and operational drills.

The defensible promise is not "we can stop every AI agent." It is:

**For explicitly registered deployments and stated execution paths, we
test whether specified effects can be contained, report the evidence and
limitations, and identify changes needed to improve containment
readiness.**
