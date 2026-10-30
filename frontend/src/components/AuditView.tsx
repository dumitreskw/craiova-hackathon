import { useMemo, useState } from "react";
import { api, qs } from "../api";
import type { AuditEvent } from "../types";
import { Badge, Empty, ErrorBox, Mono, useLoad, when } from "./ui";

export function AuditView({ investigationId }: { investigationId: string }) {
  const [rows, error, loading, reload] = useLoad(
    () => api.get<AuditEvent[]>(`/audit${qs({ investigation_id: investigationId, limit: 2000 })}`),
    [investigationId],
  );
  const [filter, setFilter] = useState<"all" | "people" | "refused">("all");

  const shown = useMemo(() => {
    if (!rows) return [];
    if (filter === "refused") return rows.filter((r) => r.outcome !== "ok");
    if (filter === "people") return rows.filter((r) => r.action !== "connector.call");
    return rows;
  }, [rows, filter]);

  return (
    <div className="scroll-thin min-h-0 flex-1 overflow-y-auto px-5 py-4">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <p className="mr-auto max-w-prose text-sm text-muted">
          Every action and lookup in this investigation, newest first. Entries cannot be edited or deleted.
        </p>
        <div className="flex rounded-md border border-line p-0.5 text-xs" role="group" aria-label="Filter">
          {([
            ["all", "Everything"],
            ["people", "Analyst actions"],
            ["refused", "Blocked or failed"],
          ] as const).map(([k, label]) => (
            <button key={k} onClick={() => setFilter(k)} aria-pressed={filter === k}
              className={`rounded px-2.5 py-1 ${filter === k ? "bg-raised text-ink" : "text-muted hover:text-ink"}`}>
              {label}
            </button>
          ))}
        </div>
        <button className="btn px-2.5 py-1 text-xs" onClick={reload} disabled={loading}>Refresh</button>
      </div>
      {error ? <ErrorBox error={error} /> : null}
      {rows && shown.length === 0 && <Empty title="Nothing matches this filter" />}
      {shown.length > 0 && (
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="border-b border-line text-left text-xs text-muted">
              <th className="py-2 pr-4 font-medium">When</th>
              <th className="py-2 pr-4 font-medium">Who</th>
              <th className="py-2 pr-4 font-medium">Action</th>
              <th className="py-2 pr-4 font-medium">Target</th>
              <th className="py-2 pr-4 font-medium">Outcome</th>
              <th className="py-2 font-medium">Detail</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((r) => (
              <tr key={r.id} className="border-b border-line/60 align-top">
                <td className="whitespace-nowrap py-1.5 pr-4 text-muted">{when(r.ts)}</td>
                <td className="py-1.5 pr-4">{r.user}</td>
                <td className="py-1.5 pr-4">
                  {r.action}
                  {r.connector && <span className="text-muted"> ({r.connector})</span>}
                </td>
                <td className="max-w-[16rem] truncate py-1.5 pr-4"><Mono>{r.target || "—"}</Mono></td>
                <td className="py-1.5 pr-4"><Badge value={r.outcome} /></td>
                <td className="py-1.5">
                  <Mono className="line-clamp-2 break-all text-muted">
                    {typeof r.detail?.reason === "string" ? r.detail.reason : JSON.stringify(r.detail)}
                  </Mono>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
