"""OSINT MCP server.

Exposes each connector as an MCP tool over streamable HTTP. The tools are thin
wrappers: they call the connector and return its ``to_dict()`` payload
(``raw`` evidence + extracted entity/relationship candidates). The same
connector functions are imported directly by the backend's quick-scan and
agent code, so there is one implementation per source.

Run locally:

    uv run python mcp_server.py            # streamable HTTP on :8787
    uv run mcp dev mcp_server.py           # MCP Inspector

Each tool marks itself passive or active in its description. The backend only
offers active tools (e.g. http_probe) to an agent when the investigation is in
active mode — the server itself does not enforce that; it is a tool provider.
"""

from __future__ import annotations

import os
from typing import Any

from mcp.server.mcpserver import MCPServer

from connectors import (
    crtsh_subdomains,
    dns_lookup,
    dork_search,
    europol_wanted,
    github_user,
    gravatar,
    http_probe,
    interpol_notices,
    national_wanted,
    rdap_domain,
    sanctions,
)

mcp = MCPServer("osint")


@mcp.tool()
async def dns_records(domain: str) -> dict[str, Any]:
    """Resolve DNS records (A, AAAA, MX, NS, TXT, SOA, CAA) for a domain.

    Passive. Returns IP addresses, mail/name servers, and any emails found in
    SOA/TXT records. Input: a bare domain like "example.com".
    """
    return (await dns_lookup(domain)).to_dict()


@mcp.tool()
async def rdap_registration(domain: str) -> dict[str, Any]:
    """Look up domain registration data (registrar, dates, registrant) via RDAP.

    Passive. Many registries redact registrant contacts under GDPR; that is
    expected. Input: a bare domain.
    """
    return (await rdap_domain(domain)).to_dict()


@mcp.tool()
async def crtsh_subdomains_tool(domain: str) -> dict[str, Any]:
    """Enumerate subdomains and certificates from certificate transparency logs (crt.sh).

    Passive. Good first move for mapping a domain's web presence. Input: a bare
    domain.
    """
    return (await crtsh_subdomains(domain)).to_dict()


@mcp.tool()
async def http_probe_site(domain: str) -> dict[str, Any]:
    """Fetch a website's front page and extract title, tech stack, emails, and social links.

    ACTIVE: this sends a request to the target's own web server. Only use when
    the investigation permits active collection. Input: a domain or full URL.
    """
    return (await http_probe(domain)).to_dict()


@mcp.tool()
async def gravatar_profile(email: str) -> dict[str, Any]:
    """Fetch the public Gravatar profile (avatar, name, linked accounts) for an email.

    Passive. Input: an email address.
    """
    return (await gravatar(email)).to_dict()


@mcp.tool()
async def github_profile(username: str) -> dict[str, Any]:
    """Fetch a public GitHub profile, any linked email, and the user's repositories.

    Passive. Uses GITHUB_TOKEN if set for a higher rate limit. Input: a GitHub
    username.
    """
    return (await github_user(username)).to_dict()


@mcp.tool()
async def web_search(query: str, engines: list[str] | None = None) -> dict[str, Any]:
    """Search the public web for mentions of a target, across multiple engines.

    Passive. Pass a full query or a Google-style dork (e.g. '"Jane Doe"
    site:linkedin.com'). Returns matching URLs with titles and snippets.
    Optionally restrict to specific engines (default: all available).
    """
    return (await dork_search(query, engines)).to_dict()


@mcp.tool()
async def interpol_wanted(name: str, notice_type: str = "red") -> dict[str, Any]:
    """Search INTERPOL Red/UN notices for a person by name.

    Passive. notice_type is "red" (wanted) or "un" (UN Security Council).
    Results are NAME-BASED POSSIBLE MATCHES, not confirmations — each carries
    disambiguators (date of birth, nationality) and an official listing URL.
    Never state that someone is wanted without corroborating a match.
    """
    return (await interpol_notices(name, notice_type)).to_dict()


@mcp.tool()
async def eu_most_wanted(name: str) -> dict[str, Any]:
    """Search the EU Most Wanted (ENFAST) fugitive list for a person by name.

    Passive. Covers fugitives sought by EU national police units. Results are
    NAME-BASED POSSIBLE MATCHES with a requesting country and an official
    listing URL; treat as unverified until corroborated.
    """
    return (await europol_wanted(name)).to_dict()


@mcp.tool()
async def national_wanted_lists(name: str, countries: list[str] | None = None) -> dict[str, Any]:
    """Search national police wanted lists for a person by name.

    Passive. Available sources: "fbi" (US, live), "ro" (Romania), "be"
    (Belgium), "nl" (Netherlands); pass `countries` to restrict (default: all).
    EU national sites often block server clients — those return a clear
    "unavailable" note rather than failing. Results are NAME-BASED POSSIBLE
    MATCHES with disambiguators and an official listing URL; treat as unverified.
    """
    return (await national_wanted(name, countries)).to_dict()


@mcp.tool()
async def sanctions_screen(name: str) -> dict[str, Any]:
    """Screen a person or organization name against OpenSanctions.

    Passive. Covers EU/UN/OFAC/UK sanctions, watchlists, and PEP data. Needs
    OPENSANCTIONS_API_KEY (skips with a note if unset). Results are NAME-BASED
    POSSIBLE MATCHES with topics (sanction/crime/pep), country, and an official
    entity URL; treat as unverified until corroborated.
    """
    return (await sanctions(name)).to_dict()


if __name__ == "__main__":
    mcp.run(
        transport="streamable-http",
        host=os.environ.get("MCP_HOST", "127.0.0.1"),
        port=int(os.environ.get("MCP_PORT", "8787")),
    )
