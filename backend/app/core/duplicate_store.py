"""SQLite-backed store of already-approved invoice numbers (duplicate_check).

Kept deliberately tiny: a single table keyed by invoice_number. The store is a
read/write seam injected into the decision flow — the check reads it; a caller
records an approval only after an ALLOW decision. Timestamps are passed in (not
generated here) so the store has no hidden nondeterminism.
"""

from __future__ import annotations

import sqlite3


class DuplicateStore:
    """Tracks invoice numbers that have already been approved."""

    def __init__(self, db_path: str = ":memory:") -> None:
        self._conn = sqlite3.connect(db_path)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS approved_invoices ("
            "  invoice_number TEXT PRIMARY KEY,"
            "  approved_at TEXT"
            ")"
        )
        self._conn.commit()

    def is_approved(self, invoice_number: str) -> bool:
        cur = self._conn.execute(
            "SELECT 1 FROM approved_invoices WHERE invoice_number = ? LIMIT 1",
            (invoice_number,),
        )
        return cur.fetchone() is not None

    def mark_approved(self, invoice_number: str, approved_at: str = "") -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO approved_invoices (invoice_number, approved_at) "
            "VALUES (?, ?)",
            (invoice_number, approved_at),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
