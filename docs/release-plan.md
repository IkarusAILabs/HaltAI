# HaltAI release plan

**Last updated:** 25 October 2026 · **Implements:** [roadmap](roadmap.md) revision 0.4 · **Current release:** 0.2.0

This plan turns the roadmap's phases into numbered releases, each built from small, reviewable steps. The roadmap says *what* and *why*; this plan says *in which release* and *in what order*.

> **Two version numbers.** "Roadmap 0.4" is the revision of the planning document. Releases of the software are numbered separately (0.2.0, 0.3.0, …). They don't correspond.

## 1. Versioning rules

- [Semantic versioning](https://semver.org/) in the `0.x` range: a minor release (0.3.0) may break the CLI or configuration, and must say so in `CHANGELOG.md`. Patch releases (0.3.1) only fix things.
- Every release has a git tag `vX.Y.Z`, a GitHub release, a `CHANGELOG.md` entry, and an updated `__version__` and `pyproject.toml`.
- **1.0.0 means production-ready break-glass** (section 4). Nothing before 1.0.0 may `halt` a production environment.
- A release ships only when its exit criteria hold and CI is green on every supported Python version.

## 2. Overview

| Release | Roadmap phase | Theme | Target | Key issues |
|---|---|---|---|---|
| **0.2.1** | Phase 0 wrap-up | Housekeeping and contributor work | 8 Nov 2026 | #4, #6, #9, #11, #16, PRs #12 and #15 |
| **0.3.0** | Phase 1 | The emergency boundary | 22 Nov 2026 | #18, #19, #20, #21 |
| **0.4.0** | Phase 2a | Break-glass commands in simulation | 20 Dec 2026 | to open |
| **0.5.0** | Phase 2b | AWS lever adapters and the canary (mock-tested) | 17 Jan 2027 | to open |
| **0.6.0** | Phase 3 | Real non-production proof | 26 Jan 2027 | to open |
| — | | **Decision review** | 29 Jan 2027 | |
| **0.7.0** | Phase 4 | Operational safety and distribution | after the review | to open |
| **1.0.0** | Phase 4 gate | Production-ready break-glass | when the 1.0 criteria hold | |
| 1.x | Phase 5 | Expansion | evidence-led | |

**Critical path:** 0.6.0 needs a disposable non-production AWS setup with AWS Organizations (for the dedicated-account variant), **requested by 15 November 2026**. Without it, the decision review can only assess simulation and mocks, and must be moved.

## 3. Releases and build steps

Each step is one pull request unless noted. Steps within a release are in dependency order; steps marked ∥ can run in parallel.

### 0.2.1 — Housekeeping (Phase 0 wrap-up)

1. ∥ Merge the contributor PRs once their reviews are addressed: #15 (CONTRIBUTING) and #12 (more Claim 3 escape paths).
2. ∥ #6: show the actions Claim 3 checked in `report.md`.
3. ∥ #16: `check --json` gets `backend`, `deployment` and `scenarios`; unambiguous exit codes (0, 1, 2, 4).
4. ∥ #11: commit the simulated matrix results and check them in CI.
5. Add `CHANGELOG.md` (with 0.2.0 written retrospectively), tag `v0.2.0` on the last v0.2 commit (`ae086b6`), and add a release workflow that builds the wheel and creates a GitHub release from a tag.

**Exit criteria:** the steps above are merged; `v0.2.1` is tagged from CI; the CHANGELOG is accurate.

### 0.3.0 — The emergency boundary (Phase 1)

1. #18: `environment.yaml` schema, loader, `check_environment()`, and `haltai validate --environment`. `haltai init` writes an example.
2. ∥ #20: canary-probe design (`docs/design/canary-probe.md`), together with the execution-role design (roadmap 7.1): trust policy, halt-SCP exception, protection SCP. **Design only.**
3. #19: simulated environment backend with the four levers and the six lever failure scenarios. Depends on step 1.
4. ∥ #21: CDK reference environment (dedicated-account and shared-account variants), with `cdk synth` in CI. Depends on step 1 and uses step 2.
5. Claim 3 extended to the boundary in simulation. No identity inside the boundary may modify, assume or pass the execution role, retarget the protection SCP, or retag HaltAI-staged resources. Add simulated scenarios for each.
6. Docs: `docs/environment.md`, threat-model updates from the designs, README "try it" for `validate --environment`.

**Exit criteria:** the roadmap's Phase 1 gate (the boundary is fully declarable; the simulator models every lever and failure scenario); the design docs are agreed; `cdk synth` is green. `deployment.yaml` and today's drills are unchanged.

### 0.4.0 — Break-glass in simulation (Phase 2a)

1. **Lever state model** (`src/haltai/levers.py`): `not_covered`, `requested`, `accepted`, `effect_verified`, `partial`, `failed`, `unverified`, `released`, `recovery_failed`. Transitions are unit-tested.
   - `effect_verified` can only be reached through an *effect observation*. Tests must prove that a lever's own state can't produce it.
2. **Audit log:** append-only JSON lines for every prepare, halt, status and release action: who, when, scope, lever, request and result. Local file first; the protected store comes in 0.7.0.
3. **`haltai prepare`** (simulation): stage the controls without activating them; operator-permission check; Claim 3 on the boundary; fingerprint written to `prepared.json`. A blocking finding stops it.
4. **`haltai halt --profile halt-agents`** (simulation): order identity → network → intake → workload; continue past failed levers; production refused.
5. **`haltai status`:** per-lever state, residual activity and the evidence behind each `effect_verified`; `--json`.
6. **`haltai release`:** explicit `--confirm` plus a reason; reverse order; recovery checks; reports anything not restored. It never runs automatically.
7. **`haltai drill`** rebased on prepared controls. A drill is halt, then status, then release, then recovery, run against the failure scenarios. Today's gateway and IAM drill profiles stay available as `--legacy`.
8. **Exit codes** for the new commands, documented:
   - 0 = every in-scope lever is `effect_verified` (halt/status) or recovered (release);
   - 1 = blocking finding (prepare);
   - 2 = usage error;
   - 4 = error;
   - 5 = partial: at least one lever not verified.
9. Deprecation notice: `deployment.yaml` and the legacy drill profiles are deprecated, to be removed in 0.7.0.

**Exit criteria:** the roadmap's Phase 2 gate in simulation (every failure scenario produces the expected lever states, and nothing reports `effect_verified` without effect evidence); `haltai matrix` covers the new environment scenarios.

### 0.5.0 — AWS adapters and canary (Phase 2b, mock-tested)

Lever adapters behind the same interface as the simulator. Each one has `stage`, `apply`, `release` and `observe`, moto or stub tests, least-privilege permissions documented in `docs/aws-backend.md`, and its limitations listed.

1. **Execution role and protection SCP** created by `prepare` (from the 0.3.0 design); checks for the six protections.
2. ∥ **Identity lever:** attach the halt SCP (dedicated account) or attach deny policies to roles (shared account); session revocation via `aws:TokenIssueTime`.
3. ∥ **Workload lever:**
   - ECS: record the desired count, set it to 0 and stop tasks, suspend Application Auto Scaling;
   - Lambda: reserved concurrency 0.
4. ∥ **Work-intake lever:** disable EventBridge rules and Scheduler schedules; disable event source mappings; deny `sqs:ReceiveMessage` to consumer roles.
5. **Canary:** HaltAI's task definition registered in the agent account, run with the agent role in agent subnets, results reported through the exit code (from the 0.3.0 design). "Could not start" counts as `unverified`.
6. **Network lever:** prepared quarantine NACL and swapping subnet associations. If this slips, it moves to 0.6.0 and `status` reports the network lever as `not_covered`.
7. Operator permission sets (read-only, prepare, halt and release) published as IAM policy documents.

**Exit criteria:** every adapter is mock-tested, including partial failure; `status` never reports `effect_verified` from control-plane state alone; production is refused.

### 0.6.0 — Real non-production proof (Phase 3)

1. Deploy the CDK reference environment (dedicated-account variant first) to the disposable setup.
2. First supervised `drill` with one repetition: halt-agents, then status, then release, then recovery.
3. Answer the roadmap's five validation questions with measured evidence. Include:
   - time to effective denial per lever (distribution and resolution);
   - whether established connections survive;
   - interrupted halt and interrupted release.
4. Run every failure toggle; any false `effect_verified` is a release blocker.
5. Measure the isolate profile's blast radius on in-boundary and out-of-boundary services.
6. Publish `docs/results/phase-3.md` (redacted raw evidence alongside), and score the baseline checklist and existing tools against the same conditions.
7. Fix the adapters found wanting. Patch releases (0.6.x) as needed.

**Exit criteria:** the roadmap's Phase 3 gate. The results doc is the main input to the decision review on 29 January 2027.

### 0.7.0 — Operational safety and distribution (Phase 4)

Only if the decision review says go.

1. **Protected audit store** (for example S3 with Object Lock in the security account), with the local log as a fallback.
2. **Dual authorisation** for `--profile isolate`, and optionally for halt-agents by policy.
3. **Partial failure and safe retry:** idempotent apply and release; resume after the HaltAI process dies mid-halt; tested under API errors, throttling and propagation delay.
4. **Drift detection:** `status` and a scheduled check compare against `prepared.json` and flag the halt as unverified when it has drifted.
5. **Independent recovery path:** a documented manual fallback, plus `release` working with only the operator role.
6. **Break-glass availability.** HaltAI must work when everything else is failing:
   - pinned dependencies with a lockfile;
   - an offline install bundle (wheels) and a container image;
   - no network calls except to AWS;
   - pre-installation guidance for the operator's workstation or bastion.
7. **Supply chain:**
   - signed releases (Sigstore);
   - an SBOM;
   - reproducible builds from the release workflow.
8. Remove `deployment.yaml` and the legacy drill profiles (deprecated in 0.4.0).
9. **Production policy:** `halt` may target production only with `authorisation.production_halt_allowed: true`, dual authorisation, and a drill passed within a configured interval.

**Exit criteria:** the roadmap's Phase 4 gate.

### 1.0.0 — Production-ready break-glass

Released when all of these hold:

- [ ] Phase 4 gate passed; 0.7.x has been used in non-production drills for at least four weeks without a false `effect_verified`.
- [ ] At least two external teams have prepared and drilled HaltAI in their own non-production environments.
- [ ] The threat model and execution-role design have been reviewed by someone outside the core team.
- [ ] Every command, exit code, permission set and limitation is documented; no open critical or high-severity issues.
- [ ] A private vulnerability-reporting route (`SECURITY.md`) exists.

### 1.x — Expansion (Phase 5)

In order of user evidence, not promised up front: EKS workloads, AgentCore session controls at depth, discovery of known cloud workloads (roadmap Approach B), and other clouds.

## 4. Every release: checklist

- [ ] Exit criteria met; CI green on all supported Python versions; `haltai matrix` green.
- [ ] `CHANGELOG.md` updated, including breaking changes and deprecations.
- [ ] Version bumped in `src/haltai/__init__.py` and `pyproject.toml`.
- [ ] README status line and "roadmap at a glance" updated; no claim beyond what the release does.
- [ ] Tag `vX.Y.Z` pushed; the release workflow publishes the GitHub release.
- [ ] Issues for the next release opened and labelled (`phase-N`).

## 5. Risks to this plan

| Risk | Effect | Mitigation |
|---|---|---|
| No AWS non-production setup by mid-November | 0.6.0 and the decision review slip | Request it now; if late, review on simulation and mocks and say so explicitly |
| Phase 2 is large (four commands, four AWS levers, canary) | 0.5.0 slips | Split into 0.4.0 (simulation) and 0.5.0 (AWS); network lever may move to 0.6.0 |
| Workstreams have no owners | Steps stall | Assign owners per release before each one starts |
| The canary can't report under a network halt | No verification without agent code | Resolved in the 0.3.0 design (exit-code reporting), before any adapter work |
| Contributor PRs go stale | Housekeeping drags into 0.3.0 | Review within a week; maintainers finish them if needed |
