"""Independent source fetch — the trust-anchor upgrade (PRD SS0/SS9, D45/D46).

In fetch mode the caller names WHICH invoice; the operator-configured system of
record says what it contains. Resolution happens at the boundary (HTTP and MCP
share ``resolve_source``), never inside the pure ``decide()``. Every failure —
no store configured, record not found, corrupt/oversized/ambiguous entries —
raises the typed ``SourceOfRecordError``, which the boundary converts to a
fail-closed ESCALATE (D11/D45): if the gate cannot obtain trustworthy evidence,
it does not approve.

The reference store is a directory of ``*.json`` record files, each a
caller-mode ``Source`` document validated through the same schema and bounds as
caller input (D46). The lookup key is the ``invoice_number`` INSIDE each record
— filenames key nothing, so no path is ever constructed from caller input and
path traversal is structurally impossible.
"""

from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path
from typing import Mapping, NamedTuple, Optional, Protocol

from pydantic import ValidationError

from .schemas import Invoice, Source

# Mirrors the HTTP body cap (D36): no schema-valid record approaches it, so the
# cap only ever rejects a mistake, never a valid record.
MAX_RECORD_BYTES = 1_048_576

# Prefixed onto every evidence_used entry of a fetched decision (D45), so
# provenance is visible on the wire: "system_of_record:invoice:INV-001".
SYSTEM_OF_RECORD_PREFIX = "system_of_record:"


class SourceOfRecordError(Exception):
    """The system of record could not produce a trustworthy record.

    The boundary converts this to a fail-closed escalate Decision — never a
    crash, never an allow (D11/D45)."""


class SourceOfRecord(Protocol):
    """Anything that can resolve an invoice number to a stored Source record.

    ``DirectorySourceOfRecord`` is the v1 reference implementation; a real
    ERP/ledger connector is a later implementation of this same protocol."""

    def fetch(self, invoice_number: str) -> Source: ...


class ResolvedSource(NamedTuple):
    """What the boundary hands to ``decide()`` after resolving a request's
    Source: the invoice, its grounding text (if any), and whether the evidence
    came from the system of record (drives the provenance prefix, D45)."""

    invoice: Invoice
    raw_text: Optional[str]
    fetched: bool


class DirectorySourceOfRecord:
    """A directory of ``*.json`` record files, re-scanned on every fetch so
    operator edits need no restart (D46). Each file must be a caller-mode
    ``Source`` document within ``MAX_RECORD_BYTES``; any unparseable, invalid,
    or oversized file fails the fetch — the broken file might be the requested
    record, and a store with corrupt entries cannot anchor trust."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self._path = Path(path)

    def fetch(self, invoice_number: str) -> Source:
        """Return the record whose invoice carries ``invoice_number``.

        Raises ``SourceOfRecordError`` on a missing directory, any invalid
        record file, no match, or more than one match (an ambiguous store
        cannot anchor trust)."""
        if not self._path.is_dir():
            raise SourceOfRecordError(
                f"records directory {self._path} does not exist or is not a "
                "directory; check AGENTGATE_RECORDS_DIR."
            )
        match: Optional[Source] = None
        for file in sorted(self._path.glob("*.json")):
            record = self._load_record(file)
            # record.invoice is non-None: _load_record rejects fetch-mode files.
            if record.invoice.invoice_number == invoice_number:
                if match is not None:
                    raise SourceOfRecordError(
                        "system of record is ambiguous: more than one record "
                        f"claims invoice number {invoice_number!r}."
                    )
                match = record
        if match is None:
            raise SourceOfRecordError(
                f"invoice {invoice_number!r} was not found in the system of record."
            )
        return match

    def _load_record(self, file: Path) -> Source:
        try:
            size = file.stat().st_size
        except OSError as exc:
            raise SourceOfRecordError(
                f"record file {file.name} is unreadable ({type(exc).__name__})."
            ) from exc
        if size > MAX_RECORD_BYTES:
            raise SourceOfRecordError(
                f"record file {file.name} is {size} bytes (max {MAX_RECORD_BYTES})."
            )
        try:
            # parse_float=Decimal: D1 applies to disk — a stored numeric
            # 1240.00 stays the exact Decimal of its literal text (D35/D46).
            payload = json.loads(file.read_text(encoding="utf-8"), parse_float=Decimal)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SourceOfRecordError(
                f"record file {file.name} is not valid JSON ({type(exc).__name__})."
            ) from exc
        try:
            record = Source.model_validate(payload)
        except ValidationError as exc:
            # Operator data gets caller-grade validation (D46). Report the
            # first field only, never the offending value (D34's bounding
            # discipline applies to record contents too).
            first = exc.errors(include_url=False, include_input=False)[0]
            loc = ".".join(str(part) for part in first["loc"]) or "(record)"
            raise SourceOfRecordError(
                f"record file {file.name} is not a valid source record "
                f"({loc}: {first['msg']}; {exc.error_count()} error(s) total)."
            ) from exc
        if record.invoice is None:
            raise SourceOfRecordError(
                f"record file {file.name} must contain a structured invoice "
                "(a stored record cannot itself be in fetch mode)."
            )
        return record


def build_source_of_record(
    env: Optional[Mapping[str, str]] = None,
) -> Optional[DirectorySourceOfRecord]:
    """Wire the system of record from the environment.

    ``AGENTGATE_RECORDS_DIR`` names the records directory; unset or empty means
    no store is configured and fetch-mode requests escalate (D45). Never raises
    — a missing directory surfaces per-fetch as a fail-closed escalate, not a
    startup crash."""
    raw = (os.environ if env is None else env).get("AGENTGATE_RECORDS_DIR", "").strip()
    if not raw:
        return None
    return DirectorySourceOfRecord(raw)


def resolve_source(
    source: Source, source_of_record: Optional[SourceOfRecord]
) -> ResolvedSource:
    """Resolve a request's Source into the evidence ``decide()`` consumes (D45).

    Caller mode passes through unchanged. Fetch mode pulls the record from the
    system of record, raising ``SourceOfRecordError`` when no store is
    configured or the store cannot produce a trustworthy record — the boundary
    converts that to a fail-closed escalate."""
    if source.fetch is None:
        # The Source validator guarantees invoice is present in caller mode.
        return ResolvedSource(source.invoice, source.raw_text, fetched=False)
    if source_of_record is None:
        raise SourceOfRecordError(
            f"cannot fetch invoice {source.fetch!r}: no system of record is "
            "configured (set AGENTGATE_RECORDS_DIR to the records directory)."
        )
    record = source_of_record.fetch(source.fetch)
    if record.invoice is None:
        raise SourceOfRecordError(
            "system of record returned a record without a structured invoice."
        )
    return ResolvedSource(record.invoice, record.raw_text, fetched=True)


def system_of_record_evidence(evidence: list[str]) -> list[str]:
    """Prefix every evidence entry with the system-of-record provenance marker.

    Stamped at the boundary on fetched decisions only — ``decide()`` cannot
    know where its invoice came from; provenance is a boundary fact like
    latency (D45)."""
    return [f"{SYSTEM_OF_RECORD_PREFIX}{entry}" for entry in evidence]
