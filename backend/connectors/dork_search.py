"""Multi-engine search / dork connector.

Passive — queries search engines, not the target. Runs the same query across
several engines behind a common interface, merges and de-duplicates hits by
URL, and returns them as ``URL`` entities (web mentions). This is a discovery
tool: it finds where a target is named or mentioned.

Only the DuckDuckGo HTML engine is implemented here because it needs no API
key. Google / Bing / Brave are added behind ``ENGINES`` once keys exist; each
engine is an async callable ``(query) -> list[Hit]``.

See docs/02-architecture.md (dork_search) and docs/04-sources.md.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import asdict, dataclass
from urllib.parse import parse_qs, urlparse

import httpx
from bs4 import BeautifulSoup

from .base import (
    ConnectorResult,
    EntityCandidate,
    EntityType,
)
from ._http import get_client, retry

MAX_RESULTS_PER_ENGINE = 15
MAX_SEARXNG_RESULTS = 40


@dataclass(slots=True)
class Hit:
    url: str
    title: str
    snippet: str
    engine: str


# A browser-like UA noticeably reduces DuckDuckGo throttling vs. our default UA.
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def _clean_ddg_url(href: str) -> str:
    """DuckDuckGo wraps targets as //duckduckgo.com/l/?uddg=<encoded>. Unwrap it."""
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if parsed.path.startswith("/l/"):
        q = parse_qs(parsed.query)
        if "uddg" in q:
            return q["uddg"][0]
    return href


def _parse_ddg_lite(html: str) -> list[Hit]:
    """Parse the lite endpoint (lite.duckduckgo.com/lite/): a results table."""
    soup = BeautifulSoup(html, "html.parser")
    hits: list[Hit] = []
    for a in soup.select("a.result-link"):
        href = a.get("href")
        if not href:
            continue
        # The snippet sits in a following row's td.result-snippet.
        row = a.find_parent("tr")
        snippet = ""
        if row:
            nxt = row.find_next("td", class_="result-snippet")
            if nxt:
                snippet = nxt.get_text(strip=True)
        hits.append(Hit(
            url=_clean_ddg_url(href),
            title=a.get_text(strip=True),
            snippet=snippet,
            engine="duckduckgo",
        ))
        if len(hits) >= MAX_RESULTS_PER_ENGINE:
            break
    return hits


async def _duckduckgo(query: str) -> list[Hit]:
    """Query DuckDuckGo's lite endpoint.

    Raises on persistent throttling so :func:`dork_search` records it as an
    error rather than reporting a misleading empty result. A real
    no-results query is rare on DDG; a 200 with no parseable results is almost
    always throttling.
    """
    client = get_client()

    async def fetch() -> list[Hit]:
        resp = await client.post(
            "https://lite.duckduckgo.com/lite/",
            data={"q": query},
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": _BROWSER_UA,
                "Referer": "https://lite.duckduckgo.com/",
            },
        )
        resp.raise_for_status()
        hits = _parse_ddg_lite(resp.text)
        if not hits:
            raise httpx.HTTPStatusError(
                "no parseable results (likely throttled or blocked)",
                request=resp.request,
                response=httpx.Response(503, request=resp.request),
            )
        return hits

    return await retry(fetch, attempts=3, base_delay=1.0)


async def _searxng(query: str) -> list[Hit]:
    """Self-hosted SearXNG metasearch (``SEARXNG_URL``): one JSON call fans out
    to Brave, Bing, Google, Startpage, Mojeek... and ranks by agreement, so a
    single engine throttling us doesn't empty the result."""
    base = os.environ["SEARXNG_URL"].rstrip("/")
    client = get_client()
    hits: list[Hit] = []
    for page in (1, 2):
        resp = await client.get(f"{base}/search", timeout=20.0, params={
            "q": query, "format": "json", "pageno": page, "safesearch": 0})
        resp.raise_for_status()
        data = resp.json()
        for r in data.get("results", []):
            if r.get("url"):
                hits.append(Hit(url=r["url"], title=(r.get("title") or "")[:300],
                                snippet=(r.get("content") or "")[:500],
                                engine="searxng:" + ",".join(r.get("engines") or [])))
        if len(hits) >= MAX_SEARXNG_RESULTS or not data.get("results"):
            break
    if not hits and data.get("unresponsive_engines"):
        raise RuntimeError(f"no results; unresponsive engines: {data['unresponsive_engines']}")
    return hits[:MAX_SEARXNG_RESULTS]


# engine name -> (callable, requires_env_key)
ENGINES: dict[str, tuple] = {
    "searxng": (_searxng, "SEARXNG_URL"),
    "duckduckgo": (_duckduckgo, None),
    # "bing": (_bing, "BING_API_KEY"),
    # "brave": (_brave, "BRAVE_API_KEY"),
    # "google": (_google, "SERPAPI_KEY"),
}


async def dork_search(query: str, engines: list[str] | None = None) -> ConnectorResult:
    """Run ``query`` across the available engines and merge the hits.

    Default: SearXNG when configured (it already aggregates many engines),
    falling back to DuckDuckGo only if SearXNG fails; otherwise DuckDuckGo.
    """
    if engines:
        wanted = engines
    elif os.environ.get("SEARXNG_URL"):
        result = await dork_search(query, ["searxng"])
        if result.raw["errors"] or not result.entities:
            fallback = await dork_search(query, ["duckduckgo"])
            fallback.raw["errors"] = {**result.raw["errors"], **fallback.raw["errors"]}
            if fallback.entities or not result.entities:
                return fallback
        return result
    else:
        wanted = [e for e in ENGINES if e != "searxng"]
    tasks = []
    used: list[str] = []
    skipped: dict[str, str] = {}

    for name in wanted:
        if name not in ENGINES:
            skipped[name] = "unknown engine"
            continue
        fn, key_env = ENGINES[name]
        if key_env and not os.environ.get(key_env):
            skipped[name] = f"missing {key_env}"
            continue
        tasks.append(fn(query))
        used.append(name)

    merged: dict[str, Hit] = {}
    errors: dict[str, str] = {}
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for name, outcome in zip(used, results):
        if isinstance(outcome, Exception):
            errors[name] = f"{type(outcome).__name__}: {outcome}"
            continue
        for hit in outcome:
            merged.setdefault(hit.url, hit)  # first engine to find a URL wins

    entities: list[EntityCandidate] = []
    for hit in merged.values():
        entities.append(EntityCandidate(
            EntityType.URL, hit.url,
            {"title": hit.title, "snippet": hit.snippet, "engine": hit.engine},
            match_quality=0.5,
        ))

    note_parts = []
    if skipped:
        note_parts.append("skipped: " + ", ".join(f"{k} ({v})" for k, v in skipped.items()))
    if errors:
        note_parts.append("errors: " + ", ".join(f"{k} ({v})" for k, v in errors.items()))

    return ConnectorResult(
        source="dork_search", passive=True,
        raw={"query": query, "engines_used": used,
             "hits": [asdict(h) for h in merged.values()],
             "skipped": skipped, "errors": errors},
        entities=entities,
        note="; ".join(note_parts) or None,
    )
