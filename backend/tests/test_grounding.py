"""Tests for source grounding and LLM-output JSON recovery (D19/D21/D22).

Two surviving surfaces from the original walking skeleton (its extract-then-
ground pipeline was removed as dead code — D19 named it disposable, and no
production caller existed): ``is_grounded``/``money_tokens`` (deterministic
token-level Decimal matching; the verification path's grounding primitive) and
``parse_llm_json`` (JSON recovery from messy model output; consumed by the
demo agent's proposal step).

Mutation checks (a plausible wrong impl MUST redden >=1 test):
  * substring grounding instead of token-level -> the anti-substring tests redden
  * grounding hard-coded True -> anti-substring / not-grounded assertions redden
  * float money instead of Decimal-from-string -> test_money_rejects_float reddens
  * parse_llm_json crashing (or returning junk) on truncation -> the
    fail-closed ExtractionError test reddens
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from agentgate.core.grounding import is_grounded, money_tokens
from agentgate.core.llm_json import ExtractionError, parse_llm_json
from agentgate.core.schemas import Money
from tests.conftest import load_sample

GOOD = load_sample("acme_good.txt")  # realistic invoice; Total Due $1,240.00


# --- grounding: token-level Decimal matching (D21) -----------------------------


def test_grounded_in_realistic_invoice_text():
    # The real total grounds against the sample's grouped "$1,240.00".
    assert is_grounded(Decimal("1240.00"), GOOD)


def test_not_grounded_when_value_absent():
    assert not is_grounded(Decimal("9999.99"), GOOD)


def test_decimal_lossless_across_formats():
    assert is_grounded(Decimal("1240"), "Total: $1,240.00")
    assert is_grounded(Decimal("1240.00"), "Total: 1240")


def test_anti_substring_guard():
    # 1240 hides inside INV-31240, 12/40, and $11,240.00 — none is the value (D21).
    raw = "Reference INV-31240 dated 12/40 for prior period. Amount billed: $11,240.00."
    assert not is_grounded(Decimal("1240.00"), raw)


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


# --- parse_llm_json: messy model output still yields an object (D22) -----------


def test_messy_output_still_parses():
    raw = (
        "Sure! Based on the invoice, here is the JSON you asked for:\n\n"
        "```json\n"
        '{\n    "total_value": "1240.00",\n    "currency": "USD",\n}\n'  # trailing comma
        "```\n\n"
        "Let me know if you need anything else."
    )
    data = parse_llm_json(raw)
    assert data == {"total_value": "1240.00", "currency": "USD"}


def test_json_numbers_decode_to_exact_decimals():
    # parse_float=Decimal (D1): a bare 1240.00 must not become a float.
    data = parse_llm_json('{"value": 1240.00}')
    assert data["value"] == Decimal("1240.00")
    assert not isinstance(data["value"], float)


def test_truncated_output_fails_closed():
    with pytest.raises(ExtractionError):
        parse_llm_json('{"total_value": "1240.00", "curren')  # no closing brace


def test_no_json_object_fails_closed():
    with pytest.raises(ExtractionError):
        parse_llm_json("I could not find a total in that document.")


def test_invalid_json_between_braces_fails_closed():
    # Braces present but the content is not JSON — must raise, never return a
    # partial/empty object (an empty dict would silently drop every field).
    with pytest.raises(ExtractionError):
        parse_llm_json('{"total_value": 12,40, "currency": USD}')


# --- Money hard rule: Decimal-from-string, never float (D1) --------------------


def test_money_rejects_float():
    with pytest.raises(ValueError):
        Money(value=1240.00, currency="USD")  # float must be refused


def test_money_parses_string_and_int():
    assert Money(value="1,240.00".replace(",", ""), currency="USD").value == Decimal("1240.00")
    assert Money(value=1240, currency="USD").value == Decimal("1240")
