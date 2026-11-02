# 5. API (draft)

Base path: `/api/v1`. JSON everywhere. Auth (docs/10-auth.md): the browser's
session cookie, a workspace API key (`Authorization: Bearer osk_…`) or a
Keycloak access token. Every request acts in one workspace; account and
workspace endpoints are `/me`, `/orgs/current/...` and `/platform/orgs`.

## Investigations

```text
POST   /investigations                 create
GET    /investigations                 list (paginated)
GET    /investigations/{id}            details + settings + stats
PATCH  /investigations/{id}            update settings
DELETE /investigations/{id}            delete (soft) + purge evidence job
```

```json
// POST /investigations
{
  "name": "Example SRL due diligence",
  "purpose": "Vendor vetting before contract",
  "mode": "passive",                // passive | active
  "retention_days": 90,
  "settings": {
    "max_depth": 2,
    "connectors": ["*"],            // or explicit allow-list
    "exclude_connectors": ["dehashed"],
    "budget_credits": 500           // planned
  }
}
```

## Targets & runs

```text
POST   /investigations/{id}/targets    add seed + start a quick scan {value, type, profile?}
PUT    /investigations/{id}/targets/{tid}/profile  replace a Person target's subject profile
                                       {profile, scan}; new identifiers become targets
GET    /investigations/{id}/identity   profiles, namesake / list-match counts, common-name
                                       warning, who's-who clusters
POST   /identity-clusters/{cid}/choose the analyst picks the subject {reason}
POST   /investigations/{id}/collect    start the collection agent {goal, target?, max_tool_calls}
POST   /investigations/{id}/report-agent  report pipeline: verify -> write -> fact-check
                                       {goal, max_tool_calls, verify=true, review=true}
POST   /investigations/{id}/verify     verification agent only {goal?, max_tool_calls}
GET    /investigations/{id}/reviews    verdict counts + entities left uncertain for the analyst
POST   /investigations/{id}/deep       deep analysis: orchestrator + parallel workers + the
                                       report pipeline
                                       {goal, target?, max_tool_calls (shared), costly_connectors}
POST   /investigations/{id}/capture    screenshot a URL as evidence now {url, entity_id?}
GET    /findings/{fid}/screenshot      the banner-stamped PNG of a capture
POST   /investigations/{id}/scan       re-run the quick scan {entity_ids?, connectors?, max_depth?}
GET    /investigations/{id}/runs       scans / agent runs, newest first
GET    /agent-runs/{rid}               transcript, summary, token usage
POST   /agent-runs/{rid}/cancel
POST   /entities/{eid}/pivot           run selected connectors on an entity
```

```json
// POST /investigations/{id}/targets
{ "value": "example.com", "type": "auto" }   // auto-detects Domain

// 202 Accepted
{ "target_id": "t_1", "entity_id": "e_dom_1", "connectors_started": 9 }

// A person, with what tells them apart from namesakes (all fields optional)
{ "value": "Ion Popescu", "type": "Person",
  "profile": { "birth_year": 1985, "nationalities": ["Romanian"], "locations": ["Craiova"],
               "employers": ["Example SRL"], "emails": ["ion@example.com"], "usernames": ["ionp"] } }
```

Graph nodes and `GET /entities/{eid}` carry `verdict` / `verdict_reason`: the
latest review (confirmed, rejected, uncertain) or `null`.

```json
// POST /investigations/{id}/collect
{ "target": "example.com", "goal": "Map the attack surface and who is behind it", "max_tool_calls": 40 }

// 202 Accepted
{ "run_id": "ar_1", "kind": "collection" }
```

```json
// POST /investigations/{id}/report-agent
// 1. verify: Claude judges every person, account, mention and domain worth it
//    (is it the subject?), with page reads, searches and the research personas;
//    verdicts go to entity_reviews and set the entity status. An analyst's
//    verdict is final; name matches on wanted/sanctions lists are never
//    confirmed by Claude.
// 2. write: one writer works from a dossier of everything that survived
//    (app/agent/dossier.py), captures evidence and submits through the linter.
// 3. review: an independent reviewer checks each claim against its sources;
//    the writer fixes what it flags (one round). The result is kept in body.qa.
{ "goal": "Map the attack surface and who is behind it", "max_tool_calls": 25 }

// 202 Accepted
{ "run_id": "ar_2", "kind": "report" }
```

```json
// POST /entities/{eid}/pivot
{ "connectors": ["hibp", "gravatar"], "depth": 1 }
```

## Analyst-supplied material

```text
GET    /investigations/{id}/materials        list (with digest: outline, identifiers, preview)
POST   /investigations/{id}/materials        multipart, field "files" (repeatable):
                                             .pdf .docx .md .markdown .txt, 25 MB each
POST   /investigations/{id}/materials/text   {title, text}  a pasted note
POST   /investigations/{id}/materials/link   {url}  read now (403 for the target's own host
                                             unless active + in scope)
GET    /materials/{mid}                      the material + its markdown
GET    /materials/{mid}/file                 the original upload
DELETE /materials/{mid}                      withdraw (409 while a report cites it)
```

```json
// POST /investigations/{id}/materials   (2 files, one unsupported)
// 201 Created; a file that fails is reported, the others are stored.
// Re-uploading the same bytes returns the existing item with duplicate: true.
{ "materials": [{ "id": "m_1", "kind": "file", "name": "dossier.docx", "format": "docx",
                  "chars": 5210, "finding_id": "f_9", "duplicate": false,
                  "digest": { "outline": ["Subject dossier"], "company_identifiers": ["RO12345678"],
                              "emails": ["jane@example.com"], "urls": [], "preview": "# Subject…" } }],
  "errors": [{ "name": "tool.exe", "status": 415, "error": "tool.exe: supported files are …" }] }
```

Agents see these items in their brief and read them with the `read_material`
tool; see [Architecture](02-architecture.md#analyst-supplied-material).

## Graph & entities

```text
GET    /investigations/{id}/graph?min_confidence=0.5&types=Domain,IPAddress
GET    /entities/{eid}                 attributes + sources + edges
GET    /entities/{eid}/evidence        raw findings
POST   /entities/{eid}/confirm         analyst confirms (confidence → 1.0) {reason}
POST   /entities/{eid}/reject          hide + resolver feedback {reason}
GET    /investigations/{id}/findings   ledger of connector calls (?connector=)
GET    /findings/{fid}                 one finding + its raw payload
POST   /entities/merge                 { "ids": ["e_1","e_2"] }
POST   /entities/{eid}/notes
```

Graph response (Cytoscape-compatible):

```json
{
  "nodes": [
    { "data": { "id": "e_dom_1", "type": "Domain", "label": "example.com", "confidence": 1.0 } }
  ],
  "edges": [
    { "data": { "id": "r_1", "source": "e_dom_2", "target": "e_dom_1",
                "type": "subdomain_of", "sources": ["crtsh"], "confidence": 0.9 } }
  ]
}
```

## Live updates

`GET /investigations/{id}/stream` (Server-Sent Events)

```text
event: tool.started     data: {"connector": "crtsh", "input": {"domain": "example.com"}, "by": "agent"}
event: entity.created   data: {"entity": {...}}
event: edge.created     data: {"edge": {...}}
event: tool.finished    data: {"connector": "crtsh", "found": 14}
event: tool.failed      data: {"connector": "hibp", "error": "rate_limited"}
event: agent.started    data: {"run_id": "ar_1", "kind": "collection", "goal": "..."}
event: agent.text       data: {"run_id": "ar_1", "delta": "Found 3 subdomains, checking who registered..."}
event: agent.update     data: {"run_id": "ar_1", "delta": "<progress note between tool calls>"}
event: agent.tool       data: {"run_id": "ar_1", "name": "crtsh", "input": {"value": "example.com"}}
event: agent.tool_result data: {"run_id": "ar_1", "name": "crtsh", "error": false, "content": "..."}
event: agent.finished   data: {"run_id": "ar_1", "kind": "collection", "status": "finished", "summary": "...", "report_id": null}
event: agent.refused    data: {"run_id": "ar_1", "category": "..."}
event: agent.failed     data: {"run_id": "ar_1", "error": "..."}
```

## Reports

```text
POST   /investigations/{id}/reports    { body? } — omit body to draft from the graph
GET    /investigations/{id}/reports    list
GET    /reports/{rid}                  report body (claims + evidence ids)
PUT    /reports/{rid}                  { body } → returns lint
GET    /reports/{rid}/lint?audience=internal|client
GET    /reports/{rid}/render?audience=internal|client&format=md|html|docx|pdf&cite=true
                                       docx: house layout (A4, Arial, header/footer from
                                       OSINT_REPORT_HEADER / OSINT_REPORT_FOOTER, screenshots embedded)
                                       pdf: the HTML render printed by headless Chromium
                                       docx/pdf come as attachments: <title>_<audience>_<date>.<ext>
                                       409 on lint errors unless &force=true (audited)
GET    /reports/{rid}/captures         cited sources with a public page and no screenshot yet
POST   /reports/{rid}/captures         screenshot those pages now; each capture is attached to
                                       its evidence entry (body.captures) and shown in every copy
```

The two copies differ on purpose: the **internal** copy is the working file
(claim ids, every status and severity, and per evidence item the finding id,
hash, payload path, who ran it and which claims cite it, plus the collection
method); the **client** copy keeps the statements, each source with its public
page, what it returned, the retrieval date and the page capture.

A report body is a list of sections of **claims**; each claim lists the finding
ids that support it and a status (`verified | unverified | leaked |
inference`). See `backend/app/report/__init__.py` and
[Lessons from field casework](08-field-lessons.md).

## Admin

```text
GET    /health                         {"ok": true, "build": "<first 8 chars of the commit>"}
GET    /connectors                     list, health, which keys are configured
GET    /audit?investigation_id=&user=&from=&to=   audit log (append-only)
POST   /admin/purge-expired            purge investigations past retention (not on legal hold)
```
