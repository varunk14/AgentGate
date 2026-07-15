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
  * stub _apply_fix to a no-op            -> GATE a (block->fix->allow) reddens
  * let the loop rewrite BLOCK->ESCALATE  -> test_verdict_never_rewritten reddens
  * mis-map ESCALATE in _resolution_for   -> GATE b (escalated_by_gate) reddens
  * off-by-one / remove the cap bound     -> test_cap_exhausted reddens (loop won't stop)
"""

from __future__ import annotations

from enum import Enum
from typing import Callable

from pydantic import BaseModel

from .decision import decide
from .schemas import BlockType, Decision, DecisionType, Invoice, ProposedAction


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


def _apply_fix(action: ProposedAction, reason) -> ProposedAction:
    """Deterministically apply a BlockReason's fix: set the named field to the
    expected value. Raises ``UnfixableBlockError`` if the reason lacks a
    ``field_to_change`` or ``expected`` (never silently no-op)."""
    if reason is None or reason.field_to_change is None or reason.expected is None:
        raise UnfixableBlockError(
            f"Block reason is not machine-fixable: {getattr(reason, 'check', reason)!r}"
        )
    # v1 fixes only top-level proposed_action fields (e.g. proposed_action.amount).
    attr = reason.field_to_change.split(".")[-1]
    return action.model_copy(update={attr: reason.expected})


def _resolution_for(decision: DecisionType) -> Resolution:
    """Map the final gate verdict to the loop's resolution (D25 — derived from the
    verdict, never by mutating it)."""
    if decision is DecisionType.allow:
        return Resolution.allowed
    if decision is DecisionType.escalate:
        return Resolution.escalated_by_gate
    return Resolution.escalated_to_human  # BLOCK the loop could not resolve


def run_with_retry(
    invoice: Invoice,
    action: ProposedAction,
    *,
    is_duplicate: bool = False,
    max_attempts: int = 2,
    decide_fn: Callable[..., Decision] = decide,
) -> RetryOutcome:
    """Submit, and on a fixable BLOCK apply the fix and resubmit, up to
    ``max_attempts`` resubmissions. ESCALATE/ALLOW stop immediately; an unfixable
    BLOCK stops immediately (no pointless resubmit). ``decide_fn`` is injectable
    for tests (the cap bound)."""
    current = action
    decision = decide_fn(invoice, current, is_duplicate=is_duplicate)
    history: list[Decision] = [decision]

    resubmissions = 0
    while decision.decision is DecisionType.block and resubmissions < max_attempts:
        reason = next(
            (r for r in decision.reasons if r.block_type is BlockType.agent_fixable), None
        )
        try:
            current = _apply_fix(current, reason)
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
