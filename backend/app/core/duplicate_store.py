"""SQLite-backed store of already-approved invoice numbers (duplicate_check).

Kept deliberately tiny: a single table keyed by invoice_number. The store is a
read/write seam injected into the decision flow — the check reads it at decision
time; recording an approval is a separate, explicit post-payment call, never a
side effect of an ALLOW decision (/verify stays read-only — ALLOW means
"consistent with the evidence," not "paid"; D33). Keys arrive schema-normalized
(D31) — the store does not re-normalize. Timestamps are passed in (not generated
here) so the store has no hidden nondeterminism.

Thread safety (D33): FastAPI serves sync endpoints from a threadpool, so the
single shared connection is opened with ``check_same_thread=False`` and every
operation is serialized behind one lock. A per-operation connection would
silently break the ``":memory:"`` default — each new ``":memory:"`` connection
is a fresh empty database.
"""

from __future__ import annotations

import sqlite3
import threading


class AlreadyApprovedError(ValueError):
    """The invoice number is already recorded as approved. Raised instead of
    silently ignoring the write (D33): v1 has no replay path, so a second write
    for the same key is always a caller bug or a double-payment that already
    slipped past the gate — fail loud, never swallow."""


class DuplicateStore:
    """Tracks invoice numbers that have already been approved."""

    def __init__(self, db_path: str = ":memory:") -> None:
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        with self._lock:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS approved_invoices ("
                "  invoice_number TEXT PRIMARY KEY,"
                "  approved_at TEXT"
                ")"
            )
            self._conn.commit()

    def is_approved(self, invoice_number: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "SELECT 1 FROM approved_invoices WHERE invoice_number = ? LIMIT 1",
                (invoice_number,),
            )
            return cur.fetchone() is not None

    def mark_approved(self, invoice_number: str, approved_at: str = "") -> None:
        """Record an approved invoice number. Raises ``AlreadyApprovedError`` if
        it is already recorded — the collision the store exists to detect is
        never swallowed (D33)."""
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO approved_invoices (invoice_number, approved_at) "
                    "VALUES (?, ?)",
                    (invoice_number, approved_at),
                )
            except sqlite3.IntegrityError as exc:
                self._conn.rollback()  # leave no transaction wedged open
                raise AlreadyApprovedError(
                    f"Invoice {invoice_number!r} is already recorded as approved; "
                    "refusing to record it again (possible double-payment or a "
                    "caller bug — v1 has no replay path)."
                ) from exc
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()
