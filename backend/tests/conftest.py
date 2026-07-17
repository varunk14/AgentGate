"""Shared test helpers. The router is the ONLY mocked seam (DECISIONS D9):
tests inject stubbed raw LLM outputs; grounding/decision logic runs unmocked."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from agentgate.core.llm_router import LLMRouterError

SAMPLES = Path(__file__).resolve().parent.parent / "data" / "sample_invoices"


def load_sample(name: str) -> str:
    return (SAMPLES / name).read_text()


def stub_router(output: str) -> Callable[[str], str]:
    """A fake router that ignores the prompt and returns canned raw LLM text."""

    def _call(prompt: str) -> str:  # noqa: ARG001 - prompt intentionally ignored
        return output

    return _call


def failing_router(message: str = "rate limit") -> Callable[[str], str]:
    """A fake router that fails like a real provider would (rate-limit/timeout)."""

    def _call(prompt: str) -> str:  # noqa: ARG001
        raise LLMRouterError(message)

    return _call
