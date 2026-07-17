"""Real-provider tests — the 1-2 live-marked calls the testing policy requires
(D9). NEVER run in CI (excluded by addopts '-m "not live"'); run manually:

    pytest -m live

Requires the `llm` extra (litellm) plus provider credentials in the
environment (GEMINI_API_KEY for the default model, or AGENTGATE_LLM_MODEL
pointed at another configured provider).

The target is the ONE production path that touches a model: the demo agent's
proposal step. Assertions are deliberately weak on content (a live model is
nondeterministic) and strong on contract: the round-trip must produce a
schema-valid ProposedAction for the invoice it was shown.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from agentgate.agent.graph import _propose_action
from agentgate.core.llm_router import call_llm
from agentgate.core.schemas import ActionType, Invoice

pytestmark = pytest.mark.live


def test_live_agent_proposal_round_trip():
    invoice = Invoice.model_validate(
        {
            "invoice_number": "INV-001",
            "vendor": "Acme Corp",
            "date": "2026-01-15",
            "currency": "USD",
            "line_items": [
                {
                    "description": "Widget",
                    "quantity": "1",
                    "unit_price": {"value": "1240.00", "currency": "USD"},
                    "amount": {"value": "1240.00", "currency": "USD"},
                    "kind": "charge",
                }
            ],
            "total": {"value": "1240.00", "currency": "USD"},
        }
    )
    action = _propose_action(invoice, llm_call=call_llm)
    # Contract, not content: the live model produced a valid, on-frame action
    # with exact-Decimal money (no float survived the round trip).
    assert action.action_type is ActionType.approve_payment
    assert action.invoice_number == "INV-001"
    assert action.amount.currency == "USD"
    assert isinstance(action.amount.value, Decimal)
