"""Honest evaluation harness (DECISIONS D6/D26).

Runs the real ``decide()`` over a hand-labeled dataset of structured invoices —
no LLM in the path, fully deterministic, safe for CI.

Semantics (D26):
  * Every case carries TWO independent labels: ``truth_wrong`` (is the proposed
    action actually wrong?) and ``expected_decision`` (what the checks are
    specced to return).
  * An INTERVENTION is any non-ALLOW decision (block or escalate).
  * Escalating a legitimate payment counts as a FALSE POSITIVE — fail-closed
    routing is correct behavior AND a real human cost, and hiding that cost
    would let the gate escalate everything and look perfect.
  * Out-of-scope wrongs the gate ALLOWs by design (renumbered double-bills,
    forged-but-consistent sources) land as recall misses and are reported by
    name; in-scope recall is reported alongside.

The metric is NOT "block-accuracy": precision, recall, and false-positive rate
are reported separately (D6).

Run from ``backend/``:  python -m eval.harness
"""

from __future__ import annotations

import json
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

from app.core.decision import decide
from app.core.schemas import DecisionType, Invoice, ProposedAction

DATASET_PATH = Path(__file__).resolve().parent / "dataset.jsonl"


class CaseKind(str, Enum):
    clean = "clean"  # ordinary legit invoice, should ALLOW
    honest_error = "honest_error"  # the mistakes the gate exists to catch
    fp_trap = "fp_trap"  # legit-but-unusual; a naive check would wrongly block
    escalation_cost = "escalation_cost"  # legit but fail-closed-escalated (human cost)
    out_of_scope = "out_of_scope"  # consistent-but-wrong; missed BY DESIGN (threat model)


class EvalCase(BaseModel):
    """One labeled dataset row."""

    id: str
    kind: CaseKind
    description: str
    truth_wrong: bool  # ground truth: is the proposed action actually wrong?
    expected_decision: DecisionType  # what the checks are specced to return
    invoice: Invoice
    action: ProposedAction
    is_duplicate: bool = False
    notes: str = ""


class Bucket(str, Enum):
    tp = "tp"  # wrong action, gate intervened
    fp = "fp"  # fine action, gate intervened (incl. fail-closed escalations)
    fn = "fn"  # wrong action, gate allowed (the out-of-scope misses)
    tn = "tn"  # fine action, gate allowed


class CaseResult(BaseModel):
    case_id: str
    kind: CaseKind
    decision: DecisionType
    expected_decision: DecisionType
    matched_expected: bool
    bucket: Bucket


class Report(BaseModel):
    results: list[CaseResult]
    tp: int
    fp: int
    fn: int
    tn: int
    precision: Optional[Decimal]
    recall: Optional[Decimal]
    false_positive_rate: Optional[Decimal]
    in_scope_recall: Optional[Decimal]  # recall excluding out-of-scope cases
    known_misses: list[str]  # out-of-scope case ids the gate allowed, by design
    mismatches: list[str]  # case ids where decision != expected_decision


def load_dataset(path: Path = DATASET_PATH) -> list[EvalCase]:
    """Load JSONL cases. Money reaches Pydantic as strings; ``parse_float=Decimal``
    is the backstop so a float is never constructed (D1)."""
    cases: list[EvalCase] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        cases.append(EvalCase.model_validate(json.loads(line, parse_float=Decimal)))
    return cases


def _ratio(numerator: int, denominator: int) -> Optional[Decimal]:
    if denominator == 0:
        return None
    return (Decimal(numerator) / Decimal(denominator)).quantize(Decimal("0.001"))


def run_eval(cases: list[EvalCase]) -> Report:
    """Run ``decide()`` over every case and score interventions against truth."""
    results: list[CaseResult] = []
    for case in cases:
        decision = decide(case.invoice, case.action, is_duplicate=case.is_duplicate)
        intervened = decision.decision is not DecisionType.allow
        if case.truth_wrong:
            bucket = Bucket.tp if intervened else Bucket.fn
        else:
            bucket = Bucket.fp if intervened else Bucket.tn
        results.append(
            CaseResult(
                case_id=case.id,
                kind=case.kind,
                decision=decision.decision,
                expected_decision=case.expected_decision,
                matched_expected=decision.decision is case.expected_decision,
                bucket=bucket,
            )
        )

    tp = sum(1 for r in results if r.bucket is Bucket.tp)
    fp = sum(1 for r in results if r.bucket is Bucket.fp)
    fn = sum(1 for r in results if r.bucket is Bucket.fn)
    tn = sum(1 for r in results if r.bucket is Bucket.tn)

    in_scope = [r for r in results if r.kind is not CaseKind.out_of_scope]
    in_tp = sum(1 for r in in_scope if r.bucket is Bucket.tp)
    in_fn = sum(1 for r in in_scope if r.bucket is Bucket.fn)

    return Report(
        results=results,
        tp=tp,
        fp=fp,
        fn=fn,
        tn=tn,
        precision=_ratio(tp, tp + fp),
        recall=_ratio(tp, tp + fn),
        false_positive_rate=_ratio(fp, fp + tn),
        in_scope_recall=_ratio(in_tp, in_tp + in_fn),
        known_misses=sorted(
            r.case_id
            for r in results
            if r.kind is CaseKind.out_of_scope and r.bucket is Bucket.fn
        ),
        mismatches=sorted(r.case_id for r in results if not r.matched_expected),
    )


def format_report(report: Report) -> str:
    """Human-readable report: per-case table, confusion matrix, the three
    metrics reported separately (D6), and the known misses named honestly."""
    lines: list[str] = []
    lines.append("AgentGate evaluation — interventions vs. ground truth (D6/D26)")
    lines.append("")
    lines.append(f"{'case':<38} {'kind':<16} {'expected':<10} {'got':<10} bucket")
    for r in report.results:
        flag = "" if r.matched_expected else "   <-- MISMATCH vs spec"
        lines.append(
            f"{r.case_id:<38} {r.kind.value:<16} {r.expected_decision.value:<10} "
            f"{r.decision.value:<10} {r.bucket.value}{flag}"
        )
    lines.append("")
    lines.append(
        f"confusion matrix: TP={report.tp}  FP={report.fp}  FN={report.fn}  TN={report.tn}"
        f"  (n={len(report.results)})"
    )

    def fmt(v: Optional[Decimal]) -> str:
        return "n/a" if v is None else str(v)

    lines.append(f"precision:            {fmt(report.precision)}")
    lines.append(f"recall:               {fmt(report.recall)}")
    lines.append(f"false-positive rate:  {fmt(report.false_positive_rate)}")
    lines.append(f"in-scope recall:      {fmt(report.in_scope_recall)}")
    lines.append("")
    lines.append(
        "false positives above are fail-closed escalations of legitimate payments"
        " — the human cost of not guessing, counted honestly, not hidden."
    )
    if report.known_misses:
        lines.append("")
        lines.append("known misses (out-of-scope by the threat model, allowed by design):")
        for case_id in report.known_misses:
            lines.append(f"  - {case_id}")
        lines.append(
            "these are consistent-but-wrong cases; catching them requires the"
            " independent source fetch milestone, not better consistency checks."
        )
    if report.mismatches:
        lines.append("")
        lines.append("SPEC MISMATCHES (decision differed from the labeled expectation):")
        for case_id in report.mismatches:
            lines.append(f"  - {case_id}")
    return "\n".join(lines)


def main() -> None:
    print(format_report(run_eval(load_dataset())))


if __name__ == "__main__":
    main()
