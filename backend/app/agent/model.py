"""The Claude call behind the agents: one streamed turn of the tool-use loop.

Kept behind a tiny interface (:class:`ModelClient`) so the loop can be tested
with a scripted fake, and so the request shape lives in one place:

* streaming (long turns), adaptive thinking with ``effort`` as the depth knob;
* ``display: "updates"`` turns the model's between-tool progress notes into
  short thinking summaries we forward to the UI (beta, switchable off);
* ``fallbacks: "default"``: if a safety classifier declines, the API re-runs
  the request on the recommended fallback model instead of returning a refusal;
* automatic prompt caching of the stable prefix (tools + system).
"""

from __future__ import annotations

import json
import os
from types import SimpleNamespace
from typing import Any, Callable, Protocol

FALLBACK_BETA = "server-side-fallback-2026-07-01"
UPDATES_BETA = "thinking-display-updates-2026-08-18"
MAX_TOKENS = 64_000

# Venice AI (https://venice.ai): OpenAI-compatible chat completions. The analyst
# can assign a Venice model to any agent task from the Models settings page; it
# is a deliberate per-task provider choice, not an automatic refusal fallback.
DEFAULT_VENICE_URL = "https://api.venice.ai/api/v1"

OnDelta = Callable[[str, str], None]  # (kind: "text" | "update", chunk)


class ModelClient(Protocol):
    async def turn(self, *, system: str, tools: list[dict], messages: list[dict],
                   effort: str, on_delta: OnDelta) -> Any:
        """Run one streamed request; return the final message object."""


def configured() -> bool:
    """Whether Claude credentials are available to the SDK."""
    return any(os.environ.get(k) for k in
               ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"))


def venice_configured() -> bool:
    """Whether a Venice API key is on the server (enables Venice models)."""
    return bool(os.environ.get("VENICE_INFERENCE_KEY"))


class AnthropicModel:
    def __init__(self, model: str, progress_updates: bool = True, client: Any = None):
        import anthropic

        self.client = client or anthropic.AsyncAnthropic()
        self.model = model
        self.progress_updates = progress_updates

    async def turn(self, *, system: str, tools: list[dict], messages: list[dict],
                   effort: str, on_delta: OnDelta) -> Any:
        betas = [FALLBACK_BETA]
        thinking: dict[str, Any] = {"type": "adaptive"}
        if self.progress_updates:
            betas.append(UPDATES_BETA)
            thinking["display"] = "updates"
        async with self.client.beta.messages.stream(
            model=self.model,
            max_tokens=MAX_TOKENS,
            system=system,
            tools=tools,
            messages=messages,
            thinking=thinking,
            output_config={"effort": effort},
            betas=betas,
            fallbacks="default",
            cache_control={"type": "ephemeral"},
        ) as stream:
            async for event in stream:
                if event.type != "content_block_delta":
                    continue
                delta = event.delta
                if delta.type == "text_delta":
                    on_delta("text", delta.text)
                elif delta.type == "thinking_delta" and delta.thinking:
                    on_delta("update", delta.thinking)
            return await stream.get_final_message()


class VeniceModel:
    """An agent model served by Venice AI (OpenAI-compatible chat completions).

    Speaks the :class:`ModelClient` interface but converts requests and
    responses to the OpenAI shape: ``effort`` is ignored (passed through only
    as a hint), tool calls are assembled from streamed deltas, and finish
    reasons map back onto Anthropic stop reasons (``content_filter`` ->
    ``refusal``). Unparseable tool-argument JSON raises ``ValueError``, which
    the agent loop already catches and retries.
    """

    def __init__(self, model: str, api_key: str | None = None,
                 base_url: str = DEFAULT_VENICE_URL, client: Any = None):
        import httpx

        key = api_key or os.environ.get("VENICE_INFERENCE_KEY")
        if not key and client is None:
            raise RuntimeError("A Venice model needs VENICE_INFERENCE_KEY on the server.")
        self.client = client or httpx.AsyncClient(
            base_url=base_url, timeout=httpx.Timeout(600.0, connect=30.0),
            headers={"Authorization": f"Bearer {key}"})
        self.model = model

    async def turn(self, *, system: str, tools: list[dict], messages: list[dict],
                   effort: str, on_delta: OnDelta) -> Any:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": _openai_messages(system, messages),
            "max_tokens": MAX_TOKENS,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            payload["tools"] = _openai_tools(tools)
        parts: list[str] = []
        calls: dict[int, dict[str, str]] = {}
        finish: str | None = None
        usage: dict[str, Any] = {}
        async with self.client.stream("POST", "/chat/completions", json=payload) as resp:
            if resp.status_code >= 400:
                body = (await resp.aread()).decode("utf-8", "replace")[:500]
                raise RuntimeError(f"Venice {resp.status_code}: {body}")
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    continue
                chunk = json.loads(data)
                usage = chunk.get("usage") or usage
                for choice in chunk.get("choices") or []:
                    delta = choice.get("delta") or {}
                    if delta.get("reasoning_content"):
                        on_delta("update", delta["reasoning_content"])
                    if delta.get("content"):
                        parts.append(delta["content"])
                        on_delta("text", delta["content"])
                    for call in delta.get("tool_calls") or []:
                        slot = calls.setdefault(call.get("index", 0),
                                                {"id": "", "name": "", "arguments": ""})
                        if call.get("id"):
                            slot["id"] += call["id"]
                        fn = call.get("function") or {}
                        if fn.get("name"):
                            slot["name"] += fn["name"]
                        if fn.get("arguments"):
                            slot["arguments"] += fn["arguments"]
                    finish = choice.get("finish_reason") or finish
        blocks: list[Any] = []
        if parts:
            blocks.append(SimpleNamespace(type="text", text="".join(parts)))
        for index in sorted(calls):
            slot = calls[index]
            args = json.loads(slot["arguments"] or "{}")  # ValueError: the loop retries
            blocks.append(SimpleNamespace(type="tool_use", id=slot["id"] or f"call_{index}",
                                          name=slot["name"], input=args))
        stop = "end_turn"
        if finish == "content_filter":
            stop = "refusal"
        elif finish == "length":
            stop = "max_tokens"
        elif finish == "tool_calls" or calls:
            stop = "tool_use"
        return SimpleNamespace(
            content=blocks, stop_reason=stop, stop_details=None,
            usage=SimpleNamespace(input_tokens=usage.get("prompt_tokens", 0) or 0,
                                  output_tokens=usage.get("completion_tokens", 0) or 0,
                                  cache_read_input_tokens=0, cache_creation_input_tokens=0))


def _openai_messages(system: str, messages: list[dict]) -> list[dict]:
    """Anthropic-shaped history (what the loop keeps) -> OpenAI chat messages."""
    out: list[dict] = [{"role": "system", "content": system}] if system else []
    for m in messages:
        content = m["content"]
        if isinstance(content, str):
            out.append({"role": m["role"], "content": content})
        elif m["role"] == "user":  # tool results, one OpenAI "tool" message each
            for b in content:
                if b.get("type") != "tool_result":
                    continue
                text = b["content"] if isinstance(b["content"], str) else "".join(
                    c.get("text", "") for c in b["content"] if isinstance(c, dict))
                out.append({"role": "tool", "tool_call_id": b["tool_use_id"],
                            "content": text})
        else:  # assistant turn: text blocks + tool_use blocks
            texts, calls = [], []
            for b in content:
                if getattr(b, "type", None) == "text":
                    texts.append(b.text)
                elif getattr(b, "type", None) == "tool_use":
                    calls.append({"id": b.id, "type": "function", "function": {
                        "name": b.name, "arguments": json.dumps(b.input)}})
            entry: dict[str, Any] = {"role": "assistant", "content": "".join(texts)}
            if calls:
                entry["tool_calls"] = calls
            out.append(entry)
    return out


def _openai_tools(tools: list[dict]) -> list[dict]:
    return [{"type": "function", "function": {
        "name": t["name"], "description": t.get("description", ""),
        "parameters": t.get("input_schema") or {"type": "object", "properties": {}},
    }} for t in tools]


def echo_content(content: list[Any]) -> list[Any]:
    """Assistant content to append to the history.

    After a server-side fallback, blocks the declined model produced before the
    last ``fallback`` marker (thinking, tool calls) must not be echoed back;
    text and everything after the marker are kept.
    """
    marks = [i for i, b in enumerate(content) if b.type == "fallback"]
    if not marks:
        return list(content)
    last = marks[-1]
    drop = {"thinking", "redacted_thinking", "tool_use", "server_tool_use"}
    return [b for i, b in enumerate(content) if i > last or b.type not in drop]
