"""Runnable demo — a live model proposes payment actions; the gate verifies them.

    python -m agentgate.agent.demo

Requires the ``agent`` and ``llm`` extras (``pip install -e ".[agent,llm]"``)
and a provider key in the environment (``GEMINI_API_KEY`` for the default
model; ``AGENTGATE_LLM_MODEL`` swaps providers). The verification path runs no
LLM — the model appears only in the proposal step, behind the router — so the
verdicts shown here are the same deterministic decisions the tests assert.

Two scenarios (D48): a clean invoice the model reads and the gate checks (a
misread comes back as a machine-fixable block the agent corrects), then an
invoice over the policy amount ceiling, which escalates and pauses the graph
for a human to approve or reject on stdin. The narration reports whatever
actually happened; nothing is scripted.
"""

from __future__ import annotations

import importlib.util
from typing import Callable, Optional

from ..core.llm_router import call_llm
from ..core.schemas import Invoice

# Public so tests can build canned model replies against the exact values.
CLEAN_INVOICE: dict = {
    "invoice_number": "INV-2026-0107",
    "vendor": "Acme Corp",
    "date": "2026-07-01",
    "currency": "USD",
    "line_items": [
        {
            "description": "Widget assembly",
            "quantity": "3",
            "unit_price": {"value": "1000.00", "currency": "USD"},
            "amount": {"value": "3000.00", "currency": "USD"},
            "kind": "charge",
        },
        {
            "description": "Shipping",
            "quantity": "1",
            "unit_price": {"value": "300.00", "currency": "USD"},
            "amount": {"value": "300.00", "currency": "USD"},
            "kind": "shipping",
        },
    ],
    "tax_lines": [{"rate": "0.10", "amount": {"value": "330.00", "currency": "USD"}}],
    "total": {"value": "3630.00", "currency": "USD"},
}

OVER_CEILING_INVOICE: dict = {
    "invoice_number": "INV-2026-0201",
    "vendor": "Northwind Data Services",
    "date": "2026-07-10",
    "currency": "USD",
    "line_items": [
        {
            "description": "Annual data platform license",
            "quantity": "1",
            "unit_price": {"value": "12500.00", "currency": "USD"},
            "amount": {"value": "12500.00", "currency": "USD"},
            "kind": "charge",
        }
    ],
    "total": {"value": "12500.00", "currency": "USD"},
}


def _fmt_money(money: Optional[dict]) -> str:
    """Render a wire Money dict as its exact strings (D1 ends at the pixel)."""
    if not money:
        return "n/a"
    return f"{money['value']} {money['currency']}"


def _render_decision(decision: Optional[dict], out: Callable[[str], None]) -> None:
    """Print a Decision dict as returned — score None is 'not computed' (D32)."""
    if decision is None:
        out("  gate decision: none (no valid proposal reached the gate)")
        return
    score = decision.get("score")
    out(
        f"  gate decision: {decision['decision'].upper()}"
        f"   score: {score if score is not None else 'not computed'}"
    )
    checks = decision.get("checks") or []
    if checks:
        passed = sum(1 for check in checks if check["passed"])
        out(f"  checks: {passed}/{len(checks)} passed")
        for check in checks:
            if not check["passed"]:
                out(f"    [FAIL] {check['name']}: {check['detail']}")
    for reason in decision.get("reasons") or []:
        out(f"  reason: {reason['message']}")


def _run_scenario(
    graph,
    title: str,
    invoice_data: dict,
    *,
    ask: Callable[[str], str],
    out: Callable[[str], None],
    thread: str,
) -> None:
    """Run one invoice through the agent graph and narrate what happened."""
    from langgraph.types import Command

    from .graph import initial_state

    invoice = Invoice.model_validate(invoice_data)
    out(f"=== {title} ===")
    out(
        f"invoice {invoice.invoice_number} from {invoice.vendor}"
        f" — total {invoice.total.value} {invoice.total.currency}"
    )
    config = {"configurable": {"thread_id": thread}}
    state = graph.invoke(initial_state(invoice), config)

    action = state.get("action")
    if action:
        out(
            f"model proposes: {action['action_type']} {action['invoice_number']}"
            f" for {_fmt_money(action['amount'])}"
        )
    if state.get("error"):
        out(f"  proposal failed closed to a human: {state['error']}")
    attempts = state.get("attempts") or 0
    if attempts > 1 and state.get("outcome") == "allowed":
        out(f"  (the gate blocked a misread and the agent corrected it: {attempts} submissions)")
    elif attempts > 1:
        out(f"  (the agent retried {attempts} submissions but could not self-correct)")
    _render_decision(state.get("decision"), out)

    if "__interrupt__" in state:
        out("  the graph paused for human review")
        reply = ask("  approve or reject? ")
        state = graph.invoke(Command(resume=reply), config)

    out(f"outcome: {state.get('outcome')}")
    if state.get("decision") is not None and state.get("outcome") == "approved_by_human":
        out(
            "  (the recorded gate decision is unchanged — a human approval routes"
            " the action, it never rewrites the verdict)"
        )
    out("")


def run_demo(
    *,
    llm_call: Callable[[str], str] = call_llm,
    ask: Callable[[str], str] = input,
    out: Callable[[str], None] = print,
) -> None:
    """Run both demo scenarios through the real agent graph and gate.

    ``llm_call`` is the router seam (a live provider by default; tests inject
    canned output), ``ask`` collects the human verdict on an escalation, and
    ``out`` receives each narration line.
    """
    try:
        from .graph import build_agent
    except ImportError as exc:
        raise SystemExit(
            f"the demo needs the 'agent' extra: pip install -e '.[agent,llm]' ({exc})"
        ) from exc

    graph = build_agent(llm_call=llm_call)
    _run_scenario(
        graph,
        "Scenario 1: a clean invoice",
        CLEAN_INVOICE,
        ask=ask,
        out=out,
        thread="demo-clean",
    )
    _run_scenario(
        graph,
        "Scenario 2: over the policy ceiling",
        OVER_CEILING_INVOICE,
        ask=ask,
        out=out,
        thread="demo-ceiling",
    )


def main() -> None:
    """Entry point for ``python -m agentgate.agent.demo``."""
    if importlib.util.find_spec("litellm") is None:
        raise SystemExit(
            "the demo makes real model calls: pip install -e '.[agent,llm]' and set"
            " GEMINI_API_KEY (or AGENTGATE_LLM_MODEL plus that provider's key)"
        )
    run_demo()


if __name__ == "__main__":
    main()
