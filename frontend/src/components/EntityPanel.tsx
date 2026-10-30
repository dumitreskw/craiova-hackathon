import { useState, type FormEvent } from "react";
import { api } from "../api";
import { typeColor } from "../entityStyle";
import type { Connector, EntityDetail, Finding } from "../types";
import { Badge, ErrorBox, JsonView, Modal, Mono, shortHash, useLoad, when } from "./ui";

function fmt(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "string") return v;
  return JSON.stringify(v);
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
  const [capturing, setCapturing] = useState(false);
  const [captureError, setCaptureError] = useState<unknown>(null);

  async function captureEvidence(url: string, id: string) {
    setCapturing(true);
    setCaptureError(null);
    try {
      await api.post(`/investigations/${investigationId}/capture`, { url, entity_id: id });
      reload();
    } catch (err) {
      setCaptureError(err);
    } finally {
      setCapturing(false);
    }
  }

  if (error) return <div className="p-4"><ErrorBox error={error} /></div>;
  if (!ent) return <p className="p-4 text-sm text-muted">{loading ? "Loading…" : ""}</p>;

  const candidate = ent.status === "candidate";
  const applicable = (connectors || []).filter((c) => c.accepts === ent.type);
  const disambiguators = ent.attributes.disambiguators as Record<string, unknown> | undefined;
  const screenshotFinding = ent.type === "Document" && ent.attributes.kind === "screenshot"
    ? ent.sources.find((s) => s.connector === "screenshot")?.id
    : undefined;

  return (
    <div className="space-y-5 p-4">
      <div>
        <div className="flex items-center gap-2 text-xs text-muted">
          <span className="h-2.5 w-2.5 rounded-full" style={{ background: typeColor(ent.type) }} />
          {ent.type}
          <Badge value={ent.status} />
        </div>
        <h2 className="mt-1 break-words text-base font-semibold">{ent.label}</h2>
        {ent.label !== ent.key && <Mono className="break-all text-muted">{ent.key}</Mono>}
        <div className="mt-3">
          <div className="flex justify-between text-xs text-muted">
            <span>Confidence</span>
            <span className="font-mono">{ent.confidence.toFixed(2)}</span>
          </div>
          <div className="mt-1 h-1.5 overflow-hidden rounded bg-raised">
            <div className={`h-full ${candidate ? "bg-danger" : "bg-accent"}`} style={{ width: `${Math.round(ent.confidence * 100)}%` }} />
          </div>
          <p className="mt-1 text-2xs text-muted">
            From {ent.sources.length} source{ent.sources.length === 1 ? "" : "s"}; first seen {when(ent.first_seen)}.
          </p>
        </div>
      </div>

      {candidate && (
        <div className="rounded-md border border-dashed border-danger/70 bg-danger/5 p-3 text-sm">
          <p className="font-medium text-danger">Name match only, not confirmed</p>
          <p className="mt-1 text-muted">
            This listing shares a name with your subject. Many people share names. Confirm it only if a detail
            such as date of birth, nationality, a photo or an ID number matches.
          </p>
          {disambiguators && Object.keys(disambiguators).length > 0 && (
            <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-xs">
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

      <div className="flex flex-wrap gap-1.5">
        {ent.status !== "confirmed" && (
          <button className="btn px-2.5 py-1 text-xs" onClick={() => setAction("confirm")}>Confirm</button>
        )}
        {ent.status !== "rejected" && (
          <button className="btn btn-danger px-2.5 py-1 text-xs" onClick={() => setAction("reject")}>Reject</button>
        )}
        {!candidate && ent.status !== "rejected" && applicable.length > 0 && (
          <button className="btn px-2.5 py-1 text-xs" onClick={() => setAction("pivot")}>Look up further</button>
        )}
        {ent.type === "URL" && /^https?:\/\//.test(ent.key) && (
          <button className="btn px-2.5 py-1 text-xs" disabled={capturing}
            onClick={() => captureEvidence(ent.key, ent.id)}>
            {capturing ? "Capturing…" : "Capture evidence"}
          </button>
        )}
        <button className="btn btn-quiet px-2.5 py-1 text-xs" onClick={() => setAction("note")}>Add note</button>
      </div>
      {captureError !== null && <ErrorBox error={captureError} onDismiss={() => setCaptureError(null)} />}

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

      <section>
        <h3 className="mb-1.5 text-xs font-semibold text-muted">Attributes</h3>
        {Object.keys(ent.attribute_sources).length === 0 ? (
          <p className="text-sm text-muted">None recorded.</p>
        ) : (
          <dl className="space-y-1.5 text-sm">
            {Object.entries(ent.attribute_sources).map(([name, values]) => (
              <div key={name}>
                <dt className="text-xs text-muted">
                  {name.replace(/_/g, " ")}
                  {values.length > 1 && <span className="ml-1.5 text-warn">sources disagree</span>}
                </dt>
                {values.map((v, i) => (
                  <dd key={i} className="flex items-baseline gap-2 break-all">
                    <span className={values.length > 1 ? "text-ink" : ""}>{fmt(v.value)}</span>
                    {values.length > 1 && <span className="shrink-0 text-2xs text-muted">{v.sources.length} source(s)</span>}
                  </dd>
                ))}
              </div>
            ))}
          </dl>
        )}
      </section>

      <section>
        <h3 className="mb-1.5 text-xs font-semibold text-muted">Where this came from</h3>
        <ul className="space-y-1">
          {ent.sources.map((s) => (
            <li key={s.id}>
              <button onClick={() => setFinding(s.id)}
                className="w-full rounded-md border border-line px-2.5 py-1.5 text-left hover:border-muted">
                <div className="flex items-baseline justify-between gap-2">
                  <span className="text-sm font-medium">{s.connector}</span>
                  <span className="text-2xs text-muted">{when(s.fetched_at)}</span>
                </div>
                <div className="flex items-baseline justify-between gap-2 text-2xs text-muted">
                  <Mono className="truncate">{String(s.input?.arg ?? "")}</Mono>
                  <Mono>sha256 {shortHash(s.sha256, 8)}</Mono>
                </div>
              </button>
            </li>
          ))}
          {ent.sources.length === 0 && <li className="text-sm text-muted">Added by you as a target; no lookup has returned it yet.</li>}
        </ul>
      </section>

      <section>
        <h3 className="mb-1.5 text-xs font-semibold text-muted">Links ({ent.edges.length})</h3>
        <ul className="space-y-0.5 text-sm">
          {ent.edges.slice(0, 60).map((e) => {
            const outgoing = e.source === ent.id;
            const other = outgoing ? e.target : e.source;
            const label = e.other ? `${e.other.type}: ${e.other.label}` : other;
            return (
              <li key={e.id} className="flex items-baseline gap-2">
                <span className={`shrink-0 text-xs ${e.type === "possible_match" ? "text-danger" : "text-muted"}`}>
                  {outgoing ? e.type : `${e.type} (from)`}
                </span>
                <button className={`truncate text-left text-[12px] hover:underline ${e.other ? "" : "font-mono"}`}
                  title={e.other?.key ?? other} onClick={() => onSelect(other)}>{label}</button>
              </li>
            );
          })}
          {ent.edges.length > 60 && <li className="text-xs text-muted">{ent.edges.length - 60} more not listed.</li>}
        </ul>
      </section>

      {ent.notes.length > 0 && (
        <section>
          <h3 className="mb-1.5 text-xs font-semibold text-muted">Notes</h3>
          <ul className="space-y-2">
            {ent.notes.map((n) => (
              <li key={n.id} className="rounded-md bg-raised px-3 py-2 text-sm">
                <p className="whitespace-pre-wrap">{n.body}</p>
                <p className="mt-1 text-2xs text-muted">{n.author}, {when(n.created_at)}</p>
              </li>
            ))}
          </ul>
        </section>
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
    <form onSubmit={submit} className="space-y-2 rounded-md border border-line bg-ground p-3">
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
        <button className={`btn ${kind === "reject" ? "btn-danger" : "btn-primary"} px-2.5 py-1 text-xs`} disabled={reason.trim().length < 3}>
          {kind === "confirm" ? "Confirm" : "Reject"}
        </button>
        <button type="button" className="btn btn-quiet px-2.5 py-1 text-xs" onClick={onCancel}>Cancel</button>
      </div>
    </form>
  );
}

function PivotForm({ entityId, connectors, onDone, onCancel }: {
  entityId: string; connectors: Connector[]; onDone: () => void; onCancel: () => void;
}) {
  const [chosen, setChosen] = useState<Set<string>>(() => new Set(connectors.filter((c) => c.passive).map((c) => c.name)));
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
    <form onSubmit={submit} className="space-y-2 rounded-md border border-line bg-ground p-3">
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
            {c.requires_key && c.key_configured === false && <span className="ml-1.5 text-2xs text-muted">no API key set</span>}
            <span className="block text-xs text-muted">{c.description}</span>
          </span>
        </label>
      ))}
      <ErrorBox error={error} />
      <div className="flex gap-2">
        <button className="btn btn-primary px-2.5 py-1 text-xs" disabled={!chosen.size}>Run lookups</button>
        <button type="button" className="btn btn-quiet px-2.5 py-1 text-xs" onClick={onCancel}>Cancel</button>
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
    <form onSubmit={submit} className="space-y-2 rounded-md border border-line bg-ground p-3">
      <textarea className="field min-h-[60px]" value={body} onChange={(e) => setBody(e.target.value)} aria-label="Note" />
      <ErrorBox error={error} />
      <div className="flex gap-2">
        <button className="btn btn-primary px-2.5 py-1 text-xs" disabled={!body.trim()}>Save note</button>
        <button type="button" className="btn btn-quiet px-2.5 py-1 text-xs" onClick={onCancel}>Cancel</button>
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
