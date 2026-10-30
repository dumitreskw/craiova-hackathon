"""Live smoke test — hits the real sources. Network required.

    uv run python scripts/smoke.py [domain]

Prints a short summary per connector so you can eyeball that each one parses
real responses. Not part of the pytest suite (which is offline).
"""

from __future__ import annotations

import asyncio
import sys

from connectors import (
    crtsh_subdomains,
    dns_lookup,
    dork_search,
    europol_wanted,
    github_user,
    gravatar,
    interpol_notices,
    rdap_domain,
    sanctions,
)
from connectors._http import aclose


async def main(domain: str) -> None:
    async def show(name, coro):
        try:
            r = await coro
            ents = len(r.entities)
            print(f"  {name:16} ok   entities={ents:<4} note={r.note or '-'}")
        except Exception as e:  # noqa: BLE001 — smoke test, surface anything
            print(f"  {name:16} FAIL {type(e).__name__}: {e}")

    print(f"domain = {domain}")
    await show("dns", dns_lookup(domain))
    await show("rdap", rdap_domain(domain))
    await show("crtsh", crtsh_subdomains(domain))
    await show("dork_search", dork_search(f'"{domain}"'))
    print("email = github@github.com")
    await show("gravatar", gravatar("github@github.com"))
    print("github user = octocat")
    await show("github_user", github_user("octocat"))
    print("legal checks (name = 'Aleksandar')")
    await show("europol", europol_wanted("Aleksandar"))
    await show("interpol", interpol_notices("Aleksandar"))
    await show("sanctions", sanctions("Aleksandar"))
    await aclose()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "github.com"))
