# 5. API (draft)

Base path: `/api/v1`. JSON everywhere. Auth: `Authorization: Bearer <jwt>` (target);
the MVP sits behind Caddy basic auth and reads the analyst from `X-Analyst`.

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
  "legal_basis": "legitimate_interest",  // GDPR Art. 6 basis
  "mode": "passive",                // passive | active
  "scope": [],                      // active mode: hosts/domains/CIDRs authorized for active connectors
  "authorization": null,            // active mode: who authorized it, when, reference document
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
POST   /investigations/{id}/targets    add seed + start a quick scan
POST   /investigations/{id}/collect    start the collection agent (gathers)
POST   /investigations/{id}/report     start the report agent (curate + evidence)
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
```

```json
// POST /investigations/{id}/collect
{ "target": "example.com", "goal": "Map the attack surface and who is behind it", "max_tool_calls": 40 }

// 202 Accepted
{ "run_id": "ar_1", "kind": "collection" }
```

```json
// POST /investigations/{id}/report
// The report agent reads the existing graph; it runs no collection tools.
{ "goal": "Map the attack surface and who is behind it", "capture_evidence": true }

// 202 Accepted
{ "run_id": "ar_2", "kind": "report" }
```

```json
// POST /entities/{eid}/pivot
{ "connectors": ["hibp", "gravatar"], "depth": 1 }
```

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
event: agent.text       data: {"delta": "Found 3 subdomains, checking who registered..."}
event: agent.finished   data: {"run_id": "ar_1", "summary": "..."}
event: agent.refused    data: {"run_id": "ar_1", "category": "..."}
```

## Reports

```text
POST   /investigations/{id}/reports    { body? } — omit body to draft from the graph
GET    /investigations/{id}/reports    list
GET    /reports/{rid}                  report body (claims + evidence ids)
PUT    /reports/{rid}                  { body } → returns lint
GET    /reports/{rid}/lint?audience=internal|client
GET    /reports/{rid}/render?audience=internal|client&format=md|html&cite=true
                                       409 on lint errors unless &force=true (audited)
```

A report body is a list of sections of **claims**; each claim lists the finding
ids that support it and a status (`verified | unverified | leaked |
inference`). See `backend/app/report/__init__.py` and
[Lessons from field casework](08-field-lessons.md).

## Admin

```text
GET    /connectors                     list, health, which keys are configured
GET    /audit?investigation_id=&user=&from=&to=   audit log (append-only)
POST   /admin/purge-expired            purge investigations past retention (not on legal hold)
```
