"""Grounding: does a claimed value literally appear in the source text?

The universal primitive (DECISIONS D20) — deliberately domain-agnostic, no
invoice imports. It confirms a number is present in the caller-supplied
``raw_text``; it does NOT prove the number is the total or that the source is
genuine (D4/D21 boundary).

Token-level, not substring (D21): money-shaped tokens are extracted with
boundaries, each parsed to ``Decimal``, and compared as Decimals. So ``1240`` is
NOT grounded inside ``INV-31240`` (identifier), ``12/40`` (date), or
``$11,240.00`` (a different amount). Matching on Decimal value is lossless — all
number formats collapse to one Decimal (D4/D10).
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

# A money-shaped token:
#   - left boundary: not preceded by an alphanumeric, '.', ',', '/', '+', or '-'
#     (so we never start mid-identifier like INV-31240, mid-number/date, or after
#     an exponent sign)
#   - optional currency symbol
#   - digits with either grouped thousands (1,240 / 11,240.00) or plain (1240.00)
#   - right boundary: not followed by more digits reachable through word/./-/,//+
#     chars — this kills dates 12/40, identifiers, the fractional half of a
#     decimal-comma amount (1240,50 must NOT yield a bare 1240), and scientific
#     notation (1E+3 must NOT yield a bare 1 or 3). The ',' and '+' belong in BOTH
#     boundary classes; omitting them from the right side let a misread European
#     total false-ground and slip past the decisive total-grounding gate (D27).
_MONEY_TOKEN_RE = re.compile(
    r"""
    (?<![\w.,/+-])                      # left boundary
    [$€£]?\s?                           # optional currency symbol
    (?P<num>
        \d{1,3}(?:,\d{3})+(?:\.\d+)?    # grouped thousands: 1,240 or 11,240.00
        |
        \d+(?:\.\d+)?                   # plain: 1240 or 1240.00
    )
    (?![\w/.,+-]*\d)                    # right boundary: no following digit
    (?![A-Za-z_])                       # not followed by a letter/underscore
    """,
    re.VERBOSE,
)


def money_tokens(text: str) -> list[Decimal]:
    """Return every money-shaped value in ``text`` as a ``Decimal``."""
    values: list[Decimal] = []
    for match in _MONEY_TOKEN_RE.finditer(text):
        raw = match.group("num").replace(",", "")
        try:
            values.append(Decimal(raw))
        except InvalidOperation:  # pragma: no cover - regex guarantees numeric
            continue
    return values


def is_grounded(value: Decimal, raw_text: str) -> bool:
    """True if ``value`` appears as a money-shaped token in ``raw_text``.

    Compared on Decimal value, so ``1240``, ``1,240.00`` and ``$1,240.00`` all
    match a claim of ``1240`` — but ``1240`` does not match inside ``INV-31240``.
    """
    if raw_text is None:
        return False
    return any(token == value for token in money_tokens(raw_text))
