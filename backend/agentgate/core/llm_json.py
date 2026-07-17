"""JSON recovery from messy LLM output.

Real LLM output is messy (D22): markdown fences, chatty preamble, trailing
commas, truncation. ``parse_llm_json`` recovers the JSON object when it can and
fails closed (typed ``ExtractionError``) when it cannot (D11). Its consumer is
the demo agent's proposal/re-proposal step (``agent/graph.py``) — the
verification path itself is fully deterministic and never calls a model.

Money is decoded with ``parse_float=Decimal`` so a float is NEVER created in the
path (D1): a bare JSON number like ``1240.00`` becomes ``Decimal('1240.00')``
losslessly from its source text.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal

# Strips ``,`` that sits directly before a closing ``}`` or ``]`` (a trailing comma).
_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")


class ExtractionError(RuntimeError):
    """Raised when a trustworthy JSON object cannot be recovered from LLM output.

    Fail-closed (D11): callers route this to a human, never a crash or an allow."""


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
