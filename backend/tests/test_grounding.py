"""Tests for invoice-total extraction and source grounding (DECISIONS D19/D21/D22).

Output under test is a GROUNDING RESULT: grounded | not_grounded | ungroundable
— NOT allow/block/escalate. The router is mocked; grounding/decision run
unmocked. Canned outputs reproduce real LLM messiness (D22).

Mutation checks (a plausible wrong impl MUST redden >=1 test):
  * substring grounding instead of token-level -> test_anti_substring_guard reddens
  * grounding hard-coded True -> test_not_grounded / anti-substring redden
  * float money instead of Decimal-from-string -> test_money_rejects_float reddens
  * dropping fail-closed (crash/allow on bad JSON) -> the ungroundable tests redden
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from agentgate.core.decision import GroundingResult, assess_grounding
from agentgate.core.grounding import is_grounded, money_tokens
from agentgate.core.schemas import Money
from tests.conftest import failing_router, load_sample, stub_router

GOOD = load_sample("acme_good.txt")  # realistic invoice; Total Due $1,240.00


# --- 1. grounded (happy path, messy-but-valid canned output) ------------------
def test_grounded_happy_path():
    canned = (
        "Here is the extracted total:\n"
        "```json\n"
        '{\n  "total_value": "1240.00",\n  "currency": "USD"\n}\n'
        "```"
    )
    outcome = assess_grounding(GOOD, llm_call=stub_router(canned))
    assert outcome.result is GroundingResult.grounded
    assert outcome.extracted_value == Decimal("1240.00")
    assert outcome.currency == "USD"


# --- 2. not_grounded (extracted value absent from source) ---------------------
def test_not_grounded():
    canned = '{"total_value": "9999.99", "currency": "USD"}'
    outcome = assess_grounding(GOOD, llm_call=stub_router(canned))
    assert outcome.result is GroundingResult.not_grounded


# --- 3. decimal-lossless: raw "$1,240.00" grounds a bare int 1240 (D10) -------
def test_decimal_lossless_and_string_vs_int():
    # int value (string-vs-int messiness) grounded against grouped "$1,240.00".
    canned = '{"total_value": 1240, "currency": "USD"}'
    outcome = assess_grounding(GOOD, llm_call=stub_router(canned))
    assert outcome.result is GroundingResult.grounded
    assert outcome.extracted_value == Decimal("1240")


# --- 4. ungroundable on malformed JSON (fail-closed, D11) ---------------------
def test_ungroundable_truncated_json():
    canned = '{"total_value": "1240.00", "curren'  # truncated, no closing brace
    outcome = assess_grounding(GOOD, llm_call=stub_router(canned))
    assert outcome.result is GroundingResult.ungroundable


def test_ungroundable_null_total():
    canned = '{"total_value": null, "currency": "USD"}'
    outcome = assess_grounding(GOOD, llm_call=stub_router(canned))
    assert outcome.result is GroundingResult.ungroundable


def test_ungroundable_on_router_failure():
    # Rate-limit / timeout must fail closed, never crash, never grounded (D11).
    outcome = assess_grounding(GOOD, llm_call=failing_router("429 rate limit"))
    assert outcome.result is GroundingResult.ungroundable


# --- 5. anti-substring guard (D21) --------------------------------------------
def test_anti_substring_guard():
    # 1240 hides inside INV-31240, 12/40, and $11,240.00 — none is the value.
    raw = "Reference INV-31240 dated 12/40 for prior period. Amount billed: $11,240.00."
    canned = '{"total_value": "1240.00", "currency": "USD"}'
    outcome = assess_grounding(raw, llm_call=stub_router(canned))
    assert outcome.result is GroundingResult.not_grounded


# --- 6. messy LLM output: fence + preamble + trailing comma still extracts ----
def test_messy_output_still_extracts():
    canned = (
        "Sure! Based on the invoice, here is the JSON you asked for:\n\n"
        "```json\n"
        '{\n    "total_value": "1240.00",\n    "currency": "USD",\n}\n'  # trailing comma
        "```\n\n"
        "Let me know if you need anything else."
    )
    outcome = assess_grounding(GOOD, llm_call=stub_router(canned))
    assert outcome.result is GroundingResult.grounded


# --- Focused grounding unit tests (mutation teeth on the tokenizer) -----------
def test_money_tokens_are_token_level():
    raw = "INV-31240 on 12/40 billed $11,240.00 and also 1240.00 exactly."
    tokens = money_tokens(raw)
    assert Decimal("11240.00") in tokens
    assert Decimal("1240.00") in tokens  # the standalone one IS present
    # The spurious 1240 that a substring scan would find is NOT a token here:
    assert Decimal("31240") not in tokens


def test_is_grounded_matches_across_formats():
    assert is_grounded(Decimal("1240"), "Total: $1,240.00")
    assert is_grounded(Decimal("1240.00"), "Total: 1240")
    assert not is_grounded(Decimal("1240"), "Order INV-31240 total 12/40")


# --- Money hard rule: Decimal-from-string, never float (D1) -------------------
def test_money_rejects_float():
    with pytest.raises(ValueError):
        Money(value=1240.00, currency="USD")  # float must be refused


def test_money_parses_string_and_int():
    assert Money(value="1,240.00".replace(",", ""), currency="USD").value == Decimal("1240.00")
    assert Money(value=1240, currency="USD").value == Decimal("1240")
