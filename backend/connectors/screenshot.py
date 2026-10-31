"""Screenshot connector: render a URL in headless Chromium as evidence.

Fetches the page, so it is active toward whatever host serves the URL. For
news articles, profiles and official listings that host is a third party.
The app (``app/evidence.py``) refuses active connectors unless the
investigation is in active mode; this module only does the rendering.

The PNG gets a source banner (URL + capture time, UTC) composed on top, so a
screenshot pasted into a report still says where it came from. The banner
carries nothing about how it was collected (no egress, no tool names): the
same image works in an internal and a client report.

Sites that block automated browsers are reported, not hidden: the result
carries ``blocked: true`` and a note asking for a manual capture.

Needs the optional ``playwright`` package and a Chromium build
(``playwright install chromium``); without them the call fails with a clear
error, which the ledger records like any other connector failure.
"""

from __future__ import annotations

import base64
import hashlib
import html
import os
import re
from datetime import UTC, datetime
from pathlib import Path

from .base import ConnectorResult, EntityCandidate, EntityType, RelationshipCandidate

VIEWPORT = {"width": 1366, "height": 900}
MAX_HEIGHT = 6000          # px; long pages are cut, the banner says so
NAV_TIMEOUT_MS = 45_000     # until the HTML is parsed
LOAD_GRACE_MS = 10_000     # then images, fonts, scripts: best effort
SETTLE_MS = 1500

_BLOCK_STATUS = {401, 403, 429, 503}
_BLOCK_TITLE = re.compile(
    r"just a moment|attention required|access denied|are you a robot|captcha|"
    r"verify you are human|request blocked|unusual traffic", re.IGNORECASE)


async def screenshot(url: str, out_dir: str | None = None) -> ConnectorResult:
    if not re.match(r"^https?://[^\s\"'<>]+$", url):
        raise ValueError(f"not an http(s) URL: {url!r}")
    try:
        from playwright.async_api import TimeoutError as PlaywrightTimeout
        from playwright.async_api import async_playwright
    except ImportError as e:  # pragma: no cover - depends on the install
        raise RuntimeError("playwright is not installed (uv sync --extra evidence; "
                           "playwright install chromium)") from e

    out = Path(out_dir or os.environ.get("OSINT_SCREENSHOT_DIR", "screenshots"))
    out.mkdir(parents=True, exist_ok=True)
    captured_at = datetime.now(UTC).isoformat(timespec="seconds")

    async with async_playwright() as pw:
        # Same egress as the HTTP connectors (OSINT_HTTP_PROXY), so captures
        # come from where the rest of the collection comes from.
        proxy = os.environ.get("OSINT_HTTP_PROXY")
        browser = await pw.chromium.launch(proxy={"server": proxy} if proxy else None)
        try:
            context = await browser.new_context(viewport=VIEWPORT)
            page = await context.new_page()
            # Slow servers (crt.sh) and pages with endless trackers: the HTML
            # is what counts, so capture once it is parsed and give the rest
            # of the page a bounded chance to finish.
            response = await page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
            try:
                await page.wait_for_load_state("load", timeout=LOAD_GRACE_MS)
            except PlaywrightTimeout:
                pass
            await page.wait_for_timeout(SETTLE_MS)
            status = response.status if response else None
            title = (await page.title() or "").strip()
            final_url = page.url
            height = await page.evaluate("document.documentElement.scrollHeight") or VIEWPORT["height"]
            clipped = height > MAX_HEIGHT
            shot = await page.screenshot(full_page=True, clip={
                "x": 0, "y": 0, "width": VIEWPORT["width"], "height": min(height, MAX_HEIGHT)})

            banner = await context.new_page()
            await banner.set_content(_banner_html(final_url, captured_at, clipped, shot))
            stamped = await banner.locator("#capture").screenshot()
        finally:
            await browser.close()

    blocked = (status in _BLOCK_STATUS) or bool(_BLOCK_TITLE.search(title))
    digest = hashlib.sha256(stamped).hexdigest()
    filename = f"{digest[:20]}.png"
    (out / filename).write_bytes(stamped)

    doc_attrs = {"kind": "screenshot", "url": final_url, "file": filename, "sha256": digest,
                 "captured_at": captured_at, "http_status": status, "blocked": blocked}
    return ConnectorResult(
        source="screenshot", passive=True,
        raw={"url": url, "final_url": final_url, "http_status": status, "title": title,
             "file": filename, "sha256": digest, "captured_at": captured_at,
             "clipped": clipped, "blocked": blocked},
        entities=[EntityCandidate(EntityType.URL, url, {"title": title, "http_status": status}),
                  EntityCandidate(EntityType.DOCUMENT, digest, doc_attrs)],
        relationships=[RelationshipCandidate("captured_as", EntityType.URL, url,
                                             EntityType.DOCUMENT, digest)],
        note=(f"capture blocked (bot wall or HTTP {status}): open the URL in a normal "
              "browser and capture it manually") if blocked else None,
    )


def _banner_html(url: str, captured_at: str, clipped: bool, png: bytes) -> str:
    data = base64.b64encode(png).decode("ascii")
    suffix = f" | page cut at {MAX_HEIGHT} px" if clipped else ""
    when = captured_at.replace("T", " ").replace("+00:00", "")
    text = html.escape(f"SOURCE: {url}  |  captured {when} UTC{suffix}")
    return (
        "<html><body style='margin:0'>"
        f"<div id=capture style='display:inline-block;width:{VIEWPORT['width']}px'>"
        "<div style='background:#111;color:#fff;font:600 14px/1.4 Arial,sans-serif;"
        f"padding:8px 12px;word-break:break-all'>{text}</div>"
        f"<img style='display:block;width:{VIEWPORT['width']}px' src='data:image/png;base64,{data}'>"
        "</div></body></html>")
