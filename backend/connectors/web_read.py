"""Read a web page: clean text (markdown), contacts, links and structured data.

The agents use this to read the pages behind search hits instead of judging a
target from snippets. It fetches the page like any visitor would, so like
``screenshot`` it touches whatever host serves the URL, including the
target's own public pages (``app/evidence.py``).

Backends, cheapest first, each used only when the previous one returned a
thin, blocked or JavaScript-only page:

* **Firecrawl** (``FIRECRAWL_API_URL`` and/or ``FIRECRAWL_API_KEY``):
  best markdown, JS rendering, and anti-bot handling on the hosted service.
* **Lightpanda** (``lightpanda`` binary, see ``_cli``): headless browser built
  for machines — runs JavaScript, starts in milliseconds, uses a fraction of
  Chromium's memory. One render per page.
* **Plain HTTP**: no JavaScript, always available.

Whatever the backend, the HTML goes through the same extraction:

* **selectolax** (lexbor) for links, contacts, JSON-LD and meta tags — about
  20x faster than BeautifulSoup on real pages, so a page no longer blocks the
  event loop for tens of milliseconds;
* **trafilatura** for the main text without menus and footers (what the agent
  reads; a fraction of the tokens) and for the page's own author / publish
  date / site name, which a report citation needs.

Contacts are taken from the whole page, not only the main text, because they
usually live in the footer.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from urllib.parse import urljoin, urlparse

import trafilatura
from selectolax.lexbor import LexborHTMLParser

from . import _cli
from ._http import get_client
from .base import ConnectorResult, EntityCandidate, EntityType, RelationshipCandidate

MAX_TEXT = 40_000          # chars of markdown kept in the finding
MIN_MAIN_TEXT = 400        # below this, trafilatura missed the content: use the full page
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
_URL = re.compile(r"^https?://[^\s\"'<>]+$")
_SOCIAL_HOSTS = {
    "twitter.com": "twitter", "x.com": "twitter", "facebook.com": "facebook",
    "instagram.com": "instagram", "linkedin.com": "linkedin", "github.com": "github",
    "youtube.com": "youtube", "t.me": "telegram", "tiktok.com": "tiktok",
    "mastodon.social": "mastodon", "bsky.app": "bluesky", "reddit.com": "reddit",
}
_IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp")
# Romanian sites print their fiscal code ("CUI: RO14399840", "CIF 14399840") and
# UK ones their company number; both lead straight to the official registry.
_RO_CUI = re.compile(r"\b(?:C\.?U\.?I\.?|C\.?I\.?F\.?|cod(?:ul)? (?:unic de inregistrare|fiscal))"
                     r"\s*[:.\-]?\s*(RO)?\s?(\d{2,10})\b", re.IGNORECASE)
_UK_NO = re.compile(r"\b(?:company (?:registration )?(?:number|no\.?)|registered in england"
                    r"(?: and wales)?(?: under)?(?: company)?(?: number| no\.?)?)\s*[:.]?\s*"
                    r"([0-9A-Z]{2}\d{6})\b", re.IGNORECASE)


_JS_SHELL = re.compile(r"enable javascript|requires javascript|javascript is (disabled|required)"
                       r"|you need to enable", re.IGNORECASE)


async def web_read(url: str) -> ConnectorResult:
    """Cheapest backend that yields a readable page.

    Most pages are server-rendered, where plain HTTP + extraction gives the
    same text as a browser 2-10x faster (measured), so HTTP goes first and
    the Lightpanda browser is used only when the page needs JavaScript (thin
    text, a JS-required shell) or HTTP was refused. Firecrawl, when
    configured, is the premium reader and goes first.
    """
    if not _URL.match(url):
        raise ValueError(f"not an http(s) URL: {url!r}")
    errors: dict[str, str] = {}
    fallback: ConnectorResult | None = None
    for name, backend in _backends():
        try:
            page = await backend(url)
        except Exception as e:  # try the next backend, keep the reason
            errors[name] = f"{type(e).__name__}: {e}"[:300]
            continue
        page["backend"] = name
        # Parsing is CPU work: keep it off the event loop.
        result = await asyncio.to_thread(_result, url, page, errors)
        if _good_enough(page, result):
            return result
        errors[name] = "thin, blocked or JavaScript-only page; escalating"
        fallback = result if fallback is None or len(result.raw.get("markdown") or "") > len(
            fallback.raw.get("markdown") or "") else fallback
    if fallback is not None:
        return fallback
    raise RuntimeError("all backends failed: " + "; ".join(f"{k}: {v}" for k, v in errors.items()))


_BLOCKED = re.compile(r"just a moment|attention required|checking your browser|verify you are "
                      r"human|are you a robot|captcha|access denied|request blocked|"
                      r"unusual traffic", re.IGNORECASE)


def _good_enough(page: dict, result: ConnectorResult) -> bool:
    status = page.get("status") or 200
    text = result.raw.get("markdown") or ""
    head = (result.raw.get("title") or "") + " " + text[:1500]
    if page.get("backend") == "firecrawl":  # the last resort: take what it read
        return status < 400 and bool(text.strip())
    return (status < 400 and result.raw.get("text_source") in ("main_content", "firecrawl")
            and not (len(text) < 2000 and (_JS_SHELL.search(text) or _BLOCKED.search(head))))


def _backends():
    # Cheapest first: plain HTTP (free, fastest), the Lightpanda browser (free,
    # runs JavaScript), then Firecrawl (paid credits, gets through bot walls).
    yield "http", _plain_http
    if _cli.available("lightpanda"):
        yield "lightpanda", _lightpanda
    if os.environ.get("FIRECRAWL_API_URL") or os.environ.get("FIRECRAWL_API_KEY"):
        yield "firecrawl", _firecrawl


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
    # One render: the rendered HTML carries everything we extract.
    code, html, err = await _cli.run(
        "lightpanda", ["fetch", "--dump", "html", "--log-level", "warn", "--wait-ms", "6000",
                       "--terminate-ms", "20000", url],
        timeout=45, env={"LIGHTPANDA_DISABLE_TELEMETRY": "true"})
    if code != 0:
        raise RuntimeError(err.strip()[-300:] or f"lightpanda exited {code}")
    return {"final_url": url, "status": None, "title": None, "markdown": None, "html": html}


async def _plain_http(url: str) -> dict:
    r = await get_client().get(url, timeout=20.0)
    return {"final_url": str(r.url), "status": r.status_code, "title": None,
            "markdown": None, "html": r.text}


# --- extraction ---------------------------------------------------------------------

def _result(url: str, page: dict, errors: dict) -> ConnectorResult:
    final = page["final_url"]
    host = urlparse(final).hostname or ""
    html = page.get("html") or ""
    tree = LexborHTMLParser(html)
    structured = _structured(tree)
    hrefs = {urljoin(final, a.attributes.get("href") or "") for a in tree.css("a[href]")}
    hrefs |= {h for h in _sameas(structured) if _URL.match(h)}
    title_node = tree.css_first("title")
    title = page.get("title") or (title_node.text(strip=True) if title_node else None)

    # The whole page's visible text (contacts live in footers) ...
    tree.strip_tags(["script", "style", "noscript", "svg", "template"])
    body = tree.body or tree.root
    full_text = re.sub(r"\n\s*\n+", "\n\n", body.text(separator="\n")) if body else ""
    # ... and the main content, which is what the agent reads.
    markdown, text_source = page.get("markdown"), "firecrawl"
    if not markdown:
        markdown = trafilatura.extract(html, url=final, output_format="markdown",
                                       include_tables=True, include_links=False,
                                       favor_recall=True) or ""
        text_source = "main_content"
        if len(markdown) < MIN_MAIN_TEXT:
            markdown, text_source = full_text.strip(), "full_page"
    meta = trafilatura.extract_metadata(html, default_url=final) if html else None
    page_meta = {k: getattr(meta, k, None) for k in
                 ("author", "date", "sitename", "description")} if meta else {}

    emails = {e.lower() for e in _EMAIL.findall(full_text + " " + markdown + " " + " ".join(
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

    entities = [EntityCandidate(EntityType.URL, url, {
        "title": title, "http_status": page.get("status"),
        "author": page_meta.get("author"), "published": page_meta.get("date"),
        "site": page_meta.get("sitename")})]
    rels: list[RelationshipCandidate] = []
    for e in sorted(emails)[:50]:
        entities.append(EntityCandidate(EntityType.EMAIL, e, match_quality=0.8))
        rels.append(RelationshipCandidate("mentions_email", EntityType.URL, url,
                                          EntityType.EMAIL, e))
    # Firecrawl returns markdown without the raw HTML, so look in both.
    id_text = full_text + "\n" + markdown
    identifiers = sorted({f"RO{m.group(2)}" for m in _RO_CUI.finditer(id_text)}
                         | {f"GB{m.group(1).upper()}" for m in _UK_NO.finditer(id_text)})
    for ident in identifiers[:5]:
        entities.append(EntityCandidate(EntityType.ORGANIZATION, ident,
                                        {"identifier_seen_on": final}, match_quality=0.8))
        rels.append(RelationshipCandidate("names_company", EntityType.URL, url,
                                          EntityType.ORGANIZATION, ident))
    for key, (platform, link) in sorted(socials.items())[:30]:
        entities.append(EntityCandidate(EntityType.SOCIAL_PROFILE, key,
                                        {"platform": platform, "url": link}, match_quality=0.8))
        rels.append(RelationshipCandidate("links_to", EntityType.URL, url,
                                          EntityType.SOCIAL_PROFILE, key))
    return ConnectorResult(
        source="web_read", passive=True,
        raw={"url": url, "final_url": final, "backend": page["backend"],
             "http_status": page.get("status"), "title": title,
             "page_metadata": page_meta, "text_source": text_source,
             "markdown": markdown[:MAX_TEXT], "truncated": len(markdown) > MAX_TEXT,
             "emails": sorted(emails), "socials": {k: v[1] for k, v in socials.items()},
             "company_identifiers": identifiers,
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


def _structured(tree: LexborHTMLParser) -> dict:
    """JSON-LD blocks and OpenGraph / Twitter / description meta tags."""
    json_ld = []
    for tag in tree.css('script[type="application/ld+json"]'):
        try:
            json_ld.append(json.loads(tag.text() or ""))
        except ValueError:
            continue
    meta = {}
    for tag in tree.css("meta"):
        attrs = tag.attributes
        key, content = attrs.get("property") or attrs.get("name"), attrs.get("content")
        if key and content and (key.startswith(("og:", "twitter:")) or key in (
                "description", "author", "generator", "keywords")):
            meta[key] = content[:500]
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
