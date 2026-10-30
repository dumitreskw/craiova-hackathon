"""FastAPI entry point.

    uv run uvicorn app.main:app --reload          # http://127.0.0.1:8000/docs
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from connectors._http import aclose

from .api import router
from .platform import Platform
from .retention import purge_expired


def create_app(platform: Platform | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        p = platform or Platform()
        app.state.platform = p
        purge_expired(p.db, p.settings)
        yield
        p.close()
        await aclose()

    app = FastAPI(title="OSINT platform", version="0.1.0", lifespan=lifespan)
    app.include_router(router)
    return app


app = create_app()
