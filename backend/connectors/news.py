"""News coverage of a person or company (GDELT DOC 2.0).

Passive, free and open for any use: GDELT indexes news sites worldwide in
100+ languages, Romanian included, and returns the matching articles (URL,
title, site, language, country, date). Coverage is the recent past, not an
archive; web search (dork_search) covers older pages.

The query is passed as given, so the scan can send the name with context
(``"Ion Popescu" "Craiova"``) instead of the bare name. Articles are leads:
a namesake's interview matches just as well, so the verify agent reads the
page before anything goes in a report.

GDELT allows one request every five seconds per client; calls here are
spaced accordingly, and a rate-limit reply is a note, not a failure.
"""

from __future__ import annotations

import asyncio
import time
from urllib.parse import urlparse

from ._http import get_client
from .base import ConnectorResult, EntityCandidate, EntityType

API = "https://api.gdeltproject.org/api/v2/doc/doc"
MAX_ARTICLES = 50
MIN_INTERVAL_S = 5.5

_lock = asyncio.Lock()
_last = 0.0


def _query(value: str) -> str:
    """Phrases stay phrases; a bare name becomes one."""
    v = " ".join(value.split())
    return v if '"' in v else f'"{v}"'


def _date(seen: str | None) -> str | None:
    # "20261015T120000Z" -> "2026-10-15"
    s = (seen or "")[:8]
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 and s.isdigit() else None


async def news(value: str) -> ConnectorResult:
    global _last
    query = _query(value)
    params = {"query": query, "mode": "artlist", "format": "json",
              "maxrecords": str(MAX_ARTICLES), "sort": "datedesc", "timespan": "1y"}
    async with _lock:
        wait = MIN_INTERVAL_S - (time.monotonic() - _last)
        if wait > 0:
            await asyncio.sleep(wait)
        try:
            r = await get_client().get(API, params=params, timeout=40.0)
        finally:
            _last = time.monotonic()
    r.raise_for_status()
    body = r.text.strip()
    if not body.startswith("{"):
        # Plain-text replies: the rate limit, or a query GDELT refuses (too short...).
        return ConnectorResult(source="news", passive=True, raw={"query": query,
                                                                 "reply": body[:300]},
                               note=f"GDELT did not search: {body[:160]}")
    articles = (r.json().get("articles") or [])[:MAX_ARTICLES]
    entities, kept = [], []
    for a in articles:
        url = a.get("url")
        if not url or urlparse(url).scheme not in ("http", "https"):
            continue
        item = {"url": url, "title": a.get("title"), "site": a.get("domain"),
                "language": a.get("language"), "country": a.get("sourcecountry"),
                "date": _date(a.get("seendate"))}
        kept.append(item)
        entities.append(EntityCandidate(EntityType.URL, url, {
            "title": item["title"], "kind": "news", "site": item["site"],
            "published": item["date"], "language": item["language"],
            "country": item["country"]}, match_quality=0.5))
    return ConnectorResult(
        source="news", passive=True, raw={"query": query, "articles": kept},
        entities=entities,
        note=None if kept else "no news articles in the last year")
