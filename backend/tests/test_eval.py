"""Gate for the honest eval harness (DECISIONS D6/D26).

The dataset doubles as a regression suite: every case's decision is asserted
against its labeled expectation, so any check regression reddens this file.
The exact confusion counts and metric values are pinned for THIS dataset —
changing the dataset is a deliberate act that updates them together (D23).

Mutation checks (a plausible wrong impl MUST redden >=1 test):
  * intervened hard-coded True/False        -> pinned confusion counts redden
  * escalations scored as successes (fp=0)  -> pinned FP count / honest-numbers redden
  * out-of-scope dropped from known_misses  -> test_out_of_scope_reported reddens
  * precision/recall formulas swapped       -> pinned in_scope_recall (1.000) vs overall recall (0.727) reddens
  * dataset trimmed to easy cases           -> test_dataset_composition reddens
"""

from __future__ import annotations

from decimal import Decimal

from eval.harness import CaseKind, format_report, load_dataset, run_eval


def test_gate_metrics_and_honest_reporting():
    report = run_eval(load_dataset())

    # Every case decides exactly as the spec labels it (teeth for all checks).
    assert report.mismatches == []

    # Pinned confusion matrix and metrics for the current dataset. The two frame
    # cases added with the frame stage (PRD SS7 amendment, D31) shift the totals:
    # err_wrong_invoice_number (truth-wrong, escalate) -> TP, and
    # cost_reject_action_type (a legitimate reject escalated out-of-frame) -> FP
    # (D26 human-cost accounting). No existing case changed (all were frame-valid).
    # The policy amount_greater_than threshold (PRD SS8) also escalates
    # fp_trap_large_amount (250000 > 10000) as an FP.
    assert (report.tp, report.fp, report.fn, report.tn) == (8, 5, 3, 5)
    assert report.precision == Decimal("0.615")  # 8 / 13
    assert report.recall == Decimal("0.727")  # 8 / 11
    assert report.false_positive_rate == Decimal("0.500")  # 5 / 10
    assert report.in_scope_recall == Decimal("1.000")  # 8 / 8, still perfect


def test_out_of_scope_misses_reported_honestly():
    report = run_eval(load_dataset())
    assert report.known_misses == [
        "oos_doctored_source",
        "oos_renumbered_double_bill",
        "oos_unauthorized_spend",
    ]
    # And they are all ALLOWs (missed by design), not hidden interventions.
    for r in report.results:
        if r.kind is CaseKind.out_of_scope:
            assert r.decision.value == "allow"


def test_numbers_are_honest_not_rigged():
    # D6: an eval the author writes to match their own checks reports ~100%
    # and is theater. This dataset must yield imperfect overall numbers
    # (fail-closed escalations cost precision; threat-model misses cost recall)
    # while in-scope recall stays perfect (it catches what it claims to catch).
    report = run_eval(load_dataset())
    assert report.precision < 1
    assert report.recall < 1
    assert report.false_positive_rate > 0
    assert report.in_scope_recall == 1


def test_dataset_composition_requirements():
    # D6 mandates all three hard categories, plus the clean baseline.
    kinds = {case.kind for case in load_dataset()}
    assert {
        CaseKind.clean,
        CaseKind.honest_error,
        CaseKind.fp_trap,
        CaseKind.escalation_cost,
        CaseKind.out_of_scope,
    } <= kinds


def test_report_prints_all_three_metrics_and_misses():
    out = format_report(run_eval(load_dataset())).lower()
    for needle in ("precision", "recall", "false-positive rate", "known misses"):
        assert needle in out
