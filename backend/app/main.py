"""FastAPI entrypoint (PRD SS9/SS10, Slice 7a).

``create_app`` is the factory — tests inject the store/tracer/policy; the
module-level ``app`` (the uvicorn target: ``uvicorn app.main:app``) wires from
the environment once at startup: ``AGENTGATE_DB_PATH`` for the duplicate store
(default ``:memory:`` — a server default that silently creates a database file
in the working directory would be a side effect nobody asked for, D38) and the
``LANGFUSE_*`` keys for tracing (no keys -> no-op, D37).
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from fastapi import FastAPI

from app.api.verify import router
from app.core.duplicate_store import DuplicateStore
from app.core.policy import DEFAULT_POLICY, Policy
from app.core.tracing import Tracer, build_tracer

logger = logging.getLogger("agentgate.main")


def create_app(
    *,
    store: Optional[DuplicateStore] = None,
    tracer: Optional[Tracer] = None,
    policy: Optional[Policy] = None,
) -> FastAPI:
    """Build the service. Anything not injected is wired from the environment.
    An injected store is closed by its owner (the test/caller), not by the app."""
    owns_store = store is None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        try:
            app.state.tracer.shutdown()  # flush batched traces; never fatal (D37)
        except Exception:
            logger.warning("tracer shutdown failed; ignored", exc_info=True)
        if owns_store:
            app.state.store.close()

    app = FastAPI(title="AgentGate", lifespan=lifespan)
    app.state.store = (
        store
        if store is not None
        else DuplicateStore(os.environ.get("AGENTGATE_DB_PATH", ":memory:"))
    )
    app.state.tracer = tracer if tracer is not None else build_tracer()
    app.state.policy = policy if policy is not None else DEFAULT_POLICY

    app.include_router(router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        """Liveness ping (PRD SS9) — deliberately checks nothing else."""
        return {"status": "ok"}

    return app


app = create_app()
