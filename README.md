# HaltAI

**Containability assurance for AI agents.** HaltAI checks whether a *registered* AI-agent deployment can be selectively stopped from performing a specified effect (for example, new writes through a tool gateway) without disrupting unrelated applications. It produces **deployment-bound, claim-level evidence** rather than a single "safe" score.

> Status: **v0.2, validation stage.** The drill engine, evidence model and reports are complete and run against a **simulated** AWS deployment by default. An **aws** backend is implemented against boto3 and tested only with mocks; it has not yet been run against a real account. See [`docs/roadmap.md`](docs/roadmap.md) and [`docs/v1-implementation-plan.md`](docs/v1-implementation-plan.md).

## Quick start

```bash
pip install -e ".[dev]"      # or: pip install git+https://github.com/IkarusAILabs/HaltAI
mkdir demo && cd demo
haltai init                  # writes haltai.yaml + deployment.yaml (simulated backend)
haltai check                 # static checks: identity, gateway bypass, containment pre-staged
haltai drill                 # Claim 1 + Claim 2 drills, report in reports/
haltai matrix                # every failure scenario vs. the result HaltAI must report
```

Try a failure: `haltai drill -s local_flag_containment` reports Claim 2 as **FAILED**, because a replacement worker doesn't inherit a flag kept inside the old worker. `haltai scenarios` lists them all.

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

or `HALTAI_BACKEND=aws haltai drill --confirm`. Real drills also need `authorisation.disruptive_tests_allowed: true`, a non-production `environment`, and a matching account. Before switching, read [`docs/aws-backend.md`](docs/aws-backend.md) and [`docs/integration-contract.md`](docs/integration-contract.md).

## What the drills test

1. **Claim 1: new protected writes are denied.** Positive control → activate containment → poll until denied (measured interval) → observation window → rollback → recovery verified. Repeated N times.
2. **Claim 2: restart and retry don't restore write access.** While contained: worker restart, one-off fresh task, forced redeploy and job retry must all stay denied. No agent write may be initiated after containment became effective.

Each claim is reported as `verified` / `failed` / `partial` / `unknown`, with its evidence level, measured interval, assumptions, residual exposure and the reasons. The decision rules are in [`docs/claims.md`](docs/claims.md).

| Evidence level | Meaning |
|---|---|
| `configured` | The intended control appears in inspected configuration. |
| `enforcement_tested` | An authorised negative test, run as the agent, was rejected by the control. |
| `boundary_assessed` | Policy simulation shows the agent can't perform the effect without the gateway. |
| `observed_after_activation` | No agent write was initiated after denial during the observation window. |

## Simulated failure scenarios

| Scenario | Claim 1 | Claim 2 | Also reported |
|---|---|---|---|
| `clean` | verified | verified | |
| `bypass_direct_write` | partial | partial | bypass path |
| `local_flag_containment` | verified | failed | |
| `preload_queue` | verified | verified | residual queued work |
| `async_downstream` | verified | verified | writes completed after containment |
| `shared_identity` | partial | partial | collateral impact, blocking limitation |
| `broken_gateway` | unknown | unknown | no positive control |
| `slow_propagation` | failed | unknown | |
| `no_recovery` | partial | unknown | recovery not verified, drill stopped |

## Safety

- Every activation is paired with a rollback in `finally`, followed by a recovery probe.
- If recovery can't be verified, the drill stops.
- Real drills refuse to run without `--confirm`, in production, against a different account, or with blocking limitations (unless `--accept-limitations`).
- `haltai rollback --confirm` removes HaltAI containment after an interrupted drill.

## What HaltAI doesn't do

It doesn't discover agents, guarantee instant or global shutdown, reverse completed side effects, replace IAM/GuardDuty/incident response, or act as an LLM gateway.

## Repository layout

```
src/haltai/
  cli.py            haltai command
  config.py         haltai.yaml
  manifest.py       deployment.yaml + prerequisite checks
  checks.py         static checks (identity, bypass, pre-staging)
  drill.py          Claim 1 / Claim 2 drill engine, report assembly
  evaluate.py       decision rules: observations -> claim result
  report.py         report.json + report.md
  backends/
    simulated.py    in-memory model of the deployment (default)
    aws.py          boto3 backend (mock-tested only)
  contract.py       helpers + haltai-probe entry point for agent integration
  scenarios.py      failure scenarios and expected results
docs/               roadmap, plan, claims, integration contract, AWS backend
tests/              unit, scenario matrix, CLI, moto-based AWS tests
```

## License

Apache-2.0. See [LICENSE](LICENSE).
