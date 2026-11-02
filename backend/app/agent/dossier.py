"""The report writer's dossier: everything it needs, in one message.

The writer used to start from a few agents' summaries and page through the
graph with a handful of turns, so each section reflected whichever summary
covered it. Now it reads one dossier, built here from the graph after
verification: the subject, every surviving entity with its attributes and
finding ids, the verdicts (and what was rejected, so it stays out), the text of
the pages, profiles and records behind them, the screening results and the
collection notes. Large text is cut to fit a character budget, most relevant
first; the writer can still open any finding in full with ``get_finding``.
"""

from __future__ import annotations

import json
from typing import Any

from .. import identity, review
from ..models import Database
from ..report import LEGAL_CONNECTORS
from ..resolver import best_attributes

BUDGET_CHARS = 260_000        # about 65k tokens of dossier
PAGE_CHARS = 6_000            # per read page / profile / material
RAW_CHARS = 2_000             # per structured record (registries, screening...)
MAX_ENTITIES_PER_TYPE = 80
# Types that never need their own lines in a person or company dossier: they
# come back through the domains and pages they belong to.
_PLUMBING = {"Certificate", "IPAddress", "ASN"}
# Findings whose content the writer reads as text.
_TEXT_CONNECTORS = {"web_read": "markdown", "social_capture": "text",
                    "analyst_material": "markdown"}


def build(db: Database, data_dir: Any, investigation: dict, goal: str) -> str:
    inv_id = investigation["id"]
    kind = review.subject_kind(db, inv_id)
    targets = db.query("SELECT value, type, entity_id FROM targets WHERE investigation_id = ? "
                       "ORDER BY created_at", (inv_id,))
    out: list[str] = [
        "# Dossier",
        f"Investigation: {investigation['name']}",
        f"Purpose: {investigation['purpose']}",
        f"Goal for this report: {goal}",
        f"Subject kind: {kind}",
        "Targets: " + (", ".join(f"{t['value']} ({t['type']}, entity {t['entity_id']})"
                                 for t in targets) or "none"),
    ]
    if who := identity.brief(db, inv_id):
        out.append(who)
    people = identity.clusters(db, inv_id)
    if people:
        out.append("Same-name people (who's who from verification; write only about the "
                   "subject, and mention the others only to say they are different people):")
        for c in people:
            mark = (" [CHOSEN BY THE ANALYST AS THE SUBJECT]" if c["chosen"] else
                    " [the subject, per verification]" if c["is_subject"] else "")
            out.append(f"- {c['label']}{mark}: {c['distinguishing']}. {c['summary'][:400]}")

    # --- verification ---------------------------------------------------------------
    counts = review.summary(db, inv_id)
    out.append("\n## Verification\n"
               f"Confirmed {counts['confirmed']}, rejected {counts['rejected']}, uncertain "
               f"{counts['uncertain']}, not reviewed {counts['pending']}. Rejected entities "
               "are not the subject: never cite them as facts about the subject.")
    reviews = db.query(
        "SELECT r.entity_id, r.verdict, r.reason, r.by_agent, e.type, e.key "
        "FROM entity_reviews r JOIN entities e ON e.id = r.entity_id "
        "WHERE r.investigation_id = ? AND r.rowid = "
        "(SELECT max(rowid) FROM entity_reviews x WHERE x.entity_id = r.entity_id) "
        "ORDER BY r.verdict, e.type, e.key", (inv_id,))
    rejected = [r for r in reviews if r["verdict"] == "rejected"]
    if rejected:
        out.append("Rejected (not the subject): " + "; ".join(
            f"{r['type']} {r['key']} ({r['reason'][:120]})" for r in rejected[:120])
            + (f"; and {len(rejected) - 120} more" if len(rejected) > 120 else ""))
    verdict_of = {r["entity_id"]: r for r in reviews}

    # --- entities -------------------------------------------------------------------
    out.append("\n## Entities (confirmed first; uncertain ones say so)")
    rows = db.query(
        "SELECT * FROM entities WHERE investigation_id = ? AND status IN ('confirmed', "
        "'active', 'candidate') ORDER BY type, CASE status WHEN 'confirmed' THEN 0 "
        "ELSE 1 END, confidence DESC, key", (inv_id,))
    by_type: dict[str, list[dict]] = {}
    for r in rows:
        by_type.setdefault(r["type"], []).append(r)
    relevant_findings: list[str] = []
    for etype, items in by_type.items():
        if kind != "domain" and etype in _PLUMBING:
            out.append(f"### {etype}: {len(items)} (plumbing; open with list_entities if needed)")
            continue
        out.append(f"### {etype} ({len(items)})")
        for r in items[:MAX_ENTITIES_PER_TYPE]:
            attrs = {k: v for k, v in best_attributes(r["attributes"]).items()
                     if k not in ("sha256", "file", "raw_path") and v not in (None, "", [])}
            fids = [x["finding_id"] for x in db.query(
                "SELECT finding_id FROM entity_sources WHERE entity_id = ?", (r["id"],))]
            relevant_findings += fids
            v = verdict_of.get(r["id"])
            mark = r["status"]
            if v:
                mark += f"; {'Claude' if v['by_agent'] else 'analyst'} verdict {v['verdict']}: " \
                        f"{v['reason'][:200]}"
            out.append(f"- {r['id']} {r['key']} [{mark}] attrs={_compact(attrs)} "
                       f"findings={','.join(fids[:8])}")
        if len(items) > MAX_ENTITIES_PER_TYPE:
            out.append(f"- ... {len(items) - MAX_ENTITIES_PER_TYPE} more (list_entities)")

    # --- source texts ---------------------------------------------------------------
    out.append("\n## Sources (finding id, what was looked up, content)")
    used = sum(len(x) + 1 for x in out)
    seen: set[str] = set()
    findings = db.query("SELECT * FROM findings WHERE investigation_id = ? AND error IS NULL "
                        "ORDER BY fetched_at", (inv_id,))
    wanted = set(relevant_findings)
    # Pages that only produced rejected entities are about someone else.
    off_subject = {x["finding_id"] for x in db.query(
        "SELECT es.finding_id FROM entity_sources es JOIN entities e ON e.id = es.entity_id "
        "WHERE e.investigation_id = ? AND e.status = 'rejected'", (inv_id,))} - wanted
    # Screening and registries first (short, decisive), then pages, then the rest.
    def rank(f: dict) -> int:
        if f["connector"] in LEGAL_CONNECTORS:
            return 0
        if f["connector"] in ("anaf", "vies", "gleif", "companies_house"):
            return 1
        if f["connector"] in _TEXT_CONNECTORS:
            return 2 if f["id"] in wanted else 4
        return 3 if f["id"] in wanted else 5
    skipped = 0
    for f in sorted(findings, key=rank):
        if f["id"] in seen or f["connector"] == "screenshot" or f["id"] in off_subject:
            continue
        if rank(f) >= 4 and kind != "domain" and f["connector"] not in _TEXT_CONNECTORS:
            skipped += 1
            continue
        seen.add(f["id"])
        entry = finding_text(data_dir, f)
        if used + len(entry) > BUDGET_CHARS:
            skipped += 1
            continue
        out.append(entry)
        used += len(entry) + 1
    if skipped:
        out.append(f"\n({skipped} more findings not shown; list_findings / get_finding.)")

    # --- agent notes ----------------------------------------------------------------
    runs = db.query(
        "SELECT kind, goal, summary FROM agent_runs WHERE investigation_id = ? AND kind IN "
        "('collection', 'worker', 'deep', 'verify') AND summary IS NOT NULL AND summary != '' "
        "ORDER BY started_at DESC LIMIT 12", (inv_id,))
    if runs:
        out.append("\n## Notes from the collection and verification agents (leads with "
                   "finding ids; check them against the sources above)")
        for r in runs:
            out.append(f"### {r['kind']}: {r['goal'][:200]}\n{r['summary'][:4000]}")
    return "\n".join(out)


def _compact(attrs: dict) -> str:
    text = json.dumps(attrs, ensure_ascii=False, default=str)
    return text if len(text) <= 600 else text[:600] + "…}"


def finding_text(data_dir: Any, f: dict) -> str:
    head = (f"\n[{f['id']}] {f['connector']} | {(f['input'] or {}).get('arg', '')} | "
            f"{f['fetched_at'][:10]}" + (f" | note: {f['note'][:200]}" if f["note"] else ""))
    raw = None
    if f["raw_path"]:
        path = data_dir / f["raw_path"]
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8")).get("raw")
            except (OSError, ValueError):
                raw = None
    if raw is None:
        return head
    field = _TEXT_CONNECTORS.get(f["connector"])
    if field and isinstance(raw, dict):
        title = raw.get("title") or raw.get("name") or ""
        url = raw.get("final_url") or raw.get("url") or ""
        text = str(raw.get(field) or "")
        extra = ""
        if f["connector"] == "social_capture" and raw.get("profile"):
            extra = "\nprofile: " + _compact(raw["profile"])
        return (f"{head}\n{title} {url}{extra}\n{text[:PAGE_CHARS]}"
                + (" [...]" if len(text) > PAGE_CHARS else ""))
    blob = json.dumps(raw, ensure_ascii=False, default=str)
    return f"{head}\n{blob[:RAW_CHARS]}" + (" [...]" if len(blob) > RAW_CHARS else "")
