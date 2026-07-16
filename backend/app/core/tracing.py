"""Langfuse tracing behind a tiny seam (PRD SS9, D37).

Tracing is an observer, never a gate: a tracing failure must never change,
delay, or crash a decision, so the API boundary records through
``record_safely`` (which swallows and logs every tracer error), and without
LANGFUSE keys — or without the optional ``langfuse`` package — ``build_tracer``
returns a no-op and the service runs identically. The langfuse import is lazy
for the same reason litellm's is (D9): the core must not drag optional
provider SDKs.

What gets recorded is decided at the call site (api/verify.py): the validated
request dumps (already bounded at the schema, D34) and the Decision; of
``raw_text`` only its LENGTH, never its content; on the fail-closed path only
the body length, never the raw body.
"""

from __future__ import annotations

import logging
import os
from typing import Mapping, Optional, Protocol

logger = logging.getLogger("agentgate.tracing")


class Tracer(Protocol):
    """The seam the API records through. Implementations must not be trusted to
    succeed — every call goes through ``record_safely``."""

    def record(
        self, *, trace_id: str, name: str, input: dict, output: dict
    ) -> None: ...

    def shutdown(self) -> None: ...


class NoopTracer:
    """Tracing disabled: records nothing, never fails."""

    def record(self, *, trace_id: str, name: str, input: dict, output: dict) -> None:
        return None

    def shutdown(self) -> None:
        return None


class LangfuseTracer:
    """Langfuse-backed tracer (v2 client API — pinned in pyproject, D37).
    Export is batched by the client; ``shutdown`` flushes what is queued."""

    def __init__(self, public_key: str, secret_key: str, host: Optional[str] = None) -> None:
        from langfuse import Langfuse  # lazy: optional dependency

        kwargs: dict = {"public_key": public_key, "secret_key": secret_key}
        if host:
            kwargs["host"] = host
        self._client = Langfuse(**kwargs)

    def record(self, *, trace_id: str, name: str, input: dict, output: dict) -> None:
        self._client.trace(id=trace_id, name=name, input=input, output=output)

    def shutdown(self) -> None:
        self._client.flush()


def build_tracer(env: Optional[Mapping[str, str]] = None) -> Tracer:
    """A ``LangfuseTracer`` when both keys are configured and the client
    constructs; otherwise a ``NoopTracer``. Never raises — a broken tracing
    setup must not stop the gate from serving (D37)."""
    env = os.environ if env is None else env
    public_key = env.get("LANGFUSE_PUBLIC_KEY", "")
    secret_key = env.get("LANGFUSE_SECRET_KEY", "")
    if not public_key or not secret_key:
        return NoopTracer()
    try:
        return LangfuseTracer(public_key, secret_key, env.get("LANGFUSE_HOST") or None)
    except Exception:  # missing package, bad keys, network config — trace-less, not down
        logger.warning("langfuse tracer unavailable; tracing disabled", exc_info=True)
        return NoopTracer()


def record_safely(
    tracer: Tracer, *, trace_id: str, name: str, input: dict, output: dict
) -> None:
    """Record a trace, swallowing (and logging) any tracer failure — the
    decision was already made and must reach the caller unchanged (D37)."""
    try:
        tracer.record(trace_id=trace_id, name=name, input=input, output=output)
    except Exception:
        logger.warning("tracing failed; decision unaffected", exc_info=True)
