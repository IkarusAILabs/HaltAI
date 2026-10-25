# HaltAI

**Proof-testing the emergency stop for AI agents.**

> If an AI system goes out of control, how do we stop it immediately? And can there be a generic safety valve for AI systems?

HaltAI starts from that question. This README explains what we have learned about it so far, what HaltAI does today, and where it is going.

> **Status: v0.2, validation stage.** Everything below runs against a **simulated** AWS deployment. An `aws` backend is implemented on boto3 and tested only with mocks. HaltAI has not yet been run against a real account. See the [roadmap](docs/roadmap.md).

## There is no single off switch

"Stop the AI" sounds like one action. In a real deployment it is several, at different boundaries:

| Stopping this… | …does not stop this |
|---|---|
| model API calls | tool calls already in flight |
| the agent's container | jobs already accepted by a downstream system |
| the gateway route | direct writes, if the agent can reach the store another way |
| the running worker | the replacement worker that autoscaling starts a minute later |
| the agent's role | a different role the agent is allowed to assume |

So HaltAI never claims an agent has been "switched off". It makes **effect-specific claims**: this effect, on these declared paths, was denied within this measured time and stayed denied.

## But there is a generic valve *pattern*

Industry solved a version of this problem long ago. A machine's emergency stop (ISO 13850) and a process plant's safety instrumented functions (IEC 61511) are trusted because they are:

- **independent** of the logic they override,
- **latching**: they stay engaged until someone deliberately resets them,
- **proof-tested** at intervals, with recorded results.

For AI agents, that becomes a valve that:

- sits at an effect boundary **outside the agent's reach**;
- **cannot be undone** by the agent it contains;
- **stays shut** across restarts, redeploys and retries;
- reports **honestly** whether it actually shut, never "success" because an API call returned 200;
- leaves **unrelated workloads** running;
- reopens only by **deliberate reset**, followed by a check that things work again.

The pattern and the evidence format can be generic. The enforcement points are always specific to an environment.

## What HaltAI is today: the proof-tester

A valve that has never been proof-tested is an assumption. HaltAI v0.2 is the proof-tester. Given one **registered** agent deployment, it runs containment drills, then reports claim-level evidence bound to a configuration fingerprint. It never gives a single "safe" score.

| Claim | What is tested | How |
|---|---|---|
| **1. New protected writes are denied** | After activation, the agent's writes through the tool gateway are rejected within a measured interval. | Positive control → activate → poll as the agent until denied → hold → roll back → verify recovery. Repeated 5 times. |
| **2. Restart and retry don't restore access** | While contained, worker restart, a fresh task, a forced redeploy and a job retry all stay denied. | Each one is triggered for real during containment. |
| **3. The agent can't undo or outrun containment** | The agent role cannot lift the deny, grant itself permissions, switch identity, or start compute under another role. | Policy simulation of an escape catalogue. Static; nothing is activated. |

Each claim is `verified`, `failed`, `partial` or `unknown`. Each one carries its evidence level, measured intervals, assumptions, residual exposure (queued jobs, writes completed late) and the reasons for the result. Decision rules: [`docs/claims.md`](docs/claims.md).

| Evidence level | Meaning |
|---|---|
| `configured` | The intended control appears in inspected configuration. |
| `enforcement_tested` | An authorised negative test, run as the agent, was rejected. |
| `boundary_assessed` | Policy simulation shows the agent can't perform the effect, or undo the control, without passing the enforcement point. |
| `observed_after_activation` | No agent write was initiated after denial during the observation window. |

## Quick start

```bash
pip install -e ".[dev]"      # or: pip install git+https://github.com/IkarusAILabs/HaltAI
mkdir demo && cd demo
haltai init                  # writes haltai.yaml + deployment.yaml (simulated backend)
haltai check                 # static checks: identity, bypass, escape paths, activation
haltai drill                 # Claims 1-3, report in reports/
haltai matrix                # every failure scenario vs. the result HaltAI must report
```

Try a failure:

- `haltai drill -s local_flag_containment` reports Claim 2 as **FAILED**: a replacement worker doesn't inherit a flag kept inside the old worker.
- `haltai check -s agent_can_lift_deny` names the permissions that would let the agent remove its own containment.

## Failure scenarios

Each scenario is a deliberate way containment goes wrong. `haltai matrix` runs them all in CI and fails if any result changes.

| Scenario | What goes wrong | Claim 1 | Claim 2 | Claim 3 | Also reported |
|---|---|---|---|---|---|
| `clean` | nothing | verified | verified | verified | |
| `bypass_direct_write` | agent can write to the store directly | partial | partial | verified | bypass path |
| `local_flag_containment` | the deny lives inside the worker process | verified | failed | verified | |
| `preload_queue` | jobs were queued before the deny | verified | verified | verified | residual queued work |
| `async_downstream` | accepted writes complete later | verified | verified | verified | late writes with job IDs |
| `shared_identity` | an unrelated app uses the agent's role | partial | partial | verified | collateral impact |
| `broken_gateway` | writes were failing before the deny | unknown | unknown | verified | no positive control |
| `slow_propagation` | the deny takes too long | failed | unknown | verified | |
| `no_recovery` | the deny can't be lifted | partial | unknown | verified | recovery not verified, drill stopped |
| `agent_can_lift_deny` | agent may edit the gateway and its own policies | partial | partial | failed | escape actions |
| `agent_can_spawn_compute` | agent may pass another role to new compute | partial | partial | failed | escape actions |

## Is this better than a good runbook?

Not yet proven, and we say so. [`docs/baseline-checklist.md`](docs/baseline-checklist.md) defines a strong manual checklist and scores it against the same scenarios. Done perfectly, the checklist finds most of what HaltAI finds.

HaltAI's possible advantages are that it:

- never skips a step;
- can't be fooled into a false pass by a broken write path;
- measures intervals and repeats them;
- lists residual work with IDs;
- makes re-testing after a change cheap.

Whether that matters to real teams is what the roadmap's Stage 2 tests.

## Configuration

Two small YAML files:

| File | Says | Key settings |
|---|---|---|
| `deployment.yaml` | **What** is assessed: one deployment and its declared paths | role, queue, gateway route, protected effect, containment mechanism, collateral probe, authorisation |
| `haltai.yaml` | **How** HaltAI runs | `backend`, drill timing, simulation scenarios |

Switching from simulation to a real non-production AWS deployment is one line:

```yaml
backend: aws        # was: simulated
aws:
  profile: haltai-nonprod
```

or `HALTAI_BACKEND=aws haltai drill --confirm`. Real drills also need:

- `authorisation.disruptive_tests_allowed: true`;
- a non-production `environment`;
- a matching account.

Before switching, read [`docs/aws-backend.md`](docs/aws-backend.md) and [`docs/integration-contract.md`](docs/integration-contract.md).

## Safety

- Every activation is paired with a rollback in `finally`, followed by a recovery probe. If recovery can't be verified, the drill stops.
- Real drills refuse to run:
  - without `--confirm`;
  - in production;
  - against a different account;
  - with blocking limitations, unless `--accept-limitations` is passed.
- `haltai check` blocks when containment is already in place, since a drill's rollback would remove it. It also blocks when a gateway redeploy would publish other undeployed changes.
- `haltai rollback --confirm` removes HaltAI containment after an interrupted drill.

## What HaltAI doesn't do

HaltAI doesn't:

- detect incidents or discover agents;
- guarantee instant or global shutdown;
- reverse completed side effects;
- contain a compromised account or runtime;
- replace IAM, GuardDuty or incident response;
- act as an LLM gateway.

It has no activation command outside drills yet. Scope and assumptions: [`docs/threat-model.md`](docs/threat-model.md).

## Where it is going

| Stage | Goal | Status |
|---|---|---|
| 0 | Threat model, baseline checklist, Claim 3 in simulation | **Done** in this version (CDK testbed still open) |
| 1 | Real non-production AWS proof, including a pre-staged toggle compared with today's mechanisms | Needs a non-production account |
| 2 | Prove value with 10–15 teams running agents | In parallel with Stage 1 |
| 3 | The valve itself: `haltai contain` / `haltai release` with honest lifecycle states | After the Stage 1 gate |
| 4–6 | Adapters by effect, generalised control model, published methodology and benchmark | Evidence-led |

Decision review: **29 January 2027**. Details: [`docs/roadmap.md`](docs/roadmap.md).

## Documentation

| Doc | What it covers |
|---|---|
| [`roadmap.md`](docs/roadmap.md) | Mission, valve properties, stages and gates |
| [`threat-model.md`](docs/threat-model.md) | What HaltAI handles, trusts and excludes |
| [`claims.md`](docs/claims.md) | Claim procedures and decision rules |
| [`baseline-checklist.md`](docs/baseline-checklist.md) | The manual baseline and how HaltAI compares |
| [`aws-backend.md`](docs/aws-backend.md) | Running against AWS; operator permissions |
| [`integration-contract.md`](docs/integration-contract.md) | What the agent deployment must record |
| [`v1-implementation-plan.md`](docs/v1-implementation-plan.md) | v1 milestones and status |

## Repository layout

```
src/haltai/
  cli.py            haltai command
  config.py         haltai.yaml
  manifest.py       deployment.yaml + prerequisite checks
  checks.py         static checks (identity, bypass, activation guards, Claim 3)
  escape.py         Claim 3 escape catalogue
  drill.py          Claim 1 / Claim 2 drill engine, report assembly
  evaluate.py       decision rules: observations -> claim result
  report.py         report.json + report.md
  backends/
    simulated.py    in-memory model of the deployment (default)
    aws.py          boto3 backend (mock-tested only)
  contract.py       helpers + haltai-probe entry point for agent integration
  scenarios.py      failure scenarios and expected results
docs/               roadmap, threat model, claims, baseline, AWS backend, contract
tests/              unit, scenario matrix, CLI, moto-based AWS tests
```

## License

Apache-2.0. See [LICENSE](LICENSE).
