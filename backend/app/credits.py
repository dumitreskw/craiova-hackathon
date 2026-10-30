"""Live credit / quota balances for every paid or metered source.

Each provider that exposes an account endpoint is asked directly (in
parallel, cached for five minutes); the rest report what the platform itself
has used, from the ledger. Nothing here spends credits: these are account
lookups.

Shape of each entry::

    {"source", "label", "status": "ok"|"unavailable"|"error",
     "used", "limit", "remaining", "unit", "period", "resets",
     "note", "dashboard_url", "platform_used_30d", "checked_at"}
"""

from __future__ import annotations

import asyncio
import datetime
import os
import time
from typing import Any

import httpx

from .models import Database, now

CACHE_SECONDS = 300
_cache: dict[str, Any] = {"at": 0.0, "data": None}

# Claude list prices per million tokens (input, output); cache reads are
# billed at 10% of input, cache writes at 125%. Used only for an estimate.
CLAUDE_PRICES = {"claude-opus-5-5": (4.0, 20.0), "claude-sonnet-5-5": (2.0, 10.0)}


def _entry(source: str, label: str, **kw) -> dict:
    base = {"source": source, "label": label, "status": "ok", "used": None, "limit": None,
            "remaining": None, "unit": None, "period": None, "resets": None, "note": None,
            "dashboard_url": None}
    base.update(kw)
    return base


def _missing(source: str, label: str, env: str, url: str) -> dict:
    return _entry(source, label, status="unavailable", note=f"{env} not set",
                  dashboard_url=url)


async def _dehashed(c: httpx.AsyncClient) -> dict:
    key = os.environ.get("DEHASHED_API_KEY")
    url = "https://app.dehashed.com/account"
    if not key:
        return _missing("dehashed", "DeHashed", "DEHASHED_API_KEY", url)
    d = (await c.get("https://api.dehashed.com/v2/info/user",
                     headers={"Dehashed-Api-Key": key})).json()
    access = bool(d.get("search_access"))
    return _entry("dehashed", "DeHashed", remaining=d.get("search_credits"), unit="search credits",
                  # Balance was read fine; searches are just not enabled.
                  status="ok" if access else "unavailable",
                  note=None if access else "search access is off on this account: searches "
                                           "will be refused until the subscription is active",
                  dashboard_url=url, extra={"whois_credits": d.get("whois_credits")})


async def _hunter(c: httpx.AsyncClient) -> dict:
    key = os.environ.get("HUNTER_API_KEY")
    url = "https://hunter.io/account"
    if not key:
        return _missing("hunter", "Hunter.io", "HUNTER_API_KEY", url)
    d = (await c.get("https://api.hunter.io/v2/account", params={"api_key": key})).json()["data"]
    s = d["requests"]["searches"]
    return _entry("hunter", "Hunter.io", used=s["used"], limit=s["available"],
                  remaining=s.get("remaining", s["available"] - s["used"]), unit="searches",
                  period="month", resets=d.get("reset_date"), dashboard_url=url,
                  note=f"plan: {d.get('plan_name')}")


async def _tomba(c: httpx.AsyncClient) -> dict:
    key, secret = os.environ.get("TOMBA_API_KEY"), os.environ.get("TOMBA_API_SECRET")
    url = "https://app.tomba.io/account"
    if not (key and secret):
        return _missing("tomba", "Tomba.io", "TOMBA_API_KEY", url)
    h = {"X-Tomba-Key": key, "X-Tomba-Secret": secret}
    me, usage = await asyncio.gather(c.get("https://api.tomba.io/v1/me", headers=h),
                                     c.get("https://api.tomba.io/v1/usage", headers=h))
    pricing = me.json()["data"].get("pricing") or {}
    limit = pricing.get("available_searches")
    used = (usage.json().get("total") or {}).get("search", 0)
    return _entry("tomba", "Tomba.io", used=used, limit=limit,
                  remaining=(limit - used) if limit is not None else None, unit="searches",
                  period=pricing.get("frequency") or "month",
                  resets=(me.json()["data"].get("expired_subscription") or "")[:10] or None,
                  dashboard_url=url, note=f"plan: {pricing.get('name')}")


async def _virustotal(c: httpx.AsyncClient) -> dict:
    key = os.environ.get("VIRUSTOTAL_API_KEY")
    url = "https://www.virustotal.com/gui/my-apikey"
    if not key:
        return _missing("virustotal", "VirusTotal", "VIRUSTOTAL_API_KEY", url)
    d = (await c.get(f"https://www.virustotal.com/api/v3/users/{key}/overall_quotas",
                     headers={"x-apikey": key})).json()["data"]
    day = d["api_requests_daily"]["user"]
    month = d["api_requests_monthly"]["user"]
    return _entry("virustotal", "VirusTotal", used=day["used"], limit=day["allowed"],
                  remaining=day["allowed"] - day["used"], unit="requests", period="day",
                  dashboard_url=url,
                  note=f"month: {month['used']}/{month['allowed']}; max 4 per minute")


async def _firecrawl(c: httpx.AsyncClient) -> dict:
    key = os.environ.get("FIRECRAWL_API_KEY")
    url = "https://www.firecrawl.dev/app/usage"
    if not key:
        return _missing("firecrawl", "Firecrawl", "FIRECRAWL_API_KEY", url)
    d = (await c.get("https://api.firecrawl.dev/v2/team/credit-usage",
                     headers={"Authorization": f"Bearer {key}"})).json()["data"]
    plan, rem = d.get("planCredits"), d.get("remainingCredits")
    return _entry("firecrawl", "Firecrawl", remaining=rem, limit=plan,
                  used=(plan - rem) if plan is not None and rem is not None else None,
                  unit="credits", period="billing period",
                  resets=(d.get("billingPeriodEnd") or "")[:10] or None, dashboard_url=url,
                  note="used only for pages that block HTTP and Lightpanda")


async def _urlscan(c: httpx.AsyncClient) -> dict:
    key = os.environ.get("URLSCAN_API_KEY")
    url = "https://urlscan.io/user/profile/"
    if not key:
        return _missing("urlscan", "urlscan.io", "URLSCAN_API_KEY", url)
    day = (await c.get("https://urlscan.io/user/quotas/", headers={"API-Key": key})
           ).json()["limits"]["search"]["day"]
    return _entry("urlscan", "urlscan.io", used=day["used"], limit=day["limit"],
                  remaining=day["remaining"], unit="searches", period="day",
                  resets=(day.get("reset") or "")[:10] or None, dashboard_url=url)


async def _whoisxml(c: httpx.AsyncClient) -> dict:
    key = os.environ.get("WHOISXML_API_KEY")
    url = "https://user.whoisxmlapi.com/products"
    if not key:
        return _missing("whoisxml", "WhoisXML (via subfinder)", "WHOISXML_API_KEY", url)
    rows = (await c.get("https://user.whoisxmlapi.com/user-service/account-balance",
                        params={"apiKey": key})).json().get("data") or []
    whois = next((r for r in rows if (r.get("product") or {}).get("name") == "WHOIS API"), None)
    return _entry("whoisxml", "WhoisXML (via subfinder)",
                  remaining=whois.get("credits") if whois else None, unit="WHOIS credits",
                  period="one-off", dashboard_url=url,
                  note=f"{len(rows)} products on the account")


async def _netlas(c: httpx.AsyncClient) -> dict:
    key = os.environ.get("NETLAS_API_KEY")
    url = "https://app.netlas.io/profile/"
    if not key:
        return _missing("netlas", "Netlas (via subfinder)", "NETLAS_API_KEY", url)
    d = (await c.get("https://app.netlas.io/api/users/current/",
                     headers={"X-API-Key": key})).json()
    plan = d.get("plan") or {}
    return _entry("netlas", "Netlas (via subfinder)", limit=plan.get("coins"), unit="coins",
                  period="month", resets=(d.get("api_key") or {}).get("next_time_coins_will_be_updated"),
                  dashboard_url=url, note=f"plan: {plan.get('name')}")


def _static() -> list[dict]:
    out = []
    if os.environ.get("OPENSANCTIONS_API_KEY"):
        out.append(_entry("opensanctions", "OpenSanctions", unit="credits",
                          note="balance only in the dashboard (2,000 trial credits, "
                               "then from EUR 0.10 per query)",
                          dashboard_url="https://www.opensanctions.org/account/"))
    else:
        out.append(_missing("opensanctions", "OpenSanctions", "OPENSANCTIONS_API_KEY",
                            "https://www.opensanctions.org/api/"))
    out.append(_entry("companies_house", "Companies House", unit="requests", period="5 minutes",
                      limit=600, note="free; rate limit only",
                      dashboard_url="https://developer.company-information.service.gov.uk/manage-applications")
               if os.environ.get("COMPANIES_HOUSE_API_KEY") else
               _missing("companies_house", "Companies House", "COMPANIES_HOUSE_API_KEY",
                        "https://developer.company-information.service.gov.uk/"))
    out.append(_entry("hibp", "Have I Been Pwned", note="key set",
                      dashboard_url="https://haveibeenpwned.com/API/Key")
               if os.environ.get("HIBP_API_KEY") else
               _missing("hibp", "Have I Been Pwned", "HIBP_API_KEY",
                        "https://haveibeenpwned.com/API/Key"))
    return out


def _claude_spend(db: Database) -> dict:
    """Estimated Claude spend this month from the token counts agents record."""
    start = datetime.date.today().replace(day=1).isoformat()
    total = 0.0
    tokens = 0
    for r in db.query("SELECT kind, stats FROM agent_runs WHERE started_at >= ?", (start,)):
        s = r["stats"] or {}
        model = "claude-sonnet-5-5" if r["kind"] == "worker" else "claude-opus-5-5"
        pin, pout = CLAUDE_PRICES[model]
        tin, tout = s.get("input_tokens", 0), s.get("output_tokens", 0)
        cread, cwrite = s.get("cache_read_input_tokens", 0), s.get("cache_creation_input_tokens", 0)
        tokens += tin + tout + cread + cwrite
        total += (tin * pin + tout * pout + cread * pin * 0.1 + cwrite * pin * 1.25) / 1e6
    return _entry("anthropic", "Claude (Anthropic)", used=round(total, 2), unit="USD (estimate)",
                  period="month", note=f"{tokens:,} tokens this month; balance and limits in "
                                       "the Anthropic console",
                  dashboard_url="https://console.anthropic.com/settings/billing")


FETCHERS = [_dehashed, _hunter, _tomba, _virustotal, _firecrawl, _urlscan, _whoisxml, _netlas]
# credit source -> connector names, for the platform's own 30-day usage
LEDGER = {"dehashed": ["dehashed"], "hunter": ["hunter"], "tomba": ["tomba"],
          "virustotal": ["virustotal_domain", "virustotal_ip"], "firecrawl": ["web_read"],
          "urlscan": ["urlscan"], "opensanctions": ["sanctions"],
          "companies_house": ["companies_house"], "hibp": ["hibp"],
          "whoisxml": ["subfinder"], "netlas": ["subfinder"]}


async def credits(db: Database, refresh: bool = False) -> list[dict]:
    if not refresh and _cache["data"] is not None and time.time() - _cache["at"] < CACHE_SECONDS:
        return _cache["data"]
    async with httpx.AsyncClient(timeout=15.0, headers={
            "User-Agent": "osint-platform/0.2 (+https://github.com/dumitreskw/craiova-hackathon)"}) as c:
        async def safe(fn):
            try:
                return await fn(c)
            except Exception as e:  # one provider failing must not hide the others
                name = fn.__name__.strip("_")
                return _entry(name, name, status="error", note=f"{type(e).__name__}: {e}"[:200])
        results = list(await asyncio.gather(*(safe(f) for f in FETCHERS)))
    results += _static()
    results.append(_claude_spend(db))
    since = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=30)).isoformat()
    checked = now()
    for r in results:
        names = LEDGER.get(r["source"])
        if names:
            r["platform_used_30d"] = db.one(
                f"SELECT count(*) AS n FROM findings WHERE fetched_at >= ? AND connector IN "
                f"({','.join('?' * len(names))})", (since, *names))["n"]
        r["checked_at"] = checked
    _cache.update(at=time.time(), data=results)
    return results
