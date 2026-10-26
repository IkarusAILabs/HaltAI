# HaltAI

**A break-glass emergency stop for AI workloads.**

> When autonomous AI activity must stop, HaltAI gives infrastructure operators a prepared, auditable way to cut off its ability to act, without requiring agent code integration.

HaltAI is open source and self-hosted. It does not build a new gateway, agent runtime or governance platform. It prepares and operates controls your cloud already has (identity, network, work intake and workload shutdown) for a declared environment, then reports honestly what actually stopped.

**Important: HaltAI is not yet an emergency stop you can use.** The v0.2 code is the *proof-testing* half: it rehearses containment and reports evidence, by default against a simulated deployment. The AWS backend has only been tested with mocks. The emergency commands (`prepare`, `halt`, `status`, `release`) are **planned, not implemented**. See the [roadmap](docs/roadmap.md).

<img width="1344" height="1008" alt="What is HaltAI" src="https://github.com/user-attachments/assets/be068f08-b05d-4674-aaf0-0e329aa993d8" />

## Why is this needed?

Stopping an AI process does not necessarily stop its effects.

| Action taken | What might still happen |
|---|---|
| Block model API calls | A tool request already accepted may continue. |
| Stop the agent's worker | A queued job may run when a replacement worker or autoscaling starts. |
| Block a tool gateway | The agent may have another route to the same resource. |
| Deny one identity | The agent may hold another credential, or a session issued earlier. |
| Change a security group | Connections that are already open may stay open. |
| Stop new requests | An operation already accepted downstream may finish later. |

So HaltAI never reports that an agent is "switched off". It reports what each control did, which effects were verified as stopped, and what may still continue.

<img width="1344" height="1008" alt="How HaltAI works" src="https://github.com/user-attachments/assets/5eb45e1f-12c0-4c6f-99ec-f6a22644ef0b" />

## How it will work: prepare, halt, verify, release

| Step | Command | What it does | Status |
|---|---|---|---|
| **Prepare** | `haltai prepare` | Sets up the emergency controls for a declared environment in advance, without activating them. Checks HaltAI's own permissions and the recovery path, and checks that the agents **cannot undo** the controls. | Planned. Part of it exists as `haltai check`. |
| **Rehearse** | `haltai drill` | Halts, verifies, releases and recovers in non-production, including deliberate failure scenarios. | **Exists**, for one containment profile. |
| **Halt** | `haltai halt` | One command applies the prepared controls for the chosen profile. Every action is audited. | Planned. |
| **Verify** | `haltai status` | Shows, per control, whether it was only *requested*, *accepted* by the cloud, or *verified* as stopping the effect. Lists remaining activity. | Planned. |
| **Release** | `haltai release` | Deliberate, authorised reset with recovery checks. Containment is never lifted just because the command ended. | Planned. `haltai rollback` exists for drills. |

**Two profiles:**
- **halt-agents** stops agent workloads and tries to spare unrelated services.
- **isolate** quarantines the whole declared environment when stopping matters more than service. Its blast radius is rehearsed in advance.

**The four levers (AWS first):**

| Lever | Example controls |
|---|---|
| Identity | Prepared deny policy or service control policy; revoke sessions issued before the halt |
| Network | Prepared quarantine network ACL or firewall rules for agent subnets |
| Workload | Scale ECS to zero, suspend autoscaling, set Lambda concurrency to zero, stop AgentCore sessions |
| Work intake | Disable schedules, rules and event source mappings; stop queue consumers |

**No agent code needed:** HaltAI acts on an infrastructure boundary, ideally a dedicated AWS account for agent workloads. It doesn't touch each agent's code. To verify, it probes *as the agent's identity, from the agent's network*, with its own canary task. The precondition is that agents run inside that declared boundary.

## What exists today: the proof-tester

The v0.2 code rehearses one containment profile on one registered deployment and evaluates three claims. These become the verification checks for `halt`.

| Claim | In plain English | Current test approach |
|---|---|---|
| **1. New protected writes are denied** | After containment, new writes through the registered tool gateway are rejected. | A write is shown to work first; then containment is activated, denial is measured, and recovery is checked afterwards. |
| **2. Restart and retry do not restore access** | A replaced worker, a fresh task, a redeployment or a retried job doesn't regain access while containment is active. | These situations are exercised during the drill. |
| **3. The agent cannot undo the containment** | The agent has no permissions that let it remove its own deny rule, change relevant policies, switch identity or start compute under another role. | Policy-simulation checks of declared escape paths. This is not a live attack or a complete discovery of all paths. |

Each claim is `verified` (within the tested scope), `failed`, `partial` or `unknown`. Reports include the evidence level, measured intervals, assumptions, residual activity and the reasons for the result. More detail: [claims and decision rules](docs/claims.md).

Simulated results show how the workflow handles known scenarios. They are not proof that a real deployment is contained.

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

- `haltai init` creates example `haltai.yaml` and `deployment.yaml` files.
- `haltai check` runs configuration, prerequisite and escape-path checks. It supports `--json`. Exit codes:
  - `0` = no blocking issues;
  - `1` = blocking limitation, bypass or escape path found;
  - `2` = manifest not found.
- `haltai drill` runs the containment drills and writes reports under `reports/`.
- `haltai matrix` runs every failure scenario and checks that HaltAI reports the expected outcome.

Try a failure:

```bash
haltai drill -s local_flag_containment   # a replacement worker ignores an in-process flag: Claim 2 fails
haltai check -s agent_can_lift_deny      # the agent could remove its own deny rule: Claim 3 fails
```

Run `haltai scenarios` to list every scenario with its expected outcome. The [baseline checklist](docs/baseline-checklist.md) compares each one with a manual process.

## Is HaltAI better than what exists?

**Not proven yet.** Other tools cover parts of this:
- governance platforms such as ServiceNow AI Control Tower;
- platform controls such as AWS AgentCore session stop, IAM and network security;
- agent identity features;
- gateway policies;
- runbooks and fault-injection tools.

HaltAI's intended niche is a small, independent, self-hosted break-glass utility that operators can prepare and invoke without developer adoption.

The differentiators below are hypotheses to test with real teams:
- no agent code needed;
- framework independence;
- break-glass independence from the normal agent stack;
- simple, auditable actions;
- declared-scope isolation.

## AWS testing: read this first

The AWS backend has **not yet been validated against a real AWS account**. Use a disposable non-production environment.

A real drill changes a gateway resource policy or an IAM policy, starts and stops ECS tasks, and sends test messages to a queue. Before running one:

1. Read the [AWS backend](docs/aws-backend.md) instructions. Today's drills also need the [integration contract](docs/integration-contract.md) in the test workload; the planned `halt` will not.
2. Configure the manifest with dedicated test resources and the account.
3. Use a separate HaltAI operator identity with only the permissions it needs.
4. Authorise the drill explicitly, and confirm that rollback and recovery work.

Real drills refuse to run:
- without `--confirm`;
- in production;
- against an account other than the manifest's;
- with blocking limitations, unless `--accept-limitations` is passed.

If a drill is interrupted, `haltai rollback --confirm` removes HaltAI's containment.

`halt` will also refuse production until the roadmap's operational-safety gate (Phase 4) has passed.

## What HaltAI does not promise

HaltAI does **not**:

- detect incidents or discover every AI agent;
- guarantee instant, universal or global shutdown;
- prove that no unknown bypass path exists;
- reverse actions already completed or accepted downstream;
- contain a compromised cloud account, host, control plane or operator identity;
- prevent all collateral impact;
- replace cloud security controls, monitoring or incident response;
- act as an LLM gateway or governance platform.

A universal safety-valve **pattern** is the ambition. A universal guarantee is not.

<img width="1344" height="1008" alt="Scope and limitations" src="https://github.com/user-attachments/assets/aa81bb42-ff62-40ee-bc74-37890ed68262" />

## Roadmap at a glance

| Phase | Goal | Status |
|---|---|---|
| **0. Foundations** | Claims, simulation, escape-path check, threat model, baseline | Done |
| **1. Define the boundary** | Environment declaration, simulated environment, reference AWS environment as code | Next |
| **2. Build the break-glass action** | `prepare`, `halt`, `status`, `release`, in simulation and then on AWS | Planned |
| **3. Prove the hard stop** | Real non-production drills answering five validation questions | Needs an AWS account |
| **Market validation** | Interviews and comparison with existing tools | In parallel |
| **4. Operational safety** | Audit, partial failure, dual authorisation, independent recovery; then production use | After the decision review on 29 Jan 2027 |
| **5. Expand** | More runtimes, discovery, other clouds, only with evidence | Future |

Details, gates and the decision record: [roadmap](docs/roadmap.md). Release-by-release build steps: [release plan](docs/release-plan.md).

## Documentation

| Document | What it explains |
|---|---|
| [Roadmap](docs/roadmap.md) | Positioning, operating model, levers, phases and gates |
| [Release plan](docs/release-plan.md) | Releases 0.2.1 to 1.0.0: build steps, exit criteria, risks |
| [Threat model](docs/threat-model.md) | What is in scope, what is trusted and what is excluded |
| [Claims](docs/claims.md) | Claim procedures, evidence levels and decision rules |
| [Baseline checklist](docs/baseline-checklist.md) | Manual assessment to compare with HaltAI |
| [AWS backend](docs/aws-backend.md) | Today's AWS drill backend, permissions and safeguards |
| [Integration contract](docs/integration-contract.md) | Optional in-workload evidence used by today's drills |
| [v1 implementation plan](docs/v1-implementation-plan.md) | Historical v1 milestones (superseded by the roadmap) |
| [Contributing](CONTRIBUTING.md) | Development setup, quality checks and contribution rules |

## License

Apache-2.0. See [LICENSE](LICENSE).
