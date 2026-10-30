# OSINT frontend

Analyst UI for the platform: investigations, live graph, entity provenance,
runs, Claude agents, reports and the audit log. React + Vite + TypeScript +
Tailwind + Cytoscape.js. Talks to the backend at `/api/v1` (docs/05-api.md).

## Run

```bash
npm install
npm run dev        # http://localhost:5173, proxies /api to http://127.0.0.1:8000
API_TARGET=http://127.0.0.1:8011 npm run dev   # point the proxy elsewhere
npm run build      # type-checks, then writes dist/ (served by the backend at /)
```

Start the backend first: `cd ../backend && uv run uvicorn app.main:app --reload`.

## Layout

```text
src/
├── api.ts            fetch wrapper (adds X-Analyst), SSE-over-fetch reader, downloads
├── stream.ts         one live event stream per open investigation
├── types.ts          response shapes from the backend
├── entityStyle.ts    entity-type colors (red/yellow are reserved for verification state)
└── components/
    ├── Workspace.tsx        header, target bar, tabs, side panel
    ├── GraphView.tsx        Cytoscape graph: live updates, filters, legend
    ├── EntityPanel.tsx      attributes, sources, links, confirm/reject/pivot/note
    ├── ActivityFeed.tsx     live lookups and graph changes
    ├── AgentsPanel.tsx      Claude collection / report runs and transcripts
    ├── RunsView.tsx         scans and agent runs
    ├── ReportsView.tsx      claims, lint, internal/client preview, export
    └── AuditView.tsx        append-only audit log
```

The analyst name typed in the sidebar is sent as `X-Analyst` on every request
and recorded in the audit log (behind Caddy, the proxy sets it instead).

## Graph conventions

- Node color: entity type. Node size: confidence (targets are drawn largest, with a double ring).
- Dashed red node or line: an unverified name match (e.g. a sanctions or wanted
  listing that only shares a name). Confirm it only with a disambiguator.
- Line thickness: how many independent sources support the link.
- Certificates are hidden by default; click a type in the legend to toggle it.
