"""Environment backend interface: the four levers, driven by the break-glass commands.

``prepare`` stages levers, ``halt`` applies them, ``status`` observes them and
``release`` releases them. The simulated environment implements this interface now;
AWS adapters implement it in release 0.5.0.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from haltai.environment import EnvironmentDeclaration, Lever, Profile
from haltai.observations import CollateralObservation, LeverObservation


class LeverError(RuntimeError):
    pass


class EnvironmentBackend(ABC):
    name: str = "base"
    is_real: bool = False

    def __init__(self, env: EnvironmentDeclaration):
        self.env = env

    # ---- clock -------------------------------------------------------------------
    @abstractmethod
    def now(self) -> datetime: ...

    @abstractmethod
    def sleep(self, seconds: float) -> None: ...

    # ---- levers ------------------------------------------------------------------
    @abstractmethod
    def stage(self, lever: Lever, profile: Profile) -> None:
        """Record the pre-halt state and prepare the control without activating it."""

    @abstractmethod
    def apply(self, lever: Lever) -> None:
        """Activate a staged control. Raises LeverError if it was not staged."""

    @abstractmethod
    def release(self, lever: Lever) -> None:
        """Deactivate the control and restore the recorded pre-halt state."""

    @abstractmethod
    def observe(self, lever: Lever) -> LeverObservation:
        """What the lever achieved, from effects only. Read-only."""

    @abstractmethod
    def observe_collateral(self) -> list[CollateralObservation]:
        """Health of the declared in-boundary and out-of-boundary services."""
