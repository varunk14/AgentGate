"""Grounding-only decision layer (DECISIONS D19).

Output is a GROUNDING RESULT, not allow/block/escalate: the deterministic
allow/block/escalate decision is added once those checks exist. A grounding
result means "the number appears in the source," never "the payment is correct."
Keeping the output honest this way proves extract -> ground -> decide runs end to
end without masquerading as a trustworthy approval.

Fail-closed (D11): any extraction/router failure yields ``ungroundable`` — never
a crash, never a false ``grounded``.
"""

from __future__ import annotations

from decimal import Decimal
from enum import Enum
from typing import Callable, Optional

from pydantic import BaseModel, ConfigDict

from .extractor import ExtractionError, extract_total
from .grounding import is_grounded
from .llm_router import call_llm
from .schemas import Decision, DecisionType, Invoice, ProposedAction
from .verifier import CheckOutcome, Route, run_checks


class GroundingResult(str, Enum):
    grounded = "grounded"
    not_grounded = "not_grounded"
    ungroundable = "ungroundable"


class GroundingOutcome(BaseModel):
    """The grounding verdict plus what was extracted (if anything) and a
    human-readable detail."""

    model_config = ConfigDict(frozen=True)

    result: GroundingResult
    extracted_value: Optional[Decimal] = None
    currency: Optional[str] = None
    detail: str = ""


def assess_grounding(
    raw_text: str, *, llm_call: Callable[[str], str] = call_llm
) -> GroundingOutcome:
    """Extract the total from ``raw_text`` and check it is grounded in that text.

    Returns ``ungroundable`` if extraction fails (fail-closed, D11); otherwise
    ``grounded`` / ``not_grounded`` from the token-level Decimal match (D21).
    """
    try:
        total = extract_total(raw_text, llm_call=llm_call)
    except ExtractionError as exc:
        return GroundingOutcome(
            result=GroundingResult.ungroundable,
            detail=f"Could not extract a trustworthy total: {exc}",
        )

    if is_grounded(total.value, raw_text):
        return GroundingOutcome(
            result=GroundingResult.grounded,
            extracted_value=total.value,
            currency=total.currency,
            detail=f"Extracted total {total.value} appears in the source text.",
        )
    return GroundingOutcome(
        result=GroundingResult.not_grounded,
        extracted_value=total.value,
        currency=total.currency,
        detail=f"Extracted total {total.value} does not appear as a money value "
        "in the source text.",
    )


def _score(outcomes: list[CheckOutcome]) -> Decimal:
    """score = (soft-check pass ratio) × (grounding coverage) (D2/D16).

    Critical checks are a separate hard gate and are NOT in the score. For a
    structured source with no LLM in the path, grounding coverage = 1.0.
    """
    soft = [o for o in outcomes if o.check.type.value == "soft"]
    if soft:
        passed = sum(1 for o in soft if o.check.passed)
        ratio = Decimal(passed) / Decimal(len(soft))
    else:
        ratio = Decimal(1)
    grounding_coverage = Decimal("1.0")  # structured source, no LLM (D2)
    return (ratio * grounding_coverage).quantize(Decimal("0.01"))


def decide(
    invoice: Invoice, action: ProposedAction, *, is_duplicate: bool = False
) -> Decision:
    """Assemble a real ALLOW/BLOCK/ESCALATE Decision from the deterministic checks.

    Precedence BLOCK > ESCALATE > ALLOW (D3): an agent-fixable failure short-
    circuits to BLOCK; any non-agent-fixable failure escalates; otherwise allow.
    Pure — no trace_id/timestamp/latency (those are set at the API boundary).
    """
    outcomes = run_checks(invoice, action, is_duplicate=is_duplicate)
    checks = [o.check for o in outcomes]
    block_reasons = [o.reason for o in outcomes if o.route == Route.block and o.reason]
    escalate_reasons = [o.reason for o in outcomes if o.route == Route.escalate and o.reason]

    if block_reasons:
        result, reasons = DecisionType.block, block_reasons
    elif escalate_reasons:
        result, reasons = DecisionType.escalate, escalate_reasons
    else:
        result, reasons = DecisionType.allow, []

    return Decision(
        decision=result,
        score=_score(outcomes),
        checks=checks,
        reasons=reasons,
        evidence_used=[f"invoice:{invoice.invoice_number}"],
        proposed_action=action,
    )
