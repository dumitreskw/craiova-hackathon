import { useEffect, useMemo, useState } from "react";
import { CornerDownRight, ListChecks, RefreshCw, Square } from "lucide-react";
import { api } from "../api";
import type { InvestigationStream } from "../stream";
import type { Run } from "../types";
import { Badge, Empty, ErrorBox, Loading, Mono, useDebounced, useLoad, when } from "./ui";

const KIND: Record<string, string> = {
  quick_scan: "Scan",
  pivot: "Manual lookup",
  collection: "Claude collection",
  report: "Claude report",
  deep: "Deep analysis",
  worker: "Worker",
};

// Bookkeeping fields that are shown elsewhere or mean nothing to a reader.
const HIDDEN_STATS = new Set(["parent", "worker", "report_id", "report_run"]);

function duration(r: Run): string {
  if (!r.finished_at) return "running";
  const s = (new Date(r.finished_at).getTime() - new Date(r.started_at).getTime()) / 1000;
  return s < 90 ? `${Math.round(s)} s` : `${Math.round(s / 60)} min`;
}

export function RunsView({ investigationId, stream }: { investigationId: string; stream: InvestigationStream }) {
  const [runs, error, loading, reload] = useLoad(() => api.get<Run[]>(`/investigations/${investigationId}/runs`), [investigationId]);
  const [actionError, setActionError] = useState<unknown>(null);
  const anyRunning = runs?.some((r) => r.status === "running");
  const reloadSoon = useDebounced(reload, 600);

  useEffect(
    () =>
      stream.subscribe((e) => {
        if (/^(scan|agent)\.(started|finished|failed|refused)$/.test(e.event)) reloadSoon();
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [investigationId],
  );

  useEffect(() => {
    if (!anyRunning) return;
    const t = window.setInterval(reload, 4000);
    return () => window.clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [anyRunning]);

  // Parents first, each followed by its workers.
  const ordered = useMemo(() => {
    if (!runs) return [];
    const byParent = new Map<string, Run[]>();
    const ids = new Set(runs.map((r) => r.id));
    for (const r of runs) {
      const p = r.stats?.parent as string | undefined;
      if (p && ids.has(p)) byParent.set(p, [...(byParent.get(p) || []), r]);
    }
    const out: { run: Run; child: boolean }[] = [];
    for (const r of runs) {
      const p = r.stats?.parent as string | undefined;
      if (p && ids.has(p)) continue;
      out.push({ run: r, child: false });
      for (const c of byParent.get(r.id) || []) out.push({ run: c, child: true });
    }
    return out;
  }, [runs]);

  async function cancel(id: string) {
    try {
      await api.post(`/agent-runs/${id}/cancel`);
      reload();
    } catch (e) {
      setActionError(e);
    }
  }

  return (
    <div className="scroll-thin min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-6xl px-6 py-5">
        <div className="mb-4 flex items-center justify-between gap-3">
          <p className="text-sm text-muted">Scans, manual lookups and Claude runs, newest first.</p>
          <button className="btn btn-sm" onClick={reload} disabled={loading}><RefreshCw className="h-3.5 w-3.5" aria-hidden />Refresh</button>
        </div>
        {error ? <ErrorBox error={error} /> : null}
        <ErrorBox error={actionError} onDismiss={() => setActionError(null)} />
        {!runs && !error && <Loading>Loading runs</Loading>}
        {runs?.length === 0 && (
          <Empty title="No runs yet" icon={<ListChecks className="h-8 w-8" />}>Adding a target starts a scan. Claude runs show up here too.</Empty>
        )}
        {ordered.length > 0 && (
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr className="border-b border-line text-left text-xs text-muted">
                <th className="py-2 pr-4 font-medium">Run</th>
                <th className="py-2 pr-4 font-medium">Status</th>
                <th className="py-2 pr-4 font-medium">Started</th>
                <th className="py-2 pr-4 font-medium">Took</th>
                <th className="py-2 pr-4 font-medium">Result</th>
                <th className="py-2 font-medium"><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {ordered.map(({ run: r, child }) => {
                const stats = Object.entries(r.stats || {}).filter(([k, v]) => !HIDDEN_STATS.has(k) && v !== null && v !== undefined && typeof v !== "object");
                return (
                  <tr key={r.id} className={`border-b border-line/60 align-top ${child ? "bg-ground/40" : ""}`}>
                    <td className={`py-3 pr-4 ${child ? "pl-6" : ""}`}>
                      <div className="flex items-center gap-1.5 font-medium">
                        {child && <CornerDownRight className="h-3.5 w-3.5 text-muted" aria-hidden />}
                        {child && r.stats?.worker ? `Worker: ${String(r.stats.worker).replace(/_/g, " ")}` : KIND[r.kind] || r.kind}
                      </div>
                      <Mono className="text-xs text-muted">{r.id}</Mono>
                      {r.goal && <div className="mt-0.5 max-w-xs text-xs text-muted">{r.goal}</div>}
                    </td>
                    <td className="py-3 pr-4"><Badge value={r.status} /></td>
                    <td className="whitespace-nowrap py-3 pr-4 text-muted">{when(r.started_at)}</td>
                    <td className="whitespace-nowrap py-3 pr-4 text-muted">{duration(r)}</td>
                    <td className="py-3 pr-4">
                      <div className="line-clamp-3 max-w-xl" title={r.summary || undefined}>{r.summary || "—"}</div>
                      {stats.length > 0 && (
                        <div className="mt-1 flex flex-wrap gap-x-3 text-xs text-muted">
                          {stats.map(([k, v]) => <span key={k}>{k.replace(/_/g, " ")} {String(v)}</span>)}
                        </div>
                      )}
                    </td>
                    <td className="py-3 text-right">
                      {r.status === "running" && (
                        <button className="btn btn-danger btn-sm" onClick={() => cancel(r.id)}><Square className="h-3 w-3" aria-hidden />Stop</button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
