"""The single path every connector call takes: gate → run → record → resolve.

Quick scan, manual pivots and (later) the agents all call :meth:`Ledger.run`,
so no lookup can happen without leaving behind:

* a ``findings`` row (connector, input, who/what ran it, timing, sha256),
* the raw payload as a JSON file under ``evidence/<investigation>/findings/``,
* an ``audit`` row, including **refused** calls, and
* live events for the UI.

This is the chain of custody: every fact in a report can be traced to the
finding it came from, and every finding to the call that produced it.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import connectors
from connectors.base import ConnectorResult

from . import guard
from .config import Settings
from .events import EventBus
from .models import Database, new_id, now
from .resolver import FoldResult, Resolver

CALL_TIMEOUT_S = 90


@dataclass
class RunOutcome:
    finding_id: str | None
    fold: FoldResult | None
    error: str | None = None
    refused: bool = False
    result: ConnectorResult | None = None


class Ledger:
    def __init__(self, db: Database, bus: EventBus, settings: Settings):
        self.db = db
        self.bus = bus
        self.settings = settings
        self.resolver = Resolver(db)

    async def run(self, investigation: dict[str, Any], connector: str, arg: str, *,
                  user: str, run_by: str, entity_id: str | None = None,
                  kwargs: dict[str, Any] | None = None) -> RunOutcome:
        inv_id = investigation["id"]
        spec = connectors.REGISTRY.get(connector)
        if spec is None:
            raise KeyError(f"unknown connector: {connector}")
        call = {"arg": arg, **(kwargs or {})}

        try:
            guard.check(investigation, connector, spec.passive, arg)
            self._check_quota(spec)
        except guard.GateError as e:
            self.db.audit(user=user, action="connector.call", outcome="refused",
                          investigation_id=inv_id, connector=connector, target=arg,
                          detail={"reason": str(e), "run_by": run_by})
            self.bus.publish(inv_id, "tool.refused",
                             {"connector": connector, "input": call, "reason": str(e)})
            return RunOutcome(None, None, error=str(e), refused=True)

        self.bus.publish(inv_id, "tool.started",
                         {"connector": connector, "input": call, "by": run_by})
        started = time.monotonic()
        result: ConnectorResult | None = None
        error: str | None = None
        try:
            result = await asyncio.wait_for(spec.fn(arg, **(kwargs or {})),
                                            spec.timeout or CALL_TIMEOUT_S)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # connector broke: record it, keep the run going
            error = f"{type(e).__name__}: {e}"[:500]
        duration_ms = int((time.monotonic() - started) * 1000)

        finding_id = new_id("f")
        raw_path = sha = None
        if result is not None:
            payload = result.to_dict()
            sha = payload["sha256"]
            path = self.settings.evidence_dir / inv_id / "findings" / f"{finding_id}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, default=str, ensure_ascii=False, indent=1),
                            encoding="utf-8")
            raw_path = str(path.relative_to(self.settings.data_dir).as_posix())

        self.db.insert("findings", {
            "id": finding_id, "investigation_id": inv_id, "connector": connector,
            "input": call, "entity_ref": entity_id, "run_by": run_by,
            "passive": spec.passive,
            "fetched_at": result.fetched_at.isoformat(timespec="seconds") if result else now(),
            "duration_ms": duration_ms, "sha256": sha, "raw_path": raw_path,
            "note": result.note if result else None, "error": error,
            "entity_count": len(result.entities) if result else 0,
            "egress": self.settings.egress_label,
        })
        self.db.audit(user=user, action="connector.call",
                      outcome="error" if error else "ok", investigation_id=inv_id,
                      connector=connector, target=arg,
                      detail={"finding_id": finding_id, "run_by": run_by,
                              "duration_ms": duration_ms})

        if error:
            self.bus.publish(inv_id, "tool.failed", {"connector": connector, "input": call,
                                                    "finding_id": finding_id, "error": error})
            return RunOutcome(finding_id, None, error=error)

        fold = self.resolver.fold(inv_id, finding_id, result, trigger_entity_id=entity_id)
        for ent in fold.new_entities:
            self.bus.publish(inv_id, "entity.created", {"entity": ent})
        for edge in fold.new_edges:
            self.bus.publish(inv_id, "edge.created", {"edge": edge})
        self.bus.publish(inv_id, "tool.finished", {
            "connector": connector, "finding_id": finding_id, "found": len(result.entities),
            "new_entities": len(fold.new_entities), "note": result.note})
        return RunOutcome(finding_id, fold, result=result)

    def quota_used(self, connector: str, days: int) -> int:
        since = (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="seconds")
        return self.db.one("SELECT count(*) AS n FROM findings WHERE connector = ? "
                           "AND fetched_at >= ?", (connector, since))["n"]

    def _check_quota(self, spec) -> None:
        """Metered sources (Hunter, Tomba...) have a hard monthly allowance; stop
        before the provider starts failing or billing."""
        if spec.quota is None:
            return
        used = self.quota_used(spec.name, spec.quota_days)
        if used >= spec.quota:
            raise guard.GateError(
                f"'{spec.name}' quota used up ({used}/{spec.quota} in {spec.quota_days} days)")
