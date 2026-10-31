"""FastAPI entry point.

    uv run uvicorn app.main:app --reload          # http://127.0.0.1:8000/docs

When the built frontend exists (``frontend/dist``, or ``OSINT_FRONTEND_DIR``)
it is served: the public landing page at ``/`` and the analyst app at
``/app/`` (which signs in through ``/auth/login``). Unknown paths fall back to
``index.html``.
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
from .auth import AuthMiddleware, AuthSettings, get_auth
from .auth import mcp_proxy
from .auth import routes as auth_routes
from .config import BACKEND_DIR
from .platform import Platform
from .retention import purge_expired


class SPAStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as e:
            # Unknown API paths must stay 404s. Check the request URL, not `path`
            # (StaticFiles normalises separators, so it is "api\\..." on Windows).
            if e.status_code != 404 or scope.get("path", "").startswith(("/api/", "/mcp")):
                raise
            return await super().get_response("index.html", scope)


def create_app(platform: Platform | None = None,
               auth_settings: AuthSettings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        p = platform or Platform()
        app.state.platform = p
        auth = get_auth(app)
        purge_expired(p.db, p.settings)
        p.social.start()  # schedules social watches
        yield
        p.close()
        await auth.aclose()
        await mcp_proxy.aclose()
        await aclose()

    app = FastAPI(title="OSINT platform", version="0.3.0", lifespan=lifespan)
    app.state.auth_settings = auth_settings
    # Sign-in on /api and /mcp (app/auth); the landing page and app shell are public.
    app.add_middleware(AuthMiddleware)
    app.include_router(router)
    app.include_router(auth_routes.router)
    app.include_router(mcp_proxy.router)
    frontend = Path(os.environ.get("OSINT_FRONTEND_DIR", BACKEND_DIR.parent / "frontend" / "dist"))
    if (frontend / "index.html").is_file():
        app.mount("/", SPAStaticFiles(directory=frontend, html=True), name="frontend")
    return app


app = create_app()
