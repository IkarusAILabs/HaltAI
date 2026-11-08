"""Environment failure scenarios and the per-lever outcome HaltAI must report (#19).

Each lever is observed right after it is applied, with the earlier levers already
in place (the halt order is identity → network → intake → workload). That is what
`status` will show during a real halt, and it isolates what each lever achieved on
its own.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from haltai.backends.env_base import EnvironmentBackend
from haltai.backends.sim_environment import (
    AUTOSCALE,
    SG_NETWORK,
    SHARED_NAT,
    SLOW_SCP,
    UNDECLARED_SCHEDULE,
    UNTRACKED_KEY,
    SimEnvSettings,
    SimulatedEnvironment,
)
from haltai.environment import LEVER_ORDER, EnvironmentDeclaration, Lever, Profile
from haltai.observations import CollateralObservation, LeverObservation, ObservedOutcome

S, P, N = ObservedOutcome.STOPPED, ObservedOutcome.PARTIAL, ObservedOutcome.NOT_STOPPED
WARMUP_S = 60.0  # let agents run and the queue fill before the halt
SETTLE_S = 60.0  # wait this long after applying a lever before observing it


@dataclass(frozen=True)
class EnvScenario:
    name: str
    description: str
    profile: Profile
    expected: dict[Lever, ObservedOutcome]
    flags: tuple[str, ...] = ()
    collateral_violation: bool = False  # a must-not-hit service was interrupted
    in_boundary_impact: bool = False  # isolate interrupted in-boundary services (allowed)


def _all(outcome: ObservedOutcome, **overrides: ObservedOutcome) -> dict[Lever, ObservedOutcome]:
    out = {lv: outcome for lv in LEVER_ORDER}
    out.update({Lever(k): v for k, v in overrides.items()})
    return out


ENV_SCENARIOS: dict[str, EnvScenario] = {
    s.name: s
    for s in [
        EnvScenario("clean", "Every lever covered and working; halt-agents profile.",
                    Profile.HALT_AGENTS, _all(S)),
        EnvScenario("clean_isolate", "Every lever working; isolate profile interrupts the "
                    "in-boundary service, which is allowed.",
                    Profile.ISOLATE, _all(S), in_boundary_impact=True),
        EnvScenario(SG_NETWORK, "The network lever changes a security group; established "
                    "connections keep working.",
                    Profile.HALT_AGENTS, _all(S, network=P), (SG_NETWORK,)),
        EnvScenario(AUTOSCALE, "The workload lever scales services to zero but does not "
                    "suspend autoscaling; tasks come back.",
                    Profile.HALT_AGENTS, _all(S, workload=N), (AUTOSCALE,)),
        EnvScenario(UNDECLARED_SCHEDULE, "An undeclared schedule keeps starting agent tasks "
                    "outside the declared services.",
                    Profile.HALT_AGENTS, _all(S, intake=P, workload=P), (UNDECLARED_SCHEDULE,)),
        EnvScenario(UNTRACKED_KEY, "An agent holds a long-lived key for a user in another "
                    "account, out of the identity lever's reach.",
                    Profile.HALT_AGENTS, _all(S, identity=P), (UNTRACKED_KEY,)),
        EnvScenario(SHARED_NAT, "The isolate profile cuts egress at a NAT gateway that an "
                    "out-of-boundary service also uses.",
                    Profile.ISOLATE, _all(S), (SHARED_NAT,), collateral_violation=True,
                    in_boundary_impact=True),
        EnvScenario(SLOW_SCP, "The halt SCP has not taken effect within the observation "
                    "window.",
                    Profile.HALT_AGENTS, _all(S, identity=N), (SLOW_SCP,)),
    ]
}


@dataclass
class HaltRun:
    observations: dict[Lever, LeverObservation] = field(default_factory=dict)
    collateral: list[CollateralObservation] = field(default_factory=list)

    @property
    def outcomes(self) -> dict[Lever, ObservedOutcome]:
        return {lv: o.outcome for lv, o in self.observations.items()}


def halt_and_observe(backend: EnvironmentBackend, profile: Profile,
                     settle_s: float = SETTLE_S) -> HaltRun:
    """Stage, then apply and observe each lever of the profile in halt order."""
    levers = [lv for lv in LEVER_ORDER if lv in backend.env.levers.get(profile, [])]
    for lv in levers:
        backend.stage(lv, profile)
    run = HaltRun()
    for lv in levers:
        backend.apply(lv)
        backend.sleep(settle_s)
        run.observations[lv] = backend.observe(lv)
    run.collateral = backend.observe_collateral()
    return run


def run_env_scenario(env: EnvironmentDeclaration, scenario: EnvScenario,
                     seed: int = 7) -> tuple[SimulatedEnvironment, HaltRun]:
    sim = SimulatedEnvironment(env, SimEnvSettings(seed=seed, scenarios=list(scenario.flags)))
    sim.sleep(WARMUP_S)
    return sim, halt_and_observe(sim, scenario.profile)
