"""Testbed settings, read from CDK context.

Pure Python (no ``aws_cdk`` import), so the main test suite can check it without Node.js.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

PRODUCTION_NAMES = frozenset({"prod", "production", "live"})
VARIANTS = ("dedicated", "shared")

TESTBED_TAG = ("haltai:testbed", "true")
MANAGED_TAG = ("haltai:managed", "true")
SCOPE_TAG = ("haltai:scope", "agents")

# Failure toggles. Each one reproduces the simulated scenario of the same name
# (src/haltai/env_scenarios.py, docs/levers.md) on real AWS.
TOGGLES: dict[str, str] = {
    "sg_change_keeps_connections": (
        "The network lever is staged as a quarantine security group instead of a network ACL, "
        "so connections that are already open survive."),
    "autoscaling_restarts_tasks": (
        "The agent service gets Application Auto Scaling with a minimum of 1 task, which "
        "restores tasks after a scale-to-zero unless scaling is suspended."),
    "schedule_outside_boundary": (
        "An extra EventBridge Scheduler schedule, not listed in environment.yaml, starts agent "
        "tasks every 5 minutes."),
    "untracked_credential": (
        "An IAM user with a long-lived access key (kept in Secrets Manager) that the agent task "
        "uses for its AWS calls: a credential outside the declared roles."),
    "shared_nat": (
        "Shared variant only: the out-of-boundary service runs in the agent subnets and shares "
        "their NAT, so a network quarantine interrupts it."),
    "agent_can_assume_execution_role": (
        "The HaltAI execution role also trusts the agent task role (Claim 3 on the boundary)."),
    "agent_can_modify_execution_role": (
        "The agent task role may change the execution role's policies (Claim 3 on the "
        "boundary)."),
}
SHARED_ONLY = frozenset({"shared_nat"})

_ACCOUNT = re.compile(r"^\d{12}$")
_REGION = re.compile(r"^[a-z]{2}(-[a-z]+)+-\d$")


@dataclass(frozen=True)
class BedConfig:
    variant: str = "dedicated"
    environment: str = "sandbox"
    region: str = "eu-west-2"
    agent_account: str = "111111111111"
    tooling_account: str = "222222222222"
    management_account: str = "333333333333"
    # Stub endpoints live in the tooling account for the dedicated variant, so the agent
    # stack is told their URLs after the tooling stack is deployed.
    model_stub_url: str = "https://model-stub.invalid/"
    tool_stub_url: str = "https://tool-stub.invalid/"
    recovery_owner: str = "platform-oncall@example.com"
    toggles: frozenset[str] = frozenset()
    canary_endpoints: bool = True

    def __post_init__(self) -> None:
        errors = self.errors()
        if errors:
            raise ValueError("; ".join(errors))

    def errors(self) -> list[str]:
        out = []
        if self.variant not in VARIANTS:
            out.append(f"variant must be one of {', '.join(VARIANTS)}, not {self.variant!r}")
        if self.environment.lower() in PRODUCTION_NAMES:
            out.append(f"environment {self.environment!r} is a production name; the testbed "
                       "is for disposable non-production accounts only")
        if not _REGION.match(self.region):
            out.append(f"region {self.region!r} is not an AWS region name")
        accounts = [("agent_account", self.agent_account)]
        if self.variant == "dedicated":
            accounts += [("tooling_account", self.tooling_account),
                         ("management_account", self.management_account)]
        for name, value in accounts:
            if not _ACCOUNT.match(value):
                out.append(f"{name} must be a 12-digit account ID")
        if self.variant == "dedicated" and len({v for _, v in accounts}) != len(accounts):
            out.append("the dedicated variant needs three different accounts: agent, tooling "
                       "and management")
        unknown = sorted(self.toggles - TOGGLES.keys())
        if unknown:
            out.append(f"unknown toggles: {', '.join(unknown)}")
        if self.variant == "dedicated" and self.toggles & SHARED_ONLY:
            out.append(f"{', '.join(sorted(self.toggles & SHARED_ONLY))} applies to the shared "
                       "variant only")
        return out

    @classmethod
    def from_context(cls, get: Callable[[str], Any]) -> BedConfig:
        """Build from CDK context (``app.node.try_get_context``)."""
        kw: dict[str, Any] = {}
        for name in ("variant", "environment", "region", "agent_account", "tooling_account",
                     "management_account", "model_stub_url", "tool_stub_url",
                     "recovery_owner"):
            value = get(name)
            if value is not None:
                kw[name] = str(value)
        toggles = get("toggles")
        if toggles:
            items = toggles.split(",") if isinstance(toggles, str) else list(toggles)
            kw["toggles"] = frozenset(t.strip() for t in items if t.strip())
        endpoints = get("canary_endpoints")
        if endpoints is not None:
            kw["canary_endpoints"] = str(endpoints).lower() not in ("false", "0", "no")
        return cls(**kw)

    def on(self, toggle: str) -> bool:
        assert toggle in TOGGLES, toggle
        return toggle in self.toggles

    @property
    def dedicated(self) -> bool:
        return self.variant == "dedicated"

    @property
    def execution_role_arn(self) -> str:
        return f"arn:aws:iam::{self.agent_account}:role/haltai/haltai-execution"

    @property
    def agent_roles_pattern(self) -> str:
        return f"arn:aws:iam::{self.agent_account}:role/agents/*"

    @property
    def operator_role_arn(self) -> str:
        account = self.tooling_account if self.dedicated else self.agent_account
        return f"arn:aws:iam::{account}:role/haltai-operator"
