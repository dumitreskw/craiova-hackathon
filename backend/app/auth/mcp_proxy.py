"""``/mcp``: the OSINT MCP server, behind the platform's own authentication.

The MCP server (``mcp_server.py``) has no auth of its own and is only reachable
inside the compose network; the app forwards authenticated requests to it
(the middleware already checked the caller: a workspace API key for Claude
Code / Claude Desktop, or a signed-in session). Streaming responses (SSE) pass
through unbuffered.
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException, Request
from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse

from .middleware import get_auth

router = APIRouter(include_in_schema=False)

# Hop-by-hop headers, and ours (the caller's credentials never reach the MCP server).
_DROP_REQ = {"host", "connection", "keep-alive", "transfer-encoding", "te", "upgrade",
             "proxy-authorization", "authorization", "cookie", "content-length", "x-org"}
_DROP_RESP = {"connection", "keep-alive", "transfer-encoding", "content-length", "server"}

_client: httpx.AsyncClient | None = None


def _http() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=None))
    return _client


async def aclose() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


@router.api_route("/mcp", methods=["GET", "POST", "DELETE"])
@router.api_route("/mcp/{rest:path}", methods=["GET", "POST", "DELETE"])
async def mcp_proxy(request: Request, rest: str = ""):
    upstream = get_auth(request.app).s.mcp_upstream
    if not upstream:
        raise HTTPException(404, "the MCP endpoint is not enabled on this platform")
    url = upstream + request.url.path
    if request.url.query:
        url += "?" + request.url.query
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _DROP_REQ}
    req = _http().build_request(request.method, url, headers=headers, content=request.stream())
    try:
        resp = await _http().send(req, stream=True)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"MCP server unreachable: {e}") from e
    return StreamingResponse(
        resp.aiter_raw(), status_code=resp.status_code,
        headers={k: v for k, v in resp.headers.items() if k.lower() not in _DROP_RESP},
        background=BackgroundTask(resp.aclose))
