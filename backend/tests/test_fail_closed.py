"""Fail-closed factory + caller-text bounds (PRD SS6/SS9, D34).

Two halves of one data boundary:
1. ``fail_closed_decision`` converts "could not parse/verify the input" into a
   valid ESCALATE Decision — never a crash, never an allow — without echoing
   unbounded caller text into reasons (which flow to Langfuse + the dashboard).
2. Every caller-supplied text field is length-bounded at the schema, and a junk
   numeric string surfaces as a ValidationError (never a raw InvalidOperation
   that would bypass an ``except ValidationError`` fail-closed catch).
"""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from agentgate.core.decision import fail_closed_decision
from agentgate.core.extractor import ExtractionError
from agentgate.core.schemas import DecisionType, Invoice, Money, ProposedAction


def invoice_payload(**overrides) -> dict:
    payload = {
        "invoice_number": "INV-001",
        "vendor": "Acme Corp",
        "date": "2026-01-15",
        "currency": "USD",
        "line_items": [
            {
                "description": "Widget",
                "quantity": "1",
                "unit_price": {"value": "1240.00", "currency": "USD"},
                "amount": {"value": "1240.00", "currency": "USD"},
                "kind": "charge",
            }
        ],
        "tax_lines": [],
        "total": {"value": "1240.00", "currency": "USD"},
    }
    payload.update(overrides)
    return payload


def action_payload(**overrides) -> dict:
    payload = {
        "action_type": "approve_payment",
        "invoice_number": "INV-001",
        "amount": {"value": "1240.00", "currency": "USD"},
        "vendor": "Acme Corp",
        "adjustments": [],
        "agent_rationale": "Totals match.",
    }
    payload.update(overrides)
    return payload


def validation_error_from(model, payload) -> ValidationError:
    with pytest.raises(ValidationError) as excinfo:
        model.model_validate(payload)
    return excinfo.value


# --- fail_closed_decision (PRD SS9) -------------------------------------------


def test_validation_error_becomes_valid_escalate_decision():
    # float total: rejected by the Money no-float validator (D1)
    exc = validation_error_from(
        Invoice, invoice_payload(total={"value": 1240.0, "currency": "USD"})
    )
    decision = fail_closed_decision([exc])
    assert decision.decision == DecisionType.escalate
    assert decision.score is None  # not computed — never 0, never a number (D32)
    assert decision.checks == []  # no check ran; an empty table is the honest value
    assert decision.evidence_used == []
    assert decision.proposed_action is None
    assert decision.trace_id is None
    assert decision.latency_ms is None
    assert decision.timestamp is None
    (reason,) = decision.reasons
    assert reason.check == "fail_closed"
    assert reason.block_type is None
    assert reason.field_to_change is None
    assert reason.expected is None
    assert reason.received is None
    # the message names the model and the failing field so a human can act
    assert "Invoice" in reason.message
    assert "total" in reason.message


def test_unbounded_exception_text_is_truncated_not_echoed():
    junk = "J" * 10_000
    decision = fail_closed_decision([RuntimeError(junk)])
    (reason,) = decision.reasons
    assert junk not in reason.message
    assert len(reason.message) < 500
    assert "truncated" in reason.message


def test_plain_string_error_is_truncated():
    junk = "s" * 10_000
    decision = fail_closed_decision([junk])
    (reason,) = decision.reasons
    assert junk not in reason.message
    assert len(reason.message) < 500


def test_junk_field_value_never_reaches_the_message():
    junk = "x" * 9_999
    exc = validation_error_from(
        Invoice, invoice_payload(total={"value": junk, "currency": "USD"})
    )
    decision = fail_closed_decision([exc])
    (reason,) = decision.reasons
    assert junk not in reason.message
    assert "x" * 100 not in reason.message  # no long prefix of it either
    assert len(reason.message) < 1_500


def test_many_validation_errors_are_capped_with_a_tail():
    bad_item = {
        "description": "W",
        "quantity": "1",
        "unit_price": {"value": 1.0, "currency": "USD"},  # float -> error
        "amount": {"value": 1.0, "currency": "USD"},  # float -> error
        "kind": "charge",
    }
    exc = validation_error_from(
        Invoice, invoice_payload(line_items=[dict(bad_item) for _ in range(30)])
    )
    assert exc.error_count() >= 30
    decision = fail_closed_decision([exc])
    (reason,) = decision.reasons
    assert "more validation error" in reason.message
    assert len(reason.message) < 1_500


def test_one_reason_per_error_mixing_kinds():
    exc = validation_error_from(ProposedAction, action_payload(action_type="pay"))
    decision = fail_closed_decision(
        [exc, ExtractionError("router timed out"), "request body was not JSON"]
    )
    assert [r.check for r in decision.reasons] == ["fail_closed"] * 3
    assert decision.decision == DecisionType.escalate
    assert "router timed out" in decision.reasons[1].message
    assert "request body was not JSON" in decision.reasons[2].message


def test_empty_errors_is_a_programmer_error():
    # A reason-less fail-closed Decision would be an unexplained escalate.
    with pytest.raises(ValueError):
        fail_closed_decision([])


def test_decision_is_api_ready_json():
    exc = validation_error_from(Invoice, {"invoice_number": "INV-1"})
    payload = fail_closed_decision([exc]).model_dump(mode="json")
    assert payload["decision"] == "escalate"
    assert payload["score"] is None
    assert payload["checks"] == []
    assert payload["proposed_action"] is None


# --- caller-text bounds (PRD SS6) ----------------------------------------------


def test_vendor_bounded_on_both_models():
    assert Invoice.model_validate(invoice_payload(vendor="V" * 200)).vendor == "V" * 200
    with pytest.raises(ValidationError):
        Invoice.model_validate(invoice_payload(vendor="V" * 201))
    assert (
        ProposedAction.model_validate(action_payload(vendor="V" * 200)).vendor
        == "V" * 200
    )
    with pytest.raises(ValidationError):
        ProposedAction.model_validate(action_payload(vendor="V" * 201))


def test_agent_rationale_bounded():
    ok = ProposedAction.model_validate(action_payload(agent_rationale="r" * 2000))
    assert len(ok.agent_rationale) == 2000
    with pytest.raises(ValidationError):
        ProposedAction.model_validate(action_payload(agent_rationale="r" * 2001))


def test_line_item_description_bounded():
    item = {
        "description": "d" * 501,
        "quantity": "1",
        "unit_price": {"value": "1.00", "currency": "USD"},
        "amount": {"value": "1.00", "currency": "USD"},
        "kind": "charge",
    }
    with pytest.raises(ValidationError):
        Invoice.model_validate(invoice_payload(line_items=[item]))


def test_invoice_number_bounded_after_strip():
    long_id = "I" * 100
    ok = Invoice.model_validate(invoice_payload(invoice_number=f"  {long_id}  "))
    assert ok.invoice_number == long_id  # strip still applies; 100 passes
    with pytest.raises(ValidationError):
        Invoice.model_validate(invoice_payload(invoice_number="I" * 101))
    with pytest.raises(ValidationError):
        ProposedAction.model_validate(action_payload(invoice_number="I" * 101))


def test_date_and_currency_bounded():
    assert Invoice.model_validate(invoice_payload(date="d" * 50))
    with pytest.raises(ValidationError):
        Invoice.model_validate(invoice_payload(date="d" * 51))
    with pytest.raises(ValidationError):
        Invoice.model_validate(invoice_payload(currency="C" * 11))
    with pytest.raises(ValidationError):
        # Invoice.currency is now stripped + non-empty, matching Money.currency
        Invoice.model_validate(invoice_payload(currency="   "))


def test_numeric_strings_capped_before_parse():
    assert Money(value="9" * 50, currency="USD").value == Decimal("9" * 50)
    exc = validation_error_from(
        Invoice, invoice_payload(total={"value": "9" * 51, "currency": "USD"})
    )
    # the cap message reports the length, never the value itself
    assert "9" * 51 not in exc.errors()[0]["msg"]
    item = {
        "description": "d",
        "quantity": "1" * 51,
        "unit_price": {"value": "1.00", "currency": "USD"},
        "amount": {"value": "1.00", "currency": "USD"},
        "kind": "charge",
    }
    with pytest.raises(ValidationError):
        Invoice.model_validate(invoice_payload(line_items=[item]))
    with pytest.raises(ValidationError):
        Invoice.model_validate(
            invoice_payload(
                tax_lines=[
                    {"rate": "5" * 51, "amount": {"value": "1.00", "currency": "USD"}}
                ]
            )
        )


def test_junk_tax_rate_is_a_validation_error_not_a_crash():
    # Red-first proof of the leak: before 6.5(c) a junk rate string escaped
    # model_validate as a raw decimal.InvalidOperation, which would bypass an
    # `except ValidationError` fail-closed catch at the API boundary (PRD SS6).
    with pytest.raises(ValidationError):
        Invoice.model_validate(
            invoice_payload(
                tax_lines=[
                    {"rate": "not-a-rate", "amount": {"value": "1.00", "currency": "USD"}}
                ]
            )
        )


def test_adjustments_bounded_but_shape_agnostic():
    twenty = [{"type": "withholding", "value": "1.00"}] * 20
    ok = ProposedAction.model_validate(action_payload(adjustments=twenty))
    assert len(ok.adjustments) == 20
    with pytest.raises(ValidationError):
        ProposedAction.model_validate(
            action_payload(adjustments=twenty + [{"type": "x"}])
        )
    with pytest.raises(ValidationError):
        ProposedAction.model_validate(action_payload(adjustments=[{"note": "n" * 600}]))
    with pytest.raises(ValidationError):
        ProposedAction.model_validate(action_payload(adjustments=[object()]))
