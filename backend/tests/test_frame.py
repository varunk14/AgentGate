"""Tests for the frame stage — the prior gate that confirms the action IS the
thing the content checks verify (DECISIONS D31/D32).

Two frame checks, both critical, both ESCALATE (never agent_fixable):
  * action_type_supported   — action.action_type == approve_payment
  * invoice_number_match     — action.invoice_number == invoice.invoice_number
A frame failure ESCALATES, carries the frame rows ONLY (no content row runs on
non-comparable inputs), and sets score None (nothing content-verified). The
identifier is whitespace-normalized at the schema boundary (so the duplicate
store keys on the normalized value); the check is then an exact match.

Mutation checks (a plausible wrong impl MUST redden >=1 test):
  * frame check hard-coded to pass            -> test_reject_escalates / test_wrong_invoice_number_escalates redden
  * action_type routed as agent_fixable BLOCK -> test_reject_escalates decision assert reddens
  * frame short-circuit moved into the stage fn (truncates the union)
                                              -> test_critical_check_names_includes_frame reddens
  * content stage runs despite a frame failure (precedence not inverted)
                                              -> test_frame_failure_suppresses_content_block reddens
  * expected set to invoice_number instead of None (invites a fixer)
                                              -> test_frame_reasons_are_unfixable reddens
  * score set to 0/1.00 instead of None on a frame escalate
                                              -> test_frame_escalate_score_is_none reddens
  * schema strip dropped (store-key collision) -> test_invoice_number_is_stripped reddens
  * min_length dropped                        -> test_blank_invoice_number_rejected reddens
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.decision import decide
from app.core.schemas import (
    DecisionType,
    Invoice,
    LineItem,
    LineItemKind,
    Money,
    ProposedAction,
)
from app.core.verifier import critical_check_names


def m(value: str, currency: str = "USD") -> Money:
    return Money(value=value, currency=currency)


def make_invoice(*, invoice_number: str = "INV-001", total: str = "100.00") -> Invoice:
    """A structurally consistent single-charge invoice (frame-valid inputs)."""
    return Invoice(
        invoice_number=invoice_number, vendor="Acme Corp", date="2026-01-15", currency="USD",
        line_items=[LineItem(description="Item", quantity=1, unit_price=m(total),
                             amount=m(total), kind=LineItemKind.charge)],
        tax_lines=[], total=m(total),
    )


def make_action(
    *, action_type: str = "approve_payment", invoice_number: str = "INV-001",
    amount: str = "100.00", vendor: str = "Acme Corp",
) -> ProposedAction:
    return ProposedAction(action_type=action_type, invoice_number=invoice_number,
                          amount=m(amount), vendor=vendor)


_CONTENT_NAMES = {
    "structural_arithmetic", "currency_match", "action_amount_matches_total",
    "vendor_match", "duplicate_check",
}
_FRAME_NAMES = {"action_type_supported", "invoice_number_match"}


# --- a valid frame is transparent: content checks run, both frame rows show ---
def test_valid_frame_passes_through_to_content():
    decision = decide(make_invoice(), make_action())
    assert decision.decision is DecisionType.allow
    names = [c.name for c in decision.checks]
    # frame rows lead, then the content rows — an ALLOW honestly shows the frame
    # was verified too.
    assert names[:2] == ["action_type_supported", "invoice_number_match"]
    assert _CONTENT_NAMES.issubset(set(names))
    assert decision.score is not None  # content path computed a real score


# --- action_type: reject / flag are out of frame -> ESCALATE, frame rows only -
@pytest.mark.parametrize("bad_type", ["reject", "flag"])
def test_unsupported_action_type_escalates(bad_type):
    decision = decide(make_invoice(), make_action(action_type=bad_type))
    assert decision.decision is DecisionType.escalate
    # frame rows ONLY — no content check ran on a non-approval
    assert set(c.name for c in decision.checks) == _FRAME_NAMES
    assert not (_CONTENT_NAMES & set(c.name for c in decision.checks))
    assert any(r.check == "action_type_supported" for r in decision.reasons)


def test_reject_is_not_allowed():
    # The exact bug the frame stage exists for: a reject with a matching amount +
    # vendor used to return ALLOW (the gate approving a rejection).
    decision = decide(make_invoice(), make_action(action_type="reject"))
    assert decision.decision is not DecisionType.allow


# --- invoice_number mismatch -> ESCALATE -------------------------------------
def test_wrong_invoice_number_escalates():
    decision = decide(make_invoice(invoice_number="INV-001"),
                      make_action(invoice_number="INV-999"))
    assert decision.decision is DecisionType.escalate
    assert set(c.name for c in decision.checks) == _FRAME_NAMES
    r = next(r for r in decision.reasons if r.check == "invoice_number_match")
    assert r.received == "INV-999"
    assert "INV-999" in r.message and "INV-001" in r.message  # human sees both


# --- both frame checks always run: a doubly-wrong action shows both rows ------
def test_both_frame_failures_show_both_rows():
    decision = decide(make_invoice(invoice_number="INV-001"),
                      make_action(action_type="reject", invoice_number="INV-999"))
    assert decision.decision is DecisionType.escalate
    failed = {c.name for c in decision.checks if not c.passed}
    assert failed == _FRAME_NAMES  # both rows present AND both failed
    reasons = {r.check for r in decision.reasons}
    assert reasons == _FRAME_NAMES


# --- the prior-gate inversion: a frame failure suppresses a content BLOCK -----
def test_frame_failure_suppresses_content_block():
    # Wrong invoice_number (frame ESCALATE) AND an amount misread that would BLOCK
    # on the content stage. The frame is a prior gate, so the decision ESCALATES
    # (never BLOCK) and the amount check never runs — BLOCKing here would tell the
    # agent to align its amount to an invoice nobody validated.
    decision = decide(make_invoice(invoice_number="INV-001", total="100.00"),
                      make_action(invoice_number="INV-999", amount="10000.00"))
    assert decision.decision is DecisionType.escalate
    assert not any(r.check == "action_amount_matches_total" for r in decision.reasons)
    assert not any(r.block_type is not None for r in decision.reasons)  # no block


# --- frame reasons are structurally unfixable (never agent_fixable) -----------
def test_frame_reasons_are_unfixable():
    for action in (make_action(action_type="reject"), make_action(invoice_number="INV-999")):
        decision = decide(make_invoice(), action)
        for r in decision.reasons:
            assert r.block_type is None
            assert r.field_to_change is None
            assert r.expected is None  # None, not the source value, so no fixer copies it


# --- score is None (not 0/1.00) on a frame escalate: nothing was measured -----
def test_frame_escalate_score_is_none():
    decision = decide(make_invoice(), make_action(action_type="reject"))
    assert decision.score is None


# --- identifier normalization lives in the schema (fixes the store key) -------
def test_invoice_number_is_stripped_at_schema_boundary():
    action = make_action(invoice_number="  INV-001  ")
    assert action.invoice_number == "INV-001"  # stripped before it reaches any check
    invoice = Invoice(invoice_number="  INV-001  ", vendor="Acme", date="d", currency="USD",
                      line_items=[LineItem(description="x", quantity=1, unit_price=m("100.00"),
                                           amount=m("100.00"), kind=LineItemKind.charge)],
                      total=m("100.00"))
    assert invoice.invoice_number == "INV-001"
    # a whitespace-only difference therefore matches -> frame passes -> allow
    assert decide(make_invoice(), action).decision is DecisionType.allow


def test_case_and_punctuation_are_significant():
    # D8: only surrounding whitespace is lossless; case/punctuation stay meaningful.
    decision = decide(make_invoice(invoice_number="INV-001"),
                      make_action(invoice_number="inv-001"))
    assert decision.decision is DecisionType.escalate


@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
def test_blank_invoice_number_rejected(blank):
    with pytest.raises(ValidationError):
        make_action(invoice_number=blank)
    with pytest.raises(ValidationError):
        Invoice(invoice_number=blank, vendor="Acme", date="d", currency="USD",
                total=m("100.00"))


# --- the drift tripwire: frame checks are part of the critical set -----------
def test_critical_check_names_includes_frame():
    names = critical_check_names()
    assert _FRAME_NAMES.issubset(names)
    # the union is untruncated: content criticals are still present alongside
    assert {"structural_arithmetic", "currency_match", "action_amount_matches_total"}.issubset(names)
