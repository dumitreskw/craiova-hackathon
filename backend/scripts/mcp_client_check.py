"""Connect to the running MCP server over streamable HTTP, list tools, call one.

    uv run python scripts/mcp_client_check.py [url]

Proves the server speaks MCP end to end. Defaults to the local dev port.
"""

from __future__ import annotations

import asyncio
import os
import sys

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def main(url: str) -> None:
    async with streamable_http_client(url) as (read, write, *_):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print(f"connected: {len(tools.tools)} tools")
            for t in tools.tools:
                print("  -", t.name)
            print("\ncalling dns_records(example.com) ...")
            result = await session.call_tool("dns_records", {"domain": "example.com"})
            payload = result.structured_content or {}
            ents = payload.get("entities", [])
            print(f"  source={payload.get('source')} passive={payload.get('passive')} "
                  f"entities={len(ents)}")


if __name__ == "__main__":
    port = os.environ.get("MCP_PORT", "8799")
    url = sys.argv[1] if len(sys.argv) > 1 else f"http://127.0.0.1:{port}/mcp"
    asyncio.run(main(url))
