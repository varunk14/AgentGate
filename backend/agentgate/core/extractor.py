"""LLM extraction. Splits the LLM *call* (behind ``llm_router``) from the
*logic* that parses its raw output, so tests exercise the parsing unmocked
while only the router is mocked (DECISIONS D9).

Real LLM output is messy (D22): markdown fences, chatty preamble, trailing
commas, string-vs-int values, nulls, truncation. ``parse_llm_json`` recovers the
JSON object when it can and fails closed (typed error) when it cannot — the
decision layer turns that into ``ungroundable`` rather than crashing or allowing
(D11).

Money is decoded with ``parse_float=Decimal`` so a float is NEVER created in the
path (D1): a bare JSON number like ``1240.00`` becomes ``Decimal('1240.00')``
losslessly from its source text.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal
from typing import Callable

from .llm_router import LLMRouterError, call_llm
from .schemas import Money

# Strips ``,`` that sits directly before a closing ``}`` or ``]`` (a trailing comma).
_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")


class ExtractionError(RuntimeError):
    """Raised when a trustworthy value cannot be extracted from the LLM output.

    Fail-closed (D11): the decision layer maps this to ``ungroundable``."""


_EXTRACTION_PROMPT = (
    "Extract the grand total from this invoice text. Respond with ONLY a JSON "
    'object of the form {{"total_value": "<amount as a string>", '
    '"currency": "<ISO code>"}}. Do not include any other text.\n\n'
    "Invoice text:\n{raw_text}"
)


def parse_llm_json(raw: str) -> dict:
    """Recover a JSON object from messy LLM text.

    Handles markdown fences, leading/trailing prose, and trailing commas by
    slicing from the first ``{`` to the last ``}`` and cleaning within. Raises
    ``ExtractionError`` if no valid JSON object can be recovered (e.g. truncated
    output with no closing brace).
    """
    if raw is None:
        raise ExtractionError("LLM returned no content.")
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ExtractionError("No JSON object found in LLM output.")
    candidate = raw[start : end + 1]
    candidate = _TRAILING_COMMA_RE.sub(r"\1", candidate)
    try:
        # parse_float=Decimal: never construct a float for a money value (D1).
        data = json.loads(candidate, parse_float=Decimal)
    except json.JSONDecodeError as exc:
        raise ExtractionError(f"LLM output is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ExtractionError("LLM output JSON is not an object.")
    return data


def total_from_payload(data: dict) -> Money:
    """Build the invoice total ``Money`` from a parsed extraction payload.

    Fails closed on a missing/null total or an invalid amount (D11). A float is
    rejected by ``Money`` itself (D1) — though ``parse_float=Decimal`` upstream
    means one never arrives here from JSON.
    """
    value = data.get("total_value")
    if value is None:
        raise ExtractionError("Extraction payload has no 'total_value'.")
    currency = data.get("currency")
    if currency is None or (isinstance(currency, str) and not currency.strip()):
        raise ExtractionError("Extraction payload has no 'currency'.")
    try:
        return Money(value=value, currency=currency)
    except ValueError as exc:
        raise ExtractionError(f"Invalid total in extraction payload: {exc}") from exc


def extract_total(
    raw_text: str, *, llm_call: Callable[[str], str] = call_llm
) -> Money:
    """Extract the invoice total from ``raw_text`` via the LLM router.

    ``llm_call`` is the injected router seam (mocked in tests). Any router
    failure (``LLMRouterError``) or parse failure is normalized to
    ``ExtractionError`` so the caller can fail closed (D9/D11).
    """
    prompt = _EXTRACTION_PROMPT.format(raw_text=raw_text)
    try:
        raw_output = llm_call(prompt)
    except LLMRouterError as exc:
        raise ExtractionError(f"LLM router failed: {exc}") from exc
    data = parse_llm_json(raw_output)
    return total_from_payload(data)
