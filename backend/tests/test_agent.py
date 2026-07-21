"""Tests for the LangGraph demo agent + human-in-the-loop (DECISIONS D30).

The router is the only mock: a scripted stub returns canned proposal / re-proposal
text; the graph, the generalized retry loop, and the real gate all run unmocked.
Graph state is JSON-safe, so the decision is read as a dict.

Mutation checks (a plausible wrong impl MUST redden >=1 test):
  * re-proposer returns a whole action / ignores value-only -> test_block_then_fix_reaches_allow reddens
  * human approval rewrites the Decision to allow (breaks D25) -> test_escalate_pauses_then_human_* reddens
  * cap not passed from policy / off-by-one -> test_cap_exhausted_pauses_for_human reddens
  * malformed LLM proposal crashes instead of failing closed -> test_malformed_proposal_fails_closed reddens
  * malformed re-proposal loops/crashes -> test_malformed_reproposal_fails_closed reddens
"""

from __future__ import annotations

from langgraph.types import Command

from agentgate.agent.graph import build_agent, initial_state
from tests.test_decision import make_invoice


def _cfg(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


def scripted_router(*outputs: str):
    """A router stub that returns queued outputs in call order, repeating the last
    one once exhausted (handy for a re-proposer that keeps failing)."""
    state = {"i": 0}

    def _call(prompt: str) -> str:  # noqa: ARG001 - prompt intentionally ignored
        i = state["i"]
        state["i"] = i + 1
        return outputs[min(i, len(outputs) - 1)]

    return _call


def _propose_json(amount: str, currency: str = "USD", vendor: str = "Acme Corp") -> str:
    return (
        '{"action_type": "approve_payment", "invoice_number": "INV-001", '
        f'"amount": {{"value": "{amount}", "currency": "{currency}"}}, "vendor": "{vendor}"}}'
    )


def _value_json(value: str, currency: str = "USD") -> str:
    return f'{{"value": "{value}", "currency": "{currency}"}}'


def _paused_at_human(graph, thread_id: str) -> bool:
    return graph.get_state(_cfg(thread_id)).next == ("human",)


# --- BLOCK -> LLM re-proposes the value -> ALLOW -----------------------------
def test_block_then_fix_reaches_allow():
    # Propose a decimal slip (12400 vs 1240 total); on the block, re-propose 1240.
    router = scripted_router(_propose_json("12400.00"), _value_json("1240.00"))
    graph = build_agent(llm_call=router)
    result = graph.invoke(initial_state(make_invoice(total="1240.00")), _cfg("t1"))

    assert result["outcome"] == "allowed"
    assert result["decision"]["decision"] == "allow"
    assert result["attempts"] == 2  # initial BLOCK + one successful resubmit
    assert not _paused_at_human(graph, "t1")


# --- ESCALATE pauses; human approve / reject; verdict never rewritten (D25) ---
def test_escalate_pauses_then_human_approves():
    # Right amount, wrong currency -> currency_match ESCALATES (no BLOCK, no retry).
    router = scripted_router(_propose_json("1240.00", currency="EUR"))
    graph = build_agent(llm_call=router)
    graph.invoke(initial_state(make_invoice(total="1240.00")), _cfg("t2"))

    assert _paused_at_human(graph, "t2")  # graph is paused, awaiting a human
    assert graph.get_state(_cfg("t2")).values["decision"]["decision"] == "escalate"

    final = graph.invoke(Command(resume="approve"), _cfg("t2"))
    assert final["outcome"] == "approved_by_human"
    # D25: approval is the graph's resolution; the gate verdict stays ESCALATE.
    assert final["decision"]["decision"] == "escalate"


def test_escalate_pauses_then_human_rejects():
    router = scripted_router(_propose_json("1240.00", currency="EUR"))
    graph = build_agent(llm_call=router)
    graph.invoke(initial_state(make_invoice(total="1240.00")), _cfg("t3"))
    assert _paused_at_human(graph, "t3")

    final = graph.invoke(Command(resume="reject"), _cfg("t3"))
    assert final["outcome"] == "rejected_by_human"
    assert final["decision"]["decision"] == "escalate"


# --- cap: the LLM never converges -> stop at the cap, pause for a human -------
def test_cap_exhausted_pauses_for_human():
    # Initial slip, then every re-proposal is still wrong -> loop hits the cap.
    router = scripted_router(_propose_json("12400.00"), _value_json("9999.99"))
    graph = build_agent(llm_call=router)
    graph.invoke(initial_state(make_invoice(total="1240.00")), _cfg("t4"))

    assert _paused_at_human(graph, "t4")
    state = graph.get_state(_cfg("t4")).values
    assert state["decision"]["decision"] == "block"  # verdict NOT rewritten (D25)
    assert state["attempts"] == 3  # 1 initial + 2 retries (policy max_attempts), then stop


# --- fail-closed: malformed LLM output routes to a human, never crashes -------
def test_malformed_proposal_fails_closed():
    router = scripted_router("I cannot help with that.")  # no JSON at all
    graph = build_agent(llm_call=router)
    graph.invoke(initial_state(make_invoice(total="1240.00")), _cfg("t5"))

    assert _paused_at_human(graph, "t5")
    values = graph.get_state(_cfg("t5")).values
    assert values.get("action") is None
    assert "could not form a valid proposal" in values["error"]

    final = graph.invoke(Command(resume="reject"), _cfg("t5"))
    assert final["outcome"] == "rejected_by_human"


def test_malformed_reproposal_fails_closed():
    # Valid initial proposal that blocks, then garbage on the re-proposal.
    router = scripted_router(_propose_json("12400.00"), "sorry, no idea")
    graph = build_agent(llm_call=router)
    graph.invoke(initial_state(make_invoice(total="1240.00")), _cfg("t6"))

    assert _paused_at_human(graph, "t6")
    state = graph.get_state(_cfg("t6")).values
    assert state["decision"]["decision"] == "block"  # unfixable -> human, verdict intact
    assert state["attempts"] == 1  # the failed re-proposal did not burn a resubmit


# --- human approval is fail-closed: ONLY an exact "approve" approves ----------
def test_human_approval_requires_exact_approve():
    # Anything that is not "approve" must reject (fail-closed). This pins the match
    # against a lenient "improvement" (e.g. `!= "reject"` or substring matching)
    # that would approve garbage — "yes", "", "approve it", or "do not approve".
    for reply in ("yes", "", "approve it", "do not approve", "reject"):
        router = scripted_router(_propose_json("1240.00", currency="EUR"))
        graph = build_agent(llm_call=router)
        thread = f"approve-{reply or 'empty'}"
        graph.invoke(initial_state(make_invoice(total="1240.00")), _cfg(thread))
        final = graph.invoke(Command(resume=reply), _cfg(thread))
        assert final["outcome"] == "rejected_by_human", f"reply {reply!r} must NOT approve"
        assert final["decision"]["decision"] == "escalate"  # verdict intact (D25)


def test_exact_approve_still_approves():
    router = scripted_router(_propose_json("1240.00", currency="EUR"))
    graph = build_agent(llm_call=router)
    graph.invoke(initial_state(make_invoice(total="1240.00")), _cfg("approve-ok"))
    final = graph.invoke(Command(resume="approve"), _cfg("approve-ok"))
    assert final["outcome"] == "approved_by_human"


# --- a stricter injected policy actually governs the agent's gate -------------
def test_build_agent_policy_threshold_is_honored():
    # A clean, self-consistent $6000 payment allows under the default policy, but a
    # policy with amount_greater_than=5000 must make the SAME agent escalate — proof
    # the injected policy's thresholds reach decide() inside the retry loop.
    from decimal import Decimal

    from agentgate.core.policy import DEFAULT_POLICY, Policy, RetryPolicy

    strict = Policy(
        amount_greater_than=Decimal("5000"),
        score_below=DEFAULT_POLICY.score_below,
        critical_checks=DEFAULT_POLICY.critical_checks,
        retry=RetryPolicy(max_attempts=DEFAULT_POLICY.retry.max_attempts),
    )
    router = scripted_router(_propose_json("6000.00"))
    graph = build_agent(llm_call=router, policy=strict)
    graph.invoke(initial_state(make_invoice(total="6000.00")), _cfg("pol"))
    assert _paused_at_human(graph, "pol")
    values = graph.get_state(_cfg("pol")).values
    assert values["decision"]["decision"] == "escalate"
    assert any(r["check"] == "policy_amount_threshold" for r in values["decision"]["reasons"])
