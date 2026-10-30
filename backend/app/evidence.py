"""Evidence capture: screenshot a URL into the investigation's evidence store.

Used by the API ("capture evidence" on a URL node) and by the report agent.
The screenshot connector fetches the page, so it is active toward the host
serving it. That is fine for third-party pages (articles, profiles, official
listings). The target's **own** hosts get the same treatment as any other
active connector: the investigation must be active, authorized, and the host
in scope (docs/02-architecture.md, "screenshot tool").
"""

from __future__ import annotations

import re

from . import guard
from .ledger import Ledger, RunOutcome
from .scan import anchor_domains

_URL = re.compile(r"^https?://[^\s\"'<>]+$")


def screenshot_dir(ledger: Ledger, investigation_id: str):
    return ledger.settings.evidence_dir / investigation_id / "screenshots"


async def capture(ledger: Ledger, investigation: dict, url: str, *, user: str, run_by: str,
                  entity_id: str | None = None) -> RunOutcome:
    if not _URL.match(url):
        return RunOutcome(None, None, error="not an http(s) URL", refused=True)
    anchors = anchor_domains(ledger.db, investigation["id"], [])
    if anchors and guard.in_scope(url, anchors):
        try:
            guard.check(investigation, "screenshot", False, url)
        except guard.GateError as e:
            reason = f"{url} is on the target's own infrastructure: {e}"
            ledger.db.audit(user=user, action="evidence.capture", outcome="refused",
                            investigation_id=investigation["id"], connector="screenshot",
                            target=url, detail={"reason": reason, "run_by": run_by})
            return RunOutcome(None, None, error=reason, refused=True)
    out_dir = screenshot_dir(ledger, investigation["id"])
    return await ledger.run(investigation, "screenshot", url, user=user, run_by=run_by,
                            entity_id=entity_id, kwargs={"out_dir": str(out_dir)})
