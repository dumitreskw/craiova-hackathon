import { useEffect, useRef, useState, type FormEvent } from "react";
import { api, ApiError } from "../api";
import type { InvestigationStream } from "../stream";
import type {
  Persona,
  SocialCaptureResult,
  SocialJob,
  SocialStatus,
  SocialWatch,
} from "../types";
import { Badge, Empty, ErrorBox, Mono, useLoad, when } from "./ui";

const STATUS_DOT: Record<Persona["status"], string> = {
  ok: "bg-ok",
  needs_login: "bg-warn",
  disabled: "bg-muted",
};
const STATUS_LABEL: Record<Persona["status"], string> = {
  ok: "ready",
  needs_login: "needs login",
  disabled: "disabled",
};

export function SocialView({
  investigationId,
  stream,
}: {
  investigationId: string;
  stream: InvestigationStream;
}) {
  const [status, statusError, , reloadStatus] = useLoad(
    () => api.get<SocialStatus>("/social/status"),
    [investigationId],
  );
  const [jobs, , , reloadJobs] = useLoad(
    () => api.get<SocialJob[]>(`/investigations/${investigationId}/social/jobs?limit=40`),
    [investigationId],
  );
  const [watches, , , reloadWatches] = useLoad(
    () => api.get<SocialWatch[]>(`/investigations/${investigationId}/social/watches`),
    [investigationId],
  );
  const [error, setError] = useState<unknown>(null);

  // Poll the worker/queue state while the tab is open; the SSE stream nudges it.
  useEffect(() => {
    const t = setInterval(() => {
      reloadStatus();
      reloadJobs();
    }, 5000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [investigationId]);

  useEffect(
    () =>
      stream.subscribe((e) => {
        if (e.event === "social.changed" || e.event.startsWith("tool.")) {
          reloadJobs();
          reloadWatches();
        }
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [investigationId],
  );

  const platforms = (status?.personas || []).map((p) => p.platform);

  return (
    <div className="flex min-h-0 flex-1 max-md:flex-col">
      <aside className="scroll-thin w-[380px] shrink-0 space-y-5 overflow-y-auto border-r border-line bg-panel p-4 max-md:w-full max-md:border-b max-md:border-r-0">
        <WorkerStatus status={status} onRefresh={reloadStatus} />
        <p className="text-xs text-muted">
          Captures use research accounts ("personas") whose logged-in sessions live on the Kali VM and go out
          over Mullvad. Public profiles only; the worker reads and screenshots, it never follows, likes,
          messages or joins. Capture is tied to a profile already in the graph.
        </p>
        <PersonaList
          personas={status?.personas || []}
          online={!!status?.online}
          onChanged={reloadStatus}
          onError={setError}
        />
        <NewPersona onCreated={reloadStatus} onError={setError} />
      </aside>

      <section className="scroll-thin min-h-0 flex-1 space-y-6 overflow-y-auto px-5 py-4">
        <ErrorBox error={error || statusError} onDismiss={error ? () => setError(null) : undefined} />
        <CaptureForm
          investigationId={investigationId}
          personas={status?.personas || []}
          online={!!status?.online}
          onDone={() => {
            reloadJobs();
            reloadWatches();
          }}
          onError={setError}
        />
        <Watches
          investigationId={investigationId}
          watches={watches || []}
          hasPersona={platforms.length > 0}
          onChanged={reloadWatches}
          onError={setError}
        />
        <Jobs jobs={jobs || []} />
      </section>
    </div>
  );
}

function WorkerStatus({ status, onRefresh }: { status: SocialStatus | null; onRefresh: () => void }) {
  const online = !!status?.online;
  const vpn = status?.egress?.mullvad ?? status?.vpn?.connected;
  return (
    <div className="rounded-lg border border-line p-3">
      <div className="flex items-center gap-2">
        <span className={`h-2.5 w-2.5 rounded-full ${online ? "bg-ok" : "bg-danger"}`} />
        <span className="font-medium">Kali worker {online ? "online" : "offline"}</span>
        <button className="btn ml-auto px-2 py-0.5 text-xs" onClick={onRefresh}>
          Refresh
        </button>
      </div>
      {online ? (
        <dl className="mt-2 space-y-0.5 text-xs text-muted">
          <div className="flex justify-between gap-2">
            <dt>VPN</dt>
            <dd className={vpn ? "text-ok" : "text-danger"}>
              {vpn ? "Mullvad up" : "not on Mullvad"}
            </dd>
          </div>
          {status?.egress?.ip && (
            <div className="flex justify-between gap-2">
              <dt>Egress</dt>
              <dd>
                <Mono>{status.egress.ip}</Mono>
                {status.egress.country ? ` · ${status.egress.city || ""} ${status.egress.country}` : ""}
              </dd>
            </div>
          )}
          <div className="flex justify-between gap-2">
            <dt>Queue</dt>
            <dd>{status?.queued ?? 0} waiting{status?.busy ? ", busy" : ""}</dd>
          </div>
        </dl>
      ) : (
        <p className="mt-2 text-xs text-muted">
          Start <Mono>osint-worker</Mono> on the Kali VM. Captures are queued and run once it connects.
        </p>
      )}
    </div>
  );
}

function PersonaList({
  personas,
  online,
  onChanged,
  onError,
}: {
  personas: Persona[];
  online: boolean;
  onChanged: () => void;
  onError: (e: unknown) => void;
}) {
  async function login(p: Persona) {
    onError(null);
    try {
      await api.post(`/social/personas/${p.name}/login`);
      onChanged();
    } catch (e) {
      onError(e);
    }
  }
  async function toggle(p: Persona) {
    onError(null);
    try {
      await api.patch(`/social/personas/${p.name}`, { disabled: p.status !== "disabled" });
      onChanged();
    } catch (e) {
      onError(e);
    }
  }

  if (!personas.length) {
    return <p className="text-xs text-muted">No personas yet. Add one below.</p>;
  }
  return (
    <ul className="space-y-2">
      {personas.map((p) => (
        <li key={p.id} className="rounded-lg border border-line p-2.5">
          <div className="flex items-center gap-2">
            <span className={`h-2 w-2 rounded-full ${STATUS_DOT[p.status]}`} title={STATUS_LABEL[p.status]} />
            <span className="font-medium">{p.name}</span>
            <Badge value={p.platform} />
            <span className="ml-auto text-xs text-muted">{p.region}</span>
          </div>
          <div className="mt-1 flex items-center gap-2 text-xs text-muted">
            <span>{STATUS_LABEL[p.status]}</span>
            {typeof p.captures_last_hour === "number" && p.captures_last_hour > 0 && (
              <span>· {p.captures_last_hour}/h</span>
            )}
            {p.last_used && <span>· used {when(p.last_used)}</span>}
            <span className="ml-auto flex gap-1.5">
              {p.status !== "disabled" && (
                <button className="link" onClick={() => login(p)} disabled={!online} title={online ? "" : "worker offline"}>
                  {p.status === "ok" ? "Re-login" : "Log in"}
                </button>
              )}
              <button className="link" onClick={() => toggle(p)}>
                {p.status === "disabled" ? "Enable" : "Disable"}
              </button>
            </span>
          </div>
          {p.note && <p className="mt-1 text-xs text-muted">{p.note}</p>}
        </li>
      ))}
    </ul>
  );
}

const PLATFORM_OPTIONS = ["linkedin", "facebook", "instagram", "x", "tiktok", "youtube", "threads", "reddit"];

function NewPersona({ onCreated, onError }: { onCreated: () => void; onError: (e: unknown) => void }) {
  const [name, setName] = useState("");
  const [platform, setPlatform] = useState("linkedin");
  const [region, setRegion] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    onError(null);
    try {
      await api.post("/social/personas", {
        name: name.trim(),
        platform,
        region: region.trim(),
        note: note.trim() || undefined,
      });
      setName("");
      setRegion("");
      setNote("");
      onCreated();
    } catch (err) {
      onError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-2 rounded-lg border border-line p-3">
      <h3 className="text-sm font-medium">New persona</h3>
      <div className="flex gap-2">
        <input
          className="field flex-1 font-mono"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="li-ana"
          aria-label="Persona name"
        />
        <select className="field w-auto" value={platform} onChange={(e) => setPlatform(e.target.value)} aria-label="Platform">
          {PLATFORM_OPTIONS.map((p) => (
            <option key={p} value={p}>
              {p}
            </option>
          ))}
        </select>
      </div>
      <input
        className="field font-mono"
        value={region}
        onChange={(e) => setRegion(e.target.value)}
        placeholder="Mullvad region, e.g. de or de-fra"
        aria-label="Mullvad region"
      />
      <input
        className="field"
        value={note}
        onChange={(e) => setNote(e.target.value)}
        placeholder="note (optional)"
        aria-label="Note"
      />
      <button className="btn btn-primary w-full" disabled={busy || !name.trim() || !region.trim()}>
        Add persona
      </button>
      <p className="text-xs text-muted">Then “Log in” to sign in once in a browser on Kali’s desktop.</p>
    </form>
  );
}

function CaptureForm({
  investigationId,
  personas,
  online,
  onDone,
  onError,
}: {
  investigationId: string;
  personas: Persona[];
  online: boolean;
  onDone: () => void;
  onError: (e: unknown) => void;
}) {
  const [url, setUrl] = useState("");
  const [persona, setPersona] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<SocialCaptureResult | null>(null);
  const ready = personas.filter((p) => p.status === "ok");

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setResult(null);
    onError(null);
    try {
      const r = await api.post<SocialCaptureResult>(
        `/investigations/${investigationId}/social/capture`,
        { url: url.trim(), persona: persona || undefined },
      );
      setResult(r);
      onDone();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) onError(err.detail);
      else onError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rounded-lg border border-line p-4">
      <h2 className="mb-2 font-semibold">Capture a profile</h2>
      <form onSubmit={submit} className="flex flex-wrap items-center gap-2">
        <input
          className="field min-w-[18rem] flex-1 font-mono"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="https://www.linkedin.com/in/jane-doe/"
          aria-label="Profile URL"
        />
        <select className="field w-auto" value={persona} onChange={(e) => setPersona(e.target.value)} aria-label="Account to use">
          <option value="">Auto (logged out, persona at a wall)</option>
          <option value="none">Logged out only</option>
          {ready.map((p) => (
            <option key={p.id} value={p.name}>
              {p.name} ({p.platform})
            </option>
          ))}
        </select>
        <button className="btn btn-primary" disabled={busy || !url.trim() || !online}>
          {busy ? "Capturing…" : "Capture"}
        </button>
      </form>
      {!online && <p className="mt-2 text-xs text-warn">The Kali worker is offline — start it first.</p>}
      <p className="mt-2 text-xs text-muted">
        The profile must already be in the graph (add it as a target or find it with a lookup). The capture is
        saved as evidence with a source banner, like any screenshot.
      </p>
      {result && <CaptureResult result={result} investigationId={investigationId} />}
    </div>
  );
}

function CaptureResult({ result, investigationId }: { result: SocialCaptureResult; investigationId: string }) {
  void investigationId;
  const raw = result.raw;
  const changes = raw?.changes;
  return (
    <div className="mt-3 rounded-md border border-line bg-bg p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">Captured</span>
        {raw?.logged_in ? <Badge value={`as ${raw.persona}`} /> : <Badge value="logged out" />}
        {raw?.login_wall && !raw?.logged_in && <span className="text-xs text-warn">login wall (teaser only)</span>}
        {raw?.blocked && <span className="text-xs text-danger">blocked by bot wall</span>}
        {result.finding?.id && (
          <a
            className="link ml-auto text-xs"
            href={`/api/v1/findings/${result.finding.id}/screenshot`}
            target="_blank"
            rel="noreferrer"
          >
            Open screenshot
          </a>
        )}
      </div>
      {result.note && <p className="mt-1 text-xs text-muted">{result.note}</p>}
      {changes && (changes.count ?? 0) > 0 && (
        <div className="mt-2 text-xs">
          <span className="font-medium">{changes.count} change(s) since last capture:</span>
          <ul className="mt-1 list-disc pl-5 text-muted">
            {Object.entries(changes.fields || {}).map(([k, [a, b]]) => (
              <li key={k}>
                {k}: {String(a)} → {String(b)}
              </li>
            ))}
            {(changes.new_lines || []).slice(0, 5).map((l, i) => (
              <li key={`n${i}`} className="text-ok">+ {l}</li>
            ))}
            {(changes.removed_lines || []).slice(0, 3).map((l, i) => (
              <li key={`r${i}`} className="text-danger">− {l}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function Watches({
  investigationId,
  watches,
  hasPersona,
  onChanged,
  onError,
}: {
  investigationId: string;
  watches: SocialWatch[];
  hasPersona: boolean;
  onChanged: () => void;
  onError: (e: unknown) => void;
}) {
  void hasPersona;
  const [url, setUrl] = useState("");
  const [hours, setHours] = useState(24);
  const [busy, setBusy] = useState(false);
  const formRef = useRef<HTMLFormElement>(null);

  async function add(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    onError(null);
    try {
      await api.post(`/investigations/${investigationId}/social/watches`, {
        url: url.trim(),
        interval_hours: hours,
      });
      setUrl("");
      onChanged();
    } catch (err) {
      onError(err);
    } finally {
      setBusy(false);
    }
  }
  async function toggle(w: SocialWatch) {
    onError(null);
    try {
      await api.patch(`/social/watches/${w.id}`, { active: !w.active });
      onChanged();
    } catch (err) {
      onError(err);
    }
  }
  async function remove(w: SocialWatch) {
    onError(null);
    try {
      await api.del(`/social/watches/${w.id}`);
      onChanged();
    } catch (err) {
      onError(err);
    }
  }

  return (
    <div className="rounded-lg border border-line p-4">
      <h2 className="mb-2 font-semibold">Watches</h2>
      <p className="mb-2 text-xs text-muted">
        Re-capture a profile on a schedule and flag what changed (bio, counts, new posts). Runs while this
        investigation is open and the worker is online.
      </p>
      <form ref={formRef} onSubmit={add} className="mb-3 flex flex-wrap items-center gap-2">
        <input
          className="field min-w-[16rem] flex-1 font-mono"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="profile URL already in the graph"
          aria-label="Watch URL"
        />
        <label className="flex items-center gap-1 text-xs text-muted">
          every
          <input
            className="field w-16"
            type="number"
            min={1}
            max={720}
            value={hours}
            onChange={(e) => setHours(Math.max(1, Number(e.target.value) || 1))}
            aria-label="Interval in hours"
          />
          h
        </label>
        <button className="btn" disabled={busy || !url.trim()}>
          Add watch
        </button>
      </form>
      {!watches.length ? (
        <p className="text-xs text-muted">No watches yet.</p>
      ) : (
        <ul className="space-y-1.5">
          {watches.map((w) => (
            <li key={w.id} className="flex items-center gap-2 rounded-md border border-line px-2.5 py-1.5 text-sm">
              <span className={`h-2 w-2 rounded-full ${w.active ? "bg-ok" : "bg-muted"}`} />
              <span className="min-w-0 flex-1 truncate font-mono text-xs" title={w.url}>
                {w.url}
              </span>
              <span className="text-xs text-muted">every {w.interval_hours}h</span>
              {w.last_run_at && <span className="text-xs text-muted">· ran {when(w.last_run_at)}</span>}
              {w.last_error && <span className="text-xs text-danger" title={w.last_error}>· error</span>}
              <button className="link text-xs" onClick={() => toggle(w)}>
                {w.active ? "Pause" : "Resume"}
              </button>
              <button className="link text-xs" onClick={() => remove(w)}>
                Remove
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

const JOB_BADGE: Record<SocialJob["status"], string> = {
  queued: "text-muted",
  running: "text-accent",
  done: "text-ok",
  failed: "text-danger",
  expired: "text-warn",
};

function Jobs({ jobs }: { jobs: SocialJob[] }) {
  if (!jobs.length) {
    return (
      <Empty title="No capture jobs yet">
        Capture a profile above, or add a watch. Each job runs on the Kali worker and lands here.
      </Empty>
    );
  }
  return (
    <div className="rounded-lg border border-line">
      <h2 className="border-b border-line px-4 py-2 font-semibold">Recent jobs</h2>
      <ul className="divide-y divide-line">
        {jobs.map((j) => (
          <li key={j.id} className="flex items-center gap-2 px-4 py-2 text-sm">
            <Badge value={j.kind} />
            <span className={`text-xs font-medium ${JOB_BADGE[j.status]}`}>{j.status}</span>
            <span className="min-w-0 flex-1 truncate font-mono text-xs text-muted" title={j.url || ""}>
              {j.url || (j.persona ? `persona ${j.persona}` : "")}
            </span>
            {j.result?.persona_used && <Badge value={j.result.persona_used} />}
            {j.result?.login_wall && <span className="text-xs text-warn">wall</span>}
            {j.finding_id && (
              <a className="link text-xs" href={`/api/v1/findings/${j.finding_id}/screenshot`} target="_blank" rel="noreferrer">
                shot
              </a>
            )}
            <span className="text-xs text-muted">{when(j.created_at)}</span>
            {j.error && (
              <span className="max-w-[16rem] truncate text-xs text-danger" title={j.error}>
                {j.error}
              </span>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
