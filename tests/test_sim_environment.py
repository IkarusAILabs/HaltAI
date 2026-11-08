"""Simulated environment backend (issue #19): levers, scenarios and the honesty rule."""

from datetime import timedelta
from pathlib import Path

import pytest

from haltai.backends.env_base import LeverError
from haltai.backends.sim_environment import (
    SERVICE_SUBNET,
    SG_NETWORK,
    SLOW_SCP,
    SimEnvSettings,
    SimulatedEnvironment,
    _Activity,
)
from haltai.env_scenarios import ENV_SCENARIOS, WARMUP_S, halt_and_observe, run_env_scenario
from haltai.environment import LEVER_ORDER, BoundaryKind, Lever, Profile, load_environment
from haltai.observations import ObservedOutcome

EXAMPLE = Path(__file__).parent.parent / "examples" / "environment.yaml"
Out = ObservedOutcome


@pytest.fixture
def env():
    return load_environment(EXAMPLE)


def warm(env, *flags, seed=7) -> SimulatedEnvironment:
    sim = SimulatedEnvironment(env, SimEnvSettings(seed=seed, scenarios=list(flags)))
    sim.sleep(WARMUP_S)
    return sim


def halt_one(sim, lever, profile=Profile.HALT_AGENTS, settle=60):
    sim.stage(lever, profile)
    sim.apply(lever)
    sim.sleep(settle)
    return sim.observe(lever)


# --- scenario matrix -----------------------------------------------------------------

@pytest.mark.parametrize("name", list(ENV_SCENARIOS))
def test_env_scenario_matrix(env, name):
    s = ENV_SCENARIOS[name]
    _, run = run_env_scenario(env, s)
    assert run.outcomes == s.expected, {lv.value: o.evidence for lv, o in run.observations.items()}
    assert any(c.violation for c in run.collateral) == s.collateral_violation
    assert any(c.in_boundary and not c.healthy for c in run.collateral) == s.in_boundary_impact


def test_agents_are_active_before_any_halt(env):
    sim = warm(env)
    actions = {a.action for a in sim.log if a.allowed}
    assert {"aws_call", "egress", "trigger", "invoke", "task_running"} <= actions
    assert all(c.healthy for c in sim.observe_collateral())


def test_same_seed_is_reproducible(env):
    a = run_env_scenario(env, ENV_SCENARIOS["clean"], seed=3)[1]
    b = run_env_scenario(env, ENV_SCENARIOS["clean"], seed=3)[1]
    assert [o.evidence for o in a.observations.values()] == \
           [o.evidence for o in b.observations.values()]


# --- identity -------------------------------------------------------------------------

def test_identity_takes_effect_after_propagation(env):
    sim = warm(env)
    sim.stage(Lever.IDENTITY, Profile.HALT_AGENTS)
    sim.apply(Lever.IDENTITY)
    agent = sim._role_for("research-agent")
    assert not sim._identity_denies(agent)  # not yet propagated
    sim.sleep(10)
    assert sim._identity_denies(agent)


def test_identity_exempts_execution_role_and_cannot_reach_other_accounts(env):
    sim = warm(env)
    halt_one(sim, Lever.IDENTITY, Profile.ISOLATE)
    assert not sim._identity_denies(env.operator.execution_role_arn)
    assert not sim._identity_denies("arn:aws:iam::555555555555:user/someone")
    assert sim._identity_denies("arn:aws:iam::123456789012:role/metrics-exporter")


def test_halt_agents_spares_non_agent_roles_but_tagged_isolate_stays_selective(env):
    sim = warm(env)
    halt_one(sim, Lever.IDENTITY, Profile.HALT_AGENTS)
    assert not sim._identity_denies("arn:aws:iam::123456789012:role/metrics-exporter")
    env.boundary.kind = BoundaryKind.TAGGED
    env.boundary.tag_selector = {"haltai:scope": "agents"}
    sim2 = warm(env)
    halt_one(sim2, Lever.IDENTITY, Profile.ISOLATE)
    assert not sim2._identity_denies("arn:aws:iam::123456789012:role/metrics-exporter")


# --- network --------------------------------------------------------------------------

def test_nacl_cuts_established_connections(env):
    sim = warm(env)
    obs = halt_one(sim, Lever.NETWORK)
    assert obs.outcome == Out.STOPPED
    assert not sim._egress_allowed(sim._agent_subnet(), new=False)


def test_security_group_keeps_established_connections(env):
    sim = warm(env, SG_NETWORK)
    obs = halt_one(sim, Lever.NETWORK)
    assert obs.outcome == Out.PARTIAL
    assert sim._egress_allowed(sim._agent_subnet(), new=False)
    assert not sim._egress_allowed(sim._agent_subnet(), new=True)
    assert any("established" in e for e in obs.evidence)


def test_isolate_quarantines_service_subnet(env):
    sim = warm(env)
    halt_one(sim, Lever.NETWORK, Profile.ISOLATE)
    assert not sim._egress_allowed(SERVICE_SUBNET, new=True)


# --- workload --------------------------------------------------------------------------

def test_workload_stops_tasks_throttles_lambda_and_release_restores(env):
    sim = warm(env)
    before = {n: (s.desired, s.scaling_suspended) for n, s in sim.services.items()}
    obs = halt_one(sim, Lever.WORKLOAD)
    assert obs.outcome == Out.STOPPED
    assert all(not s.tasks for s in sim.services.values())
    assert set(sim.concurrency.values()) == {0}
    sim.release(Lever.WORKLOAD)
    assert {n: (s.desired, s.scaling_suspended) for n, s in sim.services.items()} == before
    assert set(sim.concurrency.values()) == {None}
    sim.sleep(10)
    assert all(s.tasks for s in sim.services.values())


def test_lambda_concurrency_zero_throttles_invocations(env):
    sim = warm(env)
    halt_one(sim, Lever.WORKLOAD)
    sim.sleep(60)
    since = sim.now() - timedelta(seconds=60)
    recent = [a for a in sim.log if a.action == "invoke" and a.at > since]
    assert recent and not any(a.allowed for a in recent)


# --- intake ----------------------------------------------------------------------------

def test_intake_stops_triggers_but_queue_keeps_filling(env):
    sim = warm(env)
    depth = sim.queue_depth
    obs = halt_one(sim, Lever.INTAKE)
    assert obs.outcome == Out.STOPPED
    assert sim.queue_depth > depth
    assert any("no pause" in r for r in obs.residual)
    sim.release(Lever.INTAKE)
    assert all(sim.rules_enabled.values()) and all(sim.esm_enabled.values())


# --- interface and the honesty rule ------------------------------------------------------

def test_apply_requires_stage_and_observe_requires_apply(env):
    sim = warm(env)
    with pytest.raises(LeverError):
        sim.apply(Lever.IDENTITY)
    sim.stage(Lever.IDENTITY, Profile.HALT_AGENTS)
    with pytest.raises(LeverError):
        sim.observe(Lever.IDENTITY)


def test_uncovered_lever_is_not_covered(env):
    env.workload.ecs_services.clear()
    env.workload.lambda_functions.clear()
    sim = warm(env)
    assert halt_one(sim, Lever.WORKLOAD).outcome == Out.NOT_COVERED


def test_active_control_alone_never_means_stopped(env):
    sim = warm(env, SLOW_SCP)
    obs = halt_one(sim, Lever.IDENTITY)
    assert sim.controls[Lever.IDENTITY].active
    assert obs.outcome == Out.NOT_STOPPED


def test_no_positive_control_is_inconclusive(env):
    sim = SimulatedEnvironment(env)  # no warm-up: nothing has happened yet
    for lv in LEVER_ORDER:
        sim.stage(lv, Profile.HALT_AGENTS)
        sim.apply(lv)
    sim.sleep(60)
    assert {sim.observe(lv).outcome for lv in LEVER_ORDER} == {Out.INCONCLUSIVE}


def test_activity_in_the_window_turns_stopped_into_partial(env):
    sim = warm(env)
    assert halt_one(sim, Lever.IDENTITY).outcome == Out.STOPPED
    sim.log.append(_Activity(sim.now(), "aws_call", "x", "arn:aws:iam::123456789012:role/ghost",
                             "aws-api", True))
    assert sim.observe(Lever.IDENTITY).outcome == Out.PARTIAL


def test_full_halt_then_release_restores_activity_and_collateral(env):
    sim = warm(env)
    run = halt_and_observe(sim, Profile.ISOLATE)
    assert set(run.outcomes.values()) == {Out.STOPPED}
    for lv in reversed(LEVER_ORDER):
        sim.release(lv)
    mark = sim.now()
    sim.sleep(60)
    assert any(a.action == "aws_call" and a.allowed and a.at > mark for a in sim.log)
    assert all(c.healthy for c in sim.observe_collateral())


def test_unknown_flag_is_rejected(env):
    with pytest.raises(ValueError):
        SimulatedEnvironment(env, SimEnvSettings(scenarios=["nope"]))
