"""Core Pydantic data models for AgentGate.

Hard rule (DECISIONS D1): money is ``Decimal`` parsed from a string or int —
NEVER float. A single float in the path reintroduces `0.1 + 0.2 != 0.3`
rounding lies and would break the trustworthy deterministic core.
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Caller-text bounds (PRD SS6, D34): generous ceilings, applied uniformly —
# every wire-model string flows verbatim into the Decision echo
# (proposed_action), check detail/message strings, Langfuse traces, and the
# dashboard, so every one is bounded. Numeric strings are capped BEFORE the
# Decimal parse: a million-digit "amount" is an arithmetic/rendering DoS, and
# an uncapped value would ride validator messages into traces. Above a bound,
# validation fails and the request fail-closes to ESCALATE — over-rejection is
# the fail-closed direction.
MAX_IDENTIFIER_LENGTH = 100
MAX_VENDOR_LENGTH = 200
MAX_DATE_LENGTH = 50
MAX_CURRENCY_LENGTH = 10
MAX_DESCRIPTION_LENGTH = 500
MAX_RATIONALE_LENGTH = 2000
MAX_ADJUSTMENTS = 20
MAX_ADJUSTMENT_CHARS = 500
MAX_NUMERIC_CHARS = 50
# List and raw-text bounds (PRD SS6/SS9, D36): same exposure, same treatment as
# the string bounds above. Documents beyond these ceilings are exotic-format
# territory (PRD SS7) and escalate by failing validation.
MAX_LINE_ITEMS = 500
MAX_TAX_LINES = 50
MAX_RAW_TEXT_LENGTH = 50_000

# Every caller-facing wire model is extra="forbid" (PRD SS6/SS9, D36): an
# unknown or misspelled field is a ValidationError -> fail-closed ESCALATE,
# never a silent drop. Load-bearing, not pedantry: a misspelled `adjustments`
# key silently dropped would default to [] and flip a declared-adjustment
# ESCALATE into an agent-fixable BLOCK whose fixer then pays the full total.
# Response-side models (Decision, Check, BlockReason) are not forbid — we
# construct them.


def _normalized_identifier(v: object, field_name: str) -> str:
    """Strip surrounding whitespace (a lossless transport artifact, never part of
    an identifier) and require a non-empty result. Case, internal spacing, and
    punctuation are preserved — an identifier has no lossless entity-level
    canonical form (D8). This normalized value is what the duplicate store keys
    on, so normalization lives here at the schema boundary, not in the frame
    check: otherwise ``" INV-001"`` and ``"INV-001"`` are distinct SQLite primary
    keys and one leading space silently defeats the duplicate check (D31)."""
    if not isinstance(v, str):
        raise ValueError(f"{field_name} must be a string.")
    v = v.strip()
    if not v:
        raise ValueError(
            f"{field_name} must be a non-empty identifier "
            "(min length 1 after stripping surrounding whitespace)."
        )
    if len(v) > MAX_IDENTIFIER_LENGTH:
        raise ValueError(
            f"{field_name} is too long ({len(v)} characters, "
            f"max {MAX_IDENTIFIER_LENGTH})."
        )
    return v


def _normalized_currency(v: str) -> str:
    """Shared by ``Money.currency`` and ``Invoice.currency`` — the two are
    compared by ``currency_match``, so they get identical treatment (D12/D34)."""
    v = v.strip()
    if not v:
        raise ValueError("currency is required and must be non-empty.")
    if len(v) > MAX_CURRENCY_LENGTH:
        raise ValueError(
            f"currency is too long ({len(v)} characters, max {MAX_CURRENCY_LENGTH})."
        )
    return v


class Money(BaseModel):
    """A monetary amount. ``value`` is a ``Decimal`` parsed from a string/int;
    ``currency`` is first-class and required (DECISIONS D1/D12)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    value: Decimal
    currency: str

    @field_validator("value", mode="before")
    @classmethod
    def _no_float(cls, v: object) -> object:
        """Reject float outright and parse strings explicitly to Decimal.

        Floats are refused because they carry rounding error (D1). JSON should
        be decoded with ``parse_float=Decimal`` upstream so a float never even
        reaches here; this validator is the defensive backstop.
        """
        if isinstance(v, bool):  # bool is an int subclass — not a valid amount
            raise ValueError("Money.value must be a number, not a bool.")
        if isinstance(v, float):
            raise ValueError(
                "Money.value must be a string or int, never float "
                "(float introduces rounding error; see DECISIONS D1)."
            )
        if isinstance(v, str):
            v = v.strip()
            if len(v) > MAX_NUMERIC_CHARS:
                # Report the length, never the value — this message can end up
                # in a fail-closed reason (D34).
                raise ValueError(
                    f"Money.value string is too long ({len(v)} characters, "
                    f"max {MAX_NUMERIC_CHARS})."
                )
            try:
                return Decimal(v)
            except (InvalidOperation, ValueError) as exc:
                raise ValueError(f"Money.value is not a valid decimal: {v!r}") from exc
        if isinstance(v, int):
            return Decimal(v)
        return v

    @field_validator("currency")
    @classmethod
    def _currency_present(cls, v: str) -> str:
        return _normalized_currency(v)


class LineItemKind(str, Enum):
    charge = "charge"
    discount = "discount"
    shipping = "shipping"
    tax = "tax"


class LineItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(max_length=MAX_DESCRIPTION_LENGTH)
    quantity: Decimal
    unit_price: Money
    amount: Money
    kind: LineItemKind = LineItemKind.charge

    @field_validator("quantity", mode="before")
    @classmethod
    def _quantity_no_float(cls, v: object) -> object:
        """Quantity is ``Decimal`` (fractional billing — 2.5 hours, 1.5 kg — is
        legitimate) parsed from a string or int, never float (D1/D29). It is
        consumed by no check, so widening it only stops rejecting valid invoices.
        """
        if isinstance(v, bool):  # bool is an int subclass — not a valid quantity
            raise ValueError("quantity must be a number, not a bool.")
        if isinstance(v, float):
            raise ValueError("quantity must be a string or int, never float (D1).")
        if isinstance(v, str):
            v = v.strip()
            if len(v) > MAX_NUMERIC_CHARS:
                raise ValueError(
                    f"quantity string is too long ({len(v)} characters, "
                    f"max {MAX_NUMERIC_CHARS})."
                )
            try:
                return Decimal(v)
            except (InvalidOperation, ValueError) as exc:
                raise ValueError(f"quantity is not a valid decimal: {v!r}") from exc
        return v


class TaxLine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rate: Decimal
    amount: Money

    @field_validator("rate", mode="before")
    @classmethod
    def _rate_no_float(cls, v: object) -> object:
        if isinstance(v, float):
            raise ValueError("tax rate must be a string, never float (D1).")
        if isinstance(v, str):
            v = v.strip()
            if len(v) > MAX_NUMERIC_CHARS:
                raise ValueError(
                    f"tax rate string is too long ({len(v)} characters, "
                    f"max {MAX_NUMERIC_CHARS})."
                )
            try:
                return Decimal(v)
            except (InvalidOperation, ValueError) as exc:
                # Without this catch a junk rate escapes model_validate as a
                # raw InvalidOperation, bypassing an `except ValidationError`
                # fail-closed catch at the API boundary (D34).
                raise ValueError(f"tax rate is not a valid decimal: {v!r}") from exc
        return v


class Invoice(BaseModel):
    """The caller-supplied source document. ``subtotal`` is accepted although no
    check consumes it — it is a field of the invoice document itself, not a
    separate evidence artifact promising a check (contrast ``source.po``, D36)."""

    model_config = ConfigDict(extra="forbid")

    invoice_number: str
    vendor: str = Field(max_length=MAX_VENDOR_LENGTH)
    date: str = Field(max_length=MAX_DATE_LENGTH)
    currency: str
    line_items: list[LineItem] = Field(default_factory=list, max_length=MAX_LINE_ITEMS)
    subtotal: Optional[Money] = None
    tax_lines: list[TaxLine] = Field(default_factory=list, max_length=MAX_TAX_LINES)
    total: Money

    @field_validator("invoice_number")
    @classmethod
    def _normalize_invoice_number(cls, v: str) -> str:
        return _normalized_identifier(v, "invoice_number")

    @field_validator("currency")
    @classmethod
    def _normalize_currency(cls, v: str) -> str:
        return _normalized_currency(v)


class ActionType(str, Enum):
    approve_payment = "approve_payment"
    flag = "flag"
    reject = "reject"


class ProposedAction(BaseModel):
    """What the agent wants to do. ``adjustments`` are declared only and
    NOT verified in v1 — any non-empty diff escalates (D13/D17)."""

    model_config = ConfigDict(extra="forbid")

    action_type: ActionType
    invoice_number: str
    amount: Money
    vendor: str = Field(max_length=MAX_VENDOR_LENGTH)
    adjustments: list = []
    agent_rationale: str = Field(default="", max_length=MAX_RATIONALE_LENGTH)

    @field_validator("invoice_number")
    @classmethod
    def _normalize_invoice_number(cls, v: str) -> str:
        return _normalized_identifier(v, "invoice_number")

    @field_validator("adjustments")
    @classmethod
    def _bounded_adjustments(cls, v: list) -> list:
        """Adjustments are declared, unverified labels (D13) echoed verbatim in
        the Decision, so bound their count and per-item serialized size while
        staying shape-agnostic — typing them now would speculate on the deferred
        adjustment-verification milestone (D34)."""
        if len(v) > MAX_ADJUSTMENTS:
            raise ValueError(f"adjustments has {len(v)} items (max {MAX_ADJUSTMENTS}).")
        for i, item in enumerate(v):
            try:
                encoded = json.dumps(item)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"adjustments[{i}] is not JSON-serializable.") from exc
            if len(encoded) > MAX_ADJUSTMENT_CHARS:
                raise ValueError(
                    f"adjustments[{i}] serializes to {len(encoded)} characters "
                    f"(max {MAX_ADJUSTMENT_CHARS})."
                )
        return v


class CheckKind(str, Enum):
    critical = "critical"
    soft = "soft"


class Check(BaseModel):
    """One verification check's result (a row in the Decision's checks table)."""

    name: str
    type: CheckKind
    passed: bool
    detail: str = ""


class BlockType(str, Enum):
    agent_fixable = "agent_fixable"  # the action misread a valid source; retry (cap 2)
    source_invalid = "source_invalid"  # the source is internally inconsistent; do not retry


class BlockReason(BaseModel):
    """Machine-readable reason attached to a BLOCK or an ESCALATE (PRD §6).

    ``expected``/``received`` are ``Money`` for money-valued checks and plain
    strings for non-money ones (vendor). ``block_type`` is set for the two block
    kinds (D7) and left ``None`` for other escalate reasons (currency, vendor,
    duplicate, declared-adjustment)."""

    check: str
    expected: Optional[Money | str] = None
    received: Optional[Money | str] = None
    field_to_change: Optional[str] = None
    block_type: Optional[BlockType] = None
    message: str


class DecisionType(str, Enum):
    allow = "allow"
    block = "block"
    escalate = "escalate"


class Decision(BaseModel):
    """What AgentGate returns (PRD §6). ``trace_id``/``latency_ms``/``timestamp``
    are populated at the API boundary; the core decision logic is pure."""

    decision: DecisionType
    # Optional: None means the decision scored nothing to score against — a
    # frame-stage escalate (content checks never ran) or a malformed-input
    # fail-closed escalate — which is distinct from 0 (measured, all failed). D32.
    score: Optional[Decimal]
    checks: list[Check] = []
    reasons: list[BlockReason] = []
    evidence_used: list[str] = []
    proposed_action: Optional[ProposedAction] = None
    trace_id: Optional[str] = None
    latency_ms: Optional[int] = None
    timestamp: Optional[str] = None


# --- HTTP request envelope (PRD SS9, Slice 7a) ----------------------------------


class Source(BaseModel):
    """Caller-supplied evidence (PRD SS0/SS9). ``invoice`` is required — /verify
    runs no extraction in v1. ``raw_text`` is optional grounding evidence and is
    treated literally when present: an empty string is evidence that grounds
    nothing (the D27 total gate escalates), never coerced to "absent" (D36).
    There is deliberately no ``po`` field: the ``po_match`` check does not exist
    yet, and accepting evidence nothing reads would make ALLOW overclaim —
    ``extra="forbid"`` rejects it until the check ships (D36)."""

    model_config = ConfigDict(extra="forbid")

    invoice: Invoice
    raw_text: Optional[str] = Field(default=None, max_length=MAX_RAW_TEXT_LENGTH)


class VerifyRequest(BaseModel):
    """The POST /verify body (PRD SS9): the proposed action plus the evidence to
    verify it against. Policy is server-side config, never part of the request —
    a caller-supplied policy would be a fail-open vector."""

    model_config = ConfigDict(extra="forbid")

    proposed_action: ProposedAction
    source: Source
