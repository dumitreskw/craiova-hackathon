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
from .archive_breach import hibp, wayback
from .crtsh import crtsh_subdomains
from .dehashed import dehashed
from .dns_lookup import dns_lookup
from .dork_search import dork_search
from .email_finder import hunter, tomba
from .europol import europol_wanted
from .github_user import github_user
from .gravatar import gravatar
from .http_probe import http_probe
from .interpol import interpol_notices
from .national_wanted import national_wanted
from .otx import otx_passive_dns
from .rdap import rdap_domain
from .recon_tools import dnstwist, holehe, maigret, subfinder
from .registries import anaf, companies_house, gleif, vies
from .sanctions import sanctions
from .screenshot import screenshot
from .urlscan import urlscan
from .virustotal import virustotal
from .web_read import web_read

Connector = Callable[..., Awaitable[ConnectorResult]]


@dataclass(frozen=True, slots=True)
class ConnectorSpec:
    name: str
    fn: Connector
    accepts: EntityType
    passive: bool
    description: str
    requires_key: str | None = None  # env var name, or None
    # "free" | "metered" (free quota that runs out) | "paid" (spends credits).
    # The quick scan only runs free connectors unless the investigation opts in.
    cost: str = "free"
    # Calls allowed per ``quota_days`` across all investigations (ledger-enforced).
    quota: int | None = None
    quota_days: int = 30
    timeout: int = 90  # seconds
    # Only run on the investigation's own targets, never on discovered entities.
    seed_only: bool = False


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
                      "Render a URL in headless Chromium as banner-stamped PNG evidence.",
                      timeout=60),
        # Like screenshot, fetches the URL's host; same target-host rule.
        ConnectorSpec("web_read", web_read, EntityType.URL, True,
                      "Read a web page (Firecrawl / Lightpanda / HTTP): clean text, contacts, "
                      "social links, structured data.", timeout=60),
        ConnectorSpec("subfinder", subfinder, EntityType.DOMAIN, True,
                      "Subdomains from ~40 passive sources at once (CT logs, passive DNS, "
                      "search APIs), using the configured API keys.", timeout=180),
        ConnectorSpec("urlscan", urlscan, EntityType.DOMAIN, True,
                      "What public urlscan.io scans of the domain saw: pages, IPs, ASNs, "
                      "servers over time.", requires_key="URLSCAN_API_KEY"),
        ConnectorSpec("otx_domain", otx_passive_dns, EntityType.DOMAIN, True,
                      "AlienVault OTX passive DNS: past IPs and hostnames of the domain.",
                      requires_key="OTX_API_KEY", timeout=100),
        ConnectorSpec("otx_ip", otx_passive_dns, EntityType.IP, True,
                      "AlienVault OTX passive DNS: domains that have pointed at this IP.",
                      requires_key="OTX_API_KEY", timeout=100),
        ConnectorSpec("dnstwist", dnstwist, EntityType.DOMAIN, True,
                      "Registered look-alike domains (typosquats); look-alikes with MX "
                      "records are a phishing risk.", timeout=250, seed_only=True),
        ConnectorSpec("hunter", hunter, EntityType.DOMAIN, True,
                      "Hunter.io: email addresses published for a company domain, with "
                      "names, roles and the address pattern.", requires_key="HUNTER_API_KEY",
                      cost="metered", quota=50, quota_days=30, seed_only=True),
        ConnectorSpec("tomba", tomba, EntityType.DOMAIN, True,
                      "Tomba.io: email addresses published for a company domain.",
                      requires_key="TOMBA_API_KEY", cost="metered", quota=25, quota_days=30,
                      seed_only=True),
        ConnectorSpec("dehashed", dehashed, EntityType.EMAIL, True,
                      "DeHashed: which breaches an email (or username / domain) appears in "
                      "and which data classes leaked. Never returns passwords.",
                      requires_key="DEHASHED_API_KEY", cost="paid", seed_only=True),
        ConnectorSpec("holehe", holehe, EntityType.EMAIL, True,
                      "Which online services an email address is registered on (checks "
                      "those services, not the mailbox).", timeout=160, seed_only=True),
        ConnectorSpec("maigret", maigret, EntityType.USERNAME, True,
                      "Where a username exists across the top ~300 sites.", timeout=320,
                      seed_only=True),
        # Official registries: who a company legally is.
        ConnectorSpec("anaf", anaf, EntityType.ORGANIZATION, True,
                      "Romanian tax registry (ANAF) by CUI: legal name, J number, CAEN, "
                      "incorporation date, addresses, VAT and inactive/struck-off status."),
        ConnectorSpec("vies", vies, EntityType.ORGANIZATION, True,
                      "EU VAT check (VIES) by VAT number, e.g. RO14399840: valid or not, "
                      "registered name and address."),
        ConnectorSpec("gleif", gleif, EntityType.ORGANIZATION, True,
                      "Global LEI index by legal name or LEI: registration number, addresses, "
                      "status, ultimate parent company."),
        ConnectorSpec("companies_house", companies_house, EntityType.ORGANIZATION, True,
                      "UK Companies House by company number (GB12345678) or exact name: "
                      "profile, officers, persons with significant control.",
                      requires_key="COMPANIES_HOUSE_API_KEY"),
        ConnectorSpec("wayback", wayback, EntityType.DOMAIN, True,
                      "Internet Archive history of a domain: age, old subdomains, archived "
                      "files and admin/login paths no longer on the live site.", timeout=90),
        ConnectorSpec("virustotal_domain", virustotal, EntityType.DOMAIN, True,
                      "VirusTotal: security-engine verdicts, reputation, categories and "
                      "passive DNS for a domain.", requires_key="VIRUSTOTAL_API_KEY",
                      cost="metered", quota=240, quota_days=1, timeout=120),
        ConnectorSpec("virustotal_ip", virustotal, EntityType.IP, True,
                      "VirusTotal: verdicts, reputation, ASN/owner and domains seen on an IP.",
                      requires_key="VIRUSTOTAL_API_KEY", cost="metered", quota=240,
                      quota_days=1, timeout=120),
        ConnectorSpec("hibp", hibp, EntityType.EMAIL, True,
                      "Have I Been Pwned: breaches an email appears in, with dates and data "
                      "classes. Never returns passwords.", requires_key="HIBP_API_KEY",
                      seed_only=True),
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
    "web_read",
    "subfinder",
    "urlscan",
    "otx_passive_dns",
    "dnstwist",
    "hunter",
    "tomba",
    "dehashed",
    "holehe",
    "maigret",
    "anaf",
    "vies",
    "gleif",
    "companies_house",
    "wayback",
    "hibp",
    "virustotal",
]
