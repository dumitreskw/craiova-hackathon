import { useEffect, useMemo, useState, type FormEvent } from "react";
import {
  Activity, Bot, FileText, ListChecks, Network, Paperclip, Plus, RotateCw, ScrollText, Users, X, type LucideIcon,
} from "lucide-react";
import { api } from "../api";
import { ENTITY_TYPES } from "../entityStyle";
import { useInvestigationStream, type InvestigationStream } from "../stream";
import type { Investigation } from "../types";
import { ActivityFeed } from "./ActivityFeed";
import { AgentsPanel } from "./AgentsPanel";
import { AuditView } from "./AuditView";
import { EntityPanel } from "./EntityPanel";
import { GraphView } from "./GraphView";
import { MaterialsView } from "./MaterialsView";
import { ReportsView } from "./ReportsView";
import { RunsView } from "./RunsView";
import { SocialView } from "./SocialView";
import {
  EMPTY_DRAFT, IdentityStrip, ProfileFields, draftIsEmpty, looksLikePerson, profileFrom, profileValid, type ProfileDraft,
} from "./SubjectIdentity";
import { Badge, ErrorBoundary, ErrorBox, Loading, useLoad } from "./ui";

export const TABS = ["graph", "material", "social", "activity", "agents", "runs", "reports", "audit"] as const;
export type Tab = (typeof TABS)[number];

const TAB_META: Record<Tab, [string, LucideIcon]> = {
  graph: ["Graph", Network],
  material: ["Material", Paperclip],
  social: ["Social", Users],
  activity: ["Activity", Activity],
  agents: ["Claude agents", Bot],
  runs: ["Runs", ListChecks],
  reports: ["Reports", FileText],
  audit: ["Audit log", ScrollText],
};

export function Workspace({ id, tab, onChanged }: { id: string; tab: Tab; onChanged: () => void }) {
  const [inv, error, , reload] = useLoad(() => api.get<Investigation>(`/investigations/${id}`), [id]);
  const stream = useInvestigationStream(id);
  const [selected, setSelected] = useState<string | null>(null);

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

  // Claude runs in flight, from the live stream: drives the dot on the tab.
  const agentsRunning = useMemo(() => {
    const live = new Set<string>();
    for (const e of stream.events) {
      const run = String(e.data.run_id ?? "");
      if (e.event === "agent.started") live.add(run);
      else if (e.event === "agent.finished" || e.event === "agent.failed" || e.event === "agent.refused") live.delete(run);
    }
    return live.size;
  }, [stream.events]);
  const problems = useMemo(
    () => stream.events.filter((e) => e.event === "tool.failed" || e.event === "tool.refused").length,
    [stream.events],
  );

  function select(entityId: string | null) {
    setSelected(entityId);
  }

  function openEntity(entityId: string) {
    setSelected(entityId);
    if (tab !== "graph") window.location.hash = `/inv/${id}/graph`;
  }

  if (error) return <div className="p-6"><ErrorBox error={error} /></div>;
  if (!inv) return <Loading>Opening investigation</Loading>;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="border-b border-line bg-panel px-5 pt-3.5">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
          <h1 className="text-[20px] font-semibold leading-tight tracking-tight">{inv.name}</h1>
          <Badge value={inv.mode} label={inv.mode === "active" ? "Active allowed" : "Passive only"}
            title={inv.mode === "active" ? "Active collection" : "Public sources only"} />
          <span className="text-xs text-muted">
            Pivot depth {inv.settings?.max_depth ?? 1}, kept{" "}
            {inv.retention_days} days{inv.legal_hold ? ", legal hold" : ""}
          </span>
          <span className="ml-auto flex items-center gap-4 text-xs text-muted">
            {inv.stats && (
              <span>
                <b className="font-semibold text-ink">{inv.stats.entities.toLocaleString()}</b> entities,{" "}
                <b className="font-semibold text-ink">{inv.stats.edges.toLocaleString()}</b> links,{" "}
                <b className="font-semibold text-ink">{inv.stats.findings.toLocaleString()}</b> lookups
              </span>
            )}
            <span className="flex items-center gap-1.5" role="status">
              <span className={`h-2 w-2 rounded-full ${stream.connected ? "bg-ok" : "animate-pulse bg-danger"}`} aria-hidden />
              {stream.connected ? "Live" : "Reconnecting"}
            </span>
          </span>
        </div>
        <p className="mt-1 max-w-[90ch] truncate text-sm text-muted" title={inv.purpose}>{inv.purpose}</p>
        <TargetBar inv={inv} stream={stream} onAdded={() => { reload(); onChanged(); }} />
        <nav className="-mb-px mt-3 flex gap-1 overflow-x-auto" aria-label="Investigation views">
          {TABS.map((t) => {
            const [label, Icon] = TAB_META[t];
            const current = tab === t;
            return (
              <a key={t} href={`#/inv/${id}/${t}`} aria-current={current ? "page" : undefined}
                className={`flex shrink-0 items-center gap-2 border-b-2 px-3 py-2.5 text-sm transition-colors duration-150 ${
                  current ? "border-accent font-semibold text-ink" : "border-transparent text-muted hover:border-line hover:text-ink"}`}>
                <Icon className="h-4 w-4" aria-hidden />
                {label}
                {t === "agents" && agentsRunning > 0 && (
                  <span className="rounded-full bg-accent px-1.5 text-2xs font-semibold text-accent-ink" title="Claude runs in progress">
                    {agentsRunning}
                  </span>
                )}
                {t === "activity" && problems > 0 && (
                  <span className="rounded-full bg-danger/15 px-1.5 text-2xs font-semibold text-danger" title="Failed or blocked lookups">
                    {problems}
                  </span>
                )}
              </a>
            );
          })}
        </nav>
      </header>

      <ErrorBoundary key={tab}>
      {tab === "graph" && (
        <div className="flex min-h-0 flex-1">
          <div className="relative min-w-0 flex-1">
            <GraphView investigationId={id} seedIds={(inv.targets || []).map((t) => t.entity_id)} stream={stream}
              selected={selected} onSelect={select} />
          </div>
          {selected && (
            <aside className="flex w-[400px] shrink-0 flex-col border-l border-line bg-panel max-lg:w-[340px]" aria-label="Entity details">
              <div className="flex items-center justify-between border-b border-line px-4 py-2">
                <span className="text-sm font-medium text-muted">Entity</span>
                <button className="icon-btn h-8 w-8" onClick={() => setSelected(null)} aria-label="Close entity panel">
                  <X className="h-4 w-4" />
                </button>
              </div>
              <div className="scroll-thin min-h-0 flex-1 overflow-y-auto">
                <EntityPanel entityId={selected} investigationId={id} onSelect={select} />
              </div>
            </aside>
          )}
        </div>
      )}
      {tab === "activity" && <ActivityFeed events={stream.events} onSelect={openEntity} />}
      {tab === "agents" && <AgentsPanel investigation={inv} stream={stream} />}
      {tab === "material" && <MaterialsView investigationId={id} stream={stream} />}
      {tab === "social" && <SocialView investigationId={id} stream={stream} />}
      {tab === "runs" && <RunsView investigationId={id} stream={stream} />}
      {tab === "reports" && <ReportsView investigationId={id} stream={stream} onOpenEntity={openEntity} />}
      {tab === "audit" && <AuditView investigationId={id} />}
      </ErrorBoundary>
    </div>
  );
}

function TargetBar({ inv, stream, onAdded }: { inv: Investigation; stream: InvestigationStream; onAdded: () => void }) {
  const [value, setValue] = useState("");
  const [type, setType] = useState("auto");
  const [draft, setDraft] = useState<ProfileDraft>(EMPTY_DRAFT);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  // A person target gets the profile form: what tells them apart from namesakes.
  const person = type === "Person" || (type === "auto" && looksLikePerson(value));

  async function add(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const profile = person && !draftIsEmpty(draft) ? profileFrom(draft) : undefined;
      const r = await api.post<{ type: string; key: string; run_id: string | null; identifier_entity_ids?: string[] }>(
        `/investigations/${inv.id}/targets`, { value: value.trim(), type, scan: true, ...(profile ? { profile } : {}) });
      const extra = r.identifier_entity_ids?.length ?? 0;
      setNotice(`Added ${r.type} ${r.key}${extra ? ` and ${extra} known ${extra === 1 ? "identifier" : "identifiers"}` : ""}. Scanning.`);
      setValue("");
      setDraft(EMPTY_DRAFT);
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

  const targets = inv.targets || [];

  return (
    <div className="mt-3">
      <form onSubmit={add} className="flex flex-wrap items-center gap-2">
        <span className="mr-1 text-xs font-medium text-muted">Targets</span>
        {targets.map((t) => (
          <span key={t.id} className="inline-flex items-center gap-1.5 rounded-md border border-accent/40 bg-accent/10 px-2 py-1 text-xs">
            <span className="text-muted">{t.type}</span>
            <span className="font-mono text-[13px] text-ink">{t.value}</span>
          </span>
        ))}
        <div className="flex min-w-[18rem] flex-1 items-center gap-2">
          <input className="field max-w-sm font-mono" value={value} onChange={(e) => setValue(e.target.value)}
            placeholder="example.com, jane@example.com, @handle" aria-label="New target" />
          <select className="field w-auto" value={type} onChange={(e) => setType(e.target.value)} aria-label="Target type">
            <option value="auto">Detect type</option>
            {ENTITY_TYPES.filter((t) => !["Certificate", "Breach", "Vulnerability", "ASN"].includes(t)).map((t) => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
          <button className="btn" disabled={busy || !value.trim() || (person && !profileValid(draft))}>
            <Plus className="h-4 w-4" aria-hidden />{busy ? "Adding" : "Add and scan"}
          </button>
          {targets.length > 0 && (
            <button type="button" className="btn btn-quiet" onClick={rescan} title="Run the quick scan on every target again">
              <RotateCw className="h-4 w-4" aria-hidden />Scan again
            </button>
          )}
        </div>
        {notice && !error && <span className="text-xs text-muted" role="status">{notice}</span>}
        {person && value.trim() && (
          <div className="basis-full">
          <fieldset className="mt-1 max-w-3xl rounded-lg border border-line bg-raised/40 p-3">
            <legend className="px-1 text-xs font-medium text-muted">
              Who is {value.trim()}? Optional, but a common name needs at least one detail.
            </legend>
            <ProfileFields draft={draft} onChange={setDraft} idPrefix="new-target" />
          </fieldset>
          </div>
        )}
      </form>
      <IdentityStrip inv={inv} targets={targets} stream={stream} onSaved={onAdded} />
      {error ? <div className="mt-2"><ErrorBox error={error} onDismiss={() => setError(null)} /></div> : null}
    </div>
  );
}
