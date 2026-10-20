"""Pre-flight guards for drills. Containment is disruptive by design."""

from __future__ import annotations

from haltai.backends.base import Backend
from haltai.manifest import PRODUCTION_NAMES
from haltai.models import AssessmentLimitation


class SafetyError(RuntimeError):
    pass


def preflight(
    backend: Backend,
    limitations: list[AssessmentLimitation],
    confirm: bool,
    accept_limitations: bool,
) -> None:
    """Raise SafetyError if a drill must not start.

    The simulated backend only checks the account binding, so failure scenarios such as
    a shared identity can be demonstrated. Real backends get every guard.
    """
    m = backend.manifest
    account = backend.caller_account()
    if account != m.aws_account_id:
        raise SafetyError(f"Caller account {account} does not match manifest account "
                          f"{m.aws_account_id}. Refusing to run.")
    if not backend.is_real:
        return
    if m.authorisation.environment.lower() in PRODUCTION_NAMES:
        raise SafetyError("v1 drills never run against production.")
    if not m.authorisation.disruptive_tests_allowed:
        raise SafetyError("authorisation.disruptive_tests_allowed is false in the manifest.")
    if not confirm:
        raise SafetyError("Real drills need --confirm. This activates containment on "
                          f"{m.identity.role_arn} in account {m.aws_account_id}.")
    blocking = [lim for lim in limitations if lim.blocking]
    if blocking and not accept_limitations:
        listed = "; ".join(f"{lim.field}: {lim.reason}" for lim in blocking)
        raise SafetyError(f"Blocking limitations: {listed}. Fix them or pass "
                          "--accept-limitations.")
