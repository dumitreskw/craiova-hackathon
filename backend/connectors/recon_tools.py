"""Connectors wrapping the field-proven Kali command-line tools.

All four are passive toward the target:

* ``subfinder`` — subdomains from ~40 passive sources at once (certificate
  logs, passive DNS, search APIs), using the API keys in the environment.
  Faster and wider than crt.sh alone.
* ``dnstwist`` — registered look-alike domains (typosquats, homoglyphs) with
  their A/MX/NS records; a registered look-alike with MX is a phishing risk.
* ``holehe`` — which online services an email address is registered on. It
  queries those services' signup/recovery endpoints, never the mailbox; some
  services may notice, so it only runs on the investigation's own targets.
* ``maigret`` — where a username exists across hundreds of sites. Also
  seed-only.

The binaries come from the Docker image, or from the Kali VM over ssh
(``OSINT_KALI_SSH``); see ``_cli``.
"""

from __future__ import annotations

import json
import os
import re
import tempfile

import yaml

from . import _cli
from .base import ConnectorResult, EntityCandidate, EntityType, RelationshipCandidate

_DOMAIN = re.compile(r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
_EMAIL = re.compile(r"^[\w.+-]+@[\w-]+(\.[\w-]+)+$")
_USERNAME = re.compile(r"^[A-Za-z0-9_.-]{2,39}$")

# subfinder provider name -> environment variable(s)
_SUBFINDER_KEYS = {
    "alienvault": "OTX_API_KEY", "certspotter": "CERTSPOTTER_API_KEY",
    "chaos": "CHAOS_API_KEY", "dnsdumpster": "DNSDUMPSTER_API_KEY",
    "netlas": "NETLAS_API_KEY", "urlscan": "URLSCAN_API_KEY",
    "whoisxmlapi": "WHOISXML_API_KEY",
}


def _domain(value: str) -> str:
    d = value.strip().lower().rstrip(".")
    if not _DOMAIN.match(d):
        raise ValueError(f"not a domain: {value!r}")
    return d


# --- subfinder ---------------------------------------------------------------------

async def subfinder(domain: str) -> ConnectorResult:
    domain = _domain(domain)
    args = ["-d", domain, "-silent", "-oJ", "-all", "-max-time", "2"]
    local = _cli.resolve("subfinder")[0] != "ssh"
    config = None
    if local:
        providers = {p: [os.environ[k]] for p, k in _SUBFINDER_KEYS.items() if os.environ.get(k)}
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as config:
            yaml.safe_dump(providers, config)
        args += ["-pc", config.name]
    try:
        code, out, err = await _cli.run("subfinder", args, timeout=170)
    finally:
        if config:
            os.unlink(config.name)
    hosts: dict[str, set[str]] = {}
    for line in out.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        host = str(row.get("host", "")).lower().lstrip("*.")
        if host == domain or host.endswith("." + domain):
            hosts.setdefault(host, set()).add(row.get("source") or "?")
    if code != 0 and not hosts:
        raise RuntimeError(err.strip()[-300:] or f"subfinder exited {code}")
    entities = [EntityCandidate(EntityType.DOMAIN, domain)]
    rels = []
    for host in sorted(hosts):
        if host == domain:
            continue
        entities.append(EntityCandidate(EntityType.DOMAIN, host,
                                        {"is_subdomain": True, "sources": sorted(hosts[host])}))
        rels.append(RelationshipCandidate("subdomain_of", EntityType.DOMAIN, host,
                                          EntityType.DOMAIN, domain))
    return ConnectorResult(
        source="subfinder", passive=True,
        raw={"domain": domain, "count": len(hosts),
             "subdomains": {h: sorted(s) for h, s in sorted(hosts.items())}},
        entities=entities, relationships=rels,
        note=None if hosts else "no subdomains found")


# --- dnstwist ------------------------------------------------------------------------

async def dnstwist(domain: str) -> ConnectorResult:
    domain = _domain(domain)
    _code, out, err = await _cli.run(
        "dnstwist", ["--registered", "--format", "json", "--threads", "80",
                     "--nameservers", "1.1.1.1,8.8.8.8,9.9.9.9", domain], timeout=240)
    try:
        rows = json.loads(out or "[]")
    except ValueError as e:
        raise RuntimeError(err.strip()[-300:] or "dnstwist returned no JSON") from e
    lookalikes = []
    entities = [EntityCandidate(EntityType.DOMAIN, domain)]
    rels = []
    for row in rows:
        name = str(row.get("domain", "")).lower()
        if not name or name == domain or row.get("fuzzer") == "*original":
            continue
        info = {"fuzzer": row.get("fuzzer"), "a": row.get("dns_a"), "mx": row.get("dns_mx"),
                "ns": row.get("dns_ns"), "phishing_risk": bool(row.get("dns_mx"))}
        lookalikes.append({"domain": name, **info})
        entities.append(EntityCandidate(EntityType.DOMAIN, name, {
            "lookalike_of": domain, "fuzzer": info["fuzzer"],
            "has_mx": info["phishing_risk"]}, match_quality=0.9))
        rels.append(RelationshipCandidate("lookalike_of", EntityType.DOMAIN, name,
                                          EntityType.DOMAIN, domain, {"fuzzer": info["fuzzer"]}))
    return ConnectorResult(
        source="dnstwist", passive=True,
        raw={"domain": domain, "registered_lookalikes": lookalikes,
             "with_mx": sum(1 for x in lookalikes if x["phishing_risk"])},
        entities=entities, relationships=rels,
        note=None if lookalikes else "no registered look-alike domains")


# --- holehe --------------------------------------------------------------------------

_HOLEHE_HIT = re.compile(r"^\[\+\]\s+(\S+)")


async def holehe(email: str) -> ConnectorResult:
    email = email.strip().lower()
    if not _EMAIL.match(email):
        raise ValueError(f"not an email address: {email!r}")
    code, out, err = await _cli.run(
        "holehe", ["--only-used", "--no-color", "--no-clear", email], timeout=150)
    # Hits look like "[+] instagram.com"; the legend line "[+] Email used" has no dot.
    sites = sorted({m.group(1) for line in out.splitlines()
                    if (m := _HOLEHE_HIT.match(line.strip()))
                    and "." in m.group(1) and "@" not in m.group(1)})
    if code != 0 and not sites:
        raise RuntimeError(err.strip()[-300:] or f"holehe exited {code}")
    entities = [EntityCandidate(EntityType.EMAIL, email)]
    rels = []
    for site in sites:
        key = f"{site}:{email}"
        entities.append(EntityCandidate(EntityType.SOCIAL_PROFILE, key, {
            "platform": site, "account_for": email, "evidence": "registration check"},
            match_quality=0.7))
        rels.append(RelationshipCandidate("registered_on", EntityType.EMAIL, email,
                                          EntityType.SOCIAL_PROFILE, key))
    return ConnectorResult(source="holehe", passive=True,
                           raw={"email": email, "registered_on": sites},
                           entities=entities, relationships=rels,
                           note=None if sites else "no registrations detected")


# --- maigret -------------------------------------------------------------------------

_MAIGRET_HIT = re.compile(r"^\[\+\]\s+([^:]+):\s+(https?://\S+)")


async def maigret(username: str) -> ConnectorResult:
    username = username.strip().lstrip("@")
    if not _USERNAME.match(username):
        raise ValueError(f"not a username: {username!r}")
    code, out, err = await _cli.run(
        "maigret", [username, "--top-sites", "300", "--timeout", "15", "--no-color",
                    "--no-progressbar", "--no-recursion"], timeout=300)
    found = {}
    for line in out.splitlines():
        m = _MAIGRET_HIT.match(line.strip())
        # Keep profile URLs only: the handle must be in the URL, and search
        # pages ("?q=octocat") are not accounts.
        if m and username.lower() in m.group(2).lower() and "?" not in m.group(2):
            found[m.group(1).strip()] = m.group(2)
    if code != 0 and not found:
        raise RuntimeError(err.strip()[-300:] or f"maigret exited {code}")
    entities = [EntityCandidate(EntityType.USERNAME, username.lower())]
    rels = []
    for site, url in sorted(found.items()):
        key = f"{site.lower()}:{username.lower()}"
        entities.append(EntityCandidate(EntityType.SOCIAL_PROFILE, key, {
            "platform": site, "url": url}, match_quality=0.6))
        rels.append(RelationshipCandidate("has_profile", EntityType.USERNAME, username.lower(),
                                          EntityType.SOCIAL_PROFILE, key))
    return ConnectorResult(source="maigret", passive=True,
                           raw={"username": username, "found": found},
                           entities=entities, relationships=rels,
                           note=None if found else "no accounts found for this username")
