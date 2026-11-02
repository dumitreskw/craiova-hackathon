"""Quick scan: deterministic collection, no LLM (docs/02-architecture.md, mode 1).

Runs every connector that applies to the seed in parallel, then pivots on the
new entities up to ``max_depth``. Guard rails:

* loop detection on ``(connector, type, key)``;
* a hard cap on connector calls per scan and on pivots per entity type;
* no pivoting on unverified candidates, rejected or low-confidence entities;
* no pivoting off the target: a domain's DNS/mail providers (NS, MX, SOA
  hostmaster) are third parties, recorded but not followed (:func:`on_target`);
* **people are only screened as seeds.** A person the scan stumbles on (a
  GitHub display name, a registrant) is recorded, but the scan does not run
  web searches or wanted/sanctions checks on them. Profiling someone needs a
  purpose; the analyst can still pivot on that person explicitly.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from typing import Callable

import connectors
from connectors.base import EntityType

from .. import guard
from ..ledger import Ledger
from ..models import new_id, now
from ..resolver import entity_view

# The pivot map from docs/04-sources.md. Names that have no connector yet are
# ignored, so this lights up as connectors land.
PIVOTS: dict[EntityType, list[str]] = {
    EntityType.DOMAIN: ["dns", "rdap", "crtsh", "subfinder", "urlscan", "otx_domain",
                        "wayback", "virustotal_domain", "dnstwist", "hunter", "tomba",
                        "http_probe"],
    EntityType.IP: ["otx_ip", "virustotal_ip", "ipinfo", "shodan"],
    EntityType.ORGANIZATION: ["anaf", "vies", "companies_house", "gleif", "opencorporates",
                              "sanctions", "seap", "news", "dork_search"],
    EntityType.PERSON: ["sanctions", "interpol", "europol", "national_wanted",
                        "court_records", "news", "dork_search"],
    EntityType.EMAIL: ["gravatar", "holehe", "dehashed", "hibp", "email_rep", "dork_search"],
    EntityType.USERNAME: ["github_user", "maigret"],
    EntityType.PHONE: ["phone_lookup", "hibp", "dehashed"],
}

# Which cost tiers a run may use: investigation setting "costly_connectors".
COST_TIERS = {"none": {"free"}, "metered": {"free", "metered"},
              "all": {"free", "metered", "paid"}}

# Which pivots may also run on entities the scan discovered (depth > 0).
PIVOT_ON_DISCOVERED = {EntityType.DOMAIN, EntityType.IP, EntityType.ORGANIZATION,
                       EntityType.EMAIL, EntityType.USERNAME}

# Turn an entity into the connector's argument. Default: the canonical key.
def _org_name(e: dict) -> str:
    return e["attributes"].get("legal_name") or e["label"] or e["key"]


ARG: dict[str, Callable[[dict], str]] = {
    "dork_search": lambda e: f'"{e["label"] if e["type"] == "Person" else e["key"]}"',
    # Name-based registries get the legal name once a registry has supplied it.
    "gleif": _org_name,
    "sanctions": lambda e: _org_name(e) if e["type"] == "Organization" else e["key"],
}

# Registries that only make sense for a matching identifier format.
APPLIES: dict[str, Callable[[dict], bool]] = {
    "anaf": lambda e: e["key"].upper().startswith("RO") and e["key"][2:].isdigit(),
    "vies": lambda e: e["key"][:2].isalpha() and e["key"][2:3].isdigit(),
    "companies_house": lambda e: e["key"].upper().startswith("GB") and e["key"][2:].isalnum(),
}

MIN_PIVOT_CONFIDENCE = 0.5
CONCURRENCY = 8


def connectors_for(entity: dict, investigation: dict, depth: int = 0) -> list[str]:
    """Connectors that may run on this entity under this investigation's rules."""
    tiers = COST_TIERS.get((investigation.get("settings") or {}).get("costly_connectors"),
                           COST_TIERS["all"])
    out = []
    for name in PIVOTS.get(EntityType(entity["type"]), []):
        spec = connectors.REGISTRY.get(name)
        if spec is None or spec.cost not in tiers or (spec.seed_only and depth > 0):
            continue
        if name in APPLIES and not APPLIES[name](entity):
            continue
        try:
            guard.check(investigation, name, spec.passive, entity["key"])
        except guard.GateError:
            continue
        out.append(name)
    return out


def arg_for(connector: str, entity: dict) -> str:
    return ARG.get(connector, lambda e: e["key"])(entity)


def anchor_domains(db, investigation_id: str, seed_ids: list[str]) -> list[str]:
    """The target's own domains: Domain seeds/targets and the domains of Email ones."""
    rows = db.query(
        "SELECT type, key FROM entities WHERE investigation_id = ? AND (id IN "
        f"({','.join('?' * len(seed_ids))}) OR id IN "
        "(SELECT entity_id FROM targets WHERE investigation_id = ?))",
        (investigation_id, *seed_ids, investigation_id))
    out = {r["key"] for r in rows if r["type"] == EntityType.DOMAIN.value}
    out |= {r["key"].rsplit("@", 1)[1] for r in rows if r["type"] == EntityType.EMAIL.value}
    return sorted(out)


def on_target(entity: dict, anchors: list[str]) -> bool:
    """Is a discovered Domain/Email part of the target, or someone else's asset?

    A domain's NS/MX records point at its DNS and mail providers, and SOA
    names the provider's hostmaster. Those belong to third parties: they are
    recorded (and listed in the report as related assets) but not pivoted on.
    Scope creep is a finding, not an action. With no domain anchors (a
    person/username seed), discovered domains and emails are the subject's own
    leads and are followed.
    """
    if not anchors:
        return True
    if entity["type"] == EntityType.DOMAIN.value:
        return guard.in_scope(entity["key"], anchors)
    if entity["type"] == EntityType.EMAIL.value:
        return guard.in_scope(entity["key"].rsplit("@", 1)[1], anchors)
    return True


class QuickScan:
    def __init__(self, ledger: Ledger):
        self.ledger = ledger
        self.db = ledger.db
        self.settings = ledger.settings
        self.tasks: dict[str, asyncio.Task] = {}

    def start(self, investigation: dict, entity_ids: list[str], user: str,
              max_depth: int | None = None, only: list[str] | None = None,
              kind: str = "quick_scan") -> str:
        run_id = new_id("ar")
        self.db.insert("agent_runs", {
            "id": run_id, "investigation_id": investigation["id"], "kind": kind,
            "goal": None, "status": "running", "stats": {}, "started_at": now(),
        })
        task = asyncio.create_task(
            self.run(run_id, investigation, entity_ids, user, max_depth, only))
        self.tasks[run_id] = task
        task.add_done_callback(lambda _t: self.tasks.pop(run_id, None))
        return run_id

    def cancel(self, run_id: str) -> bool:
        task = self.tasks.get(run_id)
        if task is None:
            return False
        task.cancel()
        return True

    async def run(self, run_id: str, investigation: dict, entity_ids: list[str], user: str,
                  max_depth: int | None = None, only: list[str] | None = None) -> dict:
        inv_id = investigation["id"]
        settings = investigation.get("settings") or {}
        depth_limit = settings.get("max_depth", 1) if max_depth is None else max_depth
        stats: Counter[str] = Counter()
        seen: set[tuple[str, str, str]] = set()
        sem = asyncio.Semaphore(CONCURRENCY)
        frontier = [(eid, 0) for eid in entity_ids]
        anchors = anchor_domains(self.db, inv_id, entity_ids)
        status = "finished"

        async def call(eid: str, depth: int, name: str, arg: str):
            async with sem:
                outcome = await self.ledger.run(investigation, name, arg, user=user,
                                                run_by=run_id, entity_id=eid)
            return eid, depth, outcome

        try:
            while frontier:
                pivots_per_type: Counter[str] = Counter()
                jobs = []
                for eid, depth in frontier:
                    row = self.db.get("entities", eid)
                    if row is None or row["status"] in ("candidate", "rejected"):
                        continue
                    ent = entity_view(row)
                    if depth > 0:
                        if EntityType(ent["type"]) not in PIVOT_ON_DISCOVERED:
                            continue
                        if ent["confidence"] < MIN_PIVOT_CONFIDENCE:
                            continue
                        if not on_target(ent, anchors):
                            stats["off_target_skipped"] += 1
                            continue
                        pivots_per_type[ent["type"]] += 1
                        if pivots_per_type[ent["type"]] > self.settings.max_pivots_per_type:
                            stats["pivots_capped"] += 1
                            continue
                    for name in connectors_for(ent, investigation, depth):
                        if only and name not in only:
                            continue
                        mark = (name, ent["type"], ent["key"])
                        if mark in seen:
                            continue
                        if stats["calls"] >= self.settings.max_calls_per_scan:
                            stats["calls_capped"] += 1
                            break
                        seen.add(mark)
                        stats["calls"] += 1
                        jobs.append(call(eid, depth, name, arg_for(name, ent)))

                frontier = []
                for eid, depth, outcome in await asyncio.gather(*jobs):
                    if outcome.refused:
                        stats["refused"] += 1
                    elif outcome.error:
                        stats["errors"] += 1
                    if outcome.fold is None:
                        continue
                    stats["new_entities"] += len(outcome.fold.new_entities)
                    if depth + 1 <= depth_limit:
                        frontier += [(e["id"], depth + 1) for e in outcome.fold.new_entities]
        except asyncio.CancelledError:
            status = "cancelled"
        except Exception as e:  # never leave a run stuck in "running"
            status = "failed"
            stats["exception"] = 1
            self.ledger.bus.publish(inv_id, "scan.failed", {"run_id": run_id, "error": str(e)})

        summary = (f"{stats['calls']} connector calls, {stats['new_entities']} new entities, "
                   f"{stats['errors']} errors, {stats['refused']} refused")
        self.db.update("agent_runs", run_id, {"status": status, "summary": summary,
                                              "stats": dict(stats), "finished_at": now()})
        self.ledger.bus.publish(inv_id, "scan.finished",
                                {"run_id": run_id, "status": status, "summary": summary})
        return dict(stats)
