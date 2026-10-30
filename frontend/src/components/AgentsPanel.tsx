import { useMemo, useState, type FormEvent } from "react";
import { api, ApiError } from "../api";
import type { Investigation, StreamEvent } from "../types";
import { Badge, ErrorBox } from "./ui";

type Piece = { kind: "text"; text: string } | { kind: "tool"; text: string } | { kind: "end"; tone: string; text: string };

interface Transcript {
  runId: string;
  pieces: Piece[];
  status: "running" | "finished" | "refused" | "failed";
}

/** Fold agent.* events into one transcript per run, oldest piece first. */
function transcripts(events: StreamEvent[]): Transcript[] {
  const byRun = new Map<string, Transcript>();
  for (const e of events) {
    if (!e.event.startsWith("agent.")) continue;
    const runId = String(e.data.run_id ?? "current");
    let t = byRun.get(runId);
    if (!t) {
      t = { runId, pieces: [], status: "running" };
      byRun.set(runId, t);
    }
    const d = e.data;
    if (e.event === "agent.text") {
      const delta = String(d.delta ?? d.text ?? "");
      const last = t.pieces[t.pieces.length - 1];
      if (last && last.kind === "text") last.text += delta;
      else t.pieces.push({ kind: "text", text: delta });
    } else if (e.event === "agent.tool") {
      const input = d.input as Record<string, unknown> | undefined;
      const arg = input ? Object.values(input).map(String).join(", ") : "";
      t.pieces.push({ kind: "tool", text: `${String(d.connector ?? d.name ?? "tool")}${arg ? `(${arg})` : ""}` });
    } else if (e.event === "agent.finished") {
      t.status = "finished";
      t.pieces.push({ kind: "end", tone: "text-ok", text: String(d.summary ?? "Finished.") });
    } else if (e.event === "agent.refused") {
      t.status = "refused";
      t.pieces.push({ kind: "end", tone: "text-danger", text: `Claude declined this request${d.category ? ` (${String(d.category)})` : ""}. The quick scan still works without it.` });
    } else if (e.event === "agent.failed") {
      t.status = "failed";
      t.pieces.push({ kind: "end", tone: "text-danger", text: `Run failed: ${String(d.error ?? "unknown error")}` });
    }
  }
  return [...byRun.values()].reverse();
}

function explain(err: unknown): unknown {
  if (err instanceof ApiError && (err.status === 404 || err.status === 405)) {
    return new Error("The Claude agents are not available on this server yet.");
  }
  return err;
}

export function AgentsPanel({ investigation, events }: { investigation: Investigation; events: StreamEvent[] }) {
  const targets = investigation.targets || [];
  const [target, setTarget] = useState(targets[0]?.value ?? "");
  const [goal, setGoal] = useState("Map the attack surface and who is behind it.");
  const [maxCalls, setMaxCalls] = useState(40);
  const [costly, setCostly] = useState<"none" | "metered" | "all">("metered");
  const [reportGoal, setReportGoal] = useState("Summarize what matters for the stated purpose, with evidence for every claim.");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const runs = useMemo(() => transcripts(events), [events]);

  async function collect(e: FormEvent) {
    e.preventDefault();
    setBusy("collect");
    setError(null);
    try {
      await api.post(`/investigations/${investigation.id}/collect`, {
        target: target.trim() || undefined,
        goal: goal.trim(),
        max_tool_calls: maxCalls,
        costly_connectors: costly,
      });
    } catch (err) {
      setError(explain(err));
    } finally {
      setBusy(null);
    }
  }

  async function writeReport(e: FormEvent) {
    e.preventDefault();
    setBusy("report");
    setError(null);
    try {
      await api.post(`/investigations/${investigation.id}/report-agent`, { goal: reportGoal.trim() });
    } catch (err) {
      setError(explain(err));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-5 p-4">
      <form onSubmit={collect} className="space-y-2">
        <h3 className="text-sm font-semibold">Collect with Claude</h3>
        <p className="text-xs text-muted">
          Claude chooses which sources to ask and what to follow up on. Every lookup goes through the same
          checks and log as a scan{investigation.mode === "passive" ? ", and stays passive" : ""}.
        </p>
        <div>
          <label className="label" htmlFor="agent-target">Target</label>
          <input id="agent-target" className="field font-mono" list="agent-targets" value={target}
            onChange={(e) => setTarget(e.target.value)} placeholder="Leave empty to use every target" />
          <datalist id="agent-targets">
            {targets.map((t) => <option key={t.id} value={t.value} />)}
          </datalist>
        </div>
        <div>
          <label className="label" htmlFor="agent-goal">Goal</label>
          <textarea id="agent-goal" className="field min-h-[56px]" value={goal} onChange={(e) => setGoal(e.target.value)} />
        </div>
        <div className="flex items-end gap-2">
          <div>
            <label className="label" htmlFor="agent-costly">Sources</label>
            <select id="agent-costly" className="field w-auto" value={costly}
              onChange={(e) => setCostly(e.target.value as "none" | "metered" | "all")}>
              <option value="none">Free only</option>
              <option value="metered">+ quota (Hunter, Tomba)</option>
              <option value="all">+ paid (DeHashed)</option>
            </select>
          </div>
          <div>
            <label className="label" htmlFor="agent-max">Lookup limit</label>
            <input id="agent-max" type="number" min={1} max={200} className="field w-24" value={maxCalls}
              onChange={(e) => setMaxCalls(Number(e.target.value) || 1)} />
          </div>
          <button className="btn btn-primary" disabled={busy !== null || !goal.trim()}>
            {busy === "collect" ? "Starting…" : "Start collection"}
          </button>
        </div>
      </form>

      <form onSubmit={writeReport} className="space-y-2 border-t border-line pt-4">
        <h3 className="text-sm font-semibold">Write report with Claude</h3>
        <p className="text-xs text-muted">
          Reads the graph only, keeps what serves the purpose, and cites a finding for every claim. Opens in
          Reports as a draft for your review.
        </p>
        <textarea className="field min-h-[56px]" value={reportGoal} onChange={(e) => setReportGoal(e.target.value)} aria-label="Report goal" />
        <button className="btn" disabled={busy !== null || !reportGoal.trim()}>
          {busy === "report" ? "Starting…" : "Write report"}
        </button>
      </form>

      <ErrorBox error={error} onDismiss={() => setError(null)} />

      <section className="border-t border-line pt-4">
        <h3 className="mb-2 text-sm font-semibold">Transcripts</h3>
        {runs.length === 0 && <p className="text-sm text-muted">Claude's reasoning and lookups stream here while a run is going.</p>}
        <div className="space-y-3">
          {runs.map((t) => (
            <article key={t.runId} className="rounded-md border border-line bg-ground p-3">
              <div className="mb-2 flex items-center justify-between">
                <span className="font-mono text-2xs text-muted">{t.runId}</span>
                <Badge value={t.status} />
              </div>
              <div className="space-y-1.5 text-[13px] leading-relaxed">
                {t.pieces.map((p, i) =>
                  p.kind === "text" ? (
                    <p key={i} className="whitespace-pre-wrap">{p.text}</p>
                  ) : p.kind === "tool" ? (
                    <p key={i} className="font-mono text-2xs text-muted">{p.text}</p>
                  ) : (
                    <p key={i} className={p.tone}>{p.text}</p>
                  ),
                )}
              </div>
            </article>
          ))}
        </div>
      </section>
    </div>
  );
}
