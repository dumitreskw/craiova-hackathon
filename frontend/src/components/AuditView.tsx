import { useMemo, useState } from "react";
import { RefreshCw, ScrollText } from "lucide-react";
import { api, qs } from "../api";
import type { AuditEvent } from "../types";
import { Badge, Empty, ErrorBox, Loading, Mono, Segmented, useLoad, when } from "./ui";

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
    <div className="scroll-thin min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-6xl px-6 py-5">
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <p className="mr-auto max-w-prose text-sm text-muted">
          Every action and lookup in this investigation, newest first. Entries cannot be edited or deleted.
        </p>
        <Segmented label="Show" value={filter} onChange={setFilter}
          options={[["all", "Everything"], ["people", "Analyst actions"], ["refused", "Blocked or failed"]]} />
        <button className="btn btn-sm" onClick={reload} disabled={loading}><RefreshCw className="h-3.5 w-3.5" aria-hidden />Refresh</button>
      </div>
      {error ? <ErrorBox error={error} /> : null}
      {!rows && !error && <Loading>Loading the audit log</Loading>}
      {rows && shown.length === 0 && <Empty title="Nothing matches this filter" icon={<ScrollText className="h-8 w-8" />} />}
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
                <td className="whitespace-nowrap py-2 pr-4 text-muted">{when(r.ts)}</td>
                <td className="py-2 pr-4">{r.user}</td>
                <td className="py-2 pr-4">
                  {r.action}
                  {r.connector && <span className="text-muted"> ({r.connector})</span>}
                </td>
                <td className="max-w-[16rem] truncate py-2 pr-4"><Mono>{r.target || "—"}</Mono></td>
                <td className="py-2 pr-4"><Badge value={r.outcome} /></td>
                <td className="py-2">
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
    </div>
  );
}
