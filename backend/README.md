# OSINT backend — app + MCP server

The FastAPI app (investigations, quick scan, graph, reports) and the OSINT MCP
server with its connectors. See [`../docs`](../docs) for the design; this
implements the *FastAPI app* and *OSINT MCP server* boxes in
[architecture](../docs/02-architecture.md).

## Layout

```text
backend/
├── app/                 # FastAPI app (docs/07-mvp-plan.md layout)
│   ├── main.py          # app factory; `uvicorn app.main:app`
│   ├── api/             # REST routers + SSE stream (/api/v1)
│   ├── models/          # SQLite schema, request schemas
│   ├── ledger.py        # gate → connector → finding + raw file + audit → resolver
│   ├── guard.py         # per-investigation scope gate for active connectors
│   ├── resolver/        # dedupe, provenance, confidence, legal-hit candidates
│   ├── scan/            # quick scan: pivot map, loop detection, caps, off-target rule
│   ├── report/          # claim model, lint, internal/client render, graph draft
│   ├── agent/           # Claude collection + report agents (tool-use loop, tools, prompts)
│   ├── evidence.py      # screenshot capture with the target-host rule
│   ├── retention.py     # expiry + purge (audit kept)
│   └── detect.py        # seed type auto-detection
├── connectors/          # one module per source; shared by the server and the app
│   ├── base.py          # the ConnectorResult / EntityCandidate contract
│   ├── _http.py         # shared async HTTP client + retry helper
│   ├── dns_lookup.py    rdap.py    crtsh.py    http_probe.py
│   ├── gravatar.py      github_user.py         dork_search.py
│   └── __init__.py      # REGISTRY: connector metadata (accepts / passive / key)
├── mcp_server.py        # MCPServer exposing each connector as a tool
├── scripts/
│   ├── smoke.py             # live check against real sources
│   └── mcp_client_check.py  # connect over MCP and list/call tools
└── tests/               # offline unit tests (HTTP mocked)
```

## Setup

Needs Python 3.12 (managed by [uv](https://docs.astral.sh/uv/)).

```bash
uv sync --extra evidence     # create venv, install deps (+ Playwright for screenshots)
uv run playwright install chromium
cp .env.example .env         # optional: add API keys
```

## Run

```bash
uv run uvicorn app.main:app --reload     # API on 127.0.0.1:8000, docs at /docs
uv run python mcp_server.py              # streamable HTTP on 127.0.0.1:8787/mcp
uv run mcp dev mcp_server.py             # MCP Inspector UI
```

Verify it end to end (server must be running on the matching port):

```bash
MCP_PORT=8799 uv run python mcp_server.py &        # terminal 1
MCP_PORT=8799 PYTHONPATH=. uv run python scripts/mcp_client_check.py   # terminal 2
```

App data (SQLite + evidence files) goes to `backend/data/` (git-ignored);
override with `OSINT_DATA_DIR`. Other knobs: `OSINT_RETENTION_DAYS` (90),
`OSINT_MAX_CALLS_PER_SCAN` (60), `OSINT_MAX_PIVOTS_PER_TYPE` (10),
`OSINT_HTTP_PROXY` / `OSINT_EGRESS_LABEL` (collection egress),
`ANTHROPIC_API_KEY`, `OSINT_AGENT_MODEL` (`claude-opus-5-5`),
`OSINT_AGENT_PROGRESS_UPDATES` (1). If `../frontend/dist` exists it is served
at `/`.

Try it:

```bash
B=http://127.0.0.1:8000/api/v1
INV=$(curl -s -X POST $B/investigations -H 'content-type: application/json'   -d '{"name":"Demo","purpose":"Authorized external footprint review","legal_basis":"legitimate_interest"}'   | jq -r .id)
curl -s -X POST $B/investigations/$INV/targets -H 'content-type: application/json' -d '{"value":"example.com"}'
curl -N $B/investigations/$INV/stream                     # live events (Ctrl-C to stop)
curl -s "$B/investigations/$INV/graph?min_confidence=0.5" | jq '.nodes | length'
RID=$(curl -s -X POST $B/investigations/$INV/reports -H 'content-type: application/json' -d '{}' | jq -r .id)
curl -s "$B/reports/$RID/render?audience=client"          # Markdown; &format=html for HTML
```

## Claude agents

`POST /investigations/{id}/collect` and `/report-agent` start the agents
(`app/agent/`). The backend runs the tool-use loop itself (streamed, adaptive
thinking, `fallbacks: "default"` for classifier declines, prompt-cached
prefix), so every tool call goes through the ledger and is streamed to the UI
as `agent.*` events.

- **Collection agent** (`effort: medium`): the connectors as tools, plus
  `graph_overview` / `get_entity`. Screening tools only run on the
  investigation's own targets; a tool budget caps lookups.
- **Report agent** (`effort: high`): read-only graph tools,
  `capture_evidence`, and `submit_report`, which lints the draft and returns
  the issues; the agent resubmits until it is clean. The run fails if no clean
  report was submitted.
- Without `ANTHROPIC_API_KEY` the endpoints return 503; quick scan and the
  graph draft report still work.

## How the app keeps collection honest

- **Every call goes through the ledger** (`app/ledger.py`): finding row, raw
  JSON file with sha256, audit row, live events — including refused calls.
- **Active connectors** (`passive=False`) run only when the investigation is
  `active`, has a recorded authorization, and the host is in *its own* scope.
  The quick scan doesn't even offer them otherwise.
- **Off-target pivots**: discovered domains/emails outside the target's own
  domains (NS, MX, SOA hostmaster…) are recorded but not followed, and the
  report lists them as related third-party assets.
- **People are only screened as seeds**: the scan never runs search or legal
  checks on a person it discovered; an explicit, audited pivot can.
- **Legal hits are candidates**: never merged into the subject; linked by a
  `possible_match` edge until an analyst confirms with a disambiguator.
- **Reports are claims with evidence ids**; lint blocks uncited claims, legal
  hits stated as fact and credential-looking text; `client` render hides
  workspace detail. See [docs/08](../docs/08-field-lessons.md).

## Test

```bash
uv run pytest                                   # offline, no network
PYTHONPATH=. uv run python scripts/smoke.py github.com   # live
```

## Tools / connectors

| MCP tool | Connector | Input | Passive? | Key |
|---|---|---|---|---|
| `dns_records` | `dns` | domain | yes | — |
| `rdap_registration` | `rdap` | domain | yes | — |
| `crtsh_subdomains_tool` | `crtsh` | domain | yes | — |
| `http_probe_site` | `http_probe` | domain/URL | **no (active)** | — |
| `gravatar_profile` | `gravatar` | email | yes | — |
| `github_profile` | `github_user` | username | yes | `GITHUB_TOKEN` (optional) |
| `web_search` | `dork_search` | query | yes | optional per engine |
| `interpol_wanted` | `interpol` | person name | yes | — |
| `eu_most_wanted` | `europol` | person name | yes | — |
| `sanctions_screen` | `sanctions` | name | yes | `OPENSANCTIONS_API_KEY` |
| `national_wanted_lists` | `national_wanted` | person name | yes | — |
| — (app only) | `screenshot` | URL | fetches the URL's host | Playwright + Chromium |

`http_probe_site` is only registered when `MCP_ALLOW_ACTIVE=1`: the MCP server
has no investigation scope to check, so the deployed `/mcp` endpoint is
passive-only.

## Notes

- **Active vs passive.** `http_probe` is the only active connector (it fetches
  the target's own site). The server provides it; the backend decides whether
  to offer it to an agent based on investigation mode.
- **`dork_search`.** Uses DuckDuckGo's lite endpoint with no key. DDG throttles
  bursts from one IP; when that happens the result reports an error rather than
  a silent empty. For reliable volume, add a keyed engine (Bing/Brave/SerpAPI)
  behind `ENGINES` in `dork_search.py` and/or route through proxies.
- **Legal / law-enforcement connectors** (`interpol`, `europol`, `sanctions`)
  are **name-based**, so every hit is an *unverified possible match*, never a
  confirmation: Person candidates carry `match_quality` 0.3, `unverified: True`,
  disambiguators (DOB, nationality), and an official-listing URL for the report
  agent to screenshot. Shared logic lives in `connectors/_legal.py`. See
  docs/04-sources.md#legal--law-enforcement.
  - `europol` (EU Most Wanted / ENFAST) works with no key.
  - `interpol` uses INTERPOL's public Notices API; its edge may return 403 to
    server-side clients — the connector returns a note instead of failing, and
    works from an allowed host or via proxy. We do not spoof browser identity.
  - `sanctions` (OpenSanctions) needs `OPENSANCTIONS_API_KEY`, or point
    `OPENSANCTIONS_API_URL` at a self-hosted yente; it skips cleanly otherwise.
  - `national_wanted` dispatches to per-country adapters (`SOURCES` in
    `national_wanted.py`):
    - **Romania** (Poliția Română, *persoane urmărite*) — **live & verified**:
      paginates the public wanted list, filters by name, and captures the
      birth-date code from each detail-page slug as a disambiguator. The site
      omits its Sectigo intermediate CA, so we ship that public intermediate
      (`connectors/certs/`) and verify the full chain via `_tls.py` — we do
      **not** disable TLS verification.
    - **FBI (US)** — live & verified (clean public JSON API).
    - **Belgium**, **Netherlands** — wired but WAF-block server-side clients
      (403); their adapters return an "unavailable" note (the run continues)
      and need a reachable host/proxy. We do not spoof browser identity.
- **Adding a connector.** Write an async `fn(seed) -> ConnectorResult` in
  `connectors/`, register it in `__init__.py`, and add a thin `@mcp.tool()`
  wrapper in `mcp_server.py`.
