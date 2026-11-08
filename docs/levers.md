# Levers: simulation, observation and failure scenarios

**Status:** release 0.3.0, issue #19. This describes the **simulated** environment the break-glass commands are built against. Simulated results show the method; they are not evidence about real AWS behaviour. AWS adapters come in 0.5.0 ([release plan](release-plan.md)).

## The interface

Every environment backend (`src/haltai/backends/env_base.py`) exposes the same small interface. The planned commands drive it:

| Method | Used by | Does |
|---|---|---|
| `stage(lever, profile)` | `prepare` | Records the pre-halt state (desired counts, scaling, concurrency, trigger states) and prepares the control without activating it |
| `apply(lever)` | `halt` | Activates a staged control. Refuses if the lever wasn't staged |
| `observe(lever)` | `status` | Reports what the lever achieved, **from effects only**. Read-only |
| `observe_collateral()` | `status` | Health of the declared in-boundary and out-of-boundary services |
| `release(lever)` | `release` | Deactivates the control and restores the recorded pre-halt state exactly |
| `agent_principals()` | `prepare`, `check --environment` | Concrete agent identities in the boundary |
| `boundary_escape_allowed(principal, actions)` | `prepare`, `check --environment` | Claim 3 on the boundary: may this identity perform each action? `None` = not evaluated |

## The simulated world

`src/haltai/backends/sim_environment.py` builds a deterministic, virtual-time model from an [environment declaration](environment.md). Time advances only through `sleep`, and results are reproducible from `seed`.

| Lever | What the simulator models |
|---|---|
| Identity | Agent roles making AWS calls. The halt SCP (`account`/`ou`) or per-role deny (`tagged`) takes effect after a propagation delay. The execution role is the SCP's only exemption. Principals in other accounts are out of reach. `isolate` denies every principal in a dedicated account. |
| Network | Agent subnets, with established connections to model and tool endpoints. A quarantine NACL drops everything; a security-group change refuses only *new* connections. `isolate` also quarantines the in-boundary service subnet. |
| Workload | ECS services with desired counts and Application Auto Scaling (which restores the minimum unless suspended), and Lambda functions with reserved concurrency (0 throttles every invocation). |
| Work intake | An SQS queue fed by an outside producer (SQS has no pause, so messages accumulate); event source mappings, EventBridge rules and schedules that start Lambda work. |
| Collateral | The declared in-boundary services (their own role, a service subnet) and out-of-boundary services (another account and subnet). |

## How an observation is decided

An observation uses only **effect evidence**:
- canary probes made as the agent's identity and from the agent's subnets;
- the activity log, the simulated equivalent of CloudTrail and VPC Flow Logs.

Activity in the first `grace_s` seconds after `apply` (default 20 s) is ignored to allow for propagation.

**The honesty rules:**
- A control being active never makes a lever `stopped`.
- If the effect was never seen working before the lever was applied (no **positive control**), the lever reports `inconclusive`.

| Lever | `not_stopped` | `partial` | `stopped` |
|---|---|---|---|
| Identity | A canary call as an agent role is allowed | Canary denied, but agent activity still made allowed AWS calls (for example with a credential outside the boundary) | Canary denied and no allowed calls in the window |
| Network | The canary reaches a model or tool endpoint from an agent subnet | Canary refused, but flows were still accepted (established connections) | Canary refused and no accepted flows |
| Work intake | A declared trigger still fired | Only undeclared triggers fired (a coverage gap) | No trigger fired |
| Workload | Declared ECS tasks are running now | Agent compute ran during the window, but none of the declared tasks are running now | No agent compute ran |

Two other outcomes:
- `not_covered`: the declaration names no resources for the lever.
- `inconclusive`: there was no positive control.

Work intake also reports **residual** activity, such as messages left in SQS. They are reported, never claimed as cancelled.

## Failure scenarios

`src/haltai/env_scenarios.py` runs each scenario as a halt:
1. Warm up for 60 s so the agents are active and the queue fills.
2. Stage every lever of the profile.
3. Apply each lever in halt order (identity → network → intake → workload), observing it 60 s after its own apply, with the earlier levers already in place.

That is what `status` will show during a real halt.

| Scenario | Profile | Identity | Network | Intake | Workload | Collateral |
|---|---|---|---|---|---|---|
| `clean` | halt-agents | stopped | stopped | stopped | stopped | none |
| `clean_isolate` | isolate | stopped | stopped | stopped | stopped | in-boundary service interrupted (allowed) |
| `sg_change_keeps_connections` | halt-agents | stopped | **partial** | stopped | stopped | none |
| `autoscaling_restarts_tasks` | halt-agents | stopped | stopped | stopped | **not_stopped** | none |
| `schedule_outside_boundary` | halt-agents | stopped | stopped | **partial** | **partial** | none |
| `untracked_credential` | halt-agents | **partial** | stopped | stopped | stopped | none |
| `shared_nat` | isolate | stopped | stopped | stopped | stopped | **out-of-boundary service interrupted (violation)** |
| `slow_scp_propagation` | halt-agents | **not_stopped** | stopped | stopped | stopped | none |
| `agent_can_assume_execution_role` | halt-agents | **partial** | stopped | stopped | stopped | none |
| `agent_can_modify_execution_role` | halt-agents | stopped | stopped | stopped | stopped | none |
| `agent_can_pass_execution_role` | halt-agents | stopped | stopped | stopped | stopped | none |
| `agent_can_leave_organization` | halt-agents | stopped | stopped | stopped | stopped | none |
| `agent_can_edit_staged_controls` | halt-agents | stopped | stopped | stopped | stopped | none |

The last five are **Claim 3 on the boundary** scenarios: each makes Claim 3 `failed` ([claims.md](claims.md#claim-3-on-the-environment-boundary)). Only `agent_can_assume_execution_role` also changes the halt. Blocked agents switch to the execution role, which the halt SCP exempts, so the identity lever is only `partial`. The others are permissions an agent could use before or around a halt; the matrix checks that Claim 3 catches them. `agent_can_retag_scope` applies to tagged boundaries only and is tested separately.

The matrix runs in CI as `tests/test_sim_environment.py::test_env_scenario_matrix`. Other tests in that file cover each lever on its own and the honesty rules: an active control alone, no positive control, and activity inside the window.

`schedule_outside_boundary` shows why HaltAI uses several levers. The undeclared schedule keeps starting tasks, so intake and workload are only `partial`. But identity and network stay `stopped`, because the stray tasks run as an agent role in an agent subnet.
