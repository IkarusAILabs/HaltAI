# HaltAI

**Containability assurance for AI agents.** HaltAI checks whether a *registered* AI-agent deployment can be selectively stopped from performing specified effects (for example, new writes through a tool gateway) without disrupting unrelated applications. It produces **deployment-bound, claim-level evidence** rather than a single "safe" score.

> Status: **v0.1, validation stage.** Scope is explicitly registered AWS agent deployments (initially one dedicated ECS worker behind an authenticated tool gateway). See [`docs/roadmap.md`](docs/roadmap.md).

## What HaltAI is (and isn't)

HaltAI **does**:

- Bind an assessment to an exact deployment and configuration snapshot (manifest + fingerprint).
- Test specific containment claims with authorised, non-production drills.
- Report each claim as `verified` / `failed` / `partial` / `unknown`, with its evidence level, assumptions, measured interval and residual exposure.
- Treat missing information as an assessment limitation, never as a pass.

HaltAI **does not**:

- Discover every agent, credential or path in an AWS estate.
- Guarantee a global or instant shutdown, or reverse completed side effects.
- Replace IAM, GuardDuty, Security Hub, gateway policy engines or incident response.
- Act as a general-purpose LLM gateway.

## v1 scope: two primary claims

1. **New protected writes are denied.** After containment is activated, new writes through the registered tool gateway are rejected within a measured interval.
2. **Restart and retry do not restore write access.** Worker restarts or retried queued jobs still can't perform the protected write while containment is active.

## Evidence levels

| Level | Meaning |
|---|---|
| `configured` | The intended control appears in inspected configuration. |
| `enforcement_tested` | An authorised negative test was rejected by the effective control. |
| `boundary_assessed` | Checks and tests assessed whether the protected effect must pass through the enforcement point. |
| `observed_after_activation` | Monitored paths showed no relevant activity during an observation window. |

These levels are not interchangeable. A passing gateway probe alone doesn't show the gateway can't be bypassed.

## Quick start (development)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
haltai validate examples/deployment.yaml
haltai fingerprint examples/deployment.yaml
pytest
```

## Repository layout

```
docs/roadmap.md          Product roadmap, prerequisites, limitations
src/haltai/models.py     Claim, evidence and result schema
src/haltai/manifest.py   Deployment manifest loading and prerequisite checks
src/haltai/fingerprint.py Configuration fingerprint (validity boundary)
src/haltai/cli.py        `haltai` command-line entry point
examples/                Example deployment manifest
tests/                   Unit tests
```

## License

Apache-2.0. See [LICENSE](LICENSE).
