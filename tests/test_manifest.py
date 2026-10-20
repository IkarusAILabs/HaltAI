from datetime import UTC, datetime, timedelta
from pathlib import Path

from haltai.fingerprint import fingerprint
from haltai.manifest import ContainmentMechanism, check_prerequisites, load_manifest
from haltai.models import Claim, ClaimId, ClaimResult

EXAMPLE = Path(__file__).parent.parent / "examples" / "deployment.yaml"


def test_example_manifest_meets_prerequisites():
    assert check_prerequisites(load_manifest(EXAMPLE)) == []


def test_shared_identity_is_a_blocking_limitation():
    m = load_manifest(EXAMPLE)
    m.identity.dedicated = False
    lims = {i.field: i for i in check_prerequisites(m)}
    assert lims["identity.dedicated"].blocking


def test_production_is_a_limitation():
    m = load_manifest(EXAMPLE)
    m.authorisation.environment = "Production"
    assert any(i.field == "authorisation.environment" for i in check_prerequisites(m))


def test_local_flag_mechanism_is_flagged():
    m = load_manifest(EXAMPLE)
    m.containment_policy.mechanism = ContainmentMechanism.LOCAL_FLAG
    assert any(i.field == "containment_policy.mechanism" for i in check_prerequisites(m))


def test_fingerprint_changes_with_manifest_and_live_config():
    m = load_manifest(EXAMPLE)
    a = fingerprint(m)
    assert a.startswith("sha256:")
    assert a != fingerprint(m, {"iam": {"statements": ["x"]}})
    m.tool_gateway.endpoint = "https://other.internal"
    assert a != fingerprint(m)


def test_declared_paths_cover_every_binding():
    paths = " ".join(load_manifest(EXAMPLE).declared_paths)
    for word in ("intake", "identity", "runtime", "gateway", "store"):
        assert word in paths


def test_claim_defaults_to_unknown_and_measures_interval():
    t0 = datetime.now(UTC)
    c = Claim(
        id=ClaimId.NEW_WRITES_DENIED,
        statement="New writes via gateway are denied",
        deployment_fingerprint="sha256:x",
        protected_effect="synthetic_write",
        declared_paths=["gateway"],
        containment_requested_at=t0,
        containment_effective_at=t0 + timedelta(seconds=4),
        measured_intervals_s=[4, 6, 5],
    )
    assert c.result is ClaimResult.UNKNOWN
    assert c.measured_interval_seconds == 4
    assert c.interval_stats == {"min": 4, "median": 5, "max": 6, "n": 3}
