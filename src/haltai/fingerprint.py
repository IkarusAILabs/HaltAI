"""Configuration fingerprint: the validity boundary for evidence.

Any change to the bound configuration (the manifest or the live snapshot of the
resources it names) produces a new fingerprint, which invalidates earlier claims.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from haltai.manifest import DeploymentManifest

REVALIDATION_TRIGGERS = [
    "Agent task definition or task role changes",
    "Any IAM policy change on the agent role",
    "Tool gateway resource policy, routes or stage deployment changes",
    "Work-intake queue configuration changes",
    "Protected-effect store (table) or its access policies change",
    "Containment policy or mechanism changes",
]


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def fingerprint(manifest: DeploymentManifest, live_config: dict[str, Any] | None = None) -> str:
    """SHA-256 over the manifest plus any captured live configuration snapshot."""
    payload = {"manifest": manifest.model_dump(mode="json"), "live": live_config or {}}
    return "sha256:" + hashlib.sha256(canonical_json(payload).encode()).hexdigest()
