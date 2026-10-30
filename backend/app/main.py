"""FastAPI entry point.

    uv run uvicorn app.main:app --reload          # http://127.0.0.1:8000/docs

When the built frontend exists (``frontend/dist``, or ``OSINT_FRONTEND_DIR``)
it is served at ``/``, with unknown paths falling back to ``index.html`` so
client-side routes work.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from connectors._http import aclose

from .api import router
from .config import BACKEND_DIR
from .platform import Platform
from .retention import purge_expired


class SPAStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as e:
            if e.status_code != 404 or path.startswith("api/"):
                raise
            return await super().get_response("index.html", scope)


def create_app(platform: Platform | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        p = platform or Platform()
        app.state.platform = p
        purge_expired(p.db, p.settings)
        yield
        p.close()
        await aclose()

    app = FastAPI(title="OSINT platform", version="0.2.0", lifespan=lifespan)
    app.include_router(router)
    frontend = Path(os.environ.get("OSINT_FRONTEND_DIR", BACKEND_DIR.parent / "frontend" / "dist"))
    if (frontend / "index.html").is_file():
        app.mount("/", SPAStaticFiles(directory=frontend, html=True), name="frontend")
    return app


app = create_app()
