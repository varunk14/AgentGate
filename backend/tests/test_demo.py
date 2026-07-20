"""Tests for the runnable demo module (D48).

The router seam and the human prompt are scripted; narration is captured. The
graph and the gate run unmocked (D9) — canned model output is realistic-messy
(fences + preamble, D22) and every verdict asserted comes from the real
``decide()``.
"""

from __future__ import annotations

import json

from agentgate.agent.demo import (
    CLEAN_INVOICE,
    OVER_CEILING_INVOICE,
    _render_decision,
    run_demo,
)
from agentgate.core.llm_router import LLMRouterError


def _proposal_json(invoice: dict) -> str:
    return json.dumps(
        {
            "action_type": "approve_payment",
            "invoice_number": invoice["invoice_number"],
            "amount": dict(invoice["total"]),
            "vendor": invoice["vendor"],
        }
    )


def _messy(payload: str) -> str:
    """Wrap a JSON payload the way real models actually reply (D22)."""
    return f"Sure! Here is the requested action:\n```json\n{payload}\n```"


def _scripted_llm(prompt: str) -> str:
    """Propose the correct action for whichever demo invoice the prompt shows."""
    if CLEAN_INVOICE["invoice_number"] in prompt and "blocked" not in prompt:
        return _messy(_proposal_json(CLEAN_INVOICE))
    if OVER_CEILING_INVOICE["invoice_number"] in prompt and "blocked" not in prompt:
        return _messy(_proposal_json(OVER_CEILING_INVOICE))
    raise AssertionError(f"unexpected prompt: {prompt[:120]}")


def _run(llm_call, ask_reply: str = "approve"):
    lines: list[str] = []
    asks: list[str] = []

    def ask(prompt: str) -> str:
        asks.append(prompt)
        return ask_reply

    run_demo(llm_call=llm_call, ask=ask, out=lines.append)
    return "\n".join(lines), asks


def test_clean_allows_and_over_ceiling_escalates_to_human_approval():
    text, asks = _run(_scripted_llm, ask_reply="approve")
    # Scenario 1: real gate ALLOW with the full check table passing.
    assert "gate decision: ALLOW" in text
    assert "score: 1.00" in text
    # Scenario 2: correct proposal, but the policy ceiling escalates it.
    assert "gate decision: ESCALATE" in text
    assert "12500.00 USD" in text
    assert "outcome: approved_by_human" in text
    # Only the escalation consulted a human; the clean allow never did.
    assert len(asks) == 1
    # D25 surfaced in the narration: approval does not rewrite the verdict.
    assert "never rewrites the verdict" in text


def test_misread_amount_is_blocked_then_corrected_by_the_agent():
    def llm(prompt: str) -> str:
        if "blocked" in prompt:
            # Value-only re-proposal (D30): the corrected amount, nothing else.
            return _messy(
                json.dumps({"value": CLEAN_INVOICE["total"]["value"], "currency": "USD"})
            )
        if CLEAN_INVOICE["invoice_number"] in prompt:
            bad = json.loads(_proposal_json(CLEAN_INVOICE))
            bad["amount"]["value"] = "363.00"  # decimal slip: one order of magnitude
            return _messy(json.dumps(bad))
        return _messy(_proposal_json(OVER_CEILING_INVOICE))

    text, _ = _run(llm)
    assert "2 submissions" in text  # blocked once, corrected, resubmitted
    assert "gate decision: ALLOW" in text  # and the corrected action passed


def test_human_reject_is_the_terminal_outcome():
    text, _ = _run(_scripted_llm, ask_reply="reject")
    assert "outcome: rejected_by_human" in text
    assert "outcome: approved_by_human" not in text


def test_null_score_renders_as_not_computed_never_zero():
    lines: list[str] = []
    _render_decision(
        {"decision": "escalate", "score": None, "checks": [], "reasons": []},
        lines.append,
    )
    text = "\n".join(lines)
    assert "not computed" in text  # D32: null means not measured, never 0
    assert "score: 0" not in text


def test_router_failure_fails_closed_to_a_human():
    def dead(prompt: str) -> str:
        raise LLMRouterError("provider down")

    text, asks = _run(dead, ask_reply="reject")
    assert "failed closed" in text
    assert len(asks) == 2  # both scenarios routed to a human, neither crashed
    assert "outcome: rejected_by_human" in text
    assert "gate decision: none" in text  # no valid proposal ever reached the gate
