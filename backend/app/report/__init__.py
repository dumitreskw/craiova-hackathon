"""Reports: a claim-level data model, a linter, and audience-aware rendering.

A report body is JSON::

    {
      "title": "...",
      "summary": "...",
      "sections": [
        {"title": "...", "claims": [
          {"id": "c1", "text": "...",
           "evidence": ["f_..."],          # finding ids that support the claim
           "entities": ["e_..."],          # graph entities the claim is about
           "status": "verified",           # verified | unverified | leaked | inference
           "sensitive": false,             # GDPR Art. 9 data, flagged on purpose
           "severity": "info"}             # optional: info | low | medium | high
        ]}
      ],
      "outstanding": ["follow-ups that need manual or credentialed access"],
      "method": "internal-only notes on how the data was collected"
    }

Why claims and not free text: each claim carries its own evidence, so the
linter can prove every statement traces to a finding, and the renderer can
number evidence consistently and print or hide citations per audience.

The ``internal`` render shows citations, finding hashes, raw paths and the
collection method. The ``client`` render shows the same findings with only the
source and retrieval date, and no workspace plumbing.
"""

from __future__ import annotations

import base64
import html
import re
from collections import Counter
from pathlib import Path
from typing import Any

from ..models import Database, now
from ..resolver import best_attributes
from ..scan import anchor_domains, on_target

STATUSES = {"verified", "unverified", "leaked", "inference"}
AUDIENCES = {"internal", "client"}
LEGAL_CONNECTORS = {"interpol", "europol", "sanctions", "national_wanted", "fbi_wanted",
                    "court_records"}
_LEGAL_IN = ",".join("?" * len(LEGAL_CONNECTORS))

SOURCE_NAMES = {
    "dns": "Public DNS", "rdap": "RDAP domain registration data",
    "crtsh": "crt.sh certificate transparency logs", "http_probe": "Target website",
    "gravatar": "Gravatar public profile", "github_user": "GitHub public profile",
    "dork_search": "Public web search", "interpol": "INTERPOL public notices",
    "europol": "EU Most Wanted (ENFAST)", "sanctions": "OpenSanctions",
    "national_wanted": "National police wanted lists",
    "screenshot": "Page capture",
    "analyst_material": "Analyst-supplied material",
}

STATUS_LABEL = {
    "unverified": "unverified",
    "leaked": "leaked record, unverified",
    "inference": "analyst inference",
}

_CREDENTIAL = re.compile(
    r"(pass(word|wd)?|pwd|secret|api[_-]?key|token)\s*[:=]\s*\S+"
    r"|\$2[aby]\$\d\d\$[./A-Za-z0-9]{20,}"
    r"|\b(hash|md5|sha-?1|sha-?256|ntlm)\b\W{0,3}[a-f0-9]{32,128}\b", re.IGNORECASE)

# GDPR Art. 9 special categories: report only when relevant, and flagged.
_SENSITIVE = re.compile(
    r"\b(religio\w*|faith|church|mosque|health|medical|diagnos\w*|disease|illness|"
    r"disabilit\w*|pregnan\w*|political\w*|party member|sexual\w*|orientation|"
    r"ethnic\w*|racial|race|trade union|biometric|genetic)\b", re.IGNORECASE)

# Workspace plumbing that has no place in a client deliverable.
_INTERNAL = re.compile(
    r"(evidence/|findings/|raw_path|\.sqlite|\bf_[0-9a-f]{12}\b|\be_[0-9a-f]{12}\b|"
    r"mullvad|wireguard|\bwg-\d|egress|\bkali\b|quick[_ ]scan|claude)",
    re.IGNORECASE)


def _claims(body: dict) -> list[tuple[dict, dict]]:
    return [(s, c) for s in body.get("sections", []) for c in s.get("claims", [])]


# --- lint -------------------------------------------------------------------

def lint(db: Database, investigation_id: str, body: dict, audience: str = "internal") -> list[dict]:
    """Return a list of ``{level, rule, claim, message}``. Errors block export."""
    issues: list[dict] = []

    def issue(level: str, rule: str, message: str, claim: str | None = None) -> None:
        issues.append({"level": level, "rule": rule, "claim": claim, "message": message})

    findings = {f["id"]: f for f in db.query(
        "SELECT id, connector, error FROM findings WHERE investigation_id = ?",
        (investigation_id,))}
    entities = {e["id"]: e for e in db.query(
        "SELECT id, type, key, status FROM entities WHERE investigation_id = ?",
        (investigation_id,))}

    if not body.get("sections"):
        issue("error", "empty", "the report has no sections")
    seen_ids: set[str] = set()
    for section, claim in _claims(body):
        cid = claim.get("id") or "?"
        if cid in seen_ids:
            issue("error", "duplicate_id", f"claim id {cid} is used twice", cid)
        seen_ids.add(cid)
        status = claim.get("status", "verified")
        text = claim.get("text", "")
        evidence = claim.get("evidence") or []

        if status not in STATUSES:
            issue("error", "status", f"unknown status '{status}'", cid)
        if not evidence and status != "inference":
            issue("error", "uncited",
                  "claim has no evidence; cite a finding or mark it as an inference", cid)
        for fid in evidence:
            f = findings.get(fid)
            if f is None:
                issue("error", "unknown_evidence", f"{fid} is not a finding of this investigation", cid)
            elif f["error"]:
                issue("error", "failed_evidence", f"{fid} is a failed connector call", cid)
        for eid in claim.get("entities") or []:
            if eid not in entities:
                issue("error", "unknown_entity", f"{eid} is not an entity of this investigation", cid)

        # Legal / enforcement hits are name matches, never verdicts.
        cited_legal = any(findings.get(f, {}).get("connector") in LEGAL_CONNECTORS
                          for f in evidence)
        cited_candidates = [entities[e] for e in claim.get("entities") or []
                            if e in entities and " @ " in entities[e]["key"]]
        confirmed = any(e["status"] == "confirmed" for e in cited_candidates)
        # A recorded "no match" is a fine verified statement; a hit is not.
        if ((cited_legal or cited_candidates) and status != "unverified" and not confirmed
                and (cited_candidates or not _is_no_match(text))):
            issue("error", "legal_assertion",
                  "a wanted/sanctions/court hit is stated as fact; mark it 'unverified' "
                  "or confirm the candidate with a disambiguator first", cid)

        # Supplied files and notes are leads from the analyst or client.
        if status == "verified" and evidence and all(
                findings.get(f, {}).get("connector") == "analyst_material" for f in evidence):
            issue("warning", "supplied_only",
                  "rests only on analyst-supplied material; cite a public source that "
                  "confirms it or mark it 'unverified'", cid)

        if _CREDENTIAL.search(text):
            issue("error", "credential",
                  "text looks like a password, hash or secret; report the exposure, never the value",
                  cid)
        if _SENSITIVE.search(text) and not claim.get("sensitive"):
            issue("warning", "sensitive",
                  "possible special-category data (GDPR Art. 9); keep only if the purpose "
                  "needs it and set sensitive: true", cid)
        if audience == "client" and _INTERNAL.search(text):
            issue("warning", "internal_detail",
                  "collection plumbing in a client report: " + _INTERNAL.search(text).group(0), cid)

    if audience == "client":
        for field in ("summary", "title"):
            m = _INTERNAL.search(body.get(field) or "")
            if m:
                issue("warning", "internal_detail", f"{field} mentions '{m.group(0)}'")
        for line in body.get("outstanding") or []:
            m = _INTERNAL.search(line)
            if m:
                issue("warning", "internal_detail", f"outstanding step mentions '{m.group(0)}'")
    return issues


def _is_no_match(text: str) -> bool:
    return bool(re.search(r"\bno (match|hit|result|listing)s?\b|\bnot (listed|found)\b", text,
                          re.IGNORECASE))


# --- render -----------------------------------------------------------------

def _evidence_numbers(body: dict) -> dict[str, int]:
    """Number findings by first citation, so the list reads in report order."""
    numbers: dict[str, int] = {}
    for _section, claim in _claims(body):
        for fid in claim.get("evidence") or []:
            numbers.setdefault(fid, len(numbers) + 1)
    return numbers


def _evidence_rows(db: Database, numbers: dict[str, int]) -> list[dict]:
    rows = []
    for fid, n in sorted(numbers.items(), key=lambda kv: kv[1]):
        f = db.get("findings", fid)
        if f:
            rows.append({"n": n, **f})
    return rows


def _screenshot(db: Database, finding: dict, evidence_dir: Path | None) -> Path | None:
    """The PNG behind a screenshot finding, if it is on disk."""
    if finding["connector"] != "screenshot" or evidence_dir is None:
        return None
    doc = db.one("SELECT e.attributes FROM entity_sources es JOIN entities e ON "
                 "e.id = es.entity_id WHERE es.finding_id = ? AND e.type = 'Document'",
                 (finding["id"],))
    name = best_attributes(doc["attributes"]).get("file") if doc else None
    path = evidence_dir / finding["investigation_id"] / "screenshots" / (name or "")
    return path if name and path.is_file() else None


def render(db: Database, investigation: dict, body: dict, audience: str = "internal",
           fmt: str = "md", cite: bool = True, evidence_dir: Path | None = None) -> str:
    """Render a report. With ``evidence_dir``, HTML embeds screenshot evidence
    inline (data URIs), so the exported file stands alone. DOCX and PDF are in
    ``app.report.export`` and use the same blocks."""
    blocks = build_blocks(db, investigation, body, audience, cite, evidence_dir)
    return _to_html(blocks) if fmt == "html" else _to_md(blocks)


def build_blocks(db: Database, investigation: dict, body: dict, audience: str = "internal",
                 cite: bool = True, evidence_dir: Path | None = None) -> list[tuple[str, Any]]:
    """The audience-specific report as a list of ``(kind, value)`` blocks.

    Every output format serializes these, so what each audience sees is
    decided in one place. Kinds: h1, meta, h2, p, claims, ul, evidence.
    """
    if audience not in AUDIENCES:
        raise ValueError(f"audience must be one of {sorted(AUDIENCES)}")
    numbers = _evidence_numbers(body)
    evidence = _evidence_rows(db, numbers)
    internal = audience == "internal"

    blocks: list[tuple[str, Any]] = [("h1", body.get("title") or investigation["name"])]
    meta = {"Purpose": investigation["purpose"], "Prepared": now()[:10]}
    if internal:
        meta.update({"Investigation": investigation["id"],
                     "Legal basis": investigation["legal_basis"],
                     "Mode": investigation["mode"]})
    blocks.append(("meta", meta))
    if body.get("summary"):
        blocks += [("h2", "Summary"), ("p", body["summary"])]

    for section in body.get("sections", []):
        blocks.append(("h2", section.get("title", "")))
        items = []
        for claim in section.get("claims", []):
            text = claim.get("text", "")
            label = STATUS_LABEL.get(claim.get("status", "verified"))
            if label:
                text += f" ({label})"
            if internal and claim.get("sensitive"):
                text += " [sensitive]"
            if internal and claim.get("severity"):
                text = f"[{claim['severity'].upper()}] " + text
            refs = [numbers[f] for f in claim.get("evidence") or [] if f in numbers]
            items.append((text, refs if cite else []))
        blocks.append(("claims", items))

    if body.get("outstanding"):
        blocks += [("h2", "Outstanding steps"), ("ul", body["outstanding"])]

    if evidence:
        blocks.append(("h2", "Evidence"))
        lines = []
        for f in evidence:
            source = SOURCE_NAMES.get(f["connector"], f["connector"])
            arg = (f.get("input") or {}).get("arg", "")
            verb = {"screenshot": "captured", "analyst_material": "supplied"}.get(
                f["connector"], "retrieved")
            line = f"[{f['n']}] {source}: {arg}, {verb} {f['fetched_at'][:10]}"
            if internal:
                line += (f" — finding {f['id']}, sha256 {(f['sha256'] or '')[:16]}…, "
                         f"{f['raw_path']}")
            lines.append((line, _screenshot(db, f, evidence_dir)))
        blocks.append(("evidence", lines))

    if internal:
        blocks += [("h2", "Collection method (internal)"),
                   ("p", body.get("method") or ""),
                   ("ul", _collection_summary(db, investigation["id"]))]

    return blocks


def _collection_summary(db: Database, investigation_id: str) -> list[str]:
    rows = db.query("SELECT connector, error, passive FROM findings WHERE investigation_id = ?",
                    (investigation_id,))
    calls = Counter(r["connector"] for r in rows)
    errors = Counter(r["connector"] for r in rows if r["error"])
    refused = db.one("SELECT count(*) AS n FROM audit WHERE investigation_id = ? "
                     "AND outcome = 'refused'", (investigation_id,))["n"]
    lines = [f"{name}: {n} call(s)" + (f", {errors[name]} failed" if errors[name] else "")
             for name, n in sorted(calls.items())]
    active = sum(1 for r in rows if not r["passive"])
    lines.append(f"active (target-touching) calls: {active}; refused by the gate: {refused}")
    return lines


def _to_md(blocks: list[tuple[str, Any]]) -> str:
    out: list[str] = []
    for kind, val in blocks:
        if kind == "h1":
            out.append(f"# {val}\n")
        elif kind == "h2":
            out.append(f"\n## {val}\n")
        elif kind == "meta":
            out += [f"- **{k}:** {v}" for k, v in val.items()]
        elif kind == "p":
            out.append(f"{val}\n")
        elif kind == "claims":
            for text, refs in val:
                cites = " " + "".join(f"[{n}]" for n in refs) if refs else ""
                out.append(f"- {text}{cites}")
        elif kind == "ul":
            out += [f"- {line}" for line in val]
        elif kind == "evidence":
            out += [f"{line}  " for line, _img in val]
    return "\n".join(out).strip() + "\n"


def _to_html(blocks: list[tuple[str, Any]]) -> str:
    e = html.escape
    body: list[str] = []
    for kind, val in blocks:
        if kind == "h1":
            body.append(f"<h1>{e(val)}</h1>")
        elif kind == "h2":
            body.append(f"<h2>{e(val)}</h2>")
        elif kind == "meta":
            body.append("<dl>" + "".join(f"<dt>{e(k)}</dt><dd>{e(str(v))}</dd>"
                                         for k, v in val.items()) + "</dl>")
        elif kind == "p":
            body.append(f"<p>{e(val)}</p>")
        elif kind == "claims":
            body.append("<ul>" + "".join(
                f"<li>{e(t)}" + "".join(f' <a href="#ev{n}">[{n}]</a>' for n in refs) + "</li>"
                for t, refs in val) + "</ul>")
        elif kind == "ul":
            body.append("<ul>" + "".join(f"<li>{e(x)}</li>" for x in val) + "</ul>")
        elif kind == "evidence":
            items = []
            for i, (line, img) in enumerate(val, 1):
                pic = ""
                if img is not None:
                    data = base64.b64encode(img.read_bytes()).decode("ascii")
                    pic = f'<br><img alt="evidence {i}" src="data:image/png;base64,{data}">'
                items.append(f'<li id="ev{i}">{e(line)}{pic}</li>')
            body.append("<ol class=evidence>" + "".join(items) + "</ol>")
    title = e(blocks[0][1]) if blocks else "Report"
    return (f"<!doctype html><html><head><meta charset=utf-8><title>{title}</title>"
            "<style>body{font:15px/1.5 system-ui,sans-serif;max-width:52rem;margin:2rem auto;"
            "padding:0 1rem}dt{font-weight:600;float:left;clear:left;width:9rem}"
            "dd{margin-left:9.5rem}.evidence{font-size:13px;color:#444}"
            ".evidence img{max-width:100%;border:1px solid #ccc;margin:.4rem 0 1rem}</style></head>"
            f"<body>{''.join(body)}</body></html>")


# --- deterministic draft (no LLM) --------------------------------------------

TYPE_SECTIONS = [
    ("Infrastructure", ["Domain", "IPAddress", "ASN", "Certificate"]),
    ("Organizations and people", ["Organization", "Person"]),
    ("Emails, usernames and profiles", ["Email", "Username", "Phone", "SocialProfile"]),
    ("Code", ["Repository"]),
    ("Web mentions", ["URL", "Document"]),
]
MAX_PER_TYPE = 15


# Attributes that are plumbing, not findings: hashes and file names would also
# trip the credential rule of the linter.
_DRAFT_SKIP = {"unverified", "sha256", "file", "raw_path", "listing_url"}


def draft_from_graph(db: Database, investigation: dict, min_confidence: float = 0.5) -> dict:
    """Build a plain, fully cited report body straight from the graph.

    This is the fallback when the report agent is unavailable: it does no
    curation, but every claim it writes is cited and passes the linter.
    """
    inv_id = investigation["id"]
    n = 0

    def cid() -> str:
        nonlocal n
        n += 1
        return f"c{n}"

    def sources(eid: str) -> list[str]:
        # Entities that only legal connectors produced (official listing URLs)
        # are reported in the screening section, with the right caveats.
        return [r["finding_id"] for r in db.query(
            "SELECT es.finding_id FROM entity_sources es JOIN findings f ON f.id = es.finding_id "
            f"WHERE es.entity_id = ? AND f.error IS NULL AND f.connector NOT IN ({_LEGAL_IN}) "
            "ORDER BY f.fetched_at",
            (eid, *sorted(LEGAL_CONNECTORS)))]

    def claim_for(row: dict) -> dict | None:
        ev = sources(row["id"])
        if not ev:
            return None  # a bare seed with nothing found yet
        attrs = best_attributes(row["attributes"])
        if row["type"] == "Document" and attrs.get("kind") == "screenshot":
            return None  # captures are evidence for other claims, not findings
        detail = ", ".join(f"{k}: {v}" for k, v in attrs.items()
                           if isinstance(v, (str, int, float)) and k not in _DRAFT_SKIP)
        text = f"{row['type']} {row['key']}" + (f" ({detail})" if detail else "")
        return {"id": cid(), "text": text[:400], "evidence": ev, "entities": [row["id"]],
                "status": "verified"}

    anchors = anchor_domains(db, inv_id, [])
    third_party: list[dict] = []
    sections = []
    for title, types in TYPE_SECTIONS:
        claims = []
        for etype in types:
            rows = db.query(
                "SELECT * FROM entities WHERE investigation_id = ? AND type = ? "
                "AND status IN ('active', 'confirmed') AND confidence >= ? "
                "ORDER BY confidence DESC, key", (inv_id, etype, min_confidence))
            own = [r for r in rows if on_target(r, anchors)]
            third_party += [r for r in rows if not on_target(r, anchors)]
            for row in own[:MAX_PER_TYPE]:
                if claim := claim_for(row):
                    claims.append(claim)
            if len(own) > MAX_PER_TYPE:
                claims.append({"id": cid(), "status": "inference", "evidence": [],
                               "text": f"{len(own) - MAX_PER_TYPE} more {etype} entities "
                                       "are in the graph and not listed here."})
        if claims:
            sections.append({"title": title, "claims": claims})

    # Scope creep is a finding, not an action: list them for the client to confirm.
    related = [c for c in map(claim_for, third_party[:MAX_PER_TYPE * 2]) if c]
    if related:
        sections.append({"title": "Related third-party assets (not investigated further)",
                         "claims": related})

    # Legal / enforcement screening: record "no match" as well as hits.
    legal_claims = []
    for f in db.query(
            "SELECT * FROM findings WHERE investigation_id = ? AND error IS NULL "
            f"AND connector IN ({_LEGAL_IN}) ORDER BY fetched_at",
            (inv_id, *sorted(LEGAL_CONNECTORS))):
        hits = db.query(
            "SELECT e.* FROM entity_sources es JOIN entities e ON e.id = es.entity_id "
            "WHERE es.finding_id = ? AND e.status IN ('candidate', 'confirmed') "
            "AND e.key LIKE '% @ %'", (f["id"],))
        source = SOURCE_NAMES.get(f["connector"], f["connector"])
        name = (f["input"] or {}).get("arg", "")
        if not hits:
            legal_claims.append({"id": cid(), "status": "verified", "evidence": [f["id"]],
                                 "text": f"{source}: no match found for {name}."})
        for h in hits:
            attrs = best_attributes(h["attributes"])
            dis = attrs.get("disambiguators") or {}
            dis_txt = ", ".join(f"{k}: {v}" for k, v in dis.items()) or "none published"
            confirmed = h["status"] == "confirmed"
            match = ("confirmed by the analyst as the subject" if confirmed
                     else "shares the name searched")
            legal_claims.append({
                "id": cid(), "evidence": [f["id"]], "entities": [h["id"]],
                "status": "verified" if confirmed else "unverified",
                "text": (f"{source}: listing '{attrs.get('title', h['key'])}' {match} "
                         f"({attrs.get('list', '')}; disambiguators: {dis_txt})."),
            })
    if legal_claims:
        sections.append({"title": "Sanctions, wanted lists and enforcement", "claims": legal_claims})

    targets = [t["value"] for t in db.query(
        "SELECT value FROM targets WHERE investigation_id = ? ORDER BY created_at", (inv_id,))]
    return {
        "title": f"{investigation['name']}: OSINT findings",
        "summary": (f"Automated draft for {', '.join(targets) or 'the investigation'}. "
                    "Lists what the collected sources returned; it is not yet curated."),
        "sections": sections,
        "outstanding": [("Analyst review: curate, remove off-purpose findings, confirm or "
                         "reject every unverified candidate.")],
        "method": "Generated by draft_from_graph from the investigation graph "
                  f"(confidence >= {min_confidence}).",
    }
