"""Claude agents: the collection agent and the report agent.

docs/02-architecture.md, modes 2 and 3. The backend runs the tool-use loop
itself, so every tool call passes through our code: it is gated and recorded
by the ledger, streamed to the UI, and capped.

Loop rules (manual loop over a streamed turn):

* ``refusal`` ends the run; the turn's tool calls are never executed (a
  refusal can cut a tool call off mid-input). Server-side fallback has
  usually already retried on another model by the time we see one.
* ``max_tokens`` with a pending tool call ends the run rather than running a
  truncated input.
* ``pause_turn`` is resumed by sending the history back.
* All tool results of a turn go back in one user message.
* The history is append-only (thinking blocks stay valid).
* Budget: a cap on lookups (tool budget) and on turns.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from typing import Any, Callable

from ..detect import DetectError, detect
from ..ledger import Ledger
from ..models import new_id, now
from ..resolver import entity_view
from ..scan import anchor_domains
from . import prompts
from .model import ModelClient, echo_content
from .tools import ToolBox, connector_tools, report_tools

EXTRA_TURNS = 15          # turns allowed beyond the tool budget (thinking, retries, final text)
MAX_JSON_RETRIES = 2


class AgentRunner:
    def __init__(self, ledger: Ledger, model_factory: Callable[[], ModelClient]):
        self.ledger = ledger
        self.db = ledger.db
        self.bus = ledger.bus
        self.model_factory = model_factory
        self.model_factory_is_default = False
        self.tasks: dict[str, asyncio.Task] = {}

    # --- lifecycle --------------------------------------------------------------

    def start(self, investigation: dict, kind: str, goal: str, user: str,
              max_tool_calls: int) -> str:
        run_id = new_id("ar")
        self.db.insert("agent_runs", {
            "id": run_id, "investigation_id": investigation["id"], "kind": kind,
            "goal": goal, "status": "running", "stats": {}, "transcript": [],
            "started_at": now(),
        })
        self.db.audit(user=user, action=f"agent.{kind}.start",
                      investigation_id=investigation["id"],
                      detail={"run_id": run_id, "goal": goal, "max_tool_calls": max_tool_calls})
        task = asyncio.create_task(self.run(run_id, investigation, kind, goal, user,
                                            max_tool_calls))
        self.tasks[run_id] = task
        task.add_done_callback(lambda _t: self.tasks.pop(run_id, None))
        return run_id

    def cancel(self, run_id: str) -> bool:
        task = self.tasks.get(run_id)
        if task is None:
            return False
        task.cancel()
        return True

    # --- the loop -----------------------------------------------------------------

    async def run(self, run_id: str, investigation: dict, kind: str, goal: str, user: str,
                  max_tool_calls: int) -> dict:
        inv_id = investigation["id"]
        box = ToolBox(self.ledger, investigation, run_id, user, budget=max_tool_calls)
        if kind == "collection":
            system, tools, effort = prompts.COLLECTION, connector_tools(investigation), "medium"
        else:
            system, tools, effort = prompts.REPORT, report_tools(), "high"
        messages: list[dict] = [{"role": "user", "content": self._brief(investigation, kind,
                                                                         goal, max_tool_calls)}]
        transcript: list[dict] = []
        usage: Counter[str] = Counter()
        status, summary = "finished", ""
        model = self.model_factory()
        self.bus.publish(inv_id, "agent.started", {"run_id": run_id, "kind": kind, "goal": goal})

        def on_delta(kind_: str, chunk: str) -> None:
            event = "agent.text" if kind_ == "text" else "agent.update"
            self.bus.publish(inv_id, event, {"run_id": run_id, "delta": chunk})

        try:
            json_retries = 0
            for _turn in range(max_tool_calls + EXTRA_TURNS):
                try:
                    msg = await model.turn(system=system, tools=tools, messages=messages,
                                           effort=effort, on_delta=on_delta)
                except ValueError:
                    # Tool input JSON the SDK could not parse at all; no tool_use id
                    # to answer, so re-issue the turn (bounded).
                    json_retries += 1
                    if json_retries > MAX_JSON_RETRIES:
                        raise
                    continue
                json_retries = 0
                _add_usage(usage, msg)

                if msg.stop_reason == "refusal":
                    details = getattr(msg, "stop_details", None)
                    category = getattr(details, "category", None)
                    status, summary = "refused", f"Claude declined (category: {category})."
                    transcript.append({"t": "refusal", "category": category})
                    self.bus.publish(inv_id, "agent.refused",
                                     {"run_id": run_id, "category": category})
                    break

                content = echo_content(msg.content)
                messages.append({"role": "assistant", "content": content})
                text = "".join(b.text for b in content if b.type == "text")
                if text:
                    transcript.append({"t": "text", "text": text})

                if msg.stop_reason == "pause_turn":
                    continue
                tool_uses = [b for b in content if b.type == "tool_use"]
                if not tool_uses:
                    summary = text.strip()
                    break
                if msg.stop_reason == "max_tokens":
                    status, summary = "failed", "Output limit hit in the middle of a tool call."
                    break

                for b in tool_uses:
                    transcript.append({"t": "tool", "name": b.name, "input": b.input})
                    self.bus.publish(inv_id, "agent.tool",
                                     {"run_id": run_id, "name": b.name, "input": b.input})
                outputs = await asyncio.gather(*(box.call(b.name, b.input) for b in tool_uses))
                results = []
                for b, (out, is_error) in zip(tool_uses, outputs):
                    results.append({"type": "tool_result", "tool_use_id": b.id,
                                    "content": out, **({"is_error": True} if is_error else {})})
                    brief = out[:300]
                    transcript.append({"t": "result", "name": b.name, "error": is_error,
                                       "content": brief})
                    self.bus.publish(inv_id, "agent.tool_result",
                                     {"run_id": run_id, "name": b.name, "error": is_error,
                                      "content": brief})
                messages.append({"role": "user", "content": results})
                self._save(run_id, transcript, usage, box)
            else:
                status, summary = "failed", "Turn limit reached."
        except asyncio.CancelledError:
            status, summary = "cancelled", "Cancelled."
        except Exception as e:  # API errors, bad credentials, network: the run ends cleanly
            status, summary = "failed", f"{type(e).__name__}: {e}"[:1000]
            self.bus.publish(inv_id, "agent.failed", {"run_id": run_id, "error": summary})

        if kind == "report" and status == "finished" and not box.report_ok:
            status = "failed"
            summary = (summary + "\n\n" if summary else "") + (
                "No lint-clean report was submitted." if box.report_id is None else
                f"Report {box.report_id} still has lint errors; review it before export.")
        stats = {**usage, "tool_calls": box.calls, "findings": len(box.findings),
                 "report_id": box.report_id}
        self.db.update("agent_runs", run_id, {"status": status, "summary": summary,
                                              "stats": stats, "transcript": transcript,
                                              "finished_at": now()})
        self.db.audit(user=user, action=f"agent.{kind}.finish", investigation_id=inv_id,
                      outcome="ok" if status == "finished" else status,
                      detail={"run_id": run_id, "status": status, **stats})
        self.bus.publish(inv_id, "agent.finished", {"run_id": run_id, "kind": kind,
                                                    "status": status, "summary": summary,
                                                    "report_id": box.report_id})
        return stats

    # --- helpers --------------------------------------------------------------------

    def _brief(self, inv: dict, kind: str, goal: str, budget: int) -> str:
        targets = self.db.query("SELECT value, type FROM targets WHERE investigation_id = ? "
                                "ORDER BY created_at", (inv["id"],))
        anchors = anchor_domains(self.db, inv["id"], [])
        lines = [
            f"Investigation: {inv['name']}",
            f"Purpose: {inv['purpose']}",
            f"Legal basis: {inv['legal_basis']}",
            f"Mode: {inv['mode']}" + (f" (authorized scope: {', '.join(inv['scope'])})"
                                      if inv["mode"] == "active" else " (passive only)"),
            "Targets: " + (", ".join(f"{t['value']} ({t['type']})" for t in targets) or "none"),
        ]
        if anchors:
            lines.append("The target's own domains: " + ", ".join(anchors))
        lines += [f"Goal: {goal}", f"Tool budget: {budget} calls."]
        if kind == "collection":
            lines.append("Use graph_overview to see what earlier scans already found.")
        else:
            lines.append("Write the report for this goal from the investigation graph.")
        return "\n".join(lines)

    def _save(self, run_id: str, transcript: list, usage: Counter, box: ToolBox) -> None:
        self.db.update("agent_runs", run_id, {"transcript": transcript, "stats": {
            **usage, "tool_calls": box.calls, "findings": len(box.findings),
            "report_id": box.report_id}})


def _add_usage(total: Counter, msg: Any) -> None:
    u = getattr(msg, "usage", None)
    for field in ("input_tokens", "output_tokens", "cache_read_input_tokens",
                  "cache_creation_input_tokens"):
        total[field] += getattr(u, field, 0) or 0


def ensure_target(platform: Any, investigation: dict, value: str, user: str) -> str | None:
    """Add ``value`` as a target if it isn't one yet (used by POST /collect)."""
    try:
        etype, key = detect(value)
    except DetectError:
        return None
    existing = platform.db.one("SELECT id FROM targets WHERE investigation_id = ? AND type = ? "
                               "AND entity_id IN (SELECT id FROM entities WHERE key = ?)",
                               (investigation["id"], etype.value, key))
    if existing:
        return existing["id"]
    entity_id = platform.resolver.ensure_entity(investigation["id"], etype, key)
    target_id = new_id("t")
    platform.db.insert("targets", {"id": target_id, "investigation_id": investigation["id"],
                                   "value": value, "type": etype.value, "entity_id": entity_id,
                                   "added_by": user, "created_at": now()})
    platform.db.audit(user=user, action="target.add", investigation_id=investigation["id"],
                      target=key, detail={"type": etype.value, "via": "collect"})
    platform.bus.publish(investigation["id"], "entity.created",
                         {"entity": entity_view(platform.db.get("entities", entity_id))})
    return target_id


__all__ = ["AgentRunner", "ensure_target"]
