"""Tools the agents can call, and how each call is executed.

Collection tools are the connectors themselves; each call goes through the
ledger, so the agent is held to exactly the same gate, audit and provenance as
the quick scan. On top of the gate, two agent-specific rules:

* screening connectors (sanctions, wanted lists) only run on the
  investigation's own Person/Organization targets;
* a per-run tool budget.

Report tools are read-only views of the graph, ``capture_evidence``, and
``submit_report`` (which lints the draft and stores it).

Tool definitions are sorted by name so the request prefix is byte-stable and
stays cached.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import connectors
from connectors.base import EntityType

from .. import report as rep
from ..detect import DetectError, canonical_key, normalize_name
from ..evidence import capture
from ..ledger import Ledger, RunOutcome
from ..models import new_id, now
from ..resolver import entity_view

MAX_RESULT_CHARS = 8000
LEGAL = rep.LEGAL_CONNECTORS

# Extra connector parameters beyond the single looked-up value.
EXTRA_PARAMS: dict[str, dict[str, dict]] = {
    "interpol": {"notice_type": {"type": "string", "enum": ["red", "un"],
                                 "description": "red = wanted, un = UN Security Council"}},
    "national_wanted": {"countries": {"type": "array", "items": {"type": "string"},
                                      "description": "Restrict to these list codes, e.g. [\"ro\", \"fbi\"]"}},
}

REPORT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "summary": {"type": "string", "description": "2-6 sentences for a decision-maker"},
        "sections": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "claims": {"type": "array", "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "unique, e.g. c1"},
                        "text": {"type": "string"},
                        "evidence": {"type": "array", "items": {"type": "string"},
                                     "description": "finding ids (f_...)"},
                        "entities": {"type": "array", "items": {"type": "string"},
                                     "description": "entity ids (e_...)"},
                        "status": {"type": "string",
                                   "enum": ["verified", "unverified", "leaked", "inference"]},
                        "sensitive": {"type": "boolean"},
                        "severity": {"type": "string", "enum": ["info", "low", "medium", "high"]},
                    },
                    "required": ["id", "text", "evidence", "status"],
                }},
            },
            "required": ["title", "claims"],
        }},
        "outstanding": {"type": "array", "items": {"type": "string"}},
        "method": {"type": "string", "description": "internal notes on how the data was gathered"},
    },
    "required": ["title", "summary", "sections"],
}


def _tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "name": name,
        "description": description,
        "input_schema": {"type": "object", "properties": properties, "required": required,
                         "additionalProperties": False},
        # Streamed requests: inputs arrive as generated; we validate them below.
        "eager_input_streaming": True,
    }


GRAPH_OVERVIEW = _tool(
    "graph_overview",
    "Summary of the investigation graph: targets, entity counts by type and status, the "
    "highest-confidence entities with their ids, and unverified candidates.", {}, [])
GET_ENTITY = _tool(
    "get_entity",
    "One entity with its attributes, the findings that support it and its edges.",
    {"entity_id": {"type": "string"}}, ["entity_id"])
LIST_ENTITIES = _tool(
    "list_entities",
    "Entities of the investigation, optionally filtered by type and minimum confidence.",
    {"type": {"type": "string", "enum": [t.value for t in EntityType]},
     "min_confidence": {"type": "number", "minimum": 0, "maximum": 1},
     "limit": {"type": "integer", "minimum": 1, "maximum": 200}}, [])
GET_FINDING = _tool(
    "get_finding",
    "One finding (a connector call): connector, input, time, hash, and an excerpt of the "
    "raw payload.", {"finding_id": {"type": "string"}}, ["finding_id"])
CAPTURE_EVIDENCE = _tool(
    "capture_evidence",
    "Screenshot a web page as evidence (banner with source URL and capture time). Returns "
    "a finding id to cite. Refused for the target's own hosts unless authorized.",
    {"url": {"type": "string"},
     "entity_id": {"type": "string", "description": "entity the page is about, if any"}},
    ["url"])
SUBMIT_REPORT = _tool(
    "submit_report",
    "Submit the full report. Runs the report linter and returns its issues; errors must be "
    "fixed and the report resubmitted.", {"report": REPORT_SCHEMA}, ["report"])


def connector_tools(investigation: dict) -> list[dict]:
    tools = []
    for name in sorted(connectors.REGISTRY):
        spec = connectors.REGISTRY[name]
        if name == "screenshot":
            continue  # evidence is the report agent's job
        if not spec.passive and investigation.get("mode") != "active":
            continue  # the gate would refuse it anyway; don't offer it
        mode = "Passive." if spec.passive else (
            "ACTIVE: contacts the target; only for in-scope hosts.")
        if name in LEGAL:
            mode += " Screening: only for the investigation's own targets; hits are unverified."
        if name == "dork_search":
            props = {"query": {"type": "string", "description": "query or dork, e.g. "
                               "'\"Jane Doe\" site:linkedin.com'"},
                     "about_entity_id": {"type": "string",
                                         "description": "entity the search is about, to link hits"}}
            tools.append(_tool(name, f"{spec.description} {mode}", props, ["query"]))
            continue
        props = {"value": {"type": "string", "description": f"the {spec.accepts.value} to look up"},
                 **EXTRA_PARAMS.get(name, {})}
        tools.append(_tool(name, f"{spec.description} Input: {spec.accepts.value}. {mode}",
                           props, ["value"]))
    return tools + [GET_ENTITY, GRAPH_OVERVIEW]


def report_tools() -> list[dict]:
    tools = [CAPTURE_EVIDENCE, GET_ENTITY, GET_FINDING, GRAPH_OVERVIEW, LIST_ENTITIES,
             SUBMIT_REPORT]
    if "screenshot" not in connectors.REGISTRY:
        tools.remove(CAPTURE_EVIDENCE)
    return sorted(tools, key=lambda t: t["name"])


class ToolError(Exception):
    """A tool call that can't run; the message goes back to the model as is_error."""


def _str(args: dict, key: str, required: bool = True, max_len: int = 500) -> str | None:
    value = args.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > max_len:
        raise ToolError(f"'{key}' must be a non-empty string of at most {max_len} characters")
    return value.strip()


def _dump(obj: Any) -> str:
    text = json.dumps(obj, default=str, ensure_ascii=False)
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + '..." [truncated]'
    return text


@dataclass
class ToolBox:
    ledger: Ledger
    investigation: dict
    run_id: str
    user: str
    budget: int
    calls: int = 0
    report_id: str | None = None
    report_ok: bool = False
    findings: list[str] = field(default_factory=list)

    @property
    def db(self):
        return self.ledger.db

    @property
    def inv_id(self) -> str:
        return self.investigation["id"]

    async def call(self, name: str, args: Any) -> tuple[str, bool]:
        """Run one tool call. Returns ``(content, is_error)``."""
        if not isinstance(args, dict):
            return json.dumps({"INVALID_JSON": json.dumps(args, default=str)}), True
        try:
            if name in connectors.REGISTRY and name != "screenshot":
                return await self._connector(name, args), False
            handler = getattr(self, f"_t_{name}", None)
            if handler is None:
                raise ToolError(f"unknown tool: {name}")
            return await handler(args), False
        except ToolError as e:
            return str(e), True

    # --- connectors ---------------------------------------------------------

    def _spend(self) -> None:
        if self.calls >= self.budget:
            raise ToolError(f"Tool budget exhausted ({self.budget} lookups). Do not call more "
                            "lookup tools; write your final summary now.")
        self.calls += 1

    def _target_keys(self) -> set[str]:
        return {normalize_name(t["value"]) for t in self.db.query(
            "SELECT value FROM targets WHERE investigation_id = ? AND type IN "
            "('Person', 'Organization')", (self.inv_id,))}

    def _find_entity(self, etype: str, key: str) -> str | None:
        row = self.db.one("SELECT id FROM entities WHERE investigation_id = ? AND type = ? "
                          "AND key = ?", (self.inv_id, etype, key))
        return row["id"] if row else None

    async def _connector(self, name: str, args: dict) -> str:
        spec = connectors.REGISTRY[name]
        kwargs: dict[str, Any] = {}
        entity_id = None
        if name == "dork_search":
            arg = _str(args, "query")
            about = _str(args, "about_entity_id", required=False, max_len=40)
            if about:
                row = self.db.get("entities", about)
                entity_id = about if row and row["investigation_id"] == self.inv_id else None
        else:
            value = _str(args, "value")
            if spec.accepts in (EntityType.PERSON, EntityType.ORGANIZATION):
                key = normalize_name(value)
                arg = value
            else:
                try:
                    key = arg = canonical_key(spec.accepts, value)
                except (DetectError, ValueError, UnicodeError) as e:
                    raise ToolError(f"'{value}' is not a valid {spec.accepts.value}: {e}") from e
            if name in LEGAL and key not in self._target_keys():
                raise ToolError(
                    f"Screening is limited to this investigation's targets; '{value}' is not "
                    "one. If screening this person or organization serves the purpose, the "
                    "analyst can add them as a target.")
            entity_id = self._find_entity(spec.accepts.value, key)
            for extra in EXTRA_PARAMS.get(name, {}):
                if extra in args:
                    kwargs[extra] = args[extra]
            if "notice_type" in kwargs and kwargs["notice_type"] not in ("red", "un"):
                raise ToolError("notice_type must be 'red' or 'un'")
            if "countries" in kwargs and not (isinstance(kwargs["countries"], list) and all(
                    isinstance(c, str) for c in kwargs["countries"])):
                raise ToolError("countries must be a list of strings")
        self._spend()
        outcome = await self.ledger.run(self.investigation, name, arg, user=self.user,
                                        run_by=self.run_id, entity_id=entity_id, kwargs=kwargs)
        return _dump(self._summarize(outcome))

    def _summarize(self, outcome: RunOutcome) -> dict:
        if outcome.refused:
            raise ToolError(f"Refused by the platform: {outcome.error}")
        if outcome.error:
            raise ToolError(f"Lookup failed (finding {outcome.finding_id}): {outcome.error}")
        self.findings.append(outcome.finding_id)
        r = outcome.result
        return {
            "finding_id": outcome.finding_id,
            "source": r.source,
            "note": r.note,
            "entity_count": len(r.entities),
            "new_entities": [{k: e[k] for k in ("id", "type", "key", "status")}
                             for e in outcome.fold.new_entities[:40]],
            "entities": [{"type": e.type.value, "key": e.key,
                          **({"unverified": True} if e.attributes.get("unverified") else {})}
                         for e in r.entities[:60]],
            "raw_excerpt": json.dumps(r.raw, default=str, ensure_ascii=False)[:3000],
        }

    # --- graph (read-only) ----------------------------------------------------

    async def _t_graph_overview(self, args: dict) -> str:
        counts = self.db.query(
            "SELECT type, status, count(*) AS n FROM entities WHERE investigation_id = ? "
            "GROUP BY type, status ORDER BY type", (self.inv_id,))
        top = self.db.query(
            "SELECT * FROM entities WHERE investigation_id = ? AND status IN ('active', "
            "'confirmed') ORDER BY confidence DESC, type, key LIMIT 60", (self.inv_id,))
        candidates = self.db.query(
            "SELECT * FROM entities WHERE investigation_id = ? AND status = 'candidate' "
            "LIMIT 30", (self.inv_id,))
        targets = self.db.query("SELECT value, type, entity_id FROM targets "
                                "WHERE investigation_id = ?", (self.inv_id,))
        findings = self.db.one("SELECT count(*) AS n FROM findings WHERE investigation_id = ?",
                               (self.inv_id,))["n"]
        def brief(r):
            return {k: v for k, v in entity_view(r).items()
                    if k in ("id", "type", "key", "label", "confidence", "status")}
        return _dump({"targets": targets, "findings": findings, "counts": counts,
                      "top_entities": [brief(r) for r in top],
                      "unverified_candidates": [brief(r) for r in candidates]})

    async def _t_get_entity(self, args: dict) -> str:
        eid = _str(args, "entity_id", max_len=40)
        row = self.db.get("entities", eid)
        if row is None or row["investigation_id"] != self.inv_id:
            raise ToolError(f"no entity {eid} in this investigation")
        sources = self.db.query(
            "SELECT f.id, f.connector, f.input, f.fetched_at FROM entity_sources es "
            "JOIN findings f ON f.id = es.finding_id WHERE es.entity_id = ? "
            "ORDER BY f.fetched_at", (eid,))
        edges = self.db.query(
            "SELECT e.type, e.confidence, a.id AS from_id, a.key AS from_key, "
            "b.id AS to_id, b.key AS to_key FROM edges e JOIN entities a ON a.id = e.from_id "
            "JOIN entities b ON b.id = e.to_id WHERE e.from_id = ? OR e.to_id = ? LIMIT 60",
            (eid, eid))
        return _dump({**entity_view(row), "sources": sources, "edges": edges})

    async def _t_list_entities(self, args: dict) -> str:
        sql = "SELECT * FROM entities WHERE investigation_id = ? AND confidence >= ?"
        params: list = [self.inv_id, float(args.get("min_confidence") or 0)]
        if args.get("type"):
            sql += " AND type = ?"
            params.append(str(args["type"]))
        limit = int(args.get("limit") or 50)
        rows = self.db.query(sql + " ORDER BY confidence DESC, key LIMIT ?",
                             [*params, max(1, min(limit, 200))])
        return _dump([{k: v for k, v in entity_view(r).items() if k != "first_seen"}
                      for r in rows])

    async def _t_get_finding(self, args: dict) -> str:
        fid = _str(args, "finding_id", max_len=40)
        f = self.db.get("findings", fid)
        if f is None or f["investigation_id"] != self.inv_id:
            raise ToolError(f"no finding {fid} in this investigation")
        raw = None
        if f["raw_path"]:
            path = self.ledger.settings.data_dir / f["raw_path"]
            if path.exists():
                raw = json.loads(path.read_text(encoding="utf-8")).get("raw")
        keep = ("id", "connector", "input", "fetched_at", "sha256", "note", "error")
        return _dump({**{k: f[k] for k in keep},
                      "raw_excerpt": json.dumps(raw, default=str, ensure_ascii=False)[:5000]})

    # --- evidence & report ------------------------------------------------------

    async def _t_capture_evidence(self, args: dict) -> str:
        url = _str(args, "url", max_len=2000)
        about = _str(args, "entity_id", required=False, max_len=40)
        if about:
            row = self.db.get("entities", about)
            about = about if row and row["investigation_id"] == self.inv_id else None
        self._spend()
        outcome = await capture(self.ledger, self.investigation, url, user=self.user,
                                run_by=self.run_id, entity_id=about)
        if outcome.refused:
            raise ToolError(f"Refused by the platform: {outcome.error}")
        if outcome.error:
            raise ToolError(f"Capture failed (finding {outcome.finding_id}): {outcome.error}")
        self.findings.append(outcome.finding_id)
        raw = outcome.result.raw
        return _dump({"finding_id": outcome.finding_id, "note": outcome.result.note,
                      "blocked": raw.get("blocked"), "title": raw.get("title"),
                      "http_status": raw.get("http_status")})

    async def _t_submit_report(self, args: dict) -> str:
        body = args.get("report")
        if not isinstance(body, dict) or not isinstance(body.get("sections"), list):
            raise ToolError(json.dumps({"INVALID_JSON": "report must be an object with a "
                                        "'sections' list"}))
        issues = rep.lint(self.db, self.inv_id, body)
        ts = now()
        title = str(body.get("title") or self.investigation["name"])[:300]
        if self.report_id is None:
            self.report_id = new_id("rep")
            self.db.insert("reports", {"id": self.report_id, "investigation_id": self.inv_id,
                                       "title": title, "body": body, "agent_run_id": self.run_id,
                                       "created_by": self.user, "created_at": ts,
                                       "updated_at": ts})
        else:
            self.db.update("reports", self.report_id, {"title": title, "body": body,
                                                       "updated_at": ts})
        errors = [i for i in issues if i["level"] == "error"]
        self.report_ok = not errors
        return _dump({"report_id": self.report_id, "ok": self.report_ok,
                      "errors": errors, "warnings": [i for i in issues if i["level"] != "error"]})
