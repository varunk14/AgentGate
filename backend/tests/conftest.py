"""Shared test helpers. The router is the ONLY mocked seam (DECISIONS D9):
tests inject stubbed raw LLM outputs; grounding/decision logic runs unmocked."""

from __future__ import annotations

from pathlib import Path

SAMPLES = Path(__file__).resolve().parent.parent / "data" / "sample_invoices"


def load_sample(name: str) -> str:
    return (SAMPLES / name).read_text()
