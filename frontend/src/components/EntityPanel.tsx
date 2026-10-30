import { useState, type FormEvent, type ReactNode } from "react";
import {
  Camera, Check, FileSearch, Link2, NotebookPen, Search, ShieldAlert, Users, X,
} from "lucide-react";
import { api } from "../api";
import { typeColor } from "../entityStyle";
import type { Connector, EntityDetail, Finding } from "../types";
import { Badge, ErrorBox, JsonView, Loading, Meter, Modal, Mono, shortHash, Spinner, useLoad, when } from "./ui";

function fmt(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "string") return v;
  return JSON.stringify(v);
}

function Section({ title, count, children }: { title: string; count?: number; children: ReactNode }) {
  return (
    <section>
      <h3 className="mb-2 flex items-baseline gap-2 text-sm font-semibold">
        {title}
        {count !== undefined && <span className="text-xs font-normal text-muted">{count}</span>}
      </h3>
      {children}
    </section>
  );
}

export function EntityPanel({
  entityId,
  investigationId,
  onSelect,
}: {
  entityId: string;
  investigationId: string;
  onSelect: (id: string) => void;
}) {
  const [ent, error, loading, reload] = useLoad(() => api.get<EntityDetail>(`/entities/${entityId}`), [entityId]);
  const [connectors] = useLoad(() => api.get<Connector[]>("/connectors"), []);
  const [finding, setFinding] = useState<string | null>(null);
  const [action, setAction] = useState<"confirm" | "reject" | "pivot" | "note" | null>(null);
  const [busy, setBusy] = useState<"read" | "capture" | "social" | null>(null);
  const [actionError, setActionError] = useState<unknown>(null);

  async function urlAction(kind: "read" | "capture", url: string, id: string) {
    setBusy(kind);
    setActionError(null);
    try {
      await api.post(`/investigations/${investigationId}/${kind}`, { url, entity_id: id });
      reload();
    } catch (err) {
      setActionError(err);
    } finally {
      setBusy(null);
    }
  }

  async function captureProfile(url: string, id: string) {
    setBusy("social");
    setActionError(null);
    try {
      await api.post(`/investigations/${investigationId}/social/capture`, { url, entity_id: id });
      reload();
    } catch (err) {
      setActionError(err);
    } finally {
      setBusy(null);
    }
  }

  if (error) return <div className="p-4"><ErrorBox error={error} /></div>;
  if (!ent) return loading ? <Loading>Loading entity</Loading> : null;

  const candidate = ent.status === "candidate";
  const applicable = (connectors || []).filter((c) => c.accepts === ent.type);
  const disambiguators = ent.attributes.disambiguators as Record<string, unknown> | undefined;
  const screenshotFinding = ent.type === "Document" && ent.attributes.kind === "screenshot"
    ? ent.sources.find((s) => s.connector === "screenshot")?.id
    : undefined;
  const isUrl = ent.type === "URL" && /^https?:\/\//.test(ent.key);

  return (
    <div className="space-y-6 p-5">
      <div>
        <div className="flex flex-wrap items-center gap-2 text-sm text-muted">
          <span className={`h-3 w-3 rounded-full ${candidate ? "ring-2 ring-danger ring-offset-2 ring-offset-panel" : ""}`}
            style={{ background: typeColor(ent.type) }} aria-hidden />
          {ent.type}
          <Badge value={ent.status} />
        </div>
        <h2 className="mt-2 break-words text-lg font-semibold leading-snug">{ent.label}</h2>
        {ent.label !== ent.key && <Mono className="mt-0.5 block break-all text-muted">{ent.key}</Mono>}
        <div className="mt-4">
          <div className="mb-1.5 flex justify-between text-xs">
            <span className="text-muted">Confidence</span>
            <span className="font-mono">{ent.confidence.toFixed(2)}</span>
          </div>
          <Meter fraction={ent.confidence} tone={candidate ? "danger" : "accent"} label="Confidence" />
          <p className="mt-1.5 text-xs text-muted">
            From {ent.sources.length} source{ent.sources.length === 1 ? "" : "s"}; first seen {when(ent.first_seen)}.
          </p>
        </div>
      </div>

      {candidate && (
        <div className="rounded-md border border-dashed border-danger/70 bg-danger/5 p-3.5 text-sm">
          <p className="flex items-center gap-2 font-medium text-danger"><ShieldAlert className="h-4 w-4" aria-hidden />Name match only, not confirmed</p>
          <p className="mt-1.5 text-muted">
            This listing shares a name with your subject, and many people share names. Confirm it only if a detail
            such as date of birth, nationality, a photo or an ID number matches.
          </p>
          {disambiguators && Object.keys(disambiguators).length > 0 && (
            <dl className="mt-2.5 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
              {Object.entries(disambiguators).map(([k, v]) => (
                <div key={k} className="contents">
                  <dt className="text-muted">{k.replace(/_/g, " ")}</dt>
                  <dd className="font-mono">{fmt(v)}</dd>
                </div>
              ))}
            </dl>
          )}
        </div>
      )}

      <div className="flex flex-wrap gap-2">
        {ent.status !== "confirmed" && (
          <button className="btn btn-sm" onClick={() => setAction("confirm")}><Check className="h-3.5 w-3.5" aria-hidden />Confirm</button>
        )}
        {ent.status !== "rejected" && (
          <button className="btn btn-danger btn-sm" onClick={() => setAction("reject")}><X className="h-3.5 w-3.5" aria-hidden />Reject</button>
        )}
        {!candidate && ent.status !== "rejected" && applicable.length > 0 && (
          <button className="btn btn-sm" onClick={() => setAction("pivot")}><Search className="h-3.5 w-3.5" aria-hidden />Look up further</button>
        )}
        {isUrl && (
          <>
            <button className="btn btn-sm" disabled={busy !== null} onClick={() => urlAction("read", ent.key, ent.id)}>
              {busy === "read" ? <Spinner className="h-3.5 w-3.5" /> : <FileSearch className="h-3.5 w-3.5" aria-hidden />}Read page
            </button>
            <button className="btn btn-sm" disabled={busy !== null} onClick={() => urlAction("capture", ent.key, ent.id)}>
              {busy === "capture" ? <Spinner className="h-3.5 w-3.5" /> : <Camera className="h-3.5 w-3.5" aria-hidden />}Capture evidence
            </button>
          </>
        )}
        {ent.type === "SocialProfile" && typeof ent.attributes.url === "string" &&
          /^https?:\/\//.test(ent.attributes.url) && (
          <button className="btn btn-sm" disabled={busy !== null}
            onClick={() => captureProfile(ent.attributes.url as string, ent.id)}>
            {busy === "social" ? <Spinner className="h-3.5 w-3.5" /> : <Users className="h-3.5 w-3.5" aria-hidden />}Capture (persona)
          </button>
        )}
        <button className="btn btn-quiet btn-sm" onClick={() => setAction("note")}><NotebookPen className="h-3.5 w-3.5" aria-hidden />Add note</button>
      </div>
      {actionError !== null && <ErrorBox error={actionError} onDismiss={() => setActionError(null)} />}

      {screenshotFinding && (
        <a href={`/api/v1/findings/${screenshotFinding}/screenshot`} target="_blank" rel="noreferrer"
          className="block overflow-hidden rounded-md border border-line">
          <img src={`/api/v1/findings/${screenshotFinding}/screenshot`} alt="Captured page"
            className="max-h-72 w-full object-cover object-top" />
        </a>
      )}

      {(action === "confirm" || action === "reject") && (
        <StatusForm entityId={ent.id} kind={action} candidate={candidate}
          onDone={() => { setAction(null); reload(); }} onCancel={() => setAction(null)} />
      )}
      {action === "pivot" && (
        <PivotForm entityId={ent.id} connectors={applicable} onDone={() => setAction(null)} onCancel={() => setAction(null)} />
      )}
      {action === "note" && (
        <NoteForm entityId={ent.id} onDone={() => { setAction(null); reload(); }} onCancel={() => setAction(null)} />
      )}

      <Section title="Attributes">
        {Object.keys(ent.attribute_sources).length === 0 ? (
          <p className="text-sm text-muted">None recorded.</p>
        ) : (
          <dl className="space-y-2.5 text-sm">
            {Object.entries(ent.attribute_sources).map(([name, values]) => (
              <div key={name}>
                <dt className="text-xs text-muted">
                  {name.replace(/_/g, " ")}
                  {values.length > 1 && <span className="ml-1.5 font-medium text-warn">sources disagree</span>}
                </dt>
                {values.map((v, i) => (
                  <dd key={i} className="flex items-baseline gap-2 break-all">
                    <span>{fmt(v.value)}</span>
                    {values.length > 1 && <span className="shrink-0 text-xs text-muted">{v.sources.length} source(s)</span>}
                  </dd>
                ))}
              </div>
            ))}
          </dl>
        )}
      </Section>

      <Section title="Where this came from" count={ent.sources.length}>
        <ul className="space-y-1.5">
          {ent.sources.map((s) => (
            <li key={s.id}>
              <button onClick={() => setFinding(s.id)}
                className="w-full rounded-md border border-line px-3 py-2 text-left transition-colors duration-150 hover:border-muted/70 hover:bg-raised/50">
                <div className="flex items-baseline justify-between gap-2">
                  <span className="text-sm font-medium">{s.connector}</span>
                  <span className="text-xs text-muted">{when(s.fetched_at)}</span>
                </div>
                <div className="mt-0.5 flex items-baseline justify-between gap-2 text-xs text-muted">
                  <Mono className="truncate text-xs">{String(s.input?.arg ?? "")}</Mono>
                  <Mono className="shrink-0 text-xs">sha256 {shortHash(s.sha256, 8)}</Mono>
                </div>
              </button>
            </li>
          ))}
          {ent.sources.length === 0 && <li className="text-sm text-muted">Added by you as a target; no lookup has returned it yet.</li>}
        </ul>
      </Section>

      <Section title="Links" count={ent.edges.length}>
        <ul className="space-y-1 text-sm">
          {ent.edges.slice(0, 80).map((e) => {
            const outgoing = e.source === ent.id;
            const other = outgoing ? e.target : e.source;
            const label = e.other ? e.other.label : other;
            return (
              <li key={e.id} className="flex items-baseline gap-2">
                <Link2 className={`h-3.5 w-3.5 shrink-0 translate-y-0.5 ${e.type === "possible_match" ? "text-danger" : "text-muted"}`} aria-hidden />
                <span className={`shrink-0 text-xs ${e.type === "possible_match" ? "text-danger" : "text-muted"}`}>
                  {(outgoing ? e.type : `${e.type} from`).replace(/_/g, " ")}
                </span>
                <button className={`min-w-0 truncate text-left hover:underline ${e.other ? "" : "font-mono text-[13px]"}`}
                  title={e.other ? `${e.other.type}: ${e.other.key}` : other} onClick={() => onSelect(other)}>
                  {e.other && <span className="mr-1.5 inline-block h-2 w-2 rounded-full align-middle" style={{ background: typeColor(e.other.type) }} aria-hidden />}
                  {label}
                </button>
              </li>
            );
          })}
          {ent.edges.length > 80 && <li className="text-xs text-muted">{ent.edges.length - 80} more not listed.</li>}
        </ul>
      </Section>

      {ent.notes.length > 0 && (
        <Section title="Notes" count={ent.notes.length}>
          <ul className="space-y-2">
            {ent.notes.map((n) => (
              <li key={n.id} className="rounded-md bg-raised px-3 py-2.5 text-sm">
                <p className="whitespace-pre-wrap">{n.body}</p>
                <p className="mt-1 text-xs text-muted">{n.author}, {when(n.created_at)}</p>
              </li>
            ))}
          </ul>
        </Section>
      )}

      {finding && <FindingModal id={finding} investigationId={investigationId} onClose={() => setFinding(null)} />}
    </div>
  );
}

function StatusForm({ entityId, kind, candidate, onDone, onCancel }: {
  entityId: string; kind: "confirm" | "reject"; candidate: boolean; onDone: () => void; onCancel: () => void;
}) {
  const [reason, setReason] = useState("");
  const [error, setError] = useState<unknown>(null);
  async function submit(e: FormEvent) {
    e.preventDefault();
    try {
      await api.post(`/entities/${entityId}/${kind}`, { reason: reason.trim() });
      onDone();
    } catch (err) {
      setError(err);
    }
  }
  return (
    <form onSubmit={submit} className="space-y-2 rounded-md border border-line bg-ground p-3.5">
      <label className="label" htmlFor="reason">
        {kind === "confirm"
          ? candidate ? "Which detail matches the subject?" : "Why is this confirmed?"
          : "Why is this wrong or irrelevant?"}
      </label>
      <textarea id="reason" className="field min-h-[60px]" value={reason} onChange={(e) => setReason(e.target.value)}
        placeholder={kind === "confirm" ? "Date of birth 1985-05-05 matches the passport copy" : "Different person: born 1970, subject born 1985"} />
      <p className="text-2xs text-muted">Saved in the audit log with your name.</p>
      <ErrorBox error={error} />
      <div className="flex gap-2">
        <button className={`btn btn-sm ${kind === "reject" ? "btn-danger" : "btn-primary"}`} disabled={reason.trim().length < 3}>
          {kind === "confirm" ? "Confirm" : "Reject"}
        </button>
        <button type="button" className="btn btn-quiet btn-sm" onClick={onCancel}>Cancel</button>
      </div>
    </form>
  );
}

function PivotForm({ entityId, connectors, onDone, onCancel }: {
  entityId: string; connectors: Connector[]; onDone: () => void; onCancel: () => void;
}) {
  // Pre-select only free, passive, available sources; quota and paid ones are opt-in.
  const [chosen, setChosen] = useState<Set<string>>(() => new Set(connectors
    .filter((c) => c.passive && (c.cost ?? "free") === "free" && c.available !== false)
    .map((c) => c.name)));
  const [error, setError] = useState<unknown>(null);
  async function submit(e: FormEvent) {
    e.preventDefault();
    try {
      await api.post(`/entities/${entityId}/pivot`, { connectors: [...chosen], depth: 1 });
      onDone();
    } catch (err) {
      setError(err);
    }
  }
  return (
    <form onSubmit={submit} className="space-y-2 rounded-md border border-line bg-ground p-3.5">
      <p className="label">Sources to ask about this entity</p>
      {connectors.map((c) => (
        <label key={c.name} className="flex items-start gap-2 text-sm">
          <input type="checkbox" className="mt-1 accent-[rgb(var(--accent))]" checked={chosen.has(c.name)}
            onChange={(e) => {
              const next = new Set(chosen);
              if (e.target.checked) next.add(c.name);
              else next.delete(c.name);
              setChosen(next);
            }} />
          <span>
            <span className="font-medium">{c.name}</span>
            {!c.passive && <span className="ml-1.5 text-2xs text-warn">contacts the target</span>}
            {c.cost === "metered" && (
              <span className="ml-1.5 text-2xs text-warn">quota {c.quota_used ?? 0}/{c.quota} this month</span>
            )}
            {c.cost === "paid" && <span className="ml-1.5 text-2xs text-danger">paid credits</span>}
            {c.available === false && <span className="ml-1.5 text-2xs text-muted">not configured</span>}
            {c.requires_key && c.key_configured === false && <span className="ml-1.5 text-2xs text-muted">no API key set</span>}
            <span className="block text-xs text-muted">{c.description}</span>
          </span>
        </label>
      ))}
      <ErrorBox error={error} />
      <div className="flex gap-2">
        <button className="btn btn-primary btn-sm" disabled={!chosen.size}>Run lookups</button>
        <button type="button" className="btn btn-quiet btn-sm" onClick={onCancel}>Cancel</button>
      </div>
    </form>
  );
}

function NoteForm({ entityId, onDone, onCancel }: { entityId: string; onDone: () => void; onCancel: () => void }) {
  const [body, setBody] = useState("");
  const [error, setError] = useState<unknown>(null);
  async function submit(e: FormEvent) {
    e.preventDefault();
    try {
      await api.post(`/entities/${entityId}/notes`, { body: body.trim() });
      onDone();
    } catch (err) {
      setError(err);
    }
  }
  return (
    <form onSubmit={submit} className="space-y-2 rounded-md border border-line bg-ground p-3.5">
      <textarea className="field min-h-[60px]" value={body} onChange={(e) => setBody(e.target.value)} aria-label="Note" />
      <ErrorBox error={error} />
      <div className="flex gap-2">
        <button className="btn btn-primary btn-sm" disabled={!body.trim()}>Save note</button>
        <button type="button" className="btn btn-quiet btn-sm" onClick={onCancel}>Cancel</button>
      </div>
    </form>
  );
}

export function FindingModal({ id, onClose }: { id: string; investigationId?: string; onClose: () => void }) {
  const [f, error] = useLoad(() => api.get<Finding>(`/findings/${id}`), [id]);
  return (
    <Modal title={f ? `${f.connector}: ${String(f.input?.arg ?? "")}` : "Finding"} onClose={onClose}>
      {error ? <ErrorBox error={error} /> : null}
      {f && (
        <div className="space-y-3">
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
            <dt className="text-muted">Retrieved</dt><dd>{when(f.fetched_at)} in {f.duration_ms} ms</dd>
            <dt className="text-muted">Contact with target</dt><dd>{f.passive ? "No (public source)" : "Yes (active)"}</dd>
            <dt className="text-muted">Run by</dt><dd><Mono>{f.run_by}</Mono></dd>
            <dt className="text-muted">sha256</dt><dd><Mono className="break-all">{f.sha256 || "—"}</Mono></dd>
            {f.note && <><dt className="text-muted">Note</dt><dd>{f.note}</dd></>}
            {f.error && <><dt className="text-danger">Error</dt><dd className="text-danger">{f.error}</dd></>}
          </dl>
          {f.payload !== undefined && f.payload !== null ? <JsonView value={f.payload} /> : <p className="text-sm text-muted">No stored payload.</p>}
        </div>
      )}
    </Modal>
  );
}
