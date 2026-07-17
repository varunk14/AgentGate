"""The decision layer: ``decide()`` and the fail-closed factory.

``decide()`` assembles the ALLOW/BLOCK/ESCALATE Decision from the frame stage,
the deterministic content checks, grounding coverage, and the policy
thresholds. It is pure and fully deterministic — regex and Decimal only, no
LLM anywhere in the verification path (the only model call in the system is
the demo agent's proposal step). ``fail_closed_decision`` converts any
inability to verify into a valid escalate Decision (D11/D34): never a crash,
never an allow the gate cannot back.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional, Sequence

from pydantic import ValidationError

from .grounding import is_grounded
from .policy import DEFAULT_POLICY, Policy
from .schemas import (
    BlockReason,
    Decision,
    DecisionType,
    Invoice,
    Money,
    ProposedAction,
)
from .verifier import CheckOutcome, Route, run_checks, run_frame_checks


def _coverage_fields(invoice: Invoice) -> list[Decimal]:
    """The money values a deterministic check consumes, for grounding coverage
    (D27): the ``total`` plus every ``line_item.amount`` plus every
    ``tax_line.amount`` — the values ``structural_arithmetic`` and
    ``action_amount_matches_total`` read. ``subtotal``, ``unit_price`` and
    ``quantity`` are excluded: no check consumes them, so grounding them would
    move the ratio without measuring anything the decision depends on."""
    fields = [invoice.total.value]
    fields += [li.amount.value for li in invoice.line_items]
    fields += [tl.amount.value for tl in invoice.tax_lines]
    return fields


def grounding_coverage(invoice: Invoice, raw_text: str) -> Decimal:
    """Fraction of the invoice's check-consumed money fields (``_coverage_fields``)
    that ground in ``raw_text`` (D27). Value-level and currency-blind, inheriting
    ``is_grounded`` (D21): grounding is PRESENCE-only, so two line items both
    ``200.00`` are BOTH satisfied by a single ``200.00`` token in the text — that
    is correct and intended (grounding proves presence, not position); do not
    "fix" it into token-consumption counting. Denominator is >= 1 (``total`` is
    always present)."""
    fields = _coverage_fields(invoice)
    grounded = sum(1 for value in fields if is_grounded(value, raw_text))
    return Decimal(grounded) / Decimal(len(fields))


def _score(outcomes: list[CheckOutcome], coverage: Decimal) -> Decimal:
    """score = (soft-check pass ratio) × (grounding coverage) (D2/D16).

    Critical checks are a separate hard gate and are NOT in the score. ``coverage``
    is 1.0 for a structured source with no ``raw_text`` in the path (nothing to
    ground against; the LLM never touched it)."""
    soft = [o for o in outcomes if o.check.type.value == "soft"]
    if soft:
        passed = sum(1 for o in soft if o.check.passed)
        ratio = Decimal(passed) / Decimal(len(soft))
    else:
        ratio = Decimal(1)
    return (ratio * coverage).quantize(Decimal("0.01"))


def decide(
    invoice: Invoice,
    action: ProposedAction,
    *,
    policy: Policy = DEFAULT_POLICY,
    raw_text: Optional[str] = None,
    is_duplicate: bool = False,
) -> Decision:
    """Assemble a real ALLOW/BLOCK/ESCALATE Decision from the deterministic checks
    and the policy thresholds.

    Precedence BLOCK > ESCALATE > ALLOW (D3): an agent-fixable failure short-
    circuits to BLOCK; policy thresholds and the total-grounding gate are
    evaluated ONLY in the non-BLOCK branch, so config can add escalations but
    never override a block. Pure and LLM-free — grounding (``is_grounded``) is
    deterministic; extraction happens upstream, at the API boundary. trace_id/
    timestamp/latency are set there too.

    The frame stage (D31) runs first as a PRIOR GATE: if the action is not an
    approve_payment against the invoice it names, ESCALATE with the frame rows
    only (no content check runs on non-comparable inputs) and ``score`` None (D32).
    BLOCK > ESCALATE > ALLOW governs only the content stage, which runs on a valid
    frame.
    """
    # Frame stage: prior gate ahead of the precedence ladder (D31).
    frame_outcomes = run_frame_checks(invoice, action)
    frame_reasons = [o.reason for o in frame_outcomes if o.route == Route.escalate and o.reason]
    if frame_reasons:
        return Decision(
            decision=DecisionType.escalate,
            score=None,  # nothing content-verified — not 0 (D32)
            checks=[o.check for o in frame_outcomes],
            reasons=frame_reasons,
            evidence_used=_evidence(invoice, raw_text),
            proposed_action=action,
        )

    outcomes = run_checks(invoice, action, is_duplicate=is_duplicate)
    # Content-path checks table leads with the (passed) frame rows so an ALLOW
    # honestly shows that action_type and invoice_number were verified too.
    checks = [o.check for o in frame_outcomes] + [o.check for o in outcomes]
    block_reasons = [o.reason for o in outcomes if o.route == Route.block and o.reason]
    escalate_reasons = [o.reason for o in outcomes if o.route == Route.escalate and o.reason]

    # Grounding coverage (D27): only when raw_text is supplied; else 1.0.
    coverage = Decimal("1.0") if raw_text is None else grounding_coverage(invoice, raw_text)
    score = _score(outcomes, coverage)

    if block_reasons:
        return Decision(
            decision=DecisionType.block,
            score=score,
            checks=checks,
            reasons=block_reasons,
            evidence_used=_evidence(invoice, raw_text),
            proposed_action=action,
        )

    # Non-BLOCK branch: collect every escalate trigger (check routes, the decisive
    # total-grounding gate, and the policy thresholds).
    reasons = list(escalate_reasons)

    # Decisive total-grounding gate (D27): an ungrounded total escalates
    # regardless of coverage — a ratio would dilute it on a large invoice and let
    # a hallucinated total (the exact thing D4 grounding catches) slip past
    # score_below. Kept out of the soft-check ratio so it does not double-count.
    if raw_text is not None and not is_grounded(invoice.total.value, raw_text):
        reasons.append(
            BlockReason(
                check="total_not_grounded",
                expected=invoice.total,
                message=(
                    f"Invoice total {invoice.total.value} does not appear as a money "
                    "value in the provided source text; the extracted total may be "
                    "hallucinated. Route to a human."
                ),
            )
        )

    # Policy thresholds (D28): amount ceiling and grounding-coverage score floor.
    if policy.amount_greater_than is not None and action.amount.value > policy.amount_greater_than:
        reasons.append(
            BlockReason(
                check="policy_amount_threshold",
                expected=Money(value=policy.amount_greater_than, currency=action.amount.currency),
                received=action.amount,
                message=(
                    f"Proposed amount {action.amount.value} exceeds the configured "
                    f"escalation threshold {policy.amount_greater_than}. Route to a human."
                ),
            )
        )
    if policy.score_below is not None and score < policy.score_below:
        reasons.append(
            BlockReason(
                check="policy_score_threshold",
                message=(
                    f"Grounding-coverage score {score} is below the configured "
                    f"threshold {policy.score_below}. Route to a human."
                ),
            )
        )

    result = DecisionType.escalate if reasons else DecisionType.allow
    return Decision(
        decision=result,
        score=score,
        checks=checks,
        reasons=reasons,
        evidence_used=_evidence(invoice, raw_text),
        proposed_action=action,
    )


def _evidence(invoice: Invoice, raw_text: Optional[str]) -> list[str]:
    evidence = [f"invoice:{invoice.invoice_number}"]
    if raw_text is not None:
        evidence.append("raw_text")
    return evidence


# --- Fail-closed factory (PRD SS9, D34) ----------------------------------------

FAIL_CLOSED_CHECK = "fail_closed"
_MAX_DETAILS_PER_ERROR = 5
_MAX_DETAIL_CHARS = 200
_MAX_ERROR_CHARS = 300
_TRUNCATION_MARK = "…[truncated]"


def _truncated(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + _TRUNCATION_MARK


def _formatted_error(error: Exception | str) -> str:
    """One bounded, human-actionable line per input error.

    ValidationErrors are formatted from field locations and pydantic messages
    only (``include_input=False`` — never the raw input value), capped at
    ``_MAX_DETAILS_PER_ERROR`` details of ``_MAX_DETAIL_CHARS`` each with a
    "+N more" tail; anything else is stringified and capped. The cap is
    load-bearing, not cosmetic: schema validators interpolate the offending
    value into their message, so an unbounded junk field would otherwise ride
    the error text into Langfuse and the dashboard (D34).
    """
    if isinstance(error, ValidationError):
        errs = error.errors(include_url=False, include_input=False)
        details = []
        for err in errs[:_MAX_DETAILS_PER_ERROR]:
            loc = ".".join(str(part) for part in err["loc"]) or "(body)"
            details.append(_truncated(f"{loc}: {err['msg']}", _MAX_DETAIL_CHARS))
        remaining = len(errs) - _MAX_DETAILS_PER_ERROR
        if remaining > 0:
            details.append(f"and {remaining} more validation errors")
        return f"{error.title} failed validation: " + "; ".join(details)
    if isinstance(error, str):
        return _truncated(error, _MAX_ERROR_CHARS)
    return _truncated(f"{type(error).__name__}: {error}", _MAX_ERROR_CHARS)


def fail_closed_decision(errors: Sequence[Exception | str]) -> Decision:
    """A valid ESCALATE Decision for input that could not be parsed or verified.

    The API boundary catches its errors (ValidationError, source-of-record
    failure, undecodable body) and calls this instead of crashing — fail closed,
    never a 5xx, never ALLOW (PRD SS9, D34). Pure: no HTTP, no I/O.

    The shape is fixed: ``score`` None (nothing was measured, D32); ``checks``
    and ``evidence_used`` empty (no check ran, nothing was verified — a
    synthetic check row would claim otherwise); ``proposed_action`` None (there
    is no VALIDATED action to echo). One ``fail_closed`` reason per error with
    ``block_type``/``field_to_change``/``expected`` all None: not agent-fixable
    (nothing mechanical to fix) and not source_invalid (the source is
    unreadable, not proven inconsistent).

    ``errors`` must be non-empty — a reason-less fail-closed Decision would be
    an unexplained escalate, which is a bug at the caller, so raise loud.
    """
    if not errors:
        raise ValueError("fail_closed_decision requires at least one error.")
    reasons = [
        BlockReason(
            check=FAIL_CLOSED_CHECK,
            message=f"{_formatted_error(error).rstrip('.')}. Route to a human.",
        )
        for error in errors
    ]
    return Decision(
        decision=DecisionType.escalate,
        score=None,
        checks=[],
        reasons=reasons,
        evidence_used=[],
        proposed_action=None,
    )
