"""Tools the agents can call, and how each call is executed.

Collection tools are the connectors themselves; each call goes through the
ledger, so the agent is held to exactly the same gate, audit and provenance as
the quick scan. On top of the gate, two agent-specific rules:

* screening connectors (sanctions, wanted lists) only run on the
  investigation's own Person/Organization targets;
* a per-run tool budget.

Report tools are read-only views of the graph, ``capture_evidence``, and
``submit_report`` (which lints the draft and stores it).

Verification tools (``review_queue``, ``set_verdicts``) let the verify agent
judge whether each entity is about the subject (app/review.py), and
``set_identities`` record the who's-who of same-name people
(app/identity.py) for the analyst to pick the subject from; the reviewer
agent fact-checks a submitted report with ``submit_review``. Persona tools
(``social_capture``, ``social_search``) drive the logged-in research personas
on the Kali VM (app/social.py), when the worker is online.

``read_material`` reads analyst-supplied material (app/materials.py) in
chunks. It is not a lookup and costs no budget; a worker can only read the
material its orchestrator handed it.

Tool definitions are sorted by name so the request prefix is byte-stable and
stays cached.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import connectors
from connectors.base import EntityType

from .. import identity, review
from .. import materials as mat
from .. import report as rep
from ..detect import DetectError, canonical_key, normalize_name
from ..evidence import LOGIN_ONLY_MESSAGE, capture, login_only, read_page
from ..ledger import Ledger, RunOutcome
from ..models import new_id, now
from ..resolver import entity_view
from ..scan import COST_TIERS
from ..social import SocialError

MAX_RESULT_CHARS = 8000
MATERIAL_CHUNK = 12000
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
    "One finding (a connector call): connector, input, time, hash, and its content: the "
    "page text for read pages, otherwise an excerpt of the raw payload.",
    {"finding_id": {"type": "string"}}, ["finding_id"])
LIST_FINDINGS = _tool(
    "list_findings",
    "Every lookup made in this investigation: finding id, connector, what was looked up, "
    "page title for read pages, and any note or error. Read pages hold most of the facts "
    "about organisations and people; open them with get_finding.",
    {"connector": {"type": "string", "description": "filter, e.g. web_read"}}, [])
CAPTURE_EVIDENCE = _tool(
    "capture_evidence",
    "Screenshot a web page as evidence (banner with source URL and capture time). Returns "
    "a finding id to cite.",
    {"url": {"type": "string"},
     "entity_id": {"type": "string", "description": "entity the page is about, if any"}},
    ["url"])
SUBMIT_REPORT = _tool(
    "submit_report",
    "Submit the full report. Runs the report linter and returns its issues; errors must be "
    "fixed and the report resubmitted.", {"report": REPORT_SCHEMA}, ["report"])


REVIEW_QUEUE = _tool(
    "review_queue",
    "Entities that still need a verdict (is this really the subject, or tied to them?), "
    "a page at a time, with their attributes, where they came from and what they link "
    "to. Sanctions/wanted-list name matches come first.",
    {"offset": {"type": "integer", "minimum": 0},
     "limit": {"type": "integer", "minimum": 1, "maximum": 60}}, [])
SET_VERDICTS = _tool(
    "set_verdicts",
    "Record verdicts. confirmed: about the subject (or the subject's own asset), with "
    "the evidence that ties it. rejected: a namesake, an unrelated account, a third "
    "party, or noise. uncertain: can't tell from what was collected; say what would "
    "settle it. Every verdict needs a reason a reviewer can check.",
    {"verdicts": {"type": "array", "maxItems": 60, "items": {
        "type": "object",
        "properties": {"entity_id": {"type": "string"},
                       "verdict": {"type": "string", "enum": list(review.VERDICTS)},
                       "reason": {"type": "string"}},
        "required": ["entity_id", "verdict", "reason"], "additionalProperties": False}}},
    ["verdicts"])
SET_IDENTITIES = _tool(
    "set_identities",
    "Record who's who when results name several different people with the subject's name "
    "(e.g. a dentist in Craiova, a footballer in Bucharest, a UK company director). One "
    "entry per distinct person, with every entity that belongs to them; mark is_subject "
    "on the one the subject profile matches, if any. Replaces the previous grouping. The "
    "analyst picks the subject from these with one click, so make labels and "
    "distinguishing details concrete.",
    {"identities": {"type": "array", "maxItems": 20, "items": {
        "type": "object",
        "properties": {
            "label": {"type": "string", "description": "name plus what they are, e.g. "
                      "'Ion Popescu, dentist in Craiova'"},
            "summary": {"type": "string", "description": "who this person is, from the "
                        "sources (with finding ids)"},
            "distinguishing": {"type": "string", "description": "what tells them apart: "
                               "birth year, city, employer, photo, linked accounts"},
            "entity_ids": {"type": "array", "items": {"type": "string"}},
            "is_subject": {"type": "boolean"}},
        "required": ["label", "entity_ids", "distinguishing"],
        "additionalProperties": False}}},
    ["identities"])
SOCIAL_CAPTURE = _tool(
    "social_capture",
    "Open a social-media profile through a logged-in research persona on the Kali VM "
    "(read-only: it never follows, likes or messages) and return the profile fields, "
    "recent post text and links. Use it to settle whether an account is the subject "
    "(bio, employer, location, linked accounts, photos' captions). The profile must "
    "already be in the graph. Can take a few minutes. Counts as a lookup.",
    {"url": {"type": "string"},
     "entity_id": {"type": "string", "description": "the SocialProfile entity, if known"}},
    ["url"])
SUBMIT_REVIEW = _tool(
    "submit_review",
    "Submit the fact-check of the report: every problem found, by claim id. An empty "
    "problems list means the report can go out as it is.",
    {"problems": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "claim_id": {"type": "string", "description": "claim id, or 'report' for the "
                         "report as a whole (summary, structure, missing topic)"},
            "kind": {"type": "string", "enum": [
                "unsupported", "wrong_subject", "overstated", "irrelevant", "missing_caveat",
                "contradiction", "missing", "other"]},
            "detail": {"type": "string", "description": "what is wrong and what the cited "
                       "source actually says"},
            "fix": {"type": "string"}},
        "required": ["claim_id", "kind", "detail"], "additionalProperties": False}},
     "assessment": {"type": "string", "description": "two or three sentences on the report "
                    "as a whole"}},
    ["problems", "assessment"])


def social_search_tool(platforms: list[str]) -> dict:
    return _tool(
        "social_search",
        "Search inside a social platform through a logged-in persona (people search), "
        "reaching profiles public web search can't. Results are candidate profiles: open "
        "the likely ones with social_capture before you rely on them. Counts as a lookup.",
        {"platform": {"type": "string", "enum": platforms},
         "query": {"type": "string", "description": "a name, optionally with employer or "
                   "city, e.g. 'Jane Doe Bucharest'"}},
        ["platform", "query"])


def persona_tools(platforms: list[str]) -> list[dict]:
    """Persona tools, offered only when the Kali worker has a logged-in persona."""
    return [SOCIAL_CAPTURE, social_search_tool(platforms)] if platforms else []


READ_PAGE = _tool(
    "read_page",
    "Read a web page (search hit, profile, article, registry page): its text as markdown, "
    "emails, social links and structured data. Use it to check what a search snippet "
    "suggests before relying on it.",
    {"url": {"type": "string"},
     "entity_id": {"type": "string", "description": "entity the page is about, if any"}},
    ["url"])


READ_MATERIAL = _tool(
    "read_material",
    "Read analyst-supplied material (an uploaded file, a pasted note or a link the analyst "
    "added), converted to markdown, in chunks. Not a lookup: it costs no budget. Cite the "
    "material's finding id. Read what the goal needs; the brief lists each item's size, "
    "headings and identifiers so you can pick.",
    {"material_id": {"type": "string", "description": "m_... from the brief"},
     "offset": {"type": "integer", "minimum": 0, "description": "character offset to start at"},
     "max_chars": {"type": "integer", "minimum": 500, "maximum": MATERIAL_CHUNK}},
    ["material_id"])


# Workstreams of a deep analysis: which tools each worker gets, and its brief.
WORKERS: dict[str, dict] = {
    "infrastructure": {
        "tools": ["dns", "rdap", "crtsh", "subfinder", "urlscan", "otx_domain", "otx_ip",
                  "virustotal_domain", "virustotal_ip", "wayback", "dnstwist", "http_probe"],
        "focus": "the target's domains and hosting: registration, DNS, subdomains, "
                 "hosting history, look-alike domains, archived files"},
    "web_presence": {
        "tools": ["dork_search", "read_page", "gravatar", "github_user", "wayback"],
        "focus": "where the target is named or described on the public web: official "
                 "pages, press, profiles; read the pages, don't rely on snippets"},
    "people_identity": {
        "tools": ["hunter", "tomba", "holehe", "maigret", "github_user", "gravatar",
                  "dork_search", "read_page", "social_capture", "social_search"],
        "focus": "the people and accounts connected to the target: staff and roles, "
                 "email pattern, accounts tied to the target's emails and usernames"},
    "company_registry": {
        "tools": ["anaf", "vies", "gleif", "companies_house", "dork_search", "read_page"],
        "focus": "who the company legally is: registry records, identifiers, addresses, "
                 "officers, owners and parent companies, status"},
    "breach_exposure": {
        "tools": ["hibp", "dehashed"],
        "focus": "which breaches the target's email addresses / phone numbers appear in, "
                 "the data classes exposed, and the leaked PII (phones, addresses, names, "
                 "credentials) per breach"},
    "legal_screening": {
        "tools": ["sanctions", "interpol", "europol", "national_wanted", "read_page"],
        "focus": "sanctions, PEP and wanted-list screening of the person or organisation "
                 "targets; every name-only hit is an unverified possible match"},
    # Synthesis only: reads the social_capture findings (no connectors). Enable the
    # findings-reading tools via reads_findings below.
    "social_profile": {
        "tools": [],
        "reads_findings": True,
        "focus": "the target's tendencies and close circle from their captured public "
                 "social profiles. Read the `social_capture` findings (`list_findings` then "
                 "`get_finding`) for profile fields, post text, links and change history, and "
                 "the `social_interactions` findings for who comments on their posts most "
                 "(likely-close-circle leads, ranked by count). "
                 "Build a picture: hobbies and activities, habits and routines (including "
                 "when they are active), interests and the topics they post about, who and "
                 "what they engage with, places and past history they reveal, language and "
                 "tone, and — where the posts support it — leanings such as politics or "
                 "religion. RULES: cite the finding id behind every item, and quote or point "
                 "to the specific post/line. State only what the captures show as fact; "
                 "anything you read between the lines is an INFERENCE — say so, keep its "
                 "confidence low, and flag anything sensitive (politics, religion, health, "
                 "sexuality, ethnicity) as a sensitive inference that may be wrong, never "
                 "as established fact. If there are no social_capture findings yet, say so "
                 "and stop (profiles must be captured first)"},
}

DISPATCH_WORKER = _tool(
    "dispatch_worker",
    "Run one specialised worker agent on a workstream and get back its summary with "
    "finding ids. Several calls in one turn run in parallel.",
    {"worker": {"type": "string", "enum": sorted(WORKERS)},
     "goal": {"type": "string", "description": "what this worker should establish"},
     "budget": {"type": "integer", "minimum": 2, "maximum": 40,
                "description": "lookups this worker may spend"},
     "materials": {"type": "array", "items": {"type": "string"},
                   "description": "material ids (m_...) this worker may read; put the facts "
                                  "it needs from them in the goal as well"}},
    ["worker", "goal", "budget"])


def worker_tools(investigation: dict, costly: str, worker: str,
                 platforms: list[str] | None = None) -> list[dict]:
    allowed = set(WORKERS[worker]["tools"])
    tools = [t for t in [*connector_tools(investigation, costly), *persona_tools(platforms or [])]
             if t["name"] in allowed or t["name"] in ("get_entity", "graph_overview")]
    # A synthesis worker (e.g. social_profile) reads existing findings instead of
    # running connectors.
    if WORKERS[worker].get("reads_findings"):
        tools += [LIST_FINDINGS, GET_FINDING]
    return tools


def orchestrator_tools() -> list[dict]:
    return [DISPATCH_WORKER, GRAPH_OVERVIEW]


def connector_tools(investigation: dict, costly: str = "all") -> list[dict]:
    tiers = COST_TIERS.get(costly, COST_TIERS["none"])
    tools = []
    for name in sorted(connectors.REGISTRY):
        spec = connectors.REGISTRY[name]
        if name in ("screenshot", "web_read"):
            continue  # screenshot is the report agent's; web_read is read_page below
        if not spec.passive and investigation.get("mode") != "active":
            continue  # the gate would refuse it anyway; don't offer it
        if spec.cost not in tiers:
            continue
        mode = "Passive." if spec.passive else (
            "ACTIVE: contacts the target.")
        if name in LEGAL:
            mode += (" Screening: for the subject and the people and companies around it "
                     "that serve the purpose; name-only hits are unverified.")
        elif spec.seed_only:
            mode += (" For the subject's own domains, addresses and usernames (targets or "
                     "ones you found), not third parties.")
        if spec.cost == "metered":
            mode += f" Uses a small monthly quota ({spec.quota}): once per target at most."
        elif spec.cost == "paid":
            mode += " Spends paid credits: only when the goal needs it."
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
    if "web_read" in connectors.REGISTRY:
        tools.append(READ_PAGE)
    return sorted(tools, key=lambda t: t["name"]) + [GET_ENTITY, GRAPH_OVERVIEW]


def verify_tools(investigation: dict, costly: str, platforms: list[str]) -> list[dict]:
    """Judge entities; corroborate with web search, page reads and the personas."""
    lookups = [t for t in connector_tools(investigation, costly)
               if t["name"] in ("dork_search", "read_page")]
    return sorted([*lookups, *persona_tools(platforms), GET_ENTITY, GET_FINDING,
                   LIST_FINDINGS, REVIEW_QUEUE, SET_IDENTITIES, SET_VERDICTS],
                  key=lambda t: t["name"])


def reviewer_tools() -> list[dict]:
    return [GET_ENTITY, GET_FINDING, LIST_FINDINGS, SUBMIT_REVIEW]


def report_tools() -> list[dict]:
    tools = [CAPTURE_EVIDENCE, GET_ENTITY, GET_FINDING, GRAPH_OVERVIEW, LIST_ENTITIES,
             LIST_FINDINGS, SUBMIT_REPORT]
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
    costly: str = "all"
    runner: Any = None  # the AgentRunner, for dispatch_worker
    social: Any = None  # the SocialHub, for the persona tools
    # Material ids this run may read; None means every material of the investigation.
    materials: set[str] | None = None
    calls: int = 0
    report_id: str | None = None
    report_ok: bool = False
    findings: list[str] = field(default_factory=list)
    verdicts: int = 0
    identities: int = 0
    review: dict | None = None  # what submit_review received

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
            if name in connectors.REGISTRY and name not in ("screenshot", "web_read"):
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

    def _target_keys(self, etype: EntityType) -> set[str]:
        rows = self.db.query(
            "SELECT t.value, e.key FROM targets t LEFT JOIN entities e ON e.id = t.entity_id "
            "WHERE t.investigation_id = ? AND t.type = ?", (self.inv_id, etype.value))
        if etype in (EntityType.PERSON, EntityType.ORGANIZATION):
            return ({normalize_name(r["value"]) for r in rows}
                    | {r["key"] for r in rows if r["key"]})
        return {r["key"] for r in rows if r["key"]}

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
            if spec.accepts == EntityType.PERSON:
                key = normalize_name(value)
                arg = value
            elif spec.accepts == EntityType.ORGANIZATION:
                # CUI / UK company numbers keep their canonical form; names normalize.
                key = canonical_key(EntityType.ORGANIZATION, value)
                arg = value
            else:
                try:
                    key = arg = canonical_key(spec.accepts, value)
                except (DetectError, ValueError, UnicodeError) as e:
                    raise ToolError(f"'{value}' is not a valid {spec.accepts.value}: {e}") from e
            entity_id = self._find_entity(spec.accepts.value, key)
            if entity_id and key not in self._target_keys(spec.accepts):
                verdict = review.latest(self.db, entity_id)
                if verdict and verdict["verdict"] == "rejected":
                    raise ToolError(
                        f"'{value}' was verified as not the subject's ({verdict['reason']}); "
                        "don't spend lookups on it.")
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
        if f["connector"] == mat.CONNECTOR and isinstance(raw, dict):
            text = raw.get("markdown") or ""
            return json.dumps({**{k: f[k] for k in keep}, "material_id": raw.get("material_id"),
                               "name": raw.get("name"), "format": raw.get("format"),
                               "text": text[:MATERIAL_CHUNK]
                               + (" [... use read_material for the rest]"
                                  if len(text) > MATERIAL_CHUNK else "")},
                              default=str, ensure_ascii=False)
        if f["connector"] == "web_read" and isinstance(raw, dict):
            text = raw.get("markdown") or ""
            return json.dumps({**{k: f[k] for k in keep}, "url": raw.get("final_url"),
                               "title": raw.get("title"),
                               "page_metadata": raw.get("page_metadata"),
                               "emails": raw.get("emails"),
                               "socials": raw.get("socials"),
                               "text": text[:12000] + (" [...]" if len(text) > 12000 else "")},
                              default=str, ensure_ascii=False)
        if f["connector"] == "social_interactions" and isinstance(raw, dict):
            return json.dumps({**{k: f[k] for k in keep}, "url": raw.get("url"),
                               "platform": raw.get("platform"),
                               "posts_examined": raw.get("posts_examined"),
                               "login_wall": raw.get("login_wall"),
                               "interactions": (raw.get("interactions") or [])[:60]},
                              default=str, ensure_ascii=False)
        if f["connector"] == "social_search" and isinstance(raw, dict):
            return json.dumps({**{k: f[k] for k in keep}, "platform": raw.get("platform"),
                               "query": raw.get("query"), "login_wall": raw.get("login_wall"),
                               "results": (raw.get("results") or [])[:60]},
                              default=str, ensure_ascii=False)
        if f["connector"] == "social_capture" and isinstance(raw, dict):
            text = raw.get("text") or ""
            return json.dumps({**{k: f[k] for k in keep}, "url": raw.get("final_url"),
                               "platform": raw.get("platform"), "title": raw.get("title"),
                               "logged_in": raw.get("logged_in"),
                               "login_wall": raw.get("login_wall"),
                               "captured_at": raw.get("captured_at"),
                               "profile": raw.get("profile"),
                               "links": (raw.get("links") or [])[:60],
                               "changes": raw.get("changes"),
                               "text": text[:12000] + (" [...]" if len(text) > 12000 else "")},
                              default=str, ensure_ascii=False)
        return _dump({**{k: f[k] for k in keep},
                      "raw_excerpt": json.dumps(raw, default=str, ensure_ascii=False)[:5000]})

    async def _t_list_findings(self, args: dict) -> str:
        sql = ("SELECT id, connector, input, fetched_at, note, error FROM findings "
               "WHERE investigation_id = ?")
        params: list = [self.inv_id]
        if args.get("connector"):
            sql += " AND connector = ?"
            params.append(str(args["connector"]))
        rows = self.db.query(sql + " ORDER BY fetched_at", params)
        out = []
        for f in rows[:300]:
            item = {"id": f["id"], "connector": f["connector"],
                    "looked_up": (f["input"] or {}).get("arg"), "at": f["fetched_at"]}
            if f["note"]:
                item["note"] = f["note"][:200]
            if f["error"]:
                item["error"] = f["error"][:120]
            if f["connector"] in ("web_read", "screenshot", "social_capture"):
                title = self.db.one(
                    "SELECT e.attributes FROM entity_sources es JOIN entities e ON "
                    "e.id = es.entity_id WHERE es.finding_id = ? AND e.type = 'URL'", (f["id"],))
                if title:
                    t = (title["attributes"].get("title") or [{}])[0].get("value")
                    if t:
                        item["title"] = t
            out.append(item)
        return _dump(out)

    # --- evidence & report ------------------------------------------------------

    async def _t_dispatch_worker(self, args: dict) -> str:
        if self.runner is None:
            raise ToolError("dispatch_worker is only available to the orchestrator")
        worker = args.get("worker")
        if worker not in WORKERS:
            raise ToolError(f"worker must be one of {sorted(WORKERS)}")
        goal = _str(args, "goal", max_len=2000)
        try:
            wanted = int(args.get("budget") or 0)
        except (TypeError, ValueError) as e:
            raise ToolError("budget must be an integer") from e
        remaining = self.budget - self.calls
        if remaining < 2:
            raise ToolError("The lookup budget is spent. Write your synthesis now.")
        budget = max(2, min(wanted, remaining, 40))
        handed = args.get("materials") or []
        if not isinstance(handed, list) or not all(isinstance(m, str) for m in handed):
            raise ToolError("materials must be a list of material ids")
        known = {r["id"] for r in self.db.query(
            "SELECT id FROM materials WHERE investigation_id = ?", (self.inv_id,))}
        if unknown := [m for m in handed if m not in known]:
            raise ToolError(f"not materials of this investigation: {', '.join(unknown[:5])}")
        self.calls += budget
        child = self.runner.new_run(self.investigation, "worker", f"[{worker}] {goal}",
                                    self.user, budget)
        stats = await self.runner.run(child, self.investigation, "worker", goal, self.user,
                                      budget, self.costly, worker=worker, parent=self.run_id,
                                      materials=sorted(set(handed)))
        # Give back what the worker didn't spend.
        self.calls -= max(0, budget - int(stats.get("tool_calls", 0)))
        row = self.db.get("agent_runs", child)
        return _dump({"worker": worker, "run_id": child, "status": row["status"],
                      "lookups": stats.get("tool_calls"), "findings": stats.get("findings"),
                      "summary": (row["summary"] or "")[:4000]})

    async def _t_read_material(self, args: dict) -> str:
        mid = _str(args, "material_id", max_len=40)
        row = self.db.get("materials", mid)
        if row is None or row["investigation_id"] != self.inv_id:
            raise ToolError(f"no material {mid} in this investigation")
        if self.materials is not None and mid not in self.materials:
            raise ToolError(f"{mid} was not handed to this worker; ask in your summary if "
                            "you need it")
        try:
            offset = max(0, int(args.get("offset") or 0))
            size = max(500, min(int(args.get("max_chars") or MATERIAL_CHUNK), MATERIAL_CHUNK))
        except (TypeError, ValueError) as e:
            raise ToolError("offset and max_chars must be integers") from e
        text = mat.text_of(self.ledger, row)
        chunk = text[offset:offset + size]
        end = offset + len(chunk)
        return json.dumps({"material_id": mid, "finding_id": row["finding_id"],
                           "name": row["name"], "kind": row["kind"], "format": row["format"],
                           "total_chars": len(text), "offset": offset,
                           "next_offset": end if end < len(text) else None,
                           **({"note": row["note"]} if row["note"] and row["kind"] != "link"
                              else {}),
                           "text": chunk}, ensure_ascii=False)

    async def _login_only(self, url: str, about: str | None) -> str:
        """LinkedIn and other login-walled sites: a logged-in research persona, or no."""
        ready = self.social.platforms_ready() if self.social is not None else []
        if login_only(url) not in ready:
            raise ToolError(LOGIN_ONLY_MESSAGE.format(platform=login_only(url).title()))
        return await self._t_social_capture({"url": url, **({"entity_id": about}
                                                              if about else {})})

    async def _t_read_page(self, args: dict) -> str:
        url = _str(args, "url", max_len=2000)
        about = _str(args, "entity_id", required=False, max_len=40)
        if about:
            row = self.db.get("entities", about)
            about = about if row and row["investigation_id"] == self.inv_id else None
        if login_only(url):
            return await self._login_only(url, about)
        self._spend()
        outcome = await read_page(self.ledger, self.investigation, url, user=self.user,
                                  run_by=self.run_id, entity_id=about)
        if outcome.refused:
            raise ToolError(f"Refused by the platform: {outcome.error}")
        if outcome.error:
            raise ToolError(f"Read failed (finding {outcome.finding_id}): {outcome.error}")
        self.findings.append(outcome.finding_id)
        raw = outcome.result.raw
        text = raw.get("markdown") or ""
        return _dump({"finding_id": outcome.finding_id, "url": raw.get("final_url"),
                      "title": raw.get("title"), "page_metadata": raw.get("page_metadata"),
                      "emails": raw.get("emails"),
                      "socials": raw.get("socials"),
                      "structured_data": raw.get("structured_data"),
                      "text": text[:6000] + (" [...]" if len(text) > 6000 else "")})

    async def _t_capture_evidence(self, args: dict) -> str:
        url = _str(args, "url", max_len=2000)
        about = _str(args, "entity_id", required=False, max_len=40)
        if about:
            row = self.db.get("entities", about)
            about = about if row and row["investigation_id"] == self.inv_id else None
        if login_only(url):
            # The persona capture is the evidence: it stores a screenshot too.
            return await self._login_only(url, about)
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

    # --- verification -------------------------------------------------------------

    async def _t_review_queue(self, args: dict) -> str:
        offset = max(0, int(args.get("offset") or 0))
        limit = max(1, min(int(args.get("limit") or 40), 60))
        return _dump(review.queue(self.db, self.inv_id, offset=offset, limit=limit))

    async def _t_set_verdicts(self, args: dict) -> str:
        items = args.get("verdicts")
        if not isinstance(items, list) or not items:
            raise ToolError("verdicts must be a non-empty list")
        done, errors = Counter(), []
        for item in items[:60]:
            if not isinstance(item, dict):
                errors.append("each verdict must be an object")
                continue
            eid = str(item.get("entity_id") or "")
            row = self.db.get("entities", eid)
            if row is None or row["investigation_id"] != self.inv_id:
                errors.append(f"{eid}: not an entity of this investigation")
                continue
            try:
                review.record(self.db, self.ledger.resolver, self.ledger.bus, eid,
                              str(item.get("verdict")), str(item.get("reason") or ""),
                              reviewer=self.run_id, user=self.user, by_agent=True)
            except review.ReviewError as e:
                errors.append(str(e))
                continue
            done[str(item.get("verdict"))] += 1
            self.verdicts += 1
        left = review.queue(self.db, self.inv_id, limit=0)["total_pending"]
        return _dump({"recorded": dict(done), "errors": errors, "still_pending": left})

    async def _t_set_identities(self, args: dict) -> str:
        items = args.get("identities")
        if not isinstance(items, list):
            raise ToolError("identities must be a list")
        clean = [i for i in items if isinstance(i, dict)]
        n = identity.save_clusters(self.db, self.inv_id, clean, self.run_id)
        self.identities = n
        self.ledger.bus.publish(self.inv_id, "identity.updated", {"count": n})
        dropped = len(items) - n
        return _dump({"recorded": n, **({"dropped": f"{dropped} without a label or any "
                                                    "entity of this investigation"}
                                       if dropped else {})})

    # --- personas -------------------------------------------------------------------

    def _persona_outcome(self, outcome: RunOutcome) -> str:
        if outcome.refused:
            raise ToolError(f"Refused by the platform: {outcome.error}")
        if outcome.error:
            raise ToolError(f"Persona job failed: {outcome.error}")
        if not outcome.finding_id:
            raise ToolError("The persona job returned nothing")
        self.findings.append(outcome.finding_id)
        return outcome.finding_id

    async def _t_social_capture(self, args: dict) -> str:
        if self.social is None or not self.social.platforms_ready():
            raise ToolError("No research persona is available (Kali worker offline or no "
                            "logged-in persona)")
        url = _str(args, "url", max_len=2000)
        about = _str(args, "entity_id", required=False, max_len=40)
        if about:
            row = self.db.get("entities", about)
            about = about if row and row["investigation_id"] == self.inv_id else None
        self._spend()
        outcome = await self.social.capture(self.investigation, url, user=self.user,
                                            run_by=self.run_id, entity_id=about)
        fid = self._persona_outcome(outcome)
        return await self._t_get_finding({"finding_id": fid})

    async def _t_social_search(self, args: dict) -> str:
        platforms = self.social.platforms_ready() if self.social is not None else []
        platform = _str(args, "platform", max_len=40)
        if platform not in platforms:
            raise ToolError(f"No logged-in persona for {platform}; available: "
                            f"{', '.join(platforms) or 'none'}")
        query = _str(args, "query", max_len=200)
        self._spend()
        try:
            job = self.social.search(self.investigation, platform, query, user=self.user,
                                     run_by=self.run_id)
        except SocialError as e:
            raise ToolError(str(e)) from e
        fid = self._persona_outcome(await self.social.wait(job["id"]))
        return await self._t_get_finding({"finding_id": fid})

    async def _t_submit_review(self, args: dict) -> str:
        problems = args.get("problems")
        if not isinstance(problems, list):
            raise ToolError("problems must be a list (empty when the report is fine)")
        self.review = {"problems": [p for p in problems if isinstance(p, dict)][:80],
                       "assessment": str(args.get("assessment") or "")[:2000]}
        return _dump({"recorded": len(self.review["problems"])})

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
