"""Scripted retry loop — the compiler-error feedback loop (DECISIONS D7/D25).

A caller receives a BLOCK, reads the machine-readable BlockReason, applies the
fix deterministically (NO LLM — the LLM-driven retry is the agent, a later
slice), and resubmits. It loops only on BLOCK; ESCALATE and ALLOW stop
immediately. The cap bounds resubmissions (D7).

Invariant (D25): the loop NEVER mutates the gate's Decision. ``final_decision``
is returned verbatim from ``decide()``; the loop's own outcome is reported via
``resolution``. A BLOCK the loop gave up on stays BLOCK — the human-routing is
expressed as ``resolution == escalated_to_human``, not by rewriting the verdict.

Mutation checks (a plausible wrong impl MUST redden >=1 test):
  * default propose_value returns a no-op -> GATE a (block->fix->allow) reddens
  * let the loop rewrite BLOCK->ESCALATE  -> test_verdict_never_rewritten reddens
  * mis-map ESCALATE in _resolution_for   -> GATE b (escalated_by_gate) reddens
  * off-by-one / remove the cap bound     -> test_cap_exhausted reddens (loop won't stop)
  * apply a fix to a non-action field     -> test_block_targeting_source_field reddens

The fix is VALUE-ONLY (D30): the loop owns which field changes (from the block
reason) and does the model_copy; the injected `propose_value` returns only the new
value for that one field. It therefore cannot declare an adjustment (which would
flip BLOCK->ESCALATE) or drift action_type/invoice_number. The default returns the
gate's `expected` (the deterministic Slice 3 fix); the agent injects an LLM
re-proposer (Slice 6).
"""

from __future__ import annotations

from enum import Enum
from typing import Callable, Optional

from pydantic import BaseModel

from .decision import decide
from .schemas import BlockReason, BlockType, Decision, DecisionType, Invoice, ProposedAction

# A value-only fixer: given the invoice, current action, and the agent-fixable
# block reason, return the new value for the field the reason names (a Money for
# amounts, a str for vendor). It must raise UnfixableBlockError to signal give-up.
ProposeValue = Callable[[Invoice, ProposedAction, BlockReason], object]


class UnfixableBlockError(RuntimeError):
    """A BLOCK whose reason lacks the information needed to apply a fix."""


class Resolution(str, Enum):
    allowed = "allowed"  # ended on ALLOW
    escalated_by_gate = "escalated_by_gate"  # the gate returned ESCALATE
    escalated_to_human = "escalated_to_human"  # loop gave up on a BLOCK (cap or unfixable)


class RetryOutcome(BaseModel):
    final_decision: Decision  # verbatim from decide() — never mutated (D25)
    attempts: int  # submissions made, including the initial one
    resolution: Resolution
    history: list[Decision]


def _fixable_field(reason: Optional[BlockReason]) -> str:
    """Validate that ``reason`` names a single, caller-changeable proposed_action
    field and return that field's name. Raises ``UnfixableBlockError`` otherwise
    (never a silent no-op). v1 fixes only top-level proposed_action fields (e.g.
    ``proposed_action.amount``); source fields, nested paths, and unknown names
    cannot converge by resubmitting, so they are unfixable."""
    if reason is None or reason.field_to_change is None:
        raise UnfixableBlockError(
            f"Block reason is not machine-fixable: {getattr(reason, 'check', reason)!r}"
        )
    parts = reason.field_to_change.split(".")
    if (
        len(parts) != 2
        or parts[0] != "proposed_action"
        or parts[1] not in ProposedAction.model_fields
    ):
        raise UnfixableBlockError(
            f"Block reason names a field the caller cannot change: "
            f"{reason.field_to_change!r}"
        )
    return parts[1]


def _deterministic_value(
    invoice: Invoice, action: ProposedAction, reason: BlockReason
) -> object:
    """Default value-only fixer (Slice 3 behavior): the gate already told us the
    exact expected value, so return it. Unfixable if it carries none."""
    if reason.expected is None:
        raise UnfixableBlockError(
            f"Block reason carries no expected value to apply: {reason.check!r}"
        )
    return reason.expected


def _resolution_for(decision: DecisionType) -> Resolution:
    """Map the final gate verdict to the loop's resolution (D25 — derived from the
    verdict, never by mutating it).

    Cap exhaustion (a still-BLOCK after the last resubmission) routes to a human,
    unconditionally and by design — fail-closed: the loop cannot fix it, so it
    neither retries forever nor approves. This is hardcoded, not a policy knob;
    the only alternative (a dead-end BLOCK with no human routing) was ruled wrong
    in the Slice 3 grill, so an on_cap_exhausted config key would be inert (D28)."""
    if decision is DecisionType.allow:
        return Resolution.allowed
    if decision is DecisionType.escalate:
        return Resolution.escalated_by_gate
    return Resolution.escalated_to_human  # BLOCK the loop could not resolve -> human


def run_with_retry(
    invoice: Invoice,
    action: ProposedAction,
    *,
    is_duplicate: bool = False,
    max_attempts: int = 2,
    decide_fn: Callable[..., Decision] = decide,
    propose_value: ProposeValue = _deterministic_value,
) -> RetryOutcome:
    """Submit, and on an agent-fixable BLOCK apply a value-only fix and resubmit,
    up to ``max_attempts`` resubmissions. ESCALATE/ALLOW stop immediately; an
    unfixable BLOCK stops immediately (no pointless resubmit). ``decide_fn`` and
    ``propose_value`` are injectable: the default fixer is deterministic (the
    gate's expected value); the agent injects an LLM re-proposer that must raise
    ``UnfixableBlockError`` to give up (e.g. on malformed output)."""
    current = action
    decision = decide_fn(invoice, current, is_duplicate=is_duplicate)
    history: list[Decision] = [decision]

    resubmissions = 0
    while decision.decision is DecisionType.block and resubmissions < max_attempts:
        reason = next(
            (r for r in decision.reasons if r.block_type is BlockType.agent_fixable), None
        )
        try:
            field = _fixable_field(reason)  # the loop owns which field changes
            value = propose_value(invoice, current, reason)  # fixer returns only the value
            current = current.model_copy(update={field: value})
        except UnfixableBlockError:
            break  # stop immediately at escalated_to_human; do not burn attempts
        resubmissions += 1
        decision = decide_fn(invoice, current, is_duplicate=is_duplicate)
        history.append(decision)

    return RetryOutcome(
        final_decision=decision,  # verbatim (D25)
        attempts=len(history),
        resolution=_resolution_for(decision.decision),
        history=history,
    )
