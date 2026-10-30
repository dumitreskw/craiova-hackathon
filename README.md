# Team Cybnetic @ Craiova Hackathon

Scope: CyberSecurity OSINT Platform

A platform that takes a **target** (a person, a company, or a website), queries
many open sources, and builds a correlated, source-attributed intelligence
graph by pivoting recursively on everything it finds.

## Documentation

1. [Overview](docs/01-overview.md): problem, target types, core concepts, capabilities
2. [Architecture](docs/02-architecture.md): single-VPS setup, OSINT MCP server, Claude agent, data flow
3. [Data Model](docs/03-data-model.md): entities, relationships, provenance, confidence, resolution
4. [Sources & Connectors](docs/04-sources.md): source catalog per target type, pivot map, priorities
5. [API](docs/05-api.md): REST + SSE draft
6. [Legal, Ethics & Safety](docs/06-legal-ethics.md): GDPR, passive-first, guardrails
7. [Hackathon MVP Plan](docs/07-mvp-plan.md): scope, team split, timeline, risks
8. [Lessons from Field Casework](docs/08-field-lessons.md): what went wrong in real OSINT reports and how the platform prevents it
