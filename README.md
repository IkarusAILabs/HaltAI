# HaltAI

**Testing whether emergency containment really works for AI agents.**

> If an AI system goes out of control, how do we stop its harmful actions—and how do we know the stop actually worked?

HaltAI explores a general security-valve pattern for AI-enabled systems: an authorised operator requests containment, existing controls block a specified action, and independent checks verify the result.

**Important: HaltAI is not yet a production-ready emergency stop.** In v0.2, the drill workflow runs against a simulated deployment by default. An AWS backend exists, but it has only been tested with mocks; it has not yet been validated against a real AWS account. There is no standalone `haltai contain` command outside the test drills yet.

See the [roadmap](docs/roadmap.md) for the current status and next steps.

## Why is this needed?

Stopping an AI process does not necessarily stop its effects.

| Action taken | What might still happen |
|---|---|
| Block model API calls | A tool request already accepted may continue. |
| Stop the agent's worker | A queued job may run when a replacement worker starts. |
| Block a tool gateway | The agent may have another route to the same resource. |
| Deny one identity | The agent may be able to use another identity. |
| Stop new requests | An operation already accepted by a downstream service may finish later. |

That is why HaltAI focuses on **specific actions and effects**, not a vague claim that an agent has been switched off.

The long-term goal is a reusable containment pattern. The actual controls will always depend on the environment—for example, its gateways, identities, queues and runtimes.

## How HaltAI works

HaltAI assesses one explicitly registered deployment at a time.

1. **Describe the deployment.** Identify the workload, execution identity, work queue, protected action, containment control and an unrelated service to check for collateral impact.
2. **Check the setup.** Inspect the declared configuration for issues such as possible gateway bypasses, shared identities or an unsafe containment configuration.
3. **Run a controlled drill.** Establish that the protected action works before containment, activate the test control, and check whether the action is denied. Then test restart/retry behaviour and verify recovery.
4. **Report the evidence.** Produce a report with the result for each claim, measured timings, assumptions, known residual activity and reasons for any failure or uncertainty.

The checks are designed to distinguish a successful control request from evidence that the protected action was actually blocked.

## What the current prototype tests

HaltAI v0.2 evaluates three claims:

| Claim | In plain English | Current test approach |
|---|---|---|
| **1. New protected writes are denied** | After containment, new writes through the registered tool gateway should be rejected. | A successful write is checked first, containment is activated, denial is measured, and recovery is checked afterwards. |
| **2. Restart and retry do not restore access** | A restarted or replaced worker, a fresh task, a redeployment or a retried job should not regain write access while containment remains active. | These situations are exercised during the drill. |
| **3. The agent cannot undo the containment control** | The agent should not have permissions that let it remove its own deny rule, change relevant policies or start compute under another role. | Static configuration and policy-simulation checks assess the declared escape paths; this is not a live attack or complete discovery of all possible paths. |

The normal quick start uses the **simulated backend**. Results from simulation show how the workflow handles known scenarios; they are not proof that a real AWS deployment is contained.

Each claim receives its own result:

- `verified` — the claim passed within the stated test scope.
- `failed` — evidence showed the claim did not hold.
- `partial` — evidence or a known limitation prevents a full pass.
- `unknown` — the available evidence was not enough to decide.

Reports also describe the evidence level, measured intervals, assumptions, residual exposure and the reasons for the result. A result applies only to the registered deployment, tested paths and captured configuration—not to every possible action an agent might take.

More detail: [claim definitions and decision rules](docs/claims.md).

## Try it locally

The default setup is a simulation. It does not contact or modify an AWS account.

```bash
git clone https://github.com/IkarusAILabs/HaltAI.git && cd HaltAI
pip install -e ".[dev]"
mkdir haltai-demo && cd haltai-demo
haltai init
haltai check
haltai drill
haltai matrix
```

The commands:

- `haltai init` creates example `haltai.yaml` and `deployment.yaml` configuration files.
- `haltai check` runs configuration and prerequisite checks.
- `haltai drill` runs the configured containment drills and writes reports under `reports/`.
- `haltai matrix` runs the known failure scenarios and checks whether HaltAI reports the expected outcomes.

### Try a failure scenario

```bash
haltai drill -s local_flag_containment
```

This scenario models containment that depends on a flag inside the original worker. A replacement worker does not inherit that flag, so Claim 2 should fail.

To see whether the agent could remove its own containment rule in the declared policy model, try:

```bash
haltai check -s agent_can_lift_deny
```

Run `haltai scenarios` to list every scenario with its expected outcome. The [baseline checklist](docs/baseline-checklist.md) compares each one with a manual process.

## What can go wrong?

The simulation includes scenarios such as:

- **Direct bypass:** the agent can write to the data store without using the gateway.
- **Restart or retry:** a replacement worker or retried job attempts an action during containment.
- **Residual work:** an operation accepted before containment completes afterwards.
- **Shared identity:** an unrelated application uses the same role, so a deny rule may disrupt it too.
- **Broken or slow control:** the action was already failing, or denial takes longer than the test allows.
- **Unsafe permissions:** the agent can alter its own containment control or start compute using another role.

These scenarios are useful tests, not proof that every bypass or failure mode has been discovered. `haltai matrix` runs the supported scenarios and checks the results against their expected behaviour.

## Is HaltAI better than a good runbook?

**That has not been proven yet.** The project includes a [baseline checklist](docs/baseline-checklist.md) so the same scenarios can be assessed with a manual process and with HaltAI.

HaltAI may help by making drills repeatable, measuring time to denial, checking restart/retry behaviour, recording residual work and making re-testing after a configuration change easier. The project still needs real-world evidence that these benefits are material.

## AWS testing: read this first

The AWS backend is implemented but has **not yet been validated against a real AWS account**. Begin with a disposable non-production environment and follow the documented integration procedure.

A real drill can change a gateway resource policy or an IAM policy, start or stop ECS tasks, and send test messages to a queue. It is intentionally guarded because it is disruptive.

Before running a real drill:

1. Read [AWS backend instructions](docs/aws-backend.md) and the [agent integration contract](docs/integration-contract.md).
2. Configure the deployment manifest with the actual, dedicated test resources and account.
3. Implement the required probe and effect-recording behaviour in the test workload.
4. Use a separate HaltAI operator identity with only the permissions needed for the test.
5. Authorise the drill explicitly, confirm the environment is non-production, and verify the rollback and recovery procedure.

Real drills refuse to run without `--confirm`, against a production environment, against a different account than the manifest, or with blocking limitations (unless `--accept-limitations` is passed). Every activation is rolled back and followed by a recovery check. If a drill is interrupted, `haltai rollback --confirm` removes HaltAI's containment.

Do not switch to `backend: aws` simply to experiment against a production deployment. Missing prerequisites and unsupported paths must remain visible as limitations, not be treated as a pass.

## What HaltAI does not promise

HaltAI does **not** currently:

- detect every incident or discover every AI agent in an environment;
- guarantee instant, universal or global shutdown;
- prove that no unknown bypass path exists;
- reverse an action that has already completed;
- guarantee containment if the runtime, cloud account or trusted control plane is compromised;
- replace cloud security controls, monitoring or incident response;
- act as a general-purpose LLM gateway.

A blocked gateway route may not stop direct access. Terminating a worker may not cancel a downstream operation already accepted. Results can become stale when relevant configuration changes.

The longer-term vision is broader than the current prototype: an authorised interface that coordinates environment-specific controls and independently verifies their effect. That goal will be pursued only as the real-world evidence supports it.

## Roadmap at a glance

| Stage | Goal | Status |
|---|---|---|
| **0. Define the scope** | Threat model, baseline checklist and initial claims | Completed for the current prototype; the real AWS testbed remains open. |
| **1. Validate on real AWS** | Run controlled non-production drills and verify the observed effects | Waiting for a suitable non-production environment and live validation. |
| **2. Validate the value** | Compare against runbooks and gather feedback from teams operating agents | Planned alongside Stage 1. |
| **3. Build the containment interface** | Add an explicit containment/release workflow with honest lifecycle states | Depends on the Stage 1 gate. |
| **4–6. Expand based on evidence** | Add supported adapters, generalise the control model and publish results | Future work; no universal coverage claim. |

Full details and decision gates: [roadmap](docs/roadmap.md) and [v1 implementation plan](docs/v1-implementation-plan.md).

## Documentation

| Document | What it explains |
|---|---|
| [Roadmap](docs/roadmap.md) | Mission, stages and decision gates |
| [Threat model](docs/threat-model.md) | What is in scope, what is trusted and what is excluded |
| [Claims](docs/claims.md) | Test procedures and rules for interpreting results |
| [Baseline checklist](docs/baseline-checklist.md) | Manual assessment to compare with HaltAI |
| [AWS backend](docs/aws-backend.md) | AWS configuration, permissions and drill safeguards |
| [Integration contract](docs/integration-contract.md) | What the test workload must record for verification |
| [Implementation plan](docs/v1-implementation-plan.md) | Milestones and implementation status |

## License

Apache-2.0. See [LICENSE](LICENSE).
