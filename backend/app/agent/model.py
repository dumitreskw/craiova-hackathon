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

import os
from typing import Any, Callable, Protocol

FALLBACK_BETA = "server-side-fallback-2026-07-01"
UPDATES_BETA = "thinking-display-updates-2026-08-18"
MAX_TOKENS = 64_000

OnDelta = Callable[[str, str], None]  # (kind: "text" | "update", chunk)


class ModelClient(Protocol):
    async def turn(self, *, system: str, tools: list[dict], messages: list[dict],
                   effort: str, on_delta: OnDelta) -> Any:
        """Run one streamed request; return the final message object."""


def configured() -> bool:
    """Whether Claude credentials are available to the SDK."""
    return any(os.environ.get(k) for k in
               ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"))


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
