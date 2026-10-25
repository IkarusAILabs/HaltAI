"""End-to-end drills against the simulated backend."""

from pathlib import Path

import pytest

from haltai.backends import make_backend
from haltai.config import DrillSettings, HaltAIConfig
from haltai.drill import assess
from haltai.manifest import load_manifest
from haltai.report import render_markdown
from haltai.scenarios import SCENARIOS

EXAMPLE = Path(__file__).parent.parent / "examples" / "deployment.yaml"


def run(scenarios: list[str], drill: DrillSettings | None = None, claims=("1", "2", "3"),
        seed=7):
    cfg = HaltAIConfig()
    cfg.simulation.seed = seed
    b = make_backend(cfg, load_manifest(EXAMPLE), scenarios)
    return b, assess(b, drill or cfg.drill, set(claims), scenarios)


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_scenario_matrix(name):
    s = SCENARIOS[name]
    _, r = run([name])
    c1, c2, c3 = r.claims
    assert c1.result == s.claim1, c1.rationale
    assert c2.result == s.claim2, c2.rationale
    assert c3.result == s.claim3, c3.rationale
    assert bool(r.collateral_impact) == s.collateral_impact
    assert any(c.residual_work_items for c in r.claims) == s.residual_exposure
    assert (r.recovery_verified is not False) == s.recovery_verified


def test_clean_run_has_all_evidence_levels_and_intervals():
    _, r = run([])
    c1 = r.claims[0]
    levels = {e.level.value for e in c1.evidence}
    assert levels == {"configured", "boundary_assessed", "enforcement_tested",
                      "observed_after_activation"}
    assert len(c1.measured_intervals_s) == 5
    assert all(0 < x <= 20 for x in c1.measured_intervals_s)


def test_containment_always_rolled_back():
    b, r = run([])
    assert not b.containment_active()
    assert r.recovery_verified is True


def test_same_seed_is_reproducible():
    _, a = run([], seed=11)
    _, b = run([], seed=11)
    assert a.claims[0].measured_intervals_s == b.claims[0].measured_intervals_s


def test_budget_exceeded_aborts_and_says_so():
    drill = DrillSettings(max_drill_duration_s=100)
    b, r = run([], drill=drill)
    assert any(lim.field == "drill" for lim in r.limitations)
    assert not b.containment_active()


def test_combined_scenarios():
    _, r = run(["bypass_direct_write", "local_flag_containment"])
    assert [c.result.value for c in r.claims] == ["partial", "failed", "verified"]


def test_claim3_is_static_and_does_not_activate():
    b, r = run(["agent_can_lift_deny"], claims=("3",))
    (c3,) = r.claims
    assert c3.result.value == "failed"
    assert not b.w.policy_changes  # no containment was requested
    assert any("UpdateRestApiPolicy" in w for w in c3.rationale)


def test_clean_claim3_is_boundary_assessed_only():
    _, r = run([], claims=("3",))
    (c3,) = r.claims
    assert {e.level.value for e in c3.evidence} == {"boundary_assessed"}


def test_markdown_report_mentions_simulation_and_no_safe_badge():
    _, r = run([])
    md = render_markdown(r)
    assert "simulated backend" in md
    assert "containable" not in md.lower()
