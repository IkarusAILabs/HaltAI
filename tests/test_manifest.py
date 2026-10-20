from datetime import UTC, datetime, timedelta
from pathlib import Path

from haltai.fingerprint import fingerprint
from haltai.manifest import check_prerequisites, load_manifest
from haltai.models import Claim, ClaimId, ClaimResult

EXAMPLE = Path(__file__).parent.parent / "examples" / "deployment.yaml"


def test_example_manifest_meets_prerequisites():
    assert check_prerequisites(load_manifest(EXAMPLE)) == []


def test_shared_identity_is_a_limitation():
    m = load_manifest(EXAMPLE)
    m.identity.dedicated = False
    fields = [i.field for i in check_prerequisites(m)]
    assert "identity.dedicated" in fields


def test_production_is_a_limitation():
    m = load_manifest(EXAMPLE)
    m.authorisation.environment = "production"
    assert any(i.field == "authorisation.environment" for i in check_prerequisites(m))


def test_fingerprint_changes_with_config():
    m = load_manifest(EXAMPLE)
    a = fingerprint(m)
    m.tool_gateway.endpoint = "https://other.internal"
    assert a != fingerprint(m)
    assert a.startswith("sha256:")


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
    )
    assert c.result is ClaimResult.UNKNOWN
    assert c.measured_interval_seconds == 4
