"""Typed policy engine (DECISIONS D28).

Loads ``policies/default.yaml`` into a ``Policy`` and exposes ``DEFAULT_POLICY``.
Policy only ever ADDS escalation triggers within the existing precedence
(BLOCK > ESCALATE > ALLOW, D3); it never relaxes the deterministic core — a
reliability gate whose safety a config typo can switch off is not a gate
(fail-closed).

Hard rules honoured here:
  * All numeric thresholds are ``Decimal``, never float (D1). ``yaml.safe_load``
    turns ``0.80`` into a Python float, so every threshold is converted through
    ``str()`` before it can reach a comparison.
  * ``critical_checks`` is an ASSERTED spec/code drift tripwire — the loader
    raises ``PolicyError`` unless it exactly equals the checks the verifier marks
    ``CheckKind.critical``.
  * There is deliberately no ``block_if.any_critical_check_failed`` knob (that
    pre-D3 "any critical => BLOCK" rule is false); a config carrying ``block_if``
    is rejected rather than silently ignored.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from .verifier import critical_check_names

DEFAULT_POLICY_PATH = Path(__file__).resolve().parents[1] / "policies" / "default.yaml"


class PolicyError(ValueError):
    """A policy file is malformed, or would weaken the gate. Fail-closed: a bad
    policy stops the service starting; it never silently opens the gate."""


class RetryPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    # max_attempts is the only live retry knob. There is no on_cap_exhausted:
    # cap exhaustion always routes to a human, hardcoded in retry.py (D28) — an
    # inert config key is worse than none.
    max_attempts: int


class Policy(BaseModel):
    """Escalation thresholds + the asserted critical-check set + retry config."""

    model_config = ConfigDict(frozen=True)

    amount_greater_than: Optional[Decimal] = None
    score_below: Optional[Decimal] = None
    critical_checks: frozenset[str]
    retry: RetryPolicy

    @field_validator("amount_greater_than", "score_below", mode="before")
    @classmethod
    def _threshold_to_decimal(cls, v: object) -> object:
        """Convert thresholds to ``Decimal`` via ``str()`` — never through a
        float (D1). A YAML ``0.80`` arrives as a float; ``Decimal(str(0.80))``
        is exactly ``0.80``, whereas ``Decimal(0.80)`` would be
        ``0.8000000000000000444...``."""
        if v is None:
            return None
        if isinstance(v, bool):
            raise ValueError("threshold must be a number, not a bool.")
        if isinstance(v, Decimal):
            return v
        if isinstance(v, (int, float, str)):
            return Decimal(str(v))
        raise ValueError(f"threshold must be a number, got {type(v).__name__}.")


def load_policy(path: Path = DEFAULT_POLICY_PATH) -> Policy:
    """Load and validate a policy file. Raises ``PolicyError`` on any problem —
    missing file, invalid YAML, bad shape, a ``block_if`` knob, or a
    ``critical_checks`` set that does not match the verifier's critical checks
    (spec/code drift)."""
    try:
        raw = yaml.safe_load(path.read_text())
    except FileNotFoundError as exc:
        raise PolicyError(f"policy file not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise PolicyError(f"policy file is not valid YAML: {exc}") from exc

    if not isinstance(raw, dict):
        raise PolicyError("policy file must be a YAML mapping.")

    if "block_if" in raw:
        raise PolicyError(
            "block_if is not supported: 'any critical failure => BLOCK' is false — "
            "only an agent-fixable critical failure BLOCKs, others ESCALATE (D3). "
            "Remove block_if."
        )

    escalate_if = raw.get("escalate_if") or {}
    retry = raw.get("retry") or {}
    if not isinstance(escalate_if, dict) or not isinstance(retry, dict):
        raise PolicyError("escalate_if and retry must be mappings.")

    try:
        policy = Policy(
            amount_greater_than=escalate_if.get("amount_greater_than"),
            score_below=escalate_if.get("score_below"),
            critical_checks=frozenset(raw.get("critical_checks") or ()),
            retry=RetryPolicy(max_attempts=retry.get("max_attempts")),
        )
    except (ValidationError, ValueError) as exc:
        raise PolicyError(f"invalid policy: {exc}") from exc

    expected = critical_check_names()
    if policy.critical_checks != expected:
        raise PolicyError(
            f"critical_checks {sorted(policy.critical_checks)} does not match the "
            f"verifier's critical checks {sorted(expected)}: spec/code drift. Update "
            "the policy and the verifier together."
        )

    return policy


# Loaded once at import. A missing or drift-inconsistent policy fails loudly here
# (fail-closed) rather than letting the service run with no/weakened policy.
DEFAULT_POLICY = load_policy()
