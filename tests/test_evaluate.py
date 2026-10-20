"""Decision table tests: one test per rule in docs/claims.md."""

from datetime import UTC, datetime, timedelta

from haltai.evaluate import evaluate_claim1, evaluate_claim2
from haltai.models import (
    Claim1Repetition,
    Claim2Check,
    Claim2Run,
    ClaimResult,
    Marker,
    ProbeOutcome,
    ProbeStatus,
    ProbeVia,
)

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def probe(status: ProbeStatus, at: datetime = T0) -> ProbeOutcome:
    return ProbeOutcome(probe_id="p", via=ProbeVia.WORKER, status=status, at=at)


def good_rep(i: int = 1) -> Claim1Repetition:
    return Claim1Repetition(
        index=i, positive_control=probe(ProbeStatus.ALLOWED), activation_ok=True,
        requested_at=T0, first_denied_at=T0 + timedelta(seconds=9),
        interval_upper_bound_s=9, window_probes=[probe(ProbeStatus.DENIED)] * 3,
        recovery=probe(ProbeStatus.ALLOWED))


# ---- claim 1


def test_c1_no_positive_control_is_unknown():
    rep = Claim1Repetition(index=1, positive_control=probe(ProbeStatus.ERROR))
    assert evaluate_claim1([rep], [])[0] == ClaimResult.UNKNOWN


def test_c1_all_good_is_verified():
    assert evaluate_claim1([good_rep(1), good_rep(2)], [], 2)[0] == ClaimResult.VERIFIED


def test_c1_not_denied_is_failed():
    rep = good_rep()
    rep.first_denied_at = None
    assert evaluate_claim1([rep], [])[0] == ClaimResult.FAILED


def test_c1_activation_failure_is_failed():
    rep = good_rep()
    rep.activation_ok = False
    assert evaluate_claim1([rep], [])[0] == ClaimResult.FAILED


def test_c1_flapping_is_failed():
    rep = good_rep()
    rep.window_probes = [probe(ProbeStatus.DENIED), probe(ProbeStatus.ALLOWED)]
    assert evaluate_claim1([rep], [])[0] == ClaimResult.FAILED


def test_c1_write_marker_in_window_is_failed():
    rep = good_rep()
    rep.post_containment_writes = [Marker(marker_id="m", principal="r", source="worker",
                                          initiated_at=T0, written_at=T0)]
    assert evaluate_claim1([rep], [])[0] == ClaimResult.FAILED


def test_c1_cap_is_partial():
    res, why = evaluate_claim1([good_rep()], ["bypass exists"])
    assert res == ClaimResult.PARTIAL and "bypass exists" in why


def test_c1_some_inconclusive_is_partial():
    bad = Claim1Repetition(index=2, positive_control=probe(ProbeStatus.TIMEOUT))
    assert evaluate_claim1([good_rep(), bad], [])[0] == ClaimResult.PARTIAL


def test_c1_fewer_reps_than_planned_is_partial():
    assert evaluate_claim1([good_rep()], [], expected_reps=5)[0] == ClaimResult.PARTIAL


# ---- claim 2


def good_run() -> Claim2Run:
    checks = [Claim2Check(name=n, outcome=probe(ProbeStatus.DENIED), passed=True)
              for n in ("restart_then_probe", "fresh_task_probe", "redeploy_then_probe",
                        "job_retry")]
    return Claim2Run(positive_control=probe(ProbeStatus.ALLOWED), containment_effective=True,
                     requested_at=T0, effective_at=T0 + timedelta(seconds=9), checks=checks)


def test_c2_verified():
    assert evaluate_claim2(good_run(), [])[0] == ClaimResult.VERIFIED


def test_c2_no_positive_control_is_unknown():
    run = good_run()
    run.positive_control = probe(ProbeStatus.ERROR)
    assert evaluate_claim2(run, [])[0] == ClaimResult.UNKNOWN


def test_c2_containment_not_effective_is_unknown():
    run = good_run()
    run.containment_effective = False
    assert evaluate_claim2(run, [])[0] == ClaimResult.UNKNOWN


def test_c2_restart_allowed_is_failed():
    run = good_run()
    run.checks[0] = Claim2Check(name="restart_then_probe", outcome=probe(ProbeStatus.ALLOWED))
    assert evaluate_claim2(run, [])[0] == ClaimResult.FAILED


def test_c2_post_containment_write_is_failed():
    run = good_run()
    run.post_containment_writes = [Marker(marker_id="m", job_id="j", principal="r",
                                          source="worker", initiated_at=T0, written_at=T0)]
    assert evaluate_claim2(run, [])[0] == ClaimResult.FAILED


def test_c2_incomplete_check_is_partial():
    run = good_run()
    run.checks[1] = Claim2Check(name="fresh_task_probe", outcome=probe(ProbeStatus.TIMEOUT))
    assert evaluate_claim2(run, [])[0] == ClaimResult.PARTIAL


def test_c2_residual_work_does_not_fail():
    run = good_run()
    run.residual_writes = [Marker(marker_id="m", principal="r", source="worker",
                                  initiated_at=T0, written_at=T0 + timedelta(seconds=30))]
    run.queued_at_activation = 3
    assert evaluate_claim2(run, [])[0] == ClaimResult.VERIFIED
