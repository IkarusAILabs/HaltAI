"""Claim 3 catalogue and escape-path rules."""

from pathlib import Path

from haltai.escape import EscapeKind, escape_catalogue, find_escape_paths
from haltai.manifest import ContainmentMechanism, load_manifest

EXAMPLE = Path(__file__).parent.parent / "examples" / "deployment.yaml"


def catalogue(mechanism=ContainmentMechanism.GATEWAY_RESOURCE_POLICY_DENY):
    m = load_manifest(EXAMPLE)
    m.containment_policy.mechanism = mechanism
    return escape_catalogue(m, m.region)


def test_lift_actions_follow_the_mechanism():
    gw = {a.action for a in catalogue() if a.kind == EscapeKind.LIFT_CONTAINMENT}
    iam = {a.action for a in catalogue(ContainmentMechanism.IAM_INLINE_DENY)
           if a.kind == EscapeKind.LIFT_CONTAINMENT}
    assert "apigateway:UpdateRestApiPolicy" in gw and "iam:DeleteRolePolicy" not in gw
    assert iam == {"iam:DeleteRolePolicy"}


def test_compute_without_pass_role_is_not_an_escape():
    cat = catalogue()
    decisions = {a.label: a.action == "ecs:RunTask" for a in cat}
    assert find_escape_paths(cat, decisions).paths == []


def test_compute_with_pass_role_is_an_escape():
    cat = catalogue()
    decisions = {a.label: a.action in ("ecs:RunTask", "iam:PassRole") for a in cat}
    (path,) = find_escape_paths(cat, decisions).paths
    assert path.startswith("iam:PassRole + ecs:RunTask")


def test_missing_decisions_are_unknown_not_denied():
    cat = catalogue()
    res = find_escape_paths(cat, {cat[0].label: False})
    assert res.evaluated == 1 and len(res.unknown) == len(cat) - 1 and not res.paths
