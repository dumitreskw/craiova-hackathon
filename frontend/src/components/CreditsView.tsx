import { useMemo } from "react";
import { ExternalLink, Gauge, RefreshCw } from "lucide-react";
import { api } from "../api";
import { readCredit, useCredits } from "../credits";
import type { Connector, CreditEntry } from "../types";
import { ago, Empty, ErrorBox, Loading, Meter, Pill, Spinner, useLoad, type Tone } from "./ui";

const STATUS_PILL: Record<CreditEntry["status"], [Tone, string]> = {
  ok: ["ok", "Checked"],
  unavailable: ["muted", "Not configured"],
  error: ["danger", "Check failed"],
};

const COST: Record<string, [Tone, string, string]> = {
  free: ["muted", "Free", "No quota or fee"],
  metered: ["warn", "Monthly quota", "A fixed number of calls each month"],
  paid: ["danger", "Paid", "Each call spends purchased credits"],
};

export function CreditsView() {
  const credits = useCredits();
  const [connectors, connError] = useLoad(() => api.get<Connector[]>("/connectors"), []);

  const ordered = useMemo(() => {
    const rank = { ok: 0, error: 1, unavailable: 2 };
    return [...(credits.data || [])].sort((a, b) => rank[a.status] - rank[b.status] || a.label.localeCompare(b.label));
  }, [credits.data]);

  const costly = (connectors || []).filter((c) => c.cost && c.cost !== "free");
  const checked = credits.data?.find((c) => c.checked_at)?.checked_at;

  return (
    <div className="scroll-thin min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-5xl px-6 py-8">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="text-[26px] font-semibold leading-tight tracking-tight">Sources and credits</h1>
            <p className="mt-2 max-w-prose text-muted">
              What each paid or quota-limited source has left. Balances come from the provider's own account
              endpoint where it has one; otherwise from what this server has spent.
            </p>
          </div>
          <div className="flex items-center gap-3">
            {checked && <span className="text-xs text-muted">Checked {ago(checked)}</span>}
            <button className="btn" onClick={credits.refresh} disabled={credits.loading}>
              {credits.loading ? <Spinner /> : <RefreshCw className="h-4 w-4" aria-hidden />}
              Check balances now
            </button>
          </div>
        </div>

        <div className="mt-6">
          <ErrorBox error={credits.error} />
          {credits.missing && (
            <Empty title="Balances aren't available on this server yet" icon={<Gauge className="h-8 w-8" />}>
              This server has no credits endpoint. Quotas below come from the source list instead.
            </Empty>
          )}
          {!credits.data && credits.loading && <Loading>Asking each provider for its balance</Loading>}
          {ordered.length > 0 && (
            <ul className="divide-y divide-line overflow-hidden rounded-lg border border-line bg-panel">
              {ordered.map((c) => <CreditRow key={c.source} c={c} />)}
            </ul>
          )}
        </div>

        <h2 className="mt-10 text-lg font-semibold">Sources that cost something</h2>
        <p className="mt-1 max-w-prose text-sm text-muted">
          Scans and Claude runs only use these when the investigation's source setting allows it. Quotas are shared by
          everyone on this server.
        </p>
        <ErrorBox error={connError} />
        {connectors && (
          <table className="mt-4 w-full border-collapse text-sm">
            <thead>
              <tr className="border-b border-line text-left text-xs text-muted">
                <th className="py-2 pr-4 font-medium">Source</th>
                <th className="py-2 pr-4 font-medium">Cost</th>
                <th className="py-2 pr-4 font-medium">Used this month</th>
                <th className="py-2 font-medium">Status</th>
              </tr>
            </thead>
            <tbody>
              {costly.map((c) => {
                const [tone, label, title] = COST[c.cost || "free"];
                return (
                  <tr key={c.name} className="border-b border-line/60 align-top">
                    <td className="py-2.5 pr-4">
                      <div className="font-medium">{c.name}</div>
                      <div className="max-w-md text-xs text-muted">{c.description}</div>
                    </td>
                    <td className="py-2.5 pr-4"><Pill tone={tone} title={title}>{label}</Pill></td>
                    <td className="w-48 py-2.5 pr-4">
                      {c.quota ? (
                        <div className="space-y-1">
                          <span className="text-xs">{c.quota_used ?? 0} of {c.quota}</span>
                          <Meter fraction={(c.quota_used ?? 0) / c.quota} tone={(c.quota_used ?? 0) / c.quota > 0.8 ? "warn" : "accent"}
                            label={`${c.name} quota used`} />
                        </div>
                      ) : (
                        <span className="text-xs text-muted">{c.cost === "paid" ? "Per call, see balance above" : "—"}</span>
                      )}
                    </td>
                    <td className="py-2.5">
                      {c.available === false || c.key_configured === false
                        ? <Pill>No API key</Pill>
                        : <Pill tone="ok">Ready</Pill>}
                    </td>
                  </tr>
                );
              })}
              {costly.length === 0 && (
                <tr><td colSpan={4} className="py-4 text-muted">Every configured source is free.</td></tr>
              )}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

function CreditRow({ c }: { c: CreditEntry }) {
  const r = readCredit(c);
  const [tone, status] = STATUS_PILL[c.status];
  const timing = [c.period && `per ${c.period}`, c.resets && `resets ${new Date(c.resets).toLocaleDateString(undefined, { dateStyle: "medium" })}`]
    .filter(Boolean).join(", ");
  return (
    <li className="grid gap-x-6 gap-y-2 px-5 py-4 sm:grid-cols-[minmax(0,14rem)_minmax(0,1fr)_auto]">
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <span className="font-medium">{c.label}</span>
          <Pill tone={tone}>{status}</Pill>
        </div>
        {timing && <div className="mt-0.5 text-xs text-muted">{timing}</div>}
      </div>
      <div className="min-w-0 space-y-1.5">
        <div className={`text-sm ${r.tone === "danger" ? "text-danger" : r.tone === "warn" ? "text-warn" : ""}`}>{r.headline}</div>
        {r.left !== null && <Meter fraction={r.left} tone={r.tone} label={`${c.label} balance left`} />}
        {(c.note && c.status === "ok") || c.status === "error" ? <p className="text-xs text-muted">{c.note}</p> : null}
        {typeof c.platform_used_30d === "number" && (
          <p className="text-xs text-muted">{c.platform_used_30d.toLocaleString()} calls from this server in the last 30 days</p>
        )}
      </div>
      <div className="flex items-start">
        {c.dashboard_url && (
          <a className="btn btn-quiet btn-sm" href={c.dashboard_url} target="_blank" rel="noreferrer">
            Dashboard <ExternalLink className="h-3.5 w-3.5" aria-hidden />
          </a>
        )}
      </div>
    </li>
  );
}

/** Three-line balance summary for the sidebar. */
export function CreditsSummary() {
  const credits = useCredits();
  const rows = (credits.data || []).filter((c) => c.status === "ok").map((c) => ({ c, r: readCredit(c) }));
  const watch = rows
    .filter(({ r }) => r.left !== null || r.tone === "danger")
    .sort((a, b) => (a.r.left ?? 0) - (b.r.left ?? 0))
    .slice(0, 3);
  return (
    <a href="#/sources" className="block rounded-md px-2.5 py-2 hover:bg-raised">
      <div className="flex items-center justify-between text-sm font-medium">
        <span className="flex items-center gap-2"><Gauge className="h-4 w-4 text-muted" aria-hidden />Sources and credits</span>
        {credits.loading && <Spinner className="h-3.5 w-3.5 text-muted" />}
      </div>
      {credits.missing && <p className="mt-1 text-xs text-muted">Balances not available</p>}
      {watch.length > 0 && (
        <ul className="mt-2 space-y-1.5">
          {watch.map(({ c, r }) => (
            <li key={c.source}>
              <div className="flex items-baseline justify-between gap-2 text-xs">
                <span className="truncate text-muted">{c.label}</span>
                <span className={r.tone === "danger" ? "text-danger" : r.tone === "warn" ? "text-warn" : "text-ink"}>
                  {r.left !== null ? `${Math.round(r.left * 100)}% left` : "empty"}
                </span>
              </div>
              {r.left !== null && <div className="mt-1"><Meter fraction={r.left} tone={r.tone} label={`${c.label} balance left`} /></div>}
            </li>
          ))}
        </ul>
      )}
    </a>
  );
}
