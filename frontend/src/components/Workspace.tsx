import { useEffect, useState, type FormEvent } from "react";
import { api } from "../api";
import { ENTITY_TYPES } from "../entityStyle";
import { useInvestigationStream } from "../stream";
import type { Investigation } from "../types";
import { ActivityFeed } from "./ActivityFeed";
import { AgentsPanel } from "./AgentsPanel";
import { AuditView } from "./AuditView";
import { EntityPanel } from "./EntityPanel";
import { GraphView } from "./GraphView";
import { ReportsView } from "./ReportsView";
import { RunsView } from "./RunsView";
import { Badge, ErrorBox, useLoad } from "./ui";

export type Tab = "graph" | "runs" | "reports" | "audit";
type Side = "activity" | "entity" | "claude";

const BASIS_LABEL: Record<string, string> = {
  legitimate_interest: "Legitimate interest",
  contract: "Contract",
  legal_obligation: "Legal obligation",
  consent: "Consent",
  public_task: "Public task",
  vital_interests: "Vital interests",
};

export function Workspace({ id, tab, onChanged }: { id: string; tab: Tab; onChanged: () => void }) {
  const [inv, error, , reload] = useLoad(() => api.get<Investigation>(`/investigations/${id}`), [id]);
  const stream = useInvestigationStream(id);
  const [selected, setSelected] = useState<string | null>(null);
  const [side, setSide] = useState<Side>("activity");

  // Keep counts in the header and the sidebar fresh when a run ends.
  useEffect(
    () =>
      stream.subscribe((e) => {
        if (e.event === "scan.finished" || e.event === "agent.finished") {
          reload();
          onChanged();
        }
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [id],
  );

  function select(entityId: string | null) {
    setSelected(entityId);
    if (entityId) setSide("entity");
  }

  if (error) return <div className="p-6"><ErrorBox error={error} /></div>;
  if (!inv) return <div className="p-6 text-muted">Loading investigation…</div>;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="border-b border-line bg-panel px-5 pb-0 pt-3">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <h1 className="text-lg font-semibold tracking-tight">{inv.name}</h1>
          <Badge value={inv.mode} title={inv.mode === "active" ? `Scope: ${inv.scope.join(", ")}` : "Public sources only"} />
          <span className="text-xs text-muted">{BASIS_LABEL[inv.legal_basis] || inv.legal_basis}</span>
          <span className="text-xs text-muted">pivot depth {inv.settings?.max_depth ?? 1}</span>
          <span className="text-xs text-muted">kept {inv.retention_days} days{inv.legal_hold ? ", legal hold" : ""}</span>
          <span className="ml-auto flex items-center gap-1.5 text-xs text-muted" title="Live updates">
            <span className={`h-2 w-2 rounded-full ${stream.connected ? "bg-ok" : "bg-danger"}`} />
            {stream.connected ? "Live" : "Reconnecting"}
          </span>
        </div>
        <p className="mt-1 max-w-[80ch] truncate text-sm text-muted" title={inv.purpose}>{inv.purpose}</p>
        <TargetBar inv={inv} onAdded={() => { reload(); onChanged(); }} />
        <nav className="mt-2 flex gap-1" aria-label="Investigation views">
          {(["graph", "runs", "reports", "audit"] as Tab[]).map((t) => (
            <a key={t} href={`#/inv/${id}/${t}`}
              className={`border-b-2 px-3 py-2 text-sm capitalize ${
                tab === t ? "border-accent font-medium text-ink" : "border-transparent text-muted hover:text-ink"}`}>
              {t}
            </a>
          ))}
          {inv.stats && (
            <span className="ml-auto self-center text-xs text-muted">
              {inv.stats.entities} entities, {inv.stats.edges} links, {inv.stats.findings} lookups
            </span>
          )}
        </nav>
      </header>

      {tab === "graph" && (
        <div className="flex min-h-0 flex-1">
          <div className="relative min-w-0 flex-1">
            <GraphView investigationId={id} seedIds={(inv.targets || []).map((t) => t.entity_id)} stream={stream}
              selected={selected} onSelect={select} />
          </div>
          <aside className="flex w-[380px] shrink-0 flex-col border-l border-line bg-panel max-lg:w-[320px]">
            <div className="flex border-b border-line" role="tablist">
              {([
                ["activity", "Activity"],
                ["entity", "Entity"],
                ["claude", "Claude"],
              ] as [Side, string][]).map(([key, label]) => (
                <button key={key} role="tab" aria-selected={side === key} onClick={() => setSide(key)}
                  className={`flex-1 border-b-2 px-3 py-2 text-sm ${
                    side === key ? "border-accent font-medium" : "border-transparent text-muted hover:text-ink"}`}>
                  {label}
                </button>
              ))}
            </div>
            <div className="scroll-thin min-h-0 flex-1 overflow-y-auto">
              {side === "activity" && <ActivityFeed events={stream.events} onSelect={select} />}
              {side === "entity" &&
                (selected ? (
                  <EntityPanel entityId={selected} investigationId={id} onSelect={select} />
                ) : (
                  <p className="p-4 text-sm text-muted">Select a node in the graph to see where it came from.</p>
                ))}
              {side === "claude" && <AgentsPanel investigation={inv} events={stream.events} />}
            </div>
          </aside>
        </div>
      )}
      {tab === "runs" && <RunsView investigationId={id} stream={stream} />}
      {tab === "reports" && <ReportsView investigationId={id} />}
      {tab === "audit" && <AuditView investigationId={id} />}
    </div>
  );
}

function TargetBar({ inv, onAdded }: { inv: Investigation; onAdded: () => void }) {
  const [value, setValue] = useState("");
  const [type, setType] = useState("auto");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  async function add(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await api.post<{ type: string; key: string; run_id: string | null }>(
        `/investigations/${inv.id}/targets`, { value: value.trim(), type, scan: true });
      setNotice(`Added ${r.type} ${r.key}; scanning.`);
      setValue("");
      onAdded();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  async function rescan() {
    setError(null);
    try {
      await api.post(`/investigations/${inv.id}/scan`, {});
      setNotice("Scanning every target again.");
    } catch (err) {
      setError(err);
    }
  }

  return (
    <div className="mt-3">
      <form onSubmit={add} className="flex flex-wrap items-center gap-2">
        <input className="field max-w-md flex-1 font-mono" value={value} onChange={(e) => setValue(e.target.value)}
          placeholder="example.com, jane@example.com, @handle, RO12345678, Jane Doe" aria-label="Target" />
        <select className="field w-auto" value={type} onChange={(e) => setType(e.target.value)} aria-label="Target type">
          <option value="auto">Detect type</option>
          {ENTITY_TYPES.filter((t) => !["Certificate", "Breach", "Vulnerability", "ASN"].includes(t)).map((t) => (
            <option key={t} value={t}>{t}</option>
          ))}
        </select>
        <button className="btn btn-primary" disabled={busy || !value.trim()}>
          {busy ? "Adding…" : "Add target and scan"}
        </button>
        {(inv.targets?.length ?? 0) > 0 && (
          <button type="button" className="btn" onClick={rescan}>Scan all targets again</button>
        )}
        {notice && !error && <span className="text-xs text-muted">{notice}</span>}
      </form>
      {(inv.targets?.length ?? 0) > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {inv.targets!.map((t) => (
            <span key={t.id} className="rounded border border-line bg-ground px-2 py-0.5 text-xs">
              <span className="text-muted">{t.type}</span> <span className="font-mono">{t.value}</span>
            </span>
          ))}
        </div>
      )}
      {error ? <div className="mt-2"><ErrorBox error={error} onDismiss={() => setError(null)} /></div> : null}
    </div>
  );
}
