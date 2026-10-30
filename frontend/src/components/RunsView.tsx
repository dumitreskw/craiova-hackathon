import { useEffect, useState } from "react";
import { api } from "../api";
import type { InvestigationStream } from "../stream";
import type { Run } from "../types";
import { Badge, Empty, ErrorBox, Mono, useLoad, when } from "./ui";

const KIND: Record<string, string> = {
  quick_scan: "Scan",
  pivot: "Manual lookup",
  collection: "Claude collection",
  report: "Claude report",
};

function duration(r: Run): string {
  if (!r.finished_at) return "running";
  const s = (new Date(r.finished_at).getTime() - new Date(r.started_at).getTime()) / 1000;
  return s < 90 ? `${Math.round(s)} s` : `${Math.round(s / 60)} min`;
}

export function RunsView({ investigationId, stream }: { investigationId: string; stream: InvestigationStream }) {
  const [runs, error, , reload] = useLoad(() => api.get<Run[]>(`/investigations/${investigationId}/runs`), [investigationId]);
  const [actionError, setActionError] = useState<unknown>(null);
  const anyRunning = runs?.some((r) => r.status === "running");

  useEffect(
    () =>
      stream.subscribe((e) => {
        if (e.event.endsWith(".finished") && (e.event.startsWith("scan") || e.event.startsWith("agent"))) reload();
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [investigationId],
  );

  useEffect(() => {
    if (!anyRunning) return;
    const t = window.setInterval(reload, 3000);
    return () => window.clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [anyRunning]);

  async function cancel(id: string) {
    try {
      await api.post(`/agent-runs/${id}/cancel`);
      reload();
    } catch (e) {
      setActionError(e);
    }
  }

  return (
    <div className="scroll-thin min-h-0 flex-1 overflow-y-auto px-5 py-4">
      {error ? <ErrorBox error={error} /> : null}
      <ErrorBox error={actionError} onDismiss={() => setActionError(null)} />
      {runs?.length === 0 && <Empty title="No runs yet">Adding a target starts a scan. Claude runs show up here too.</Empty>}
      {runs && runs.length > 0 && (
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
            {runs.map((r) => (
              <tr key={r.id} className="border-b border-line/60 align-top">
                <td className="py-2 pr-4">
                  <div className="font-medium">{KIND[r.kind] || r.kind}</div>
                  <Mono className="text-muted">{r.id}</Mono>
                  {r.goal && <div className="mt-0.5 max-w-xs text-xs text-muted">{r.goal}</div>}
                </td>
                <td className="py-2 pr-4"><Badge value={r.status} /></td>
                <td className="py-2 pr-4 text-muted">{when(r.started_at)}</td>
                <td className="py-2 pr-4 text-muted">{duration(r)}</td>
                <td className="py-2 pr-4">
                  <div>{r.summary || "—"}</div>
                  {r.stats && Object.keys(r.stats).length > 0 && (
                    <div className="mt-0.5 flex flex-wrap gap-x-3 text-2xs text-muted">
                      {Object.entries(r.stats).map(([k, v]) => <span key={k}>{k.replace(/_/g, " ")} {v}</span>)}
                    </div>
                  )}
                </td>
                <td className="py-2 text-right">
                  {r.status === "running" && (
                    <button className="btn btn-danger px-2 py-0.5 text-xs" onClick={() => cancel(r.id)}>Stop</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
