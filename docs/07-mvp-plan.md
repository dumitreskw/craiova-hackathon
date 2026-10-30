# 7. Hackathon MVP Plan

## Demo story

> "We type `example.ro` and in under a minute see its subdomains, IPs and
> hosting, the company behind it with its administrators from the trade
> registry, the public contact emails, which of them appeared in breaches, and
> the GitHub accounts linked to them. All in one graph, every node traceable
> to its source, exportable as a report."

## MVP scope

**Must have**
- [ ] Single investigation workspace, no multi-tenant auth (simple login is enough)
- [x] Input auto-detection: domain, email, username, company CUI (+ IP, phone, person name)
- [ ] Connectors: `dns`, `rdap`, `crtsh`, `http_probe` (emails/socials
      extraction), `anaf` (CUI → company), `gravatar`, `hibp`, `github`,
      `username_enum` (top 50 sites), `dork_search` (multi-engine)
- [x] OSINT MCP server exposing the connectors as tools
- [x] Quick scan: parallel connectors + one pivot round, loop detection
- [x] Collection agent: Claude loop over the connector tools, streamed to the UI
- [x] Report agent: reasons over the graph, dedupes/curates, writes the report
- [x] `screenshot` tool (Playwright) + evidence capture in the report
- [x] Deterministic de-duplication
- [x] Graph view with live updates (SSE)
- [x] Entity panel with sources and raw evidence
- [x] JSON export (graph, findings, report body)

**Nice to have**
- [x] Confidence scoring and filter slider
- [ ] PDF report built from the report agent's output + screenshots
- [ ] Timeline view
- [ ] Shodan / urlscan / VirusTotal
- [ ] Sanctions / PEP check (OpenSanctions)
- [ ] Breach-exposure worker: `hibp` + `dehashed`/`intelx` (metadata only)
- [ ] Legal/enforcement worker: `sanctions` (OpenSanctions), `interpol`,
      `europol` — with name-collision disambiguation
- [ ] Deep analysis: orchestrator fanning out to recon / dorking /
      breach-exposure / legal workers, then analysis + report agents

**Out of scope for the hackathon**
- Fuzzy person resolution, RBAC, STIX export, retention jobs, queues /
  separate workers
  (documented, not built)

## Team split (suggested, 4 people)

| Role         | Responsibilities                                              |
|--------------|---------------------------------------------------------------|
| Backend core | FastAPI, SQLite model, quick scan, both agent loops, SSE      |
| Connectors A | MCP server + infra tools: dns, rdap, crtsh, http_probe, dork_search, screenshot |
| Connectors B | People/company: anaf, hibp, gravatar, github, username_enum   |
| Frontend     | Search, graph (Cytoscape), entity panel, report + evidence view |

## Timeline (24h)

| Hours  | Milestone                                                         |
|--------|-------------------------------------------------------------------|
| 0–2    | Repo skeleton, VPS + docker compose, data model, connector contract |
| 2–8    | First 3 connectors end-to-end + raw graph rendering               |
| 8–14   | MCP server, agent loop, dedupe, remaining Tier-1 connectors       |
| 14–18  | UI polish: entity panel, filters, live stream                     |
| 18–21  | Agent prompt tuning, report export                                |
| 21–24  | Demo target rehearsal, seed cache for offline demo, slides        |

## Risks

| Risk                                | Mitigation                                          |
|-------------------------------------|-----------------------------------------------------|
| Rate limits / API outages during demo | Cache all responses; pre-run the demo target; Quick scan as fallback if Claude is unavailable |
| Graph explosion at depth ≥ 2        | Depth limit, per-connector result caps, type filters |
| Scraping breaks (layout changes)    | Prefer APIs; keep scrapers in Tier 3                |
| Agent refuses or wanders            | Clear purpose in system prompt, tool-call cap, refusal handling in UI |
| Demo on a real person               | Use team members' own data or a fictional/consenting target |

## Proposed repo layout

```text
.
├── docs/
├── backend/
│   ├── app/
│   │   ├── api/            # FastAPI routers + SSE
│   │   ├── models/         # SQLite tables
│   │   ├── scan/           # quick scan (asyncio.gather + pivot)
│   │   ├── agent/          # Claude loop, system prompt, MCP client
│   │   └── resolver/       # normalization, dedupe, scoring
│   ├── connectors/         # one module per source (shared by app and MCP)
│   ├── mcp_server.py       # FastMCP server wrapping connectors
│   └── tests/
├── frontend/
│   └── src/
│       ├── components/     # Graph, EntityPanel, SearchBar, Timeline
│       └── api/
├── Caddyfile
└── docker-compose.yml     # caddy, app, mcp
```
