"""Credits endpoint: provider balances parsed, failures isolated, cached."""

from __future__ import annotations

import httpx
import respx

from app import credits as cr


@respx.mock
async def test_credits_parse_and_isolate_failures(platform, monkeypatch):
    for k in ("DEHASHED_API_KEY", "HUNTER_API_KEY", "VIRUSTOTAL_API_KEY", "FIRECRAWL_API_KEY"):
        monkeypatch.setenv(k, "k")
    for k in ("TOMBA_API_KEY", "TOMBA_API_SECRET", "URLSCAN_API_KEY", "WHOISXML_API_KEY",
              "NETLAS_API_KEY", "OPENSANCTIONS_API_KEY", "COMPANIES_HOUSE_API_KEY",
              "HIBP_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    cr._cache.update(at=0.0, data=None)
    respx.get("https://api.dehashed.com/v2/info/user").mock(return_value=httpx.Response(
        200, json={"search_access": False, "search_credits": 85, "whois_credits": 0}))
    respx.get("https://api.hunter.io/v2/account").mock(return_value=httpx.Response(200, json={
        "data": {"plan_name": "Free", "reset_date": "2026-11-13",
                 "requests": {"searches": {"used": 1, "available": 50, "remaining": 49}}}}))
    respx.get("https://www.virustotal.com/api/v3/users/k/overall_quotas").mock(
        return_value=httpx.Response(500))
    respx.get("https://api.firecrawl.dev/v2/team/credit-usage").mock(return_value=httpx.Response(
        200, json={"data": {"remainingCredits": 995, "planCredits": 1000,
                            "billingPeriodEnd": "2026-11-30T21:41:22Z"}}))
    out = {r["source"]: r for r in await cr.credits(platform.db, refresh=True)}
    assert out["hunter"]["remaining"] == 49 and out["hunter"]["resets"] == "2026-11-13"
    assert out["dehashed"]["status"] == "error" and "search access" in out["dehashed"]["note"]
    assert out["firecrawl"]["used"] == 5 and out["firecrawl"]["resets"] == "2026-11-30"
    assert out["virustotal"]["status"] == "error"          # one failure doesn't break the rest
    assert out["tomba"]["status"] == "unavailable" and out["hibp"]["status"] == "unavailable"
    assert out["anthropic"]["unit"] == "USD (estimate)"
    assert "platform_used_30d" in out["hunter"]
    cached = await cr.credits(platform.db)                  # served from cache
    assert cached is cr._cache["data"]
