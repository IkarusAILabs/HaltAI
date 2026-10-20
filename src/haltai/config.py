"""Run configuration (``haltai.yaml``).

The manifest says *what* is assessed. This file says *how* HaltAI runs:
which backend, drill timing and, for the simulated backend, which failure
scenarios to inject. Switching from simulation to AWS is one line::

    backend: aws
"""

from __future__ import annotations

import os
from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class BackendKind(StrEnum):
    SIMULATED = "simulated"
    AWS = "aws"


class AwsSettings(BaseModel):
    profile: str | None = None
    region: str | None = None  # defaults to the manifest region


class DrillSettings(BaseModel):
    repetitions: int = Field(default=5, ge=1)
    poll_interval_s: float = Field(default=2.0, gt=0)
    deny_timeout_s: float = Field(default=120.0, gt=0)
    observation_window_s: float = Field(default=60.0, ge=0)
    max_drill_duration_s: float = Field(default=1800.0, gt=0)
    worker_ready_timeout_s: float = Field(default=300.0, gt=0)
    reports_dir: str = "reports"


class SimulationSettings(BaseModel):
    seed: int = 7
    propagation_delay_s: float = 8.0
    jitter_s: float = 3.0
    worker_restart_s: float = 6.0
    async_write_delay_s: float = 20.0
    scenarios: list[str] = Field(default_factory=list)


class HaltAIConfig(BaseModel):
    backend: BackendKind = BackendKind.SIMULATED
    manifest: str = "deployment.yaml"
    aws: AwsSettings = Field(default_factory=AwsSettings)
    drill: DrillSettings = Field(default_factory=DrillSettings)
    simulation: SimulationSettings = Field(default_factory=SimulationSettings)

    def manifest_path(self, base: Path) -> Path:
        p = Path(self.manifest)
        return p if p.is_absolute() else base / p


def load_config(path: str | Path | None = None) -> tuple[HaltAIConfig, Path]:
    """Load ``haltai.yaml``. Missing file means defaults. ``HALTAI_BACKEND`` overrides."""
    cfg_path = Path(path) if path else Path(os.environ.get("HALTAI_CONFIG", "haltai.yaml"))
    data: dict = {}
    if cfg_path.exists():
        with open(cfg_path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    if env_backend := os.environ.get("HALTAI_BACKEND"):
        data["backend"] = env_backend
    cfg = HaltAIConfig.model_validate(data)
    return cfg, cfg_path.resolve().parent
