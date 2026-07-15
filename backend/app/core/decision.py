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
