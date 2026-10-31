"""The models the analyst can assign to each agent task, and how a model id
becomes a running :class:`ModelClient`.

Two providers:

* **Anthropic** — ids begin with ``claude-``; served through the Anthropic SDK
  (needs ``ANTHROPIC_API_KEY``). A built-in shortlist is always offered.
* **Venice** — any other id; served through :class:`VeniceModel` over Venice's
  OpenAI-compatible API (needs ``VENICE_INFERENCE_KEY``). The live catalogue is
  fetched from Venice and merged in, so new Venice models appear on their own.

The Models settings page (``/api/v1/models``) stores one assignment per task in
``app_settings``; :func:`Platform` reads them to pick the model for each run.
Nothing here switches models on a refusal — a declined turn still ends the run.
"""

from __future__ import annotations

from typing import Any

from .model import AnthropicModel, DEFAULT_VENICE_URL, ModelClient, VeniceModel

# The agent roles a model can be assigned to (docs/02-architecture.md, "Models").
# Each run's `kind` maps to one of these task keys.
TASKS: list[dict[str, str]] = [
    {"key": "collection", "label": "Collection",
     "detail": "Gathers the public footprint from connectors and the graph."},
    {"key": "deep", "label": "Deep analysis (orchestrator)",
     "detail": "Plans the investigation and dispatches the worker agents."},
    {"key": "worker", "label": "Deep-analysis workers",
     "detail": "Parallel specialists; high call volume, so a cheaper model fits."},
    {"key": "verify", "label": "Verification",
     "detail": "Separates the subject from namesakes before the report."},
    {"key": "report", "label": "Report writer",
     "detail": "Writes the report from the verified dossier."},
    {"key": "review", "label": "Report fact-check",
     "detail": "Independent reviewer that checks the written report."},
]
TASK_KEYS = [t["key"] for t in TASKS]

# Anthropic shortlist — always selectable when ANTHROPIC_API_KEY is set.
ANTHROPIC_MODELS: list[dict[str, Any]] = [
    {"id": "claude-opus-5-5", "label": "Claude Opus 5.5",
     "note": "Most capable; the default for reasoning-heavy tasks."},
    {"id": "claude-sonnet-5-5", "label": "Claude Sonnet 5.5",
     "note": "Faster and cheaper; good for high-volume workers."},
    {"id": "claude-haiku-5-5", "label": "Claude Haiku 5.5",
     "note": "Cheapest and fastest."},
]


def is_anthropic(model_id: str) -> bool:
    return model_id.startswith("claude-")


def anthropic_catalog() -> list[dict[str, Any]]:
    return [{**m, "provider": "anthropic", "tools": True, "available": True}
            for m in ANTHROPIC_MODELS]


def venice_model_entry(m: dict[str, Any]) -> dict[str, Any]:
    """One Venice ``/models`` row -> a catalogue entry for the picker."""
    spec = m.get("model_spec") or {}
    caps = spec.get("capabilities") or {}
    return {
        "id": m.get("id", ""),
        "label": spec.get("name") or m.get("id", ""),
        "provider": "venice",
        "note": spec.get("description") or "",
        "tools": bool(caps.get("supportsFunctionCalling")),
        "reasoning": bool(caps.get("supportsReasoning")),
        "uncensored": bool(spec.get("uncensored")),
        "context": m.get("context_length"),
        "available": True,
    }


async def fetch_venice_models(api_key: str, base_url: str = DEFAULT_VENICE_URL,
                              client: Any = None) -> list[dict[str, Any]]:
    """Live Venice text-model catalogue. Tool-calling models first; a model that
    can't call tools can't drive an agent, so the picker marks it disabled."""
    import httpx

    owns = client is None
    client = client or httpx.AsyncClient(base_url=base_url, timeout=httpx.Timeout(30.0),
                                         headers={"Authorization": f"Bearer {api_key}"})
    try:
        resp = await client.get("/models", params={"type": "text"})
        resp.raise_for_status()
        data = resp.json().get("data") or []
    finally:
        if owns:
            await client.aclose()
    entries = [venice_model_entry(m) for m in data if m.get("id")]
    entries.sort(key=lambda e: (not e["tools"], e["label"].lower()))
    return entries


def build_model(model_id: str, *, progress_updates: bool, venice_api_key: str | None,
                venice_base_url: str = DEFAULT_VENICE_URL) -> ModelClient:
    """Construct the model client for an assignment id, routing by provider."""
    if is_anthropic(model_id):
        return AnthropicModel(model_id, progress_updates)
    if not venice_api_key:
        raise RuntimeError(f"model {model_id!r} needs VENICE_INFERENCE_KEY on the server.")
    return VeniceModel(model_id, api_key=venice_api_key, base_url=venice_base_url)
