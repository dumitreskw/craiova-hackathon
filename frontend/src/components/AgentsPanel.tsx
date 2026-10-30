import { useEffect, useMemo, useState, type FormEvent } from "react";
import { ChevronDown, ChevronRight, FileText, Layers3, Play, Sparkles, Square } from "lucide-react";
import { api, ApiError } from "../api";
import { readCredit, useCredits } from "../credits";
import type { InvestigationStream } from "../stream";
import type { CostTier, Investigation, Run, StreamEvent } from "../types";
import { ago, Badge, Empty, ErrorBox, Meter, Spinner, useDebounced, useLoad } from "./ui";

type Piece = { kind: "text"; text: string } | { kind: "tool"; text: string } | { kind: "end"; tone: string; text: string };

interface Live {
  pieces: Piece[];
  status?: "running" | "finished" | "refused" | "failed";
  worker?: string;
  parent?: string;
  kind?: string;
  goal?: string;
}

/** Fold agent.* events into one live transcript per run. */
function liveRuns(events: StreamEvent[]): Map<string, Live> {
  const byRun = new Map<string, Live>();
  for (const e of events) {
    if (!e.event.startsWith("agent.")) continue;
    const d = e.data;
    const runId = String(d.run_id ?? "current");
    let t = byRun.get(runId);
    if (!t) byRun.set(runId, (t = { pieces: [] }));
    if (e.event === "agent.started") {
      t.status = "running";
      t.worker = d.worker ? String(d.worker) : undefined;
      t.parent = d.parent ? String(d.parent) : undefined;
      t.kind = d.kind ? String(d.kind) : undefined;
      t.goal = d.goal ? String(d.goal) : undefined;
    } else if (e.event === "agent.text") {
      const delta = String(d.delta ?? d.text ?? "");
      const last = t.pieces[t.pieces.length - 1];
      if (last && last.kind === "text") last.text += delta;
      else t.pieces.push({ kind: "text", text: delta });
    } else if (e.event === "agent.tool") {
      const input = d.input as Record<string, unknown> | undefined;
      const a = input ? Object.values(input).map(String).join(", ") : "";
      t.pieces.push({ kind: "tool", text: `${String(d.connector ?? d.name ?? "tool")}${a ? ` ${a}` : ""}` });
    } else if (e.event === "agent.finished") {
      t.status = "finished";
      t.pieces.push({ kind: "end", tone: "text-ok", text: String(d.summary ?? "Finished.") });
    } else if (e.event === "agent.refused") {
      t.status = "refused";
      t.pieces.push({ kind: "end", tone: "text-danger", text: `Claude declined this request${d.category ? ` (${String(d.category)})` : ""}. Scans still work without it.` });
    } else if (e.event === "agent.failed") {
      t.status = "failed";
      t.pieces.push({ kind: "end", tone: "text-danger", text: `Run failed: ${String(d.error ?? "unknown error")}` });
    }
  }
  return byRun;
}

function explain(err: unknown): unknown {
  if (err instanceof ApiError && (err.status === 404 || err.status === 405)) {
    return new Error("The Claude agents are not available on this server yet.");
  }
  return err;
}

const KIND_LABEL: Record<string, string> = {
  collection: "Collection",
  deep: "Deep analysis",
  report: "Report writing",
  worker: "Worker",
};

const TIERS: { value: CostTier; label: string; hint: string; sources: string[] }[] = [
  { value: "none", label: "Free only", hint: "Public sources without quotas or fees.", sources: [] },
  { value: "metered", label: "Free and monthly quota", hint: "Adds Hunter and Tomba, which have a fixed number of calls each month.", sources: ["hunter", "tomba"] },
  { value: "all", label: "Everything", hint: "Adds DeHashed breach data, which spends paid credits on each search.", sources: ["hunter", "tomba", "dehashed"] },
];

const AGENT_KINDS = new Set(["collection", "deep", "report", "worker"]);

export function AgentsPanel({ investigation, stream }: { investigation: Investigation; stream: InvestigationStream }) {
  const targets = investigation.targets || [];
  const [target, setTarget] = useState("");
  const [goal, setGoal] = useState("Map the attack surface and who is behind it.");
  const [maxCalls, setMaxCalls] = useState(40);
  const [costly, setCostly] = useState<CostTier>("all");
  const [reportGoal, setReportGoal] = useState("Summarize what matters for the stated purpose, with evidence for every claim.");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const credits = useCredits();
  const [runs, runsError, , reloadRuns] = useLoad(
    () => api.get<Run[]>(`/investigations/${investigation.id}/runs`),
    [investigation.id],
  );
  const reloadSoon = useDebounced(reloadRuns, 600);
  const live = useMemo(() => liveRuns(stream.events), [stream.events]);

  useEffect(
    () =>
      stream.subscribe((e) => {
        if (e.event === "agent.started" || e.event === "agent.finished" || e.event === "agent.failed" || e.event === "agent.refused") {
          reloadSoon();
        }
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [investigation.id],
  );

  async function start(kind: "collect" | "deep") {
    setBusy(kind);
    setError(null);
    try {
      await api.post(`/investigations/${investigation.id}/${kind}`, {
        target: target.trim() || undefined,
        goal: goal.trim(),
        max_tool_calls: kind === "deep" ? Math.max(6, maxCalls) : maxCalls,
        costly_connectors: costly,
      });
      reloadSoon();
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
      reloadSoon();
    } catch (err) {
      setError(explain(err));
    } finally {
      setBusy(null);
    }
  }

  async function cancel(id: string) {
    try {
      await api.post(`/agent-runs/${id}/cancel`);
      reloadRuns();
    } catch (err) {
      setError(err);
    }
  }

  // Group worker runs under the deep analysis that started them.
  const tree = useMemo(() => {
    const all = (runs || []).filter((r) => AGENT_KINDS.has(r.kind));
    const known = new Set(all.map((r) => r.id));
    // Runs the stream has announced but the list hasn't caught up with yet.
    for (const [id, l] of live) {
      if (known.has(id) || !l.kind) continue;
      all.unshift({
        id, investigation_id: investigation.id, kind: l.kind, goal: l.goal ?? null, status: l.status ?? "running",
        summary: null, stats: { worker: l.worker, parent: l.parent }, started_at: new Date().toISOString(), finished_at: null,
      });
    }
    const children = new Map<string, Run[]>();
    const roots: Run[] = [];
    for (const r of all) {
      const parent = (r.stats?.parent as string | undefined) || live.get(r.id)?.parent;
      if (parent && all.some((p) => p.id === parent)) {
        children.set(parent, [...(children.get(parent) || []), r]);
      } else {
        roots.push(r);
      }
    }
    return { roots, children };
  }, [runs, live, investigation.id]);

  const tier = TIERS.find((t) => t.value === costly)!;
  const tierCredits = (credits.data || []).filter((c) => tier.sources.includes(c.source));

  return (
    <div className="scroll-thin min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto grid w-full max-w-[1400px] gap-8 px-6 py-6 lg:grid-cols-[minmax(0,440px)_minmax(0,1fr)]">
        <div className="space-y-6">
          <section className="panel p-5">
            <h2 className="section-title flex items-center gap-2"><Sparkles className="h-4 w-4 text-accent-text" aria-hidden />Collect with Claude</h2>
            <p className="mt-1 text-sm text-muted">
              Claude chooses which sources to ask and what to follow up on. Every lookup goes through the same checks
              and log as a scan{investigation.mode === "passive" ? ", and stays passive" : ""}.
            </p>
            <div className="mt-4 space-y-4">
              <div>
                <label className="label" htmlFor="agent-target">Target</label>
                <select id="agent-target" className="field font-mono" value={target} onChange={(e) => setTarget(e.target.value)}>
                  <option value="">Every target</option>
                  {targets.map((t) => <option key={t.id} value={t.value}>{t.value}</option>)}
                </select>
              </div>
              <div>
                <label className="label" htmlFor="agent-goal">Goal</label>
                <textarea id="agent-goal" className="field min-h-[72px]" value={goal} onChange={(e) => setGoal(e.target.value)} />
              </div>
              <fieldset>
                <legend className="label">Sources Claude may use</legend>
                <div className="space-y-1.5">
                  {TIERS.map((t) => (
                    <label key={t.value} className={`flex cursor-pointer items-start gap-3 rounded-md border px-3 py-2.5 transition-colors duration-150 ${
                      costly === t.value ? "border-accent/70 bg-accent/10" : "border-line hover:border-muted/70"}`}>
                      <input type="radio" name="tier" className="mt-1 accent-[rgb(var(--accent))]" checked={costly === t.value}
                        onChange={() => setCostly(t.value)} />
                      <span>
                        <span className="block text-sm font-medium">{t.label}</span>
                        <span className="block text-xs text-muted">{t.hint}</span>
                      </span>
                    </label>
                  ))}
                </div>
                {tierCredits.length > 0 && (
                  <ul className="mt-3 space-y-2 rounded-md bg-ground px-3 py-2.5">
                    {tierCredits.map((c) => {
                      const r = readCredit(c);
                      return (
                        <li key={c.source} className="text-xs">
                          <div className="flex justify-between gap-2">
                            <span className="text-muted">{c.label}</span>
                            <span className={r.tone === "danger" ? "text-danger" : r.tone === "warn" ? "text-warn" : ""}>{r.headline}</span>
                          </div>
                          {r.left !== null && <div className="mt-1"><Meter fraction={r.left} tone={r.tone} label={`${c.label} balance`} /></div>}
                        </li>
                      );
                    })}
                    <li><a href="#/sources" className="text-xs text-accent-text underline-offset-2 hover:underline">All balances</a></li>
                  </ul>
                )}
              </fieldset>
              <div>
                <label className="label" htmlFor="agent-max">Most lookups Claude may make</label>
                <input id="agent-max" type="number" min={1} max={200} className="field w-28" value={maxCalls}
                  onChange={(e) => setMaxCalls(Number(e.target.value) || 1)} />
              </div>
              <div className="flex flex-wrap gap-2 pt-1">
                <button className="btn btn-primary" disabled={busy !== null || !goal.trim()} onClick={() => start("collect")}>
                  {busy === "collect" ? <Spinner /> : <Play className="h-4 w-4" aria-hidden />}Start collection
                </button>
                <button className="btn" disabled={busy !== null || !goal.trim()} onClick={() => start("deep")}
                  title="Claude plans the work, runs specialised workers in parallel, then writes a report">
                  {busy === "deep" ? <Spinner /> : <Layers3 className="h-4 w-4" aria-hidden />}Deep analysis
                </button>
              </div>
              <p className="hint">Deep analysis splits the goal across parallel workers and ends with a report in the Reports tab.</p>
            </div>
          </section>

          <form onSubmit={writeReport} className="panel p-5">
            <h2 className="section-title flex items-center gap-2"><FileText className="h-4 w-4 text-muted" aria-hidden />Write a report with Claude</h2>
            <p className="mt-1 text-sm text-muted">
              Reads the graph only, keeps what serves the purpose and cites a finding for every claim. The draft opens
              in Reports for your review.
            </p>
            <label className="label mt-4" htmlFor="report-goal">What the report should answer</label>
            <textarea id="report-goal" className="field min-h-[72px]" value={reportGoal} onChange={(e) => setReportGoal(e.target.value)} />
            <button className="btn mt-3" disabled={busy !== null || !reportGoal.trim()}>
              {busy === "report" ? <Spinner /> : <FileText className="h-4 w-4" aria-hidden />}Write report
            </button>
          </form>

          <ErrorBox error={error} onDismiss={() => setError(null)} />
        </div>

        <section aria-labelledby="runs-title">
          <div className="flex items-center justify-between">
            <h2 id="runs-title" className="section-title">Claude runs</h2>
            <button className="btn btn-quiet btn-sm" onClick={reloadRuns}>Refresh</button>
          </div>
          <ErrorBox error={runsError} />
          {runs && tree.roots.length === 0 && (
            <Empty title="No Claude runs yet" icon={<Sparkles className="h-8 w-8" />}>
              Start a collection or a deep analysis. Claude's reasoning and each lookup stream here as it works.
            </Empty>
          )}
          <ul className="mt-3 space-y-3">
            {tree.roots.map((r) => (
              <RunCard key={r.id} run={r} live={live.get(r.id)} workers={tree.children.get(r.id) || []}
                liveMap={live} onCancel={cancel} />
            ))}
          </ul>
        </section>
      </div>
    </div>
  );
}

function statusOf(run: Run, live?: Live): string {
  // The list can lag the stream by a moment; prefer whichever says "done".
  if (live?.status && live.status !== "running") return live.status;
  return run.status;
}

function RunCard({ run, live, workers, liveMap, onCancel }: {
  run: Run; live?: Live; workers: Run[]; liveMap: Map<string, Live>; onCancel: (id: string) => void;
}) {
  const status = statusOf(run, live);
  const [open, setOpen] = useState(status === "running");
  const [full, setFull] = useState(false);
  const done = workers.filter((w) => statusOf(w, liveMap.get(w.id)) !== "running").length;
  const reportId = run.stats?.report_id as string | undefined;
  return (
    <li className="panel">
      <div className="flex items-start gap-3 p-4">
        <button className="icon-btn -ml-1 h-8 w-8" aria-expanded={open} aria-label={open ? "Hide transcript" : "Show transcript"}
          onClick={() => setOpen((o) => !o)}>
          {open ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
        </button>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-semibold">{KIND_LABEL[run.kind] || run.kind}</span>
            <Badge value={status} />
            <span className="text-xs text-muted">{ago(run.started_at)}</span>
          </div>
          {run.goal && <p className="mt-1 text-sm text-muted">{run.goal}</p>}
          {run.summary && (
            <div className="mt-2">
              <p className={`max-w-[80ch] whitespace-pre-wrap text-sm leading-relaxed ${full ? "" : "line-clamp-4"}`}>{run.summary}</p>
              {run.summary.length > 400 && (
                <button className="mt-1 text-xs font-medium text-accent-text hover:underline" onClick={() => setFull((f) => !f)}>
                  {full ? "Show less" : "Show the full summary"}
                </button>
              )}
            </div>
          )}
          {reportId && <a href={`${window.location.hash.replace(/\/[^/]*$/, "")}/reports`} className="mt-2 inline-block text-sm text-accent-text hover:underline">Open the report</a>}
        </div>
        {status === "running" && (
          <button className="btn btn-danger btn-sm" onClick={() => onCancel(run.id)}><Square className="h-3.5 w-3.5" aria-hidden />Stop</button>
        )}
      </div>

      {workers.length > 0 && (
        <div className="border-t border-line px-4 py-3">
          <div className="mb-2 flex items-center justify-between text-xs text-muted">
            <span>Workers</span>
            <span>{done} of {workers.length} done</span>
          </div>
          <Meter fraction={done / workers.length} tone={done === workers.length ? "ok" : "accent"} label="Workers finished" />
          <ul className="mt-3 space-y-1.5">
            {workers.map((w) => {
              const lw = liveMap.get(w.id);
              const name = (w.stats?.worker as string | undefined) || lw?.worker || KIND_LABEL[w.kind] || w.kind;
              return (
                <WorkerRow key={w.id} name={name} run={w} live={lw} />
              );
            })}
          </ul>
        </div>
      )}

      {open && <Transcript live={live} />}
    </li>
  );
}

function WorkerRow({ name, run, live }: { name: string; run: Run; live?: Live }) {
  const [open, setOpen] = useState(false);
  const status = statusOf(run, live);
  const lookups = live?.pieces.filter((p) => p.kind === "tool").length ?? 0;
  return (
    <li className="rounded-md border border-line/70 bg-ground">
      <button className="flex w-full items-center gap-2.5 px-3 py-2 text-left text-sm" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        {open ? <ChevronDown className="h-3.5 w-3.5 text-muted" aria-hidden /> : <ChevronRight className="h-3.5 w-3.5 text-muted" aria-hidden />}
        <span className="font-medium capitalize">{name.replace(/_/g, " ")}</span>
        <Badge value={status} />
        <span className="min-w-0 flex-1 truncate text-xs text-muted">{run.summary || run.goal?.replace(/^\[[^\]]+\]\s*/, "")}</span>
        {lookups > 0 && <span className="shrink-0 text-xs text-muted">{lookups} lookups</span>}
      </button>
      {open && <Transcript live={live} compact />}
    </li>
  );
}

function Transcript({ live, compact = false }: { live?: Live; compact?: boolean }) {
  if (!live || live.pieces.length === 0) {
    return <p className={`border-t border-line/70 px-4 py-3 text-sm text-muted ${compact ? "text-xs" : ""}`}>
      The transcript streams live only while this page is open. The summary above is kept.
    </p>;
  }
  return (
    <div className={`scroll-thin max-h-[420px] space-y-2 overflow-y-auto border-t border-line/70 px-4 py-3 leading-relaxed ${compact ? "text-[13px]" : "text-sm"}`}>
      {live.pieces.map((p, i) =>
        p.kind === "text" ? (
          <p key={i} className="whitespace-pre-wrap">{p.text}</p>
        ) : p.kind === "tool" ? (
          <p key={i} className="flex gap-2 font-mono text-xs text-muted"><span aria-hidden>$</span><span className="break-all">{p.text}</span></p>
        ) : (
          <p key={i} className={p.tone}>{p.text}</p>
        ),
      )}
    </div>
  );
}
