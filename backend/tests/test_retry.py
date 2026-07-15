"""Tests for the scripted retry loop (DECISIONS D7/D25).

Gate: consume a BLOCK -> fix -> resubmit -> ALLOW; a source_invalid case
ESCALATES instead of looping. Plus D25: the loop never rewrites the gate verdict.
"""

from __future__ import annotations

from app.core.retry import Resolution, RetryOutcome, run_with_retry
from app.core.schemas import (
    BlockReason,
    BlockType,
    Check,
    CheckKind,
    Decision,
    DecisionType,
    Money,
)
from tests.test_decision import m, make_action, make_invoice


def _const_decision(decision: Decision):
    """A decide_fn stub that ignores its inputs and always returns `decision`."""

    def _fn(invoice, action, *, is_duplicate=False):
        return decision

    return _fn


# --- GATE a: BLOCK -> fix -> resubmit -> ALLOW -------------------------------
def test_gate_block_then_fix_then_allow():
    outcome = run_with_retry(make_invoice(total="1240.00"), make_action(amount="12400.00"))
    assert outcome.resolution is Resolution.allowed
    assert outcome.final_decision.decision is DecisionType.allow
    assert outcome.attempts == 2                       # initial BLOCK + one resubmit
    assert outcome.history[0].decision is DecisionType.block


# --- GATE b: source_invalid ESCALATES instead of looping --------------------
def test_gate_source_invalid_escalates_without_looping():
    # line items sum to 1240 but total claims 1300 -> gate escalates immediately.
    outcome = run_with_retry(make_invoice(total="1300.00"), make_action(amount="1300.00"))
    assert outcome.resolution is Resolution.escalated_by_gate
    assert outcome.attempts == 1
    assert len(outcome.history) == 1
    assert outcome.final_decision.decision is DecisionType.escalate


# --- fix lands on a *different* escalate -------------------------------------
def test_fix_reveals_a_vendor_escalate():
    # amount is wrong (BLOCK) and vendor is a different entity (would ESCALATE).
    # BLOCK wins first; after the amount is fixed, the vendor escalate surfaces.
    outcome = run_with_retry(
        make_invoice(total="1240.00", vendor="Acme Corp"),
        make_action(amount="12400.00", vendor="Acme Corp LLC"),
    )
    assert outcome.resolution is Resolution.escalated_by_gate
    assert outcome.final_decision.decision is DecisionType.escalate
    assert outcome.attempts == 2
    assert any(r.check == "vendor_match" for r in outcome.final_decision.reasons)


# --- unfixable BLOCK -> escalated_to_human, no pointless resubmit ------------
def _unfixable_block() -> Decision:
    return Decision(
        decision=DecisionType.block,
        score=1,  # score irrelevant here
        checks=[Check(name="x", type=CheckKind.critical, passed=False, detail="")],
        reasons=[BlockReason(check="x", field_to_change=None, block_type=BlockType.agent_fixable, message="no field")],
    )


def test_unfixable_block_escalates_to_human():
    outcome = run_with_retry(
        make_invoice(), make_action(), decide_fn=_const_decision(_unfixable_block())
    )
    assert outcome.resolution is Resolution.escalated_to_human
    assert outcome.attempts == 1                        # did not burn a resubmit
    assert outcome.final_decision.decision is DecisionType.block  # verdict NOT rewritten (D25)


# --- block reason targets a field outside proposed_action -> unfixable -------
def _wrong_target_block() -> Decision:
    # A reason marked agent_fixable but pointing at the SOURCE, not the action.
    # The caller cannot change the source, so resubmitting can never converge.
    return Decision(
        decision=DecisionType.block,
        score=1,
        checks=[Check(name="structural_arithmetic", type=CheckKind.critical, passed=False, detail="")],
        reasons=[BlockReason(
            check="structural_arithmetic", expected=m("1240.00"), received=m("1300.00"),
            field_to_change="source.invoice", block_type=BlockType.agent_fixable,
            message="source is inconsistent",
        )],
    )


def test_block_targeting_source_field_is_unfixable():
    outcome = run_with_retry(
        make_invoice(), make_action(), decide_fn=_const_decision(_wrong_target_block())
    )
    assert outcome.resolution is Resolution.escalated_to_human
    assert outcome.attempts == 1                        # no pointless resubmits
    assert outcome.final_decision.decision is DecisionType.block  # verdict NOT rewritten (D25)


# --- cap exhaustion: a persistent fixable BLOCK stops at the cap -------------
def _persistent_fixable_block() -> Decision:
    return Decision(
        decision=DecisionType.block,
        score=1,
        checks=[Check(name="action_amount_matches_total", type=CheckKind.critical, passed=False, detail="")],
        reasons=[BlockReason(
            check="action_amount_matches_total", expected=m("1240.00"), received=m("12400.00"),
            field_to_change="proposed_action.amount", block_type=BlockType.agent_fixable,
            message="fix amount",
        )],
    )


def test_cap_exhausted_stops_and_escalates_to_human():
    # decide always BLOCKs (the fix never "sticks") -> loop must stop at the cap.
    outcome = run_with_retry(
        make_invoice(), make_action(amount="12400.00"),
        max_attempts=2, decide_fn=_const_decision(_persistent_fixable_block()),
    )
    assert outcome.resolution is Resolution.escalated_to_human
    assert outcome.attempts == 3                        # 1 initial + 2 retries, then stop
    assert outcome.final_decision.decision is DecisionType.block  # verdict NOT rewritten (D25)


# --- D25: in every escalated_to_human case, the verdict stays BLOCK ----------
def test_verdict_never_rewritten():
    for stub in (_unfixable_block(), _persistent_fixable_block()):
        outcome = run_with_retry(
            make_invoice(), make_action(amount="12400.00"), decide_fn=_const_decision(stub)
        )
        assert outcome.resolution is Resolution.escalated_to_human
        assert outcome.final_decision.decision is DecisionType.block
