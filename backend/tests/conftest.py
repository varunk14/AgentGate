"""Shared test helpers. The router is the only SUBSTITUTION seam (DECISIONS D9):
tests inject stubbed raw LLM outputs and run the real parsing/grounding/decision
logic unmocked; fault-injection doubles (a raising decide, a raising tracer)
exercise otherwise-unreachable failure paths and never return canned verdicts."""

from __future__ import annotations

from pathlib import Path

SAMPLES = Path(__file__).resolve().parent.parent / "data" / "sample_invoices"


def load_sample(name: str) -> str:
    return (SAMPLES / name).read_text()
