# HaltAI v1 — Roadmap Review and Implementation Plan

**Status:** Draft for review · **Covers:** Roadmap Phase 1 (claim model and experiment design) and the minimal Phase 2 prototype · **Last updated:** 20 October 2026

v1 means one thing: on one registered ECS deployment, HaltAI can test **Claim 1** (new protected writes are denied) and **Claim 2** (restart and retry do not restore write access), including the deliberate failure conditions, and produce a claim-level evidence report that can be compared with a baseline checklist.

---

## Part A — Roadmap review

### What is strong

- **Tight scope.** One runtime profile (ECS), one enforcement point (tool gateway), one synthetic effect. This is small enough to build and honest enough to defend.
- **Claim-level evidence instead of a score.** The four result states and four evidence levels make overclaiming hard, which is the main risk for a product in this space.
- **Clear non-goals and stop criteria.** These protect against drifting into a general security platform or another LLM gateway.
- **Missing information is a limitation, not a pass.** This is already enforced in `check_prerequisites`.

### Gaps to close before or during v1

| # | Gap | Why it matters | Proposed resolution |
|---|-----|----------------|---------------------|
| 1 | **Sequencing vs. the Phase 0 gate.** The roadmap says not to expand implementation until Phase 0 is met, but we are starting code now. | Building too much before discovery risks wasted work. | Limit v1 to Phase 1 artefacts plus a reference testbed and Phase 2 prototype. Use the testbed as a demo in Phase 0 interviews. No new claim types or deployment profiles until the Phase 3 gate. |
| 2 | **The tool gateway is not defined.** Who provides it, and how is containment activated on it? | The gateway is the enforcement point for both claims. | The testbed uses API Gateway (REST, IAM auth) in front of a Lambda that writes to DynamoDB. Containment is a pre-staged **deny statement for the agent's role ARN** on the write route. Two adapters to compare: API Gateway resource-policy deny and an inline IAM deny on the agent role. |
| 3 | **Probes must run as the agent.** A denial seen by an operator identity proves nothing about the agent. | This is the difference between "configured" and "enforcement tested". | Run probes as **one-off ECS tasks from the agent's own task definition** with a command override. Same role, same network, and no change to the role's trust policy. |
| 4 | **No positive control.** A denied write after containment means nothing if the write was already failing. | Without it, a broken gateway reads as successful containment. | Every drill runs: probe succeeds → activate → probe denied → roll back → probe succeeds again (recovery verified). |
| 5 | **"Measured interval" has no method.** | IAM and resource-policy changes are eventually consistent. One measurement is anecdotal. | Poll at a fixed cadence, report the interval as an upper bound with its resolution (for example "denied within ≤ 12 s, 2 s polling"), and repeat N times (default 5) to report min, median and max. |
| 6 | **"Boundary assessed" has no concrete check.** | Claim 1 is meaningless if the agent can write directly to the store. | Use `iam:SimulatePrincipalPolicy` to check whether the agent role can perform the protected effect directly (for example `dynamodb:PutItem` on the target table) without the gateway. Any allow becomes a declared bypass and the claim cannot be better than `partial`. |
| 7 | **The fingerprint covers only the manifest.** | The validity boundary must include live configuration, or drift goes unnoticed. | Add a read-only snapshot collector: task definition revision, role policies, gateway resource policy, queue attributes, table ARN. The fingerprint covers manifest + snapshot. |
| 8 | **In-flight and pre-queued work is not observable.** | The roadmap lists it as a failure condition but gives no way to detect it. | The testbed writes marker items that include job ID and timestamp. The report lists any marker written after `containment_effective_at` as residual exposure. |
| 9 | **The drill itself needs a safety model.** | Containment is disruptive by design. | Before running: account allowlist (manifest account must equal `sts:GetCallerIdentity`), non-prod check, authorisation record, a maximum drill duration with automatic rollback in `finally`, and a separate HaltAI operator role. |
| 10 | **No baseline to compare against.** | Phase 3 depends on beating "a competent engineer with a checklist". | Write `docs/baseline-checklist.md` in v1 so that every drill can be scored against it. |
| 11 | **The agent's model is unspecified.** | A real LLM adds cost and nondeterminism without changing the containment claims. | The testbed agent is a deterministic stub worker. A real model can be added later as a demo option. |

### Minor document fixes

- The Phase 0–5 task lists are rendered inline ("Tasks: - Interview … - Obtain …"). Convert them to bullet lists.
- The Owner field is unassigned. Assign owners per workstream (section 10) before Phase 2.

---

## Part B — v1 architecture

```
                      ┌──────────────── HaltAI operator role (separate) ───────────────┐
                      │  haltai CLI: snapshot · check · drill · report                 │
                      └──────┬──────────────┬───────────────────┬──────────────────────┘
                             │ read-only    │ activate/rollback │ run one-off probe tasks
                             ▼              ▼                   ▼
  SQS jobs ──► ECS agent worker (agent role) ──► API Gateway /tools/synthetic_write ──► Lambda ──► DynamoDB markers
                                                     ▲   (containment = deny for agent role)
  Unrelated app (own role) ──────────────────────────┘   /tools/read  ── collateral probe
```

Failure-injection toggles in the testbed (each one maps to a roadmap failure condition):

| Toggle | Failure condition | Expected HaltAI result |
|---|---|---|
| `bypass_direct_write` (agent role gets `dynamodb:PutItem`) | Direct-access path bypasses the gateway | Claim 1 `partial`, bypass listed |
| `local_flag_containment` (worker checks an env/in-memory flag instead of the gateway) | Replacement worker ignores local flag | Claim 2 `failed` |
| `preload_queue` (jobs enqueued before activation) | Job queued before containment | Residual exposure reported |
| `async_downstream` (Lambda accepts and writes later) | Delegated operation accepted before denial | Residual exposure reported |
| `shared_identity` (unrelated app uses the agent role) | Shared identity makes selective enforcement unsafe | Prerequisite limitation; collateral impact detected |

---

## Part C — Implementation steps

Each milestone is a reviewable pull request with its own acceptance criteria. M1–M4 need no AWS account (unit tests use moto and fakes). M5 onwards need a non-production AWS account.

### M1 — Claim specifications and result rules (Phase 1)

- `docs/claims/claim-1-new-writes-denied.md` and `docs/claims/claim-2-restart-retry.md`: statement, preconditions, procedure, the evidence each step produces, and a **decision table** mapping observations to `verified` / `failed` / `partial` / `unknown`.
- `docs/baseline-checklist.md` and a one-page `docs/threat-model.md` (trusted components, out-of-scope attackers).
- Extend `models.py`: `ProbeOutcome`, `TimelineEvent`, `DrillRun`, `CollateralObservation`, and a pure function `evaluate_claim(run) -> ClaimResult` that implements the decision table.
- **Accept when:** another engineer can read the claim docs and predict the result of any scripted scenario, and the decision table is covered 1:1 by unit tests.

### M2 — Reference testbed (infrastructure as code)

- `testbed/` with AWS CDK (Python): ECS Fargate cluster, agent worker service and dedicated task role, SQS queue with DLQ, API Gateway with IAM auth, write and read routes, Lambda, DynamoDB marker table, unrelated app with its own role, HaltAI operator role.
- A stub agent worker container: consumes jobs, calls the gateway, logs structured JSON. It also contains the `probe` entry point used by one-off tasks.
- The failure-injection toggles from Part B as CDK context flags.
- **Accept when:** `cdk deploy` creates a working stack, a job produces a marker item, and the unrelated app's health check returns 200.

### M3 — Live snapshot and fingerprint

- `haltai/aws/snapshot.py`: read-only collectors for ECS, IAM, API Gateway, SQS and DynamoDB, driven by the manifest bindings.
- `haltai snapshot <manifest>` writes `snapshot.json`. `fingerprint()` covers manifest + snapshot.
- Safety guard: refuse to run if the caller's account does not match `aws_account_id`.
- **Accept when:** moto tests show that changing any bound resource changes the fingerprint and that a wrong account is refused.

### M4 — Static checks (evidence levels `configured` and `boundary_assessed`)

- Dedicated-identity check: the agent role is not used by other task definitions or functions in the declared scope.
- Bypass check: simulate the protected effect for the agent role directly against the backing store.
- Containment pre-staging check: the deny statement or adapter target exists and is scoped to the agent principal only.
- `haltai check <manifest>` emits `Evidence` records and limitations.
- **Accept when:** with `bypass_direct_write` and `shared_identity` enabled, the checks report them. Without them, the checks pass. Unit tests use a fake simulator because moto's coverage of policy simulation is limited.

### M5 — Containment adapters

- Interface: `activate() -> TimelineEvent`, `rollback() -> TimelineEvent`, `is_active() -> bool`. Idempotent.
- Adapters: `apigw_resource_policy_deny` and `iam_inline_deny`.
- Guards: authorisation record required, non-prod only, maximum drill duration, rollback in `finally`, and an explicit `--confirm` flag.
- **Accept when:** activation and rollback succeed against the testbed. Killing the CLI mid-drill still leaves a documented single-command recovery (`haltai rollback`).

### M6 — Probe runner and Claim 1 drill

- Launch one-off ECS tasks from the agent task definition with `probe write` / `probe read`. Parse structured results from CloudWatch Logs.
- Drill sequence: positive control → activate → poll until denied (record interval and resolution) → hold for the observation window → rollback → recovery probe. Repeat N times.
- Run the collateral probe (unrelated app read route and health) continuously during the drill.
- **Accept when:** on a clean testbed, Claim 1 is `verified` with measured intervals. With `bypass_direct_write` it is `partial`. If containment is never activated, the result is `failed`, not `unknown`.

### M7 — Claim 2 drill

- While containment is active: stop the agent task and wait for the replacement, then probe. Force a new deployment, then probe. Let an in-flight job hit its visibility timeout and be retried, then check markers.
- Scan markers for writes after `containment_effective_at`. Any such write is either residual exposure (accepted before containment) or a failure (initiated after).
- **Accept when:** a clean testbed gives `verified`, `local_flag_containment` gives `failed`, and `preload_queue` / `async_downstream` show residual exposure with job IDs.

### M8 — Evidence report

- `haltai drill --claim 1|2|all <manifest> --out reports/<timestamp>/` writes `report.json` (the `AssessmentReport` schema) and `report.md`.
- Every claim shows: statement, result, highest evidence level, intervals, assumptions, residual exposure, limitations, fingerprint and revalidation triggers. Hashes of raw API responses are stored for provenance.
- The wording follows the roadmap's example claim and never says "safe" or "containable" on its own.
- **Accept when:** a reviewer can reproduce each result from the report and its raw evidence files.

### M9 — Failure matrix and baseline comparison (Phase 2 exit)

- A script runs the full matrix: clean, plus each toggle. It collects the reports into `docs/results/v1-matrix.md`.
- Run the baseline checklist on the same configurations and record what each approach caught.
- **Accept when:** every expected result in the Part B table holds, and the comparison is written up for the Phase 3 decision.

### Cross-cutting

- CI: lint, mypy, and unit tests with moto on every PR. A manually triggered integration workflow uses GitHub OIDC into the non-prod account.
- No secrets in the repo. Drill reports are git-ignored by default (`reports/`).
- Branch per milestone, PR review before merge.

---

## Part D — Decisions needed

1. **Non-production AWS account and region** for the testbed. The example manifest assumes `eu-west-2`.
2. **Infrastructure as code tool.** This plan proposes AWS CDK (Python) to stay in one language. Terraform is the alternative if target teams mostly use it.
3. **Containment adapter priority.** This plan builds both and compares them. If time is short, start with the API Gateway resource-policy deny, because it touches only the gateway and not the agent's role.
4. **Repetition count and observation window defaults.** Proposed: 5 repetitions, 60 s window.
5. **Workstream owners** (roadmap section 10).
