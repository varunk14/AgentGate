"""Core Pydantic data models for AgentGate.

Hard rule (DECISIONS D1): money is ``Decimal`` parsed from a string or int —
NEVER float. A single float in the path reintroduces `0.1 + 0.2 != 0.3`
rounding lies and would break the trustworthy deterministic core.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, field_validator


class Money(BaseModel):
    """A monetary amount. ``value`` is a ``Decimal`` parsed from a string/int;
    ``currency`` is first-class and required (DECISIONS D1/D12)."""

    model_config = ConfigDict(frozen=True)

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
            try:
                return Decimal(v.strip())
            except (InvalidOperation, ValueError) as exc:
                raise ValueError(f"Money.value is not a valid decimal: {v!r}") from exc
        if isinstance(v, int):
            return Decimal(v)
        return v

    @field_validator("currency")
    @classmethod
    def _currency_present(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("currency is required and must be non-empty.")
        return v


class LineItemKind(str, Enum):
    charge = "charge"
    discount = "discount"
    shipping = "shipping"
    tax = "tax"


class LineItem(BaseModel):
    description: str
    quantity: int
    unit_price: Money
    amount: Money
    kind: LineItemKind = LineItemKind.charge


class TaxLine(BaseModel):
    rate: Decimal
    amount: Money

    @field_validator("rate", mode="before")
    @classmethod
    def _rate_no_float(cls, v: object) -> object:
        if isinstance(v, float):
            raise ValueError("tax rate must be a string, never float (D1).")
        if isinstance(v, str):
            return Decimal(v.strip())
        return v


class Invoice(BaseModel):
    invoice_number: str
    vendor: str
    date: str
    currency: str
    line_items: list[LineItem] = []
    subtotal: Optional[Money] = None
    tax_lines: list[TaxLine] = []
    total: Money


class ActionType(str, Enum):
    approve_payment = "approve_payment"
    flag = "flag"
    reject = "reject"


class ProposedAction(BaseModel):
    """What the agent wants to do. ``adjustments`` are declared only and
    NOT verified in v1 — any non-empty diff escalates (D13/D17)."""

    action_type: ActionType
    invoice_number: str
    amount: Money
    vendor: str
    adjustments: list = []
    agent_rationale: str = ""


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
    score: Decimal
    checks: list[Check] = []
    reasons: list[BlockReason] = []
    evidence_used: list[str] = []
    proposed_action: Optional[ProposedAction] = None
    trace_id: Optional[str] = None
    latency_ms: Optional[int] = None
    timestamp: Optional[str] = None
