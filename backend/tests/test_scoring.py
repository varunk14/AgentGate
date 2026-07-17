"""Tests for grounding-coverage scoring and the decisive total gate (D27).

Extraction (the LLM step) is NOT in this path — grounding is deterministic
regex+Decimal, so decide() runs unmocked with real raw_text.

Mutation checks (a plausible wrong impl MUST redden >=1 test):
  * grounding_coverage hard-coded 1.0                 -> test_partial_coverage_escalates_via_score reddens
  * total gate removed (rely on score_below)          -> test_ungrounded_total_escalates reddens
  * total_not_grounded folded into the soft ratio     -> test_ungrounded_total_escalates score assert reddens
  * denominator includes subtotal / unit_price        -> test_coverage_denominator_is_consumed_fields reddens
"""

from __future__ import annotations

from decimal import Decimal

from agentgate.core.decision import decide, grounding_coverage
from agentgate.core.schemas import (
    DecisionType,
    Invoice,
    LineItem,
    LineItemKind,
    Money,
    ProposedAction,
    TaxLine,
)


def m(value: str, currency: str = "USD") -> Money:
    return Money(value=value, currency=currency)


def action(total: str, currency: str = "USD", vendor: str = "Acme Corp") -> ProposedAction:
    return ProposedAction(action_type="approve_payment", invoice_number="INV-S",
                          amount=m(total, currency), vendor=vendor)


# --- no raw_text -> coverage 1.0 (structured-only) ---------------------------
def test_no_raw_text_scores_one_and_allows():
    inv = Invoice(invoice_number="INV-S", vendor="Acme Corp", date="d", currency="USD",
                  line_items=[LineItem(description="x", quantity=1, unit_price=m("100.00"),
                                       amount=m("100.00"), kind=LineItemKind.charge)],
                  tax_lines=[], total=m("100.00"))
    decision = decide(inv, action("100.00"))
    assert decision.decision is DecisionType.allow
    assert decision.score == Decimal("1.00")
    assert "raw_text" not in decision.evidence_used


# --- denominator = total + line amounts + tax amounts (excludes subtotal/unit_price)
def test_coverage_denominator_is_consumed_fields():
    # consumed money fields: total 100.00, line amount 99.00, tax 1.00 (3 fields).
    # subtotal 77.00 and unit_price 49.50 are NOT consumed and must be excluded.
    inv = Invoice(invoice_number="INV-S", vendor="Acme Corp", date="d", currency="USD",
                  line_items=[LineItem(description="x", quantity=2, unit_price=m("49.50"),
                                       amount=m("99.00"), kind=LineItemKind.charge)],
                  subtotal=m("77.00"),
                  tax_lines=[TaxLine(rate="0.05", amount=m("1.00"))], total=m("100.00"))
    # raw_text has the 3 consumed values, but NOT subtotal 77.00 nor unit_price 49.50.
    raw_text = "Amount 99.00 plus tax 1.00 for a total of 100.00 due."
    # If subtotal/unit_price were counted, this would be 3/5 = 0.60, not 1.
    assert grounding_coverage(inv, raw_text) == Decimal("1")


# --- partial coverage lowers the score -> ESCALATE via score_below -----------
def test_partial_coverage_escalates_via_score():
    # Every check passes and the total IS grounded, but the line amounts are not,
    # so coverage (and thus the score) drops below 0.80 -> escalate on score_below.
    inv = Invoice(invoice_number="INV-S", vendor="Acme Corp", date="d", currency="USD",
                  line_items=[LineItem(description=f"L{i}", quantity=1, unit_price=m("50.00"),
                                       amount=m("50.00"), kind=LineItemKind.charge) for i in range(4)],
                  tax_lines=[], total=m("200.00"))
    raw_text = "Grand total 200.00 due on receipt."  # 200.00 present, 50.00 absent
    decision = decide(inv, action("200.00"), raw_text=raw_text)
    assert decision.score == Decimal("0.20")  # total(1) + 4 ungrounded lines = 1/5
    assert decision.decision is DecisionType.escalate
    assert any(r.check == "policy_score_threshold" for r in decision.reasons)
    assert not any(r.check == "total_not_grounded" for r in decision.reasons)


# --- an ungrounded total escalates decisively, independent of the score ------
def test_ungrounded_total_escalates_regardless_of_coverage():
    # Nine line items of 111.11 sum to the 999.99 total (structurally consistent),
    # all grounded by a single 111.11 token (presence-only, D21). The total itself
    # is NOT in the text -> coverage 9/10 = 0.90, which clears score_below (0.80),
    # yet the decision MUST escalate: a ratio would let a hallucinated total slip.
    inv = Invoice(invoice_number="INV-S", vendor="Acme Corp", date="d", currency="USD",
                  line_items=[LineItem(description=f"L{i}", quantity=1, unit_price=m("111.11"),
                                       amount=m("111.11"), kind=LineItemKind.charge) for i in range(9)],
                  tax_lines=[], total=m("999.99"))
    raw_text = "Each line is billed at 111.11 per unit across the order."
    decision = decide(inv, action("999.99"), raw_text=raw_text)
    assert decision.score == Decimal("0.90")  # proves total gate is NOT in the soft ratio
    assert decision.decision is DecisionType.escalate
    assert any(r.check == "total_not_grounded" for r in decision.reasons)
    # score 0.90 is above 0.80, so this escalation is the total gate, not score_below.
    assert not any(r.check == "policy_score_threshold" for r in decision.reasons)
