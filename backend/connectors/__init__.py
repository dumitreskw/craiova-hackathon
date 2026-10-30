"""Connector registry.

One place that lists every connector with the metadata the orchestrator and
the MCP server need: which entity type it accepts, whether it's passive, and
whether it needs an API key. The connector callables themselves are plain
async functions imported from the sibling modules.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from .base import ConnectorResult, EntityType
from .crtsh import crtsh_subdomains
from .dns_lookup import dns_lookup
from .dork_search import dork_search
from .europol import europol_wanted
from .github_user import github_user
from .gravatar import gravatar
from .http_probe import http_probe
from .interpol import interpol_notices
from .national_wanted import national_wanted
from .rdap import rdap_domain
from .sanctions import sanctions
from .screenshot import screenshot

Connector = Callable[..., Awaitable[ConnectorResult]]


@dataclass(frozen=True, slots=True)
class ConnectorSpec:
    name: str
    fn: Connector
    accepts: EntityType
    passive: bool
    description: str
    requires_key: str | None = None  # env var name, or None


REGISTRY: dict[str, ConnectorSpec] = {
    spec.name: spec
    for spec in [
        ConnectorSpec("dns", dns_lookup, EntityType.DOMAIN, True,
                      "Resolve A/AAAA/MX/NS/TXT/SOA/CAA records for a domain."),
        ConnectorSpec("rdap", rdap_domain, EntityType.DOMAIN, True,
                      "Domain registration data (registrar, dates, registrant) via RDAP."),
        ConnectorSpec("crtsh", crtsh_subdomains, EntityType.DOMAIN, True,
                      "Subdomains and certificates from certificate transparency logs."),
        ConnectorSpec("http_probe", http_probe, EntityType.DOMAIN, False,
                      "Fetch a site's front page; extract title, tech, emails, socials. "
                      "ACTIVE: touches the target."),
        ConnectorSpec("gravatar", gravatar, EntityType.EMAIL, True,
                      "Public Gravatar profile and linked accounts for an email."),
        ConnectorSpec("github_user", github_user, EntityType.USERNAME, True,
                      "Public GitHub profile, linked email, and repositories.",
                      requires_key=None),  # optional GITHUB_TOKEN for higher limits
        ConnectorSpec("dork_search", dork_search, EntityType.PERSON, True,
                      "Search the public web across engines for mentions of the target."),
        ConnectorSpec("interpol", interpol_notices, EntityType.PERSON, True,
                      "INTERPOL Red/UN notices for a person name (unverified matches)."),
        ConnectorSpec("europol", europol_wanted, EntityType.PERSON, True,
                      "EU Most Wanted (ENFAST) fugitive list (unverified matches)."),
        ConnectorSpec("sanctions", sanctions, EntityType.PERSON, True,
                      "OpenSanctions screening: EU/UN/OFAC/UK sanctions, PEP, "
                      "watchlists (unverified matches).",
                      requires_key="OPENSANCTIONS_API_KEY"),
        ConnectorSpec("national_wanted", national_wanted, EntityType.PERSON, True,
                      "National police wanted lists (FBI live; RO/BE/NL wired) "
                      "(unverified matches)."),
        # Fetches the URL's host. The app refuses the target's own hosts unless
        # the investigation is active and in scope (app/evidence.py).
        ConnectorSpec("screenshot", screenshot, EntityType.URL, True,
                      "Render a URL in headless Chromium as banner-stamped PNG evidence."),
    ]
}


def connectors_for(entity_type: EntityType, passive_only: bool = True) -> list[ConnectorSpec]:
    """Which connectors apply to an entity type (used by quick scan / orchestrator)."""
    return [
        spec for spec in REGISTRY.values()
        if spec.accepts == entity_type and (spec.passive or not passive_only)
    ]


__all__ = [
    "REGISTRY",
    "ConnectorSpec",
    "connectors_for",
    "ConnectorResult",
    "EntityType",
    "dns_lookup",
    "rdap_domain",
    "crtsh_subdomains",
    "http_probe",
    "gravatar",
    "github_user",
    "dork_search",
    "interpol_notices",
    "europol_wanted",
    "sanctions",
    "national_wanted",
    "screenshot",
]
