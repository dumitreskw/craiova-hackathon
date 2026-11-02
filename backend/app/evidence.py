"""Evidence capture: screenshot a URL into the investigation's evidence store.

Used by the API ("capture evidence" on a URL node) and by the report agent.
The screenshot and web_read connectors fetch the page like any visitor, so
they reach whatever host serves it, including the target's own public pages.
They are registered as passive (a single page view), so they run in any
investigation mode; truly active connectors (http_probe) still need active
mode, enforced by the ledger's gate.

LinkedIn shows anonymous visitors a login wall, so an anonymous read or
screenshot of it is a picture of the wall. Those pages are refused here with a
message that points at the research accounts: the persona capture
(app/social.py) reads them logged in, and the agents' read_page /
capture_evidence route LinkedIn URLs there when a persona is logged in.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from .ledger import Ledger, RunOutcome

_URL = re.compile(r"^https?://[^\s\"'<>]+$")
# Sites whose pages only show content to a logged-in account: host -> platform.
LOGIN_ONLY_HOSTS = {"linkedin.com": "linkedin", "lnkd.in": "linkedin"}
LOGIN_ONLY_MESSAGE = (
    "{platform} pages show a login wall to anonymous visitors, so they are read only "
    "through a logged-in research account. Capture the profile from the Social tab (or "
    "with social_capture) once a {platform} persona is logged in on the Kali worker.")


PLATFORM_NAMES = {"linkedin": "LinkedIn", "x": "X", "tiktok": "TikTok", "youtube": "YouTube"}


def platform_name(platform: str) -> str:
    return PLATFORM_NAMES.get(platform, platform.title())


def login_only(url: str) -> str | None:
    """The platform name if ``url`` is on a login-walled site, else ``None``."""
    host = (urlparse(url).hostname or "").lower()
    for domain, platform in LOGIN_ONLY_HOSTS.items():
        if host == domain or host.endswith("." + domain):
            return platform
    return None


def screenshot_dir(ledger: Ledger, investigation_id: str):
    return ledger.settings.evidence_dir / investigation_id / "screenshots"


async def fetch_page(ledger: Ledger, investigation: dict, connector: str, url: str, *,
                     user: str, run_by: str, entity_id: str | None = None,
                     kwargs: dict | None = None) -> RunOutcome:
    """Run a page-fetching connector (screenshot, web_read)."""
    if not _URL.match(url):
        return RunOutcome(None, None, error="not an http(s) URL", refused=True)
    if platform := login_only(url):
        reason = LOGIN_ONLY_MESSAGE.format(platform=platform_name(platform))
        ledger.db.audit(user=user, action="connector.call", outcome="refused",
                        investigation_id=investigation["id"], connector=connector, target=url,
                        detail={"reason": reason, "run_by": run_by})
        ledger.bus.publish(investigation["id"], "tool.refused",
                           {"connector": connector, "input": {"arg": url}, "reason": reason})
        return RunOutcome(None, None, error=reason, refused=True)
    return await ledger.run(investigation, connector, url, user=user, run_by=run_by,
                            entity_id=entity_id, kwargs=kwargs)


async def capture(ledger: Ledger, investigation: dict, url: str, *, user: str, run_by: str,
                  entity_id: str | None = None) -> RunOutcome:
    out_dir = screenshot_dir(ledger, investigation["id"])
    return await fetch_page(ledger, investigation, "screenshot", url, user=user, run_by=run_by,
                            entity_id=entity_id, kwargs={"out_dir": str(out_dir)})


async def read_page(ledger: Ledger, investigation: dict, url: str, *, user: str, run_by: str,
                    entity_id: str | None = None) -> RunOutcome:
    return await fetch_page(ledger, investigation, "web_read", url, user=user, run_by=run_by,
                            entity_id=entity_id)
