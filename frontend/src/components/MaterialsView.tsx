import { useEffect, useRef, useState, type DragEvent, type FormEvent } from "react";
import { api, download } from "../api";
import type { InvestigationStream } from "../stream";
import type { Material } from "../types";
import { Badge, Empty, ErrorBox, Mono, Modal, useLoad, when } from "./ui";

const ACCEPT = ".pdf,.docx,.md,.markdown,.txt";

const KIND_LABEL: Record<Material["kind"], string> = { file: "file", text: "note", link: "link" };

function size(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export function MaterialsView({ investigationId, stream }: { investigationId: string; stream: InvestigationStream }) {
  const [list, listError, loading, reload] = useLoad(
    () => api.get<Material[]>(`/investigations/${investigationId}/materials`),
    [investigationId],
  );
  const [preview, setPreview] = useState<Material | null>(null);
  const [error, setError] = useState<unknown>(null);

  useEffect(
    () =>
      stream.subscribe((e) => {
        if (e.event === "material.added" || e.event === "material.removed") reload();
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [investigationId],
  );

  async function open(m: Material) {
    setError(null);
    try {
      setPreview(await api.get<Material>(`/materials/${m.id}`));
    } catch (err) {
      setError(err);
    }
  }

  return (
    <div className="flex min-h-0 flex-1 max-md:flex-col">
      <aside className="scroll-thin w-[360px] shrink-0 space-y-5 overflow-y-auto border-r border-line bg-panel p-4 max-md:w-full max-md:border-b max-md:border-r-0">
        <p className="text-sm text-muted">
          What you already know about the target: client files, registry extracts, notes, links. PDF and Word
          files are converted to text. Claude gets a short index of each item and reads only what the plan
          needs; the orchestrator decides which worker sees what.
        </p>
        <UploadForm investigationId={investigationId} onDone={reload} />
        <LinkForm investigationId={investigationId} onDone={reload} />
        <NoteForm investigationId={investigationId} onDone={reload} />
      </aside>

      <section className="scroll-thin min-h-0 flex-1 overflow-y-auto px-5 py-4">
        <div className="mb-3 flex items-center gap-2">
          <h2 className="mr-auto font-semibold">Supplied material</h2>
          <button className="btn px-2.5 py-1 text-xs" onClick={reload} disabled={loading}>Refresh</button>
        </div>
        <ErrorBox error={error || listError} onDismiss={error ? () => setError(null) : undefined} />
        {list && !list.length && (
          <Empty title="Nothing added yet">
            Add files, links or notes on the left. Claims that rest only on supplied material stay unverified until
            a public source confirms them.
          </Empty>
        )}
        <ul className="space-y-2">
          {list?.map((m) => (
            <MaterialRow key={m.id} m={m} onOpen={() => open(m)} onRemoved={reload} onError={setError} />
          ))}
        </ul>
      </section>

      {preview && (
        <Modal title={preview.name} onClose={() => setPreview(null)}>
          <p className="mb-2 text-xs text-muted">
            {preview.chars.toLocaleString()} characters as Claude reads them. Finding <Mono>{preview.finding_id}</Mono>
            {preview.sha256 && <>, sha256 <Mono>{preview.sha256.slice(0, 16)}…</Mono></>}
          </p>
          <pre className="scroll-thin max-h-[60vh] overflow-auto whitespace-pre-wrap rounded-md border border-line bg-ground p-3 font-mono text-[12px] leading-5 text-ink">
            {preview.markdown || "(no text)"}
          </pre>
        </Modal>
      )}
    </div>
  );
}

function MaterialRow({ m, onOpen, onRemoved, onError }: {
  m: Material; onOpen: () => void; onRemoved: () => void; onError: (e: unknown) => void;
}) {
  const [confirm, setConfirm] = useState(false);
  const d = m.digest || {};
  const ids = [
    ...(d.company_identifiers || []),
    ...(d.emails || []),
  ];

  async function withdraw() {
    if (!confirm) {
      setConfirm(true);
      setTimeout(() => setConfirm(false), 4000);
      return;
    }
    try {
      await api.del(`/materials/${m.id}`);
      onRemoved();
    } catch (err) {
      onError(err);
    }
  }

  return (
    <li className="panel p-3">
      <div className="flex flex-wrap items-center gap-2">
        <Badge value={KIND_LABEL[m.kind]} />
        <span className="text-2xs uppercase text-muted">{m.format}</span>
        <button className="min-w-0 truncate text-left font-medium hover:underline" onClick={onOpen} title="Show the text">
          {m.name}
        </button>
        <span className="ml-auto text-2xs text-muted">
          {m.chars.toLocaleString()} chars{m.kind === "file" ? `, ${size(m.size_bytes)}` : ""} · {m.added_by},{" "}
          {when(m.created_at)}
        </span>
      </div>
      {m.kind === "link" && m.note && <div className="mt-1 truncate font-mono text-[12px] text-muted">{m.note}</div>}
      {m.note && m.kind !== "link" && <p className="mt-1 text-xs text-warn">{m.note}</p>}
      {!!d.outline?.length && (
        <p className="mt-1 truncate text-xs text-muted" title={d.outline.join(" · ")}>
          {d.outline.slice(0, 6).join(" · ")}
        </p>
      )}
      {!!ids.length && (
        <div className="mt-1.5 flex flex-wrap gap-1">
          {ids.slice(0, 10).map((v) => (
            <span key={v} className="rounded border border-line bg-ground px-1.5 py-px font-mono text-[11px]">{v}</span>
          ))}
          {ids.length > 10 && <span className="text-2xs text-muted">+{ids.length - 10} more</span>}
        </div>
      )}
      <div className="mt-2 flex gap-1.5">
        <button className="btn px-2 py-0.5 text-xs" onClick={onOpen}>Show text</button>
        {m.kind === "file" && (
          <button className="btn px-2 py-0.5 text-xs"
            onClick={() => download(`/materials/${m.id}/file`, m.name).catch(onError)}>
            Original
          </button>
        )}
        <button className={`btn px-2 py-0.5 text-xs ${confirm ? "btn-danger" : ""}`} onClick={withdraw}
          title="Deletes the file and its text; the audit log keeps a record">
          {confirm ? "Click again to remove" : "Remove"}
        </button>
      </div>
    </li>
  );
}

function UploadForm({ investigationId, onDone }: { investigationId: string; onDone: () => void }) {
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [over, setOver] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [notice, setNotice] = useState<string | null>(null);

  async function send(files: FileList | File[]) {
    const list = Array.from(files);
    if (!list.length) return;
    const form = new FormData();
    list.forEach((f) => form.append("files", f, f.name));
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const r = await api.upload<{ materials: Material[]; errors: { name: string; error: string }[] }>(
        `/investigations/${investigationId}/materials`, form);
      const dup = r.materials.filter((m) => m.duplicate).length;
      setNotice(`Added ${r.materials.length - dup}${dup ? `, ${dup} already there` : ""}.`);
      if (r.errors.length) setError(new Error(r.errors.map((e) => e.error).join("\n")));
      onDone();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  }

  function drop(e: DragEvent) {
    e.preventDefault();
    setOver(false);
    send(e.dataTransfer.files);
  }

  return (
    <div>
      <h3 className="mb-1.5 text-sm font-medium">Files</h3>
      <label
        onDragOver={(e) => { e.preventDefault(); setOver(true); }}
        onDragLeave={() => setOver(false)}
        onDrop={drop}
        className={`flex cursor-pointer flex-col items-center gap-1 rounded-md border border-dashed px-3 py-5 text-center text-sm ${
          over ? "border-accent bg-accent/10" : "border-line hover:border-accent/60"}`}>
        <span>{busy ? "Converting…" : "Drop files here or click to choose"}</span>
        <span className="text-2xs text-muted">PDF, DOCX, Markdown, text · up to 25 MB each</span>
        <input ref={input} type="file" multiple accept={ACCEPT} className="sr-only" disabled={busy}
          onChange={(e) => e.target.files && send(e.target.files)} />
      </label>
      {notice && <p className="mt-1 text-xs text-muted">{notice}</p>}
      {error ? <div className="mt-2"><ErrorBox error={error} onDismiss={() => setError(null)} /></div> : null}
    </div>
  );
}

function LinkForm({ investigationId, onDone }: { investigationId: string; onDone: () => void }) {
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function add(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.post(`/investigations/${investigationId}/materials/link`, { url: url.trim() });
      setUrl("");
      onDone();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={add}>
      <h3 className="mb-1.5 text-sm font-medium">Link</h3>
      <div className="flex gap-2">
        <input className="field min-w-0 flex-1 font-mono" type="url" value={url} onChange={(e) => setUrl(e.target.value)}
          placeholder="https://…" aria-label="Link" />
        <button className="btn" disabled={busy || !/^https?:\/\/\S+/.test(url.trim())}>
          {busy ? "Reading…" : "Add"}
        </button>
      </div>
      <p className="mt-1 text-2xs text-muted">Read now and kept as text. The target's own site needs active mode.</p>
      {error ? <div className="mt-2"><ErrorBox error={error} onDismiss={() => setError(null)} /></div> : null}
    </form>
  );
}

function NoteForm({ investigationId, onDone }: { investigationId: string; onDone: () => void }) {
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function add(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.post(`/investigations/${investigationId}/materials/text`, { title: title.trim(), text: body });
      setTitle("");
      setBody("");
      onDone();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={add} className="space-y-2">
      <h3 className="text-sm font-medium">Note</h3>
      <input className="field w-full" value={title} onChange={(e) => setTitle(e.target.value)}
        placeholder="Title, e.g. Client brief" aria-label="Note title" />
      <textarea className="field min-h-[120px] w-full" value={body} onChange={(e) => setBody(e.target.value)}
        placeholder="Known facts, aliases, identifiers, what to check…" aria-label="Note text" />
      <button className="btn" disabled={busy || !title.trim() || !body.trim()}>{busy ? "Adding…" : "Add note"}</button>
      {error ? <ErrorBox error={error} onDismiss={() => setError(null)} /> : null}
    </form>
  );
}
