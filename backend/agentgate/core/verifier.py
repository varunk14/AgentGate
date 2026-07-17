"""Deterministic verification checks (DECISIONS D3/D8/D12/D13/D15).

Pure functions over a caller-supplied structured Invoice + ProposedAction. No
LLM in the path — money is ``Decimal`` throughout. Each check returns a
``CheckOutcome``: the check row plus how it routes (ok / block / escalate). The
decision layer applies precedence to the routes.

Routing (locked design):
  * structural_arithmetic fail  -> ESCALATE (source_invalid; the source is broken)
  * currency mismatch           -> ESCALATE (no FX in v1, D12)
  * amount != total, no adjustment declared -> BLOCK (agent_fixable, D13)
  * amount != total, adjustment declared    -> ESCALATE (unverifiable, D13)
  * vendor non-exact after cosmetic normalize -> ESCALATE (entity ambiguity, D8)
  * duplicate invoice_number    -> ESCALATE (possible double-pay)
"""

from __future__ import annotations

import re
from decimal import Decimal
from enum import Enum
from typing import Optional

from pydantic import BaseModel

from .schemas import (
    ActionType,
    BlockReason,
    BlockType,
    Check,
    CheckKind,
    Invoice,
    LineItem,
    LineItemKind,
    Money,
    ProposedAction,
)

# Reserved for real vendor rounding only — NEVER for masking float error (D1/D15).
TOLERANCE = Decimal("0.01")


class Route(str, Enum):
    ok = "ok"
    block = "block"
    escalate = "escalate"


class CheckOutcome(BaseModel):
    check: Check
    route: Route
    reason: Optional[BlockReason] = None


def _ok(name: str, kind: CheckKind, detail: str) -> CheckOutcome:
    return CheckOutcome(check=Check(name=name, type=kind, passed=True, detail=detail), route=Route.ok)


def check_structural_arithmetic(invoice: Invoice) -> CheckOutcome:
    """`(Σ charges incl. shipping − Σ discounts + Σ tax across rates) == total`,
    within $0.01 (D15). Failure means the source is internally inconsistent →
    ESCALATE as ``source_invalid`` (not retryable, D7)."""
    name = "structural_arithmetic"
    cur = invoice.currency

    if not invoice.line_items and not invoice.tax_lines:
        # A total-only invoice is UNVERIFIABLE, not inconsistent: there is
        # nothing to check the stated total against. Escalate with no
        # block_type — source_invalid would wrongly tell a human the vendor's
        # document is broken (D15 boundary).
        return CheckOutcome(
            check=Check(
                name=name, type=CheckKind.critical, passed=False,
                detail="no line items or tax lines to verify against the total",
            ),
            route=Route.escalate,
            reason=BlockReason(
                check=name,
                message=(
                    f"Invoice {invoice.invoice_number} has no line items or tax "
                    "lines; the stated total cannot be verified against the "
                    "invoice structure. Route to a human."
                ),
            ),
        )

    charges = Decimal("0")
    discounts = Decimal("0")
    tax = Decimal("0")
    for li in invoice.line_items:
        if li.amount.currency != cur:
            return _mixed_currency_source(name, li.amount.currency, cur)
        if li.kind in (LineItemKind.charge, LineItemKind.shipping):
            charges += li.amount.value
        elif li.kind == LineItemKind.discount:
            discounts += li.amount.value
        elif li.kind == LineItemKind.tax:
            tax += li.amount.value
    for tl in invoice.tax_lines:
        if tl.amount.currency != cur:
            return _mixed_currency_source(name, tl.amount.currency, cur)
        tax += tl.amount.value

    computed = charges - discounts + tax
    detail = f"(charges {charges} − discounts {discounts} + tax {tax}) = {computed} vs total {invoice.total.value}"
    if abs(computed - invoice.total.value) <= TOLERANCE:
        return _ok(name, CheckKind.critical, detail)
    return CheckOutcome(
        check=Check(name=name, type=CheckKind.critical, passed=False, detail=detail),
        route=Route.escalate,
        reason=BlockReason(
            check=name,
            expected=invoice.total,
            received=Money(value=computed, currency=cur),
            field_to_change="source.invoice",
            block_type=BlockType.source_invalid,
            message=(
                f"Invoice line items sum to {computed} but the stated total is "
                f"{invoice.total.value}. The source is internally inconsistent; "
                "route to a human (agent retry cannot fix the source)."
            ),
        ),
    )


def _mixed_currency_source(name: str, found: str, expected: str) -> CheckOutcome:
    return CheckOutcome(
        check=Check(
            name=name, type=CheckKind.critical, passed=False,
            detail=f"line/tax amount in {found}, invoice currency {expected}",
        ),
        route=Route.escalate,
        reason=BlockReason(
            check=name,
            field_to_change="source.invoice",
            block_type=BlockType.source_invalid,
            message=(
                f"Source mixes currencies ({found} vs invoice {expected}); "
                "cannot verify arithmetic. Route to a human."
            ),
        ),
    )


def check_currency_match(invoice: Invoice, action: ProposedAction) -> CheckOutcome:
    """`action.currency == invoice.currency`, exact, no conversion (D12).
    Mismatch → ESCALATE (v1 does no FX)."""
    name = "currency_match"
    if action.amount.currency == invoice.currency:
        return _ok(name, CheckKind.critical, f"{action.amount.currency} == {invoice.currency}")
    return CheckOutcome(
        check=Check(
            name=name, type=CheckKind.critical, passed=False,
            detail=f"{action.amount.currency} != {invoice.currency}",
        ),
        route=Route.escalate,
        reason=BlockReason(
            check=name,
            expected=Money(value=action.amount.value, currency=invoice.currency),
            received=action.amount,
            field_to_change="proposed_action.amount.currency",
            message=(
                f"Action currency {action.amount.currency} does not match invoice "
                f"currency {invoice.currency}. AgentGate performs no FX in v1; "
                "route to a human."
            ),
        ),
    )


def check_amount_matches_total(invoice: Invoice, action: ProposedAction) -> CheckOutcome:
    """`action.amount == invoice.total` exactly ⇒ ALLOW-eligible. Otherwise split
    on whether an adjustment was declared (D13): none → BLOCK (agent_fixable);
    declared → ESCALATE (unverifiable in v1)."""
    name = "action_amount_matches_total"
    if action.amount.value == invoice.total.value:
        return _ok(name, CheckKind.critical, f"{action.amount.value} == {invoice.total.value}")

    detail = f"{action.amount.value} != total {invoice.total.value}"
    if not action.adjustments:
        return CheckOutcome(
            check=Check(name=name, type=CheckKind.critical, passed=False, detail=detail),
            route=Route.block,
            reason=BlockReason(
                check=name,
                expected=invoice.total,
                received=action.amount,
                field_to_change="proposed_action.amount",
                block_type=BlockType.agent_fixable,
                message=(
                    f"Proposed amount {action.amount.value} does not match invoice "
                    f"total {invoice.total.value}. Set proposed_action.amount to "
                    f"{invoice.total.value}."
                ),
            ),
        )
    return CheckOutcome(
        check=Check(name=name, type=CheckKind.critical, passed=False, detail=detail),
        route=Route.escalate,
        reason=BlockReason(
            check=name,
            expected=invoice.total,
            received=action.amount,
            field_to_change="proposed_action.amount",
            message=(
                f"Proposed amount {action.amount.value} differs from total "
                f"{invoice.total.value} with a declared adjustment. Adjustments are "
                "not verified in v1; route to a human."
            ),
        ),
    )


_TRAILING_PUNCT = re.compile(r"[.,;:'\"\s]+$")
_WHITESPACE = re.compile(r"\s+")


def _normalize_vendor(v: str) -> str:
    """Cosmetic normalization only (D8): case, whitespace, trailing punctuation.
    Lossless/unambiguous — NOT entity-level canonicalization."""
    v = _WHITESPACE.sub(" ", v.strip().lower())
    return _TRAILING_PUNCT.sub("", v)


def check_vendor_match(invoice: Invoice, action: ProposedAction) -> CheckOutcome:
    """Exact match after cosmetic normalization (D8). Any non-exact match →
    ESCALATE (possible different legal entity); never auto-allow/block."""
    name = "vendor_match"
    if _normalize_vendor(action.vendor) == _normalize_vendor(invoice.vendor):
        return _ok(name, CheckKind.soft, f"vendor matches after cosmetic normalization")
    return CheckOutcome(
        check=Check(
            name=name, type=CheckKind.soft, passed=False,
            detail=f"{action.vendor!r} != {invoice.vendor!r} after normalization",
        ),
        route=Route.escalate,
        reason=BlockReason(
            check=name,
            expected=invoice.vendor,
            received=action.vendor,
            field_to_change="proposed_action.vendor",
            message=(
                f"Action vendor {action.vendor!r} does not match invoice vendor "
                f"{invoice.vendor!r} after cosmetic normalization. Possible different "
                "legal entity; route to a human."
            ),
        ),
    )


def check_duplicate(invoice: Invoice, is_duplicate: bool) -> CheckOutcome:
    """Soft check: `invoice_number` already approved in the state store → ESCALATE
    (possible double-payment)."""
    name = "duplicate_check"
    if not is_duplicate:
        return _ok(name, CheckKind.soft, f"{invoice.invoice_number} not previously approved")
    return CheckOutcome(
        check=Check(
            name=name, type=CheckKind.soft, passed=False,
            detail=f"{invoice.invoice_number} already approved",
        ),
        route=Route.escalate,
        reason=BlockReason(
            check=name,
            field_to_change=None,
            message=(
                f"Invoice {invoice.invoice_number} has already been approved; "
                "possible double-payment. Route to a human."
            ),
        ),
    )


# --- Frame stage (D31) -------------------------------------------------------
# The content checks above verify one thing: an approve_payment action against
# the invoice it names. The frame checks below confirm the action IS that thing
# before those checks run. Both are critical and ESCALATE on failure — never
# agent_fixable: a frame mismatch has two opposite fixes (right action / wrong
# evidence attached, vs right evidence / typo'd action) and AgentGate cannot tell
# which, so it fails BLOCK's criterion (D3). expected/field_to_change/block_type
# stay None (structurally unfixable); the human-facing comparison is in message.


def check_action_type_supported(action: ProposedAction) -> CheckOutcome:
    """Frame check: v1 verifies ``approve_payment`` only. ``flag``/``reject`` are
    out of frame (unsupported, not malformed) → ESCALATE."""
    name = "action_type_supported"
    if action.action_type == ActionType.approve_payment:
        return _ok(name, CheckKind.critical, f"action_type {action.action_type.value} is verifiable in v1")
    return CheckOutcome(
        check=Check(
            name=name, type=CheckKind.critical, passed=False,
            detail=f"action_type {action.action_type.value} is out of frame (v1 verifies approve_payment only)",
        ),
        route=Route.escalate,
        reason=BlockReason(
            check=name,
            received=action.action_type.value,
            message=(
                f"Action type {action.action_type.value!r} is out of frame: AgentGate v1 "
                "verifies payment approvals only, not flags or rejections. Route to a human."
            ),
        ),
    )


def check_invoice_number_match(invoice: Invoice, action: ProposedAction) -> CheckOutcome:
    """Frame check: the action must reference the invoice it is being checked
    against. Exact match on values already whitespace-normalized at the schema
    boundary (§6/D8). Mismatch → ESCALATE (not agent_fixable, D31)."""
    name = "invoice_number_match"
    if action.invoice_number == invoice.invoice_number:
        return _ok(name, CheckKind.critical, f"action references invoice {invoice.invoice_number}")
    return CheckOutcome(
        check=Check(
            name=name, type=CheckKind.critical, passed=False,
            detail=f"action {action.invoice_number!r} != invoice {invoice.invoice_number!r}",
        ),
        route=Route.escalate,
        reason=BlockReason(
            check=name,
            received=action.invoice_number,
            message=(
                f"Action references invoice {action.invoice_number!r} but the attached "
                f"invoice is {invoice.invoice_number!r}. AgentGate cannot tell whether the "
                "wrong evidence was attached or the action's number is a typo; route to a human."
            ),
        ),
    )


def run_frame_checks(invoice: Invoice, action: ProposedAction) -> list[CheckOutcome]:
    """Run the frame stage (D31). ALWAYS returns both rows — no internal short-
    circuit — so a doubly-wrong action shows both, and ``critical_check_names()``
    (which unions this with ``run_checks``) cannot be truncated. The short-circuit
    (skip content checks on a frame failure) lives in ``decide()``, not here."""
    return [
        check_action_type_supported(action),
        check_invoice_number_match(invoice, action),
    ]


def run_checks(
    invoice: Invoice, action: ProposedAction, *, is_duplicate: bool = False
) -> list[CheckOutcome]:
    """Run all deterministic content checks in order and return their outcomes.

    The frame stage (``run_frame_checks``) runs before these in ``decide()``; a
    frame failure suppresses this stage entirely (checks run on non-comparable
    inputs otherwise)."""
    return [
        check_structural_arithmetic(invoice),
        check_currency_match(invoice, action),
        check_amount_matches_total(invoice, action),
        check_vendor_match(invoice, action),
        check_duplicate(invoice, is_duplicate),
    ]


def critical_check_names() -> frozenset[str]:
    """The check names the verifier emits as ``CheckKind.critical`` — the union of
    the frame stage and the content stage, derived from actual ``run_frame_checks``
    + ``run_checks`` calls so the policy drift-assertion (D28) cannot silently fall
    out of sync with the code. Both stage functions always return their full row
    set (the short-circuit is in ``decide()``, not here), so this union cannot be
    truncated (D31). A check's ``type`` is invariant of pass/fail, so the sample's
    values are irrelevant — only the emitted name set matters."""
    sample_invoice = Invoice(
        invoice_number="_",
        vendor="_",
        date="_",
        currency="USD",
        line_items=[
            LineItem(
                description="_",
                quantity=1,
                unit_price=Money(value="1", currency="USD"),
                amount=Money(value="1", currency="USD"),
                kind=LineItemKind.charge,
            )
        ],
        total=Money(value="1", currency="USD"),
    )
    sample_action = ProposedAction(
        action_type=ActionType.approve_payment,
        invoice_number="_",
        amount=Money(value="1", currency="USD"),
        vendor="_",
    )
    outcomes = run_frame_checks(sample_invoice, sample_action) + run_checks(sample_invoice, sample_action)
    return frozenset(
        outcome.check.name
        for outcome in outcomes
        if outcome.check.type is CheckKind.critical
    )
