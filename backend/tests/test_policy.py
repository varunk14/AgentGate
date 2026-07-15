"""Tests for the typed policy engine (DECISIONS D28).

Policy only ADDS escalations within the precedence (BLOCK > ESCALATE > ALLOW);
it can never open the gate. Thresholds are Decimal, never float (D1).

Mutation checks (a plausible wrong impl MUST redden >=1 test):
  * threshold parsed via Decimal(float) not Decimal(str) -> test_thresholds_are_exact_decimal reddens
  * amount threshold evaluated in the BLOCK branch      -> test_block_beats_amount_threshold reddens
  * critical drift-assert dropped                       -> test_missing_critical_check_rejected reddens
  * block_if silently ignored                           -> test_block_if_rejected reddens
  * amount threshold uses >= instead of >               -> test_amount_at_threshold_allows reddens
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.decision import decide
from app.core.policy import DEFAULT_POLICY, PolicyError, load_policy
from app.core.schemas import (
    DecisionType,
    Invoice,
    LineItem,
    LineItemKind,
    Money,
    ProposedAction,
)

_CRITICAL = "critical_checks:\n  - structural_arithmetic\n  - currency_match\n  - action_amount_matches_total\n"
_RETRY = "retry:\n  max_attempts: 2\n"


def m(value: str, currency: str = "USD") -> Money:
    return Money(value=value, currency=currency)


def consistent_invoice(total: str, currency: str = "USD", vendor: str = "Acme Corp") -> Invoice:
    """One charge line equal to the total -> structurally consistent."""
    return Invoice(
        invoice_number="INV-P", vendor=vendor, date="2026-01-01", currency=currency,
        line_items=[LineItem(description="Item", quantity=1, unit_price=m(total, currency),
                             amount=m(total, currency), kind=LineItemKind.charge)],
        tax_lines=[], total=m(total, currency),
    )


def action_for(total: str, currency: str = "USD", vendor: str = "Acme Corp") -> ProposedAction:
    return ProposedAction(action_type="approve_payment", invoice_number="INV-P",
                          amount=m(total, currency), vendor=vendor)


def _write(tmp_path, body: str):
    path = tmp_path / "policy.yaml"
    path.write_text(body)
    return path


# --- the shipped default policy loads and is Decimal-typed --------------------
def test_default_policy_shape():
    assert DEFAULT_POLICY.amount_greater_than == Decimal("10000")
    assert DEFAULT_POLICY.score_below == Decimal("0.80")
    assert DEFAULT_POLICY.retry.max_attempts == 2
    assert DEFAULT_POLICY.critical_checks == frozenset(
        {"structural_arithmetic", "currency_match", "action_amount_matches_total"}
    )


def test_thresholds_are_exact_decimal_not_float(tmp_path):
    policy = load_policy(_write(
        tmp_path,
        "escalate_if:\n  amount_greater_than: 10000\n  score_below: 0.85\n" + _CRITICAL + _RETRY,
    ))
    # Decimal(0.85) via float is 0.85000000000000008...; Decimal(str(0.85)) is exact.
    assert policy.score_below == Decimal("0.85")
    assert isinstance(policy.score_below, Decimal)
    assert isinstance(policy.amount_greater_than, Decimal)


# --- amount threshold -> ESCALATE, in the non-BLOCK branch only ---------------
def test_amount_over_threshold_escalates():
    decision = decide(consistent_invoice("20000.00"), action_for("20000.00"))
    assert decision.decision is DecisionType.escalate
    assert any(r.check == "policy_amount_threshold" for r in decision.reasons)


def test_amount_at_threshold_allows():
    # exactly 10000 is NOT > 10000 (strict "below/greater-than"), so it allows.
    decision = decide(consistent_invoice("10000.00"), action_for("10000.00"))
    assert decision.decision is DecisionType.allow
    assert decision.reasons == []


def test_block_beats_amount_threshold():
    # amount misread (BLOCK) on a >10000 invoice: BLOCK short-circuits and the
    # amount threshold is never evaluated -> exactly one block reason, no policy one.
    decision = decide(consistent_invoice("20000.00"), action_for("200000.00"))
    assert decision.decision is DecisionType.block
    assert [r.check for r in decision.reasons] == ["action_amount_matches_total"]


# --- loader rejects unsafe / drifted configs (fail-closed) -------------------
def test_missing_critical_check_rejected(tmp_path):
    body = (
        "escalate_if:\n  amount_greater_than: 10000\n"
        "critical_checks:\n  - structural_arithmetic\n  - currency_match\n" + _RETRY
    )
    with pytest.raises(PolicyError):
        load_policy(_write(tmp_path, body))


def test_block_if_rejected(tmp_path):
    body = (
        "escalate_if:\n  amount_greater_than: 10000\n"
        "block_if:\n  any_critical_check_failed: true\n" + _CRITICAL + _RETRY
    )
    with pytest.raises(PolicyError):
        load_policy(_write(tmp_path, body))


def test_missing_file_rejected(tmp_path):
    with pytest.raises(PolicyError):
        load_policy(tmp_path / "does_not_exist.yaml")
