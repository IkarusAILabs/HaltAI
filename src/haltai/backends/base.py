"""Backend interface.

Everything HaltAI does to a deployment goes through this interface, so the drill
engine is identical for the simulated world and real AWS.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from haltai.escape import EscapeAction
from haltai.manifest import DeploymentManifest
from haltai.models import Marker, ProbeOutcome


class BackendError(RuntimeError):
    pass


class Backend(ABC):
    name: str = "base"
    is_real: bool = False

    def __init__(self, manifest: DeploymentManifest):
        self.manifest = manifest

    # ---- clock -------------------------------------------------------------------
    @abstractmethod
    def now(self) -> datetime: ...

    @abstractmethod
    def sleep(self, seconds: float) -> None: ...

    # ---- read-only inspection ----------------------------------------------------
    @abstractmethod
    def caller_account(self) -> str: ...

    @abstractmethod
    def snapshot(self) -> dict[str, Any]:
        """Live configuration of every bound resource (feeds the fingerprint)."""

    @abstractmethod
    def role_consumers(self) -> list[str]:
        """Workloads that run with the agent role (dedicated-identity check)."""

    @abstractmethod
    def direct_effect_allowed(self) -> dict[str, bool]:
        """For each direct action on the effect store: can the agent role do it?"""

    @abstractmethod
    def escape_actions_allowed(self, actions: list[EscapeAction]) -> dict[str, bool | None]:
        """Claim 3: for each action, can the agent role perform it? None if not evaluated."""

    @abstractmethod
    def containment_prestaged(self) -> tuple[bool, str]:
        """Can the containment mechanism be applied to the agent principal only?"""

    def pending_gateway_changes(self) -> list[str] | None:
        """Gateway changes that activation would publish as a side effect.

        Empty when there are none or the mechanism does not redeploy the gateway;
        None when they could not be compared.
        """
        return []

    # ---- containment -------------------------------------------------------------
    @abstractmethod
    def activate_containment(self) -> None: ...

    @abstractmethod
    def rollback_containment(self) -> None: ...

    @abstractmethod
    def containment_active(self) -> bool: ...

    # ---- probes ------------------------------------------------------------------
    @abstractmethod
    def probe_write_via_worker(self) -> ProbeOutcome: ...

    @abstractmethod
    def probe_write_fresh_task(self) -> ProbeOutcome: ...

    @abstractmethod
    def probe_collateral(self) -> ProbeOutcome: ...

    # ---- runtime -----------------------------------------------------------------
    @abstractmethod
    def restart_worker(self) -> None:
        """Stop running agent tasks and wait for replacements."""

    @abstractmethod
    def force_redeploy(self) -> None: ...

    @abstractmethod
    def enqueue_job(self, job_id: str) -> None: ...

    @abstractmethod
    def queue_depth(self) -> int:
        """Jobs accepted into the work-intake path but not yet completed."""

    @abstractmethod
    def list_markers(self, since: datetime) -> list[Marker]: ...
