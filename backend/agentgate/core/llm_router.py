"""Model-agnostic LLM router — the ONLY mocked seam in tests (DECISIONS D9/D11).

Contract: text in -> raw text out. We never trust a provider's native JSON mode
or function-calling; the caller validates the output with Pydantic. Provider-
specific glue is centralized here so business logic stays model-agnostic and the
"one-line swap to a paid model" claim is honest.

Fail-closed (D11): any provider failure raises a typed ``LLMRouterError``. The
decision layer converts that to a safe outcome (ungroundable / escalate) — it
must NEVER crash and NEVER allow.
"""

from __future__ import annotations

import os


class LLMRouterError(RuntimeError):
    """Raised on any provider failure (network, rate-limit, timeout, bad config).

    Typed so the decision layer can fail closed instead of crashing (D11)."""


def call_llm(prompt: str, *, model: str | None = None) -> str:
    """Send ``prompt`` to the configured provider and return its raw text reply.

    LiteLLM is imported lazily so that tests (which mock this seam) and CI do not
    require the dependency, keeping CI fast, free, and deterministic (D9).

    Raises:
        LLMRouterError: on any provider-side or configuration failure.
    """
    model = model or os.environ.get("AGENTGATE_LLM_MODEL", "gemini/gemini-1.5-flash")
    try:
        from litellm import completion  # lazy import — see docstring
    except ImportError as exc:  # pragma: no cover - exercised only outside CI
        raise LLMRouterError(
            "litellm is not installed. Install the 'llm' extra to make real calls."
        ) from exc

    try:  # pragma: no cover - real network call, only hit by live-marked tests
        response = completion(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        return response["choices"][0]["message"]["content"]
    except Exception as exc:  # pragma: no cover - normalize every provider error
        raise LLMRouterError(f"LLM call failed for model {model!r}: {exc}") from exc
