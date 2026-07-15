"""Tests for the hardened duplicate store (DECISIONS D33).

Two Slice-2 bugs, fixed here:
  * INSERT OR IGNORE silently swallowed the exact collision the store exists to
    detect -> mark_approved now raises a typed AlreadyApprovedError.
  * The single shared connection (default check_same_thread=True) raised
    ProgrammingError under FastAPI's threadpool -> the connection is opened with
    check_same_thread=False and every operation is serialized behind one lock.

Mutation checks (a plausible wrong impl MUST redden >=1 test):
  * revert to INSERT OR IGNORE (swallow the collision)
                       -> test_marking_twice_raises reddens
  * raise replaced with a silent return-False contract
                       -> test_marking_twice_raises reddens (no exception)
  * check_same_thread=False dropped
                       -> test_usable_from_worker_threads / test_concurrent_marks redden
  * lock dropped entirely (unserialized cross-thread access)
                       -> test_concurrent_marks_exactly_one_wins is the canary
                          (sqlite3 may tolerate races intermittently; the
                          check_same_thread mutation above is the deterministic red)
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from app.core.duplicate_store import AlreadyApprovedError, DuplicateStore


# --- collision: the second write for the same key raises, never swallows ------
def test_marking_twice_raises():
    store = DuplicateStore(":memory:")
    store.mark_approved("INV-001", approved_at="2026-07-16T10:00:00Z")
    with pytest.raises(AlreadyApprovedError) as exc_info:
        store.mark_approved("INV-001", approved_at="2026-07-16T10:05:00Z")
    # the message names the key so the failure is diagnosable from the log line
    assert "INV-001" in str(exc_info.value)
    # the original record survives the refused write
    assert store.is_approved("INV-001") is True
    store.close()


def test_distinct_keys_do_not_collide():
    store = DuplicateStore(":memory:")
    store.mark_approved("INV-001")
    store.mark_approved("INV-002")  # different key: no false collision
    assert store.is_approved("INV-001") is True
    assert store.is_approved("INV-002") is True
    store.close()


def test_store_stays_usable_after_a_refused_write():
    # The failed INSERT must not wedge the connection/transaction.
    store = DuplicateStore(":memory:")
    store.mark_approved("INV-001")
    with pytest.raises(AlreadyApprovedError):
        store.mark_approved("INV-001")
    store.mark_approved("INV-003")  # subsequent writes still work
    assert store.is_approved("INV-003") is True
    store.close()


# --- threading: the FastAPI-threadpool reality -------------------------------
def test_usable_from_worker_threads():
    # FastAPI runs sync endpoints in a threadpool: the store is created on the
    # main thread and used from workers. Slice 2's connection (default
    # check_same_thread=True) raises sqlite3.ProgrammingError here.
    store = DuplicateStore(":memory:")
    store.mark_approved("INV-100")

    def read(n: str) -> bool:
        return store.is_approved(n)

    def write(n: str) -> None:
        store.mark_approved(n)

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert pool.submit(read, "INV-100").result() is True
        assert pool.submit(read, "INV-404").result() is False
        pool.submit(write, "INV-200").result()
    assert store.is_approved("INV-200") is True
    store.close()


def test_concurrent_marks_exactly_one_wins():
    # Two threads race to record the same invoice number: exactly one write
    # succeeds and the other gets AlreadyApprovedError — never two silent
    # "successes" (the double-payment accounting must stay single-entry).
    store = DuplicateStore(":memory:")
    barrier = Barrier(2)
    outcomes: list[str] = []

    def race() -> None:
        barrier.wait()
        try:
            store.mark_approved("INV-RACE")
            outcomes.append("recorded")
        except AlreadyApprovedError:
            outcomes.append("collision")

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(race), pool.submit(race)]
        for f in futures:
            f.result()

    assert sorted(outcomes) == ["collision", "recorded"]
    assert store.is_approved("INV-RACE") is True
    store.close()
