"""Read a web page: clean text (markdown), contacts, links and structured data.

The agents use this to read the pages behind search hits instead of judging a
target from snippets. It fetches the page, so like ``screenshot`` it touches
whatever host serves the URL; the app keeps the target's own hosts behind the
active-mode rules (``app/evidence.py``).

Backends, fastest/best first, chosen by what is configured:

* **Firecrawl** (``FIRECRAWL_API_URL`` and/or ``FIRECRAWL_API_KEY``):
  best markdown, JS rendering, and anti-bot handling on the hosted service.
* **Lightpanda** (``lightpanda`` binary, see ``_cli``): headless browser built
  for machines — runs JavaScript, starts in milliseconds, uses a fraction of
  Chromium's memory; emits markdown and structured data (JSON-LD, OpenGraph)
  natively.
* **Plain HTTP** + BeautifulSoup: no JavaScript, always available.

Whatever the backend, contacts and links are extracted the same way, so
results are comparable.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from . import _cli
from ._http import get_client
from .base import ConnectorResult, EntityCandidate, EntityType, RelationshipCandidate

MAX_TEXT = 40_000          # chars of markdown kept in the finding
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
_URL = re.compile(r"^https?://[^\s\"'<>]+$")
_SOCIAL_HOSTS = {
    "twitter.com": "twitter", "x.com": "twitter", "facebook.com": "facebook",
    "instagram.com": "instagram", "linkedin.com": "linkedin", "github.com": "github",
    "youtube.com": "youtube", "t.me": "telegram", "tiktok.com": "tiktok",
    "mastodon.social": "mastodon", "bsky.app": "bluesky", "reddit.com": "reddit",
}
_IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp")


async def web_read(url: str) -> ConnectorResult:
    if not _URL.match(url):
        raise ValueError(f"not an http(s) URL: {url!r}")
    errors: dict[str, str] = {}
    page = None
    for name, backend in _backends():
        try:
            page = await backend(url)
            page["backend"] = name
            break
        except Exception as e:  # try the next backend, keep the reason
            errors[name] = f"{type(e).__name__}: {e}"[:300]
    if page is None:
        raise RuntimeError("all backends failed: " + "; ".join(f"{k}: {v}" for k, v in errors.items()))
    return _result(url, page, errors)


def _backends():
    if os.environ.get("FIRECRAWL_API_URL") or os.environ.get("FIRECRAWL_API_KEY"):
        yield "firecrawl", _firecrawl
    if _cli.available("lightpanda"):
        yield "lightpanda", _lightpanda
    yield "http", _plain_http


# --- backends ---------------------------------------------------------------------

async def _firecrawl(url: str) -> dict:
    base = os.environ.get("FIRECRAWL_API_URL", "https://api.firecrawl.dev").rstrip("/")
    headers = {}
    if os.environ.get("FIRECRAWL_API_KEY"):
        headers["Authorization"] = f"Bearer {os.environ['FIRECRAWL_API_KEY']}"
    r = await get_client().post(f"{base}/v2/scrape", headers=headers, timeout=75.0, json={
        "url": url, "formats": ["markdown", "html"], "onlyMainContent": False,
        "timeout": 60000, "maxAge": 0})
    r.raise_for_status()
    data = r.json().get("data") or {}
    meta = data.get("metadata") or {}
    return {"final_url": meta.get("url") or meta.get("sourceURL") or url,
            "status": meta.get("statusCode"), "title": meta.get("title"),
            "markdown": data.get("markdown") or "", "html": data.get("html") or ""}


async def _lightpanda(url: str) -> dict:
    common = ["--log-level", "warn", "--wait-ms", "6000", "--terminate-ms", "20000"]

    async def dump(kind: str, *extra: str) -> str:
        code, out, err = await _cli.run(
            "lightpanda", ["fetch", "--dump", kind, *extra, *common, url], timeout=45,
            env={"LIGHTPANDA_DISABLE_TELEMETRY": "true"})
        if code != 0:
            raise RuntimeError(err.strip()[-300:] or f"lightpanda exited {code}")
        return out

    # Rendered HTML (links, contacts, metadata) and readable markdown, in parallel.
    html, markdown = await asyncio.gather(
        dump("html"), dump("markdown", "--strip-mode", "js", "--strip-mode", "css",
                           "--strip-mode", "ui"))
    title = BeautifulSoup(html, "html.parser").title
    return {"final_url": url, "status": None,
            "title": title.get_text(strip=True) if title else None,
            "markdown": markdown, "html": html}


async def _plain_http(url: str) -> dict:
    r = await get_client().get(url, timeout=20.0)
    soup = BeautifulSoup(r.text, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    text = re.sub(r"\n{3,}", "\n\n", soup.get_text("\n", strip=True))
    return {"final_url": str(r.url), "status": r.status_code,
            "title": soup.title.get_text(strip=True) if soup.title else None,
            "markdown": text, "html": r.text}


# --- extraction ---------------------------------------------------------------------

def _result(url: str, page: dict, errors: dict) -> ConnectorResult:
    final = page["final_url"]
    host = urlparse(final).hostname or ""
    soup = BeautifulSoup(page.get("html") or "", "html.parser")
    structured = _structured(soup)
    hrefs = {urljoin(final, a["href"]) for a in soup.find_all("a", href=True)}
    hrefs |= {h for h in _sameas(structured) if _URL.match(h)}

    emails = {e.lower() for e in _EMAIL.findall(page.get("markdown", "") + " " + " ".join(
        h[7:] for h in hrefs if h.lower().startswith("mailto:")))
        if not e.lower().endswith(_IMAGE_EXT)}
    socials: dict[str, tuple[str, str]] = {}
    external: set[str] = set()
    for h in hrefs:
        p = urlparse(h)
        if p.scheme not in ("http", "https") or not p.hostname:
            continue
        hn = p.hostname.lower().removeprefix("www.")
        if hn == host.lower().removeprefix("www."):
            continue  # the site's own pages, even when the site is a platform
        platform = _SOCIAL_HOSTS.get(hn)
        handle = _handle(platform, p.path) if platform else None
        if handle:
            socials[f"{platform}:{handle}"] = (platform, h)
        else:
            external.add(hn)

    markdown = page.get("markdown") or ""
    entities = [EntityCandidate(EntityType.URL, url, {"title": page.get("title"),
                                                      "http_status": page.get("status")})]
    rels: list[RelationshipCandidate] = []
    for e in sorted(emails)[:50]:
        entities.append(EntityCandidate(EntityType.EMAIL, e, match_quality=0.8))
        rels.append(RelationshipCandidate("mentions_email", EntityType.URL, url,
                                          EntityType.EMAIL, e))
    for key, (platform, link) in sorted(socials.items())[:30]:
        entities.append(EntityCandidate(EntityType.SOCIAL_PROFILE, key,
                                        {"platform": platform, "url": link}, match_quality=0.8))
        rels.append(RelationshipCandidate("links_to", EntityType.URL, url,
                                          EntityType.SOCIAL_PROFILE, key))
    return ConnectorResult(
        source="web_read", passive=True,
        raw={"url": url, "final_url": final, "backend": page["backend"],
             "http_status": page.get("status"), "title": page.get("title"),
             "markdown": markdown[:MAX_TEXT], "truncated": len(markdown) > MAX_TEXT,
             "emails": sorted(emails), "socials": {k: v[1] for k, v in socials.items()},
             "external_domains": sorted(external)[:200],
             "structured_data": structured,
             "backend_errors": errors},
        entities=entities, relationships=rels,
        note=None if markdown.strip() else "page returned no readable text",
    )


_NOT_HANDLES = {"share", "sharer", "sharer.php", "intent", "home", "login", "signup",
                "search", "hashtag", "explore", "watch", "embed", "plugins", "dialog"}


def _handle(platform: str, path: str) -> str | None:
    """Profile handle from a social URL path; None for share/search/etc. links."""
    parts = [s for s in path.split("/") if s]
    if not parts or parts[0].lower() in _NOT_HANDLES:
        return None
    if platform == "linkedin" and len(parts) >= 2 and parts[0] in ("in", "company", "school"):
        return f"{parts[0]}/{parts[1]}".lower()
    if platform == "youtube" and len(parts) >= 2 and parts[0] in ("c", "channel", "user"):
        return f"{parts[0]}/{parts[1]}"
    if platform == "reddit" and len(parts) >= 2 and parts[0] in ("r", "u", "user"):
        return f"{parts[0]}/{parts[1]}".lower()
    if platform in ("linkedin", "reddit"):
        return None
    return parts[0].lower() if platform != "youtube" else parts[0]


def _structured(soup: BeautifulSoup) -> dict:
    """JSON-LD blocks and OpenGraph / Twitter / description meta tags."""
    json_ld = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            json_ld.append(json.loads(tag.string or ""))
        except ValueError:
            continue
    meta = {}
    for tag in soup.find_all("meta"):
        key = tag.get("property") or tag.get("name")
        if key and tag.get("content") and (key.startswith(("og:", "twitter:")) or key in (
                "description", "author", "generator", "keywords")):
            meta[key] = tag["content"][:500]
    return {"jsonLd": json_ld[:10], "meta": meta}


def _sameas(structured: dict | None) -> list[str]:
    """``sameAs`` links from JSON-LD: an organization's own list of its profiles."""
    out: list[str] = []

    def walk(node):
        if isinstance(node, dict):
            same = node.get("sameAs")
            if isinstance(same, str):
                out.append(same)
            elif isinstance(same, list):
                out.extend(s for s in same if isinstance(s, str))
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk((structured or {}).get("jsonLd") or [])
    return out
