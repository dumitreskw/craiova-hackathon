"""Evidence capture: screenshot a URL into the investigation's evidence store.

Used by the API ("capture evidence" on a URL node) and by the report agent.
The screenshot connector fetches the page, so it is active toward the host
serving it. The investigation must be in active mode (enforced by the ledger's
gate).
"""

from __future__ import annotations

import re

from .ledger import Ledger, RunOutcome

_URL = re.compile(r"^https?://[^\s\"'<>]+$")


def screenshot_dir(ledger: Ledger, investigation_id: str):
    return ledger.settings.evidence_dir / investigation_id / "screenshots"


async def fetch_page(ledger: Ledger, investigation: dict, connector: str, url: str, *,
                     user: str, run_by: str, entity_id: str | None = None,
                     kwargs: dict | None = None) -> RunOutcome:
    """Run a page-fetching connector (screenshot, web_read)."""
    if not _URL.match(url):
        return RunOutcome(None, None, error="not an http(s) URL", refused=True)
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
