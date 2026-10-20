"""Backend selection. ``backend:`` in haltai.yaml picks one."""

from __future__ import annotations

from haltai.backends.base import Backend, BackendError
from haltai.config import BackendKind, HaltAIConfig
from haltai.manifest import DeploymentManifest

__all__ = ["Backend", "BackendError", "make_backend"]


def make_backend(cfg: HaltAIConfig, manifest: DeploymentManifest,
                 scenarios: list[str] | None = None) -> Backend:
    if cfg.backend == BackendKind.SIMULATED:
        from haltai.backends.simulated import SimulatedBackend

        settings = cfg.simulation
        if scenarios is not None:
            settings = settings.model_copy(update={"scenarios": scenarios})
        return SimulatedBackend(manifest, settings)
    if cfg.backend == BackendKind.AWS:
        from haltai.backends.aws import AwsBackend

        return AwsBackend.from_config(manifest, cfg.aws, cfg.drill)
    raise BackendError(f"Unknown backend {cfg.backend}")
