import { useEffect, useState } from "react";
import {
  AlertTriangle, CheckCircle2, Download, FilePlus2, FileText, Tag, X,
} from "lucide-react";
import { api, ApiError, download, qs } from "../api";
import type { InvestigationStream } from "../stream";
import type { LintIssue, Report, ReportSummary } from "../types";
import { FindingModal } from "./EntityPanel";
import { ago, Badge, Empty, ErrorBox, Loading, Mono, Segmented, Spinner, useLoad, when } from "./ui";

type Audience = "internal" | "client";
type Lint = { ok: boolean; issues: LintIssue[] };

const AUDIENCE_HINT: Record<Audience, string> = {
  internal: "Shows finding ids, hashes, file paths and how the data was collected.",
  client: "Shows each source and its retrieval date only. No workspace details.",
};

export function ReportsView({ investigationId, stream }: { investigationId: string; stream: InvestigationStream }) {
  const [list, listError, , reloadList] = useLoad(
    () => api.get<ReportSummary[]>(`/investigations/${investigationId}/reports`),
    [investigationId],
  );
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [creating, setCreating] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    if (!selected && list && list.length) setSelected(list[0].id);
  }, [list, selected]);

  // Reports written by the report agent or a deep analysis appear on their own.
  useEffect(
    () =>
      stream.subscribe((e) => {
        if (e.event !== "agent.finished") return;
        const rid = e.data.report_id ? String(e.data.report_id) : null;
        if (rid || e.data.kind === "report" || e.data.kind === "deep") {
          reloadList();
          if (rid) {
            setSelected(rid);
            setNotice("Claude finished a report. It is open below.");
          }
        }
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [investigationId],
  );

  async function createDraft() {
    setCreating(true);
    setError(null);
    try {
      const r = await api.post<Report>(`/investigations/${investigationId}/reports`, {});
      reloadList();
      setSelected(r.id);
      setNotice(null);
    } catch (e) {
      setError(e);
    } finally {
      setCreating(false);
    }
  }

  return (
    <div className="flex min-h-0 flex-1">
      <aside className="scroll-thin flex w-72 shrink-0 flex-col overflow-y-auto border-r border-line bg-panel max-lg:w-60">
        <div className="space-y-2 border-b border-line p-4">
          <button className="btn btn-primary w-full" onClick={createDraft} disabled={creating}>
            {creating ? <Spinner /> : <FilePlus2 className="h-4 w-4" aria-hidden />}Draft from graph
          </button>
          <p className="hint">
            A plain draft of what the sources returned, every line cited. For a curated report, use "Write report" in
            Claude agents.
          </p>
          <ErrorBox error={error || listError} />
        </div>
        <ul className="p-2" aria-label="Reports">
          {list?.map((r) => {
            const active = selected === r.id;
            return (
              <li key={r.id}>
                <button onClick={() => setSelected(r.id)} aria-current={active ? "true" : undefined}
                  className={`relative w-full rounded-md px-3 py-2.5 text-left transition-colors duration-150 hover:bg-raised ${active ? "bg-raised" : ""}`}>
                  {active && <span className="absolute inset-y-2 left-0 w-[3px] rounded-full bg-accent" aria-hidden />}
                  <div className="line-clamp-2 text-sm font-medium">{r.title}</div>
                  <div className="mt-0.5 text-xs text-muted">{r.created_by}, {ago(r.updated_at)}</div>
                </button>
              </li>
            );
          })}
          {list?.length === 0 && <li className="px-3 py-2 text-sm text-muted">No reports yet.</li>}
        </ul>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        {notice && (
          <div className="flex items-center gap-2 border-b border-ok/40 bg-ok/10 px-5 py-2 text-sm" role="status">
            <CheckCircle2 className="h-4 w-4 text-ok" aria-hidden />
            <span className="flex-1">{notice}</span>
            <button className="rounded p-1 text-muted hover:text-ink" onClick={() => setNotice(null)} aria-label="Dismiss"><X className="h-4 w-4" /></button>
          </div>
        )}
        {selected ? (
          <ReportDetail key={selected} reportId={selected} />
        ) : list ? (
          <Empty title="No reports yet" icon={<FileText className="h-8 w-8" />}
            action={<button className="btn btn-primary" onClick={createDraft} disabled={creating}>Draft from graph</button>}>
            Draft one from the graph once a scan has found something, or ask Claude to write one.
          </Empty>
        ) : (
          <Loading>Loading reports</Loading>
        )}
      </div>
    </div>
  );
}

type ExportFormat = "docx" | "pdf" | "html" | "md";
const EXPORT_FORMATS: [ExportFormat, string][] = [["docx", "Word"], ["pdf", "PDF"], ["html", "HTML"], ["md", "Markdown"]];

function ReportDetail({ reportId }: { reportId: string }) {
  const [report, error] = useLoad(() => api.get<Report>(`/reports/${reportId}`), [reportId]);
  const [audience, setAudience] = useState<Audience>("internal");
  const [cite, setCite] = useState(true);
  const [view, setView] = useState<"claims" | "preview">("claims");
  const [internal] = useLoad(() => api.get<Lint>(`/reports/${reportId}/lint${qs({ audience: "internal" })}`), [reportId]);
  const [client] = useLoad(() => api.get<Lint>(`/reports/${reportId}/lint${qs({ audience: "client" })}`), [reportId]);
  const [finding, setFinding] = useState<string | null>(null);
  const [exportError, setExportError] = useState<unknown>(null);
  const [exporting, setExporting] = useState<ExportFormat | null>(null);

  if (error) return <div className="p-5"><ErrorBox error={error} /></div>;
  if (!report) return <Loading>Loading report</Loading>;

  const lint = audience === "internal" ? internal : client;
  const issues = lint?.issues || [];
  const byClaim = new Map<string, LintIssue[]>();
  for (const i of issues) if (i.claim) byClaim.set(i.claim, [...(byClaim.get(i.claim) || []), i]);

  async function exportAs(format: ExportFormat, force = false) {
    setExportError(null);
    setExporting(format);
    try {
      await download(`/reports/${reportId}/render${qs({ audience, format, cite, force: force || undefined })}`,
        `${report!.title.replace(/[^\w.-]+/g, "_").slice(0, 60)}_${audience}.${format}`);
    } catch (e) {
      setExportError(e);
    } finally {
      setExporting(null);
    }
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="border-b border-line px-6 pb-3 pt-4">
        <div className="flex flex-wrap items-start gap-x-6 gap-y-3">
          <div className="mr-auto min-w-0">
            <h2 className="text-[20px] font-semibold leading-tight tracking-tight">{report.title}</h2>
            <p className="mt-1 text-xs text-muted">Updated {when(report.updated_at)} by {report.created_by}</p>
          </div>
          <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Export">
            <span className="mr-1 text-xs text-muted">Export {audience} copy</span>
            {EXPORT_FORMATS.map(([format, label]) => (
              <button key={format} className={`btn btn-sm ${format === "docx" ? "btn-primary" : ""}`} disabled={exporting !== null}
                onClick={() => exportAs(format)}>
                {exporting === format ? <Spinner className="h-3.5 w-3.5" /> : <Download className="h-3.5 w-3.5" aria-hidden />}{label}
              </button>
            ))}
          </div>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <Segmented label="Audience" value={audience} onChange={setAudience}
            options={[["internal", "Internal", AUDIENCE_HINT.internal], ["client", "Client", AUDIENCE_HINT.client]]} />
          <Segmented label="View" value={view} onChange={setView} options={[["claims", "Claims"], ["preview", "Preview"]]} />
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={cite} onChange={(e) => setCite(e.target.checked)} className="accent-[rgb(var(--accent))]" />
            Citations in the copy
          </label>
          <span className="text-xs text-muted">{AUDIENCE_HINT[audience]}</span>
        </div>
      </div>

      <LintPanel internal={internal} client={client} audience={audience} onPick={setAudience}
        onJump={(claim) => {
          setView("claims");
          window.setTimeout(() => document.getElementById(`claim-${claim}`)?.scrollIntoView({ block: "center", behavior: "smooth" }), 50);
        }} />

      {exportError ? (
        <div className="px-6 pt-3">
          <LintBlocked error={exportError} onForce={(f) => exportAs(f, true)} onDismiss={() => setExportError(null)} />
        </div>
      ) : null}

      <div className="scroll-thin min-h-0 flex-1 overflow-y-auto">
        {view === "claims" ? (
          <ClaimsView report={report} byClaim={byClaim} onFinding={setFinding} />
        ) : (
          <Preview reportId={reportId} audience={audience} cite={cite} />
        )}
      </div>
      {finding && <FindingModal id={finding} onClose={() => setFinding(null)} />}
    </div>
  );
}

function LintPanel({ internal, client, audience, onPick, onJump }: {
  internal: Lint | null; client: Lint | null; audience: Audience; onPick: (a: Audience) => void;
  onJump: (claim: string) => void;
}) {
  const rows: [Audience, Lint | null][] = [["internal", internal], ["client", client]];
  const current = audience === "internal" ? internal : client;
  const general = (current?.issues || []).filter((i) => !i.claim);
  const blocking = (current?.issues || []).filter((i) => i.claim && i.level === "error");
  return (
    <div className="border-b border-line bg-ground/60 px-6 py-3">
      <div className="flex flex-wrap gap-x-8 gap-y-2">
        {rows.map(([a, l]) => {
          const errors = l?.issues.filter((i) => i.level === "error").length ?? 0;
          const warnings = l?.issues.filter((i) => i.level === "warning").length ?? 0;
          return (
            <button key={a} onClick={() => onPick(a)} aria-pressed={audience === a}
              className={`flex items-center gap-2 rounded-md px-2 py-1 text-sm transition-colors ${audience === a ? "bg-raised" : "hover:bg-raised/60"}`}>
              {!l ? <Spinner className="h-4 w-4 text-muted" /> : errors ? (
                <AlertTriangle className="h-4 w-4 text-danger" aria-hidden />
              ) : (
                <CheckCircle2 className="h-4 w-4 text-ok" aria-hidden />
              )}
              <span className="font-medium capitalize">{a} copy</span>
              <span className={errors ? "text-danger" : "text-muted"}>
                {!l ? "checking" : errors
                  ? `${errors} problem${errors === 1 ? "" : "s"} block export`
                  : warnings ? `ready, ${warnings} warning${warnings === 1 ? "" : "s"}` : "ready to export"}
              </span>
            </button>
          );
        })}
      </div>
      {blocking.length > 0 && (
        <ul className="mt-2 space-y-1 pl-2 text-sm">
          {blocking.slice(0, 6).map((i, n) => (
            <li key={n} className="flex items-baseline gap-2 text-danger">
              <button className="shrink-0 rounded border border-danger/40 px-1.5 font-mono text-xs hover:bg-danger/10" onClick={() => onJump(i.claim!)}
                title="Show this claim">{i.claim}</button>
              <span>{i.message}</span>
            </li>
          ))}
          {blocking.length > 6 && <li className="text-xs text-muted">{blocking.length - 6} more, marked on the claims.</li>}
        </ul>
      )}
      {general.length > 0 && (
        <ul className="mt-2 space-y-1 pl-2 text-sm">
          {general.map((i, n) => (
            <li key={n} className={`flex gap-2 ${i.level === "error" ? "text-danger" : "text-warn"}`}>
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />{i.message}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function LintBlocked({ error, onForce, onDismiss }: {
  error: unknown; onForce: (format: ExportFormat) => void; onDismiss: () => void;
}) {
  if (error instanceof ApiError && error.status === 409) {
    const issues = ((error.detail as { issues?: LintIssue[] })?.issues || []) as LintIssue[];
    return (
      <div className="rounded-md border border-danger/50 bg-danger/10 p-4 text-sm" role="alert">
        <div className="flex items-start justify-between gap-3">
          <p className="font-medium">Export stopped by {issues.length} problem{issues.length === 1 ? "" : "s"}</p>
          <button className="rounded p-1 text-muted hover:text-ink" onClick={onDismiss} aria-label="Dismiss"><X className="h-4 w-4" /></button>
        </div>
        <ul className="mt-2 list-disc space-y-1 pl-5">
          {issues.slice(0, 8).map((i, n) => (
            <li key={n}>{i.claim ? <Mono className="mr-1">{i.claim}</Mono> : null}{i.message}</li>
          ))}
          {issues.length > 8 && <li className="text-muted">{issues.length - 8} more, marked on the claims.</li>}
        </ul>
        <p className="mt-3 text-muted">Fix the marked claims, or export anyway. The override is written to the audit log.</p>
        <div className="mt-2 flex flex-wrap gap-2">
          {EXPORT_FORMATS.map(([format, label]) => (
            <button key={format} className="btn btn-danger btn-sm" onClick={() => onForce(format)}>
              Export {label} anyway
            </button>
          ))}
        </div>
      </div>
    );
  }
  return <ErrorBox error={error} onDismiss={onDismiss} />;
}

function ClaimsView({ report, byClaim, onFinding }: {
  report: Report; byClaim: Map<string, LintIssue[]>; onFinding: (id: string) => void;
}) {
  const b = report.body;
  return (
    <div className="mx-auto max-w-4xl px-6 py-6">
      {b.summary && <p className="max-w-[70ch] text-base leading-relaxed">{b.summary}</p>}
      {b.sections.map((s, si) => (
        <section key={si} className="mt-8">
          <h3 className="mb-3 text-[17px] font-semibold">{s.title}</h3>
          <ul className="space-y-2">
            {s.claims.map((c) => {
              const problems = byClaim.get(c.id) || [];
              const blocked = problems.some((p) => p.level === "error");
              const evidence = c.evidence || [];
              return (
                <li key={c.id} id={`claim-${c.id}`} className={`scroll-mt-4 rounded-md border px-4 py-3 ${blocked ? "border-danger/50 bg-danger/5" : "border-line/70"}`}>
                  <div className="flex items-start gap-3">
                    <Mono className="w-9 shrink-0 pt-0.5 text-muted">{c.id}</Mono>
                    <div className="min-w-0 flex-1">
                      <p className="max-w-[72ch] leading-relaxed">{c.text}</p>
                      <div className="mt-2 flex flex-wrap items-center gap-1.5">
                        <Badge value={c.status || "verified"} />
                        {c.severity && <Badge value={c.severity} label={`${c.severity[0].toUpperCase()}${c.severity.slice(1)} severity`} />}
                        {c.sensitive && <Badge value="sensitive" title="Special-category data (GDPR Art. 9), kept on purpose" />}
                        {evidence.map((f) => (
                          <button key={f} onClick={() => onFinding(f)} title="Open the stored finding"
                            className="inline-flex items-center gap-1 rounded-full border border-accent/40 bg-accent/5 px-2 py-px font-mono text-xs text-accent-text transition-colors hover:border-accent hover:bg-accent/15">
                            <Tag className="h-3 w-3" aria-hidden />{f}
                          </button>
                        ))}
                        {!evidence.length && c.status !== "inference" && (
                          <span className="text-xs font-medium text-danger">No evidence cited</span>
                        )}
                      </div>
                      {problems.map((p, n) => (
                        <p key={n} className={`mt-2 flex gap-1.5 text-sm ${p.level === "error" ? "text-danger" : "text-warn"}`}>
                          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />{p.message}
                        </p>
                      ))}
                    </div>
                  </div>
                </li>
              );
            })}
          </ul>
        </section>
      ))}
      {b.outstanding && b.outstanding.length > 0 && (
        <section className="mt-8">
          <h3 className="mb-2 text-[17px] font-semibold">Outstanding steps</h3>
          <ul className="list-disc space-y-1 pl-5">{b.outstanding.map((o, i) => <li key={i}>{o}</li>)}</ul>
        </section>
      )}
    </div>
  );
}

function Preview({ reportId, audience, cite }: { reportId: string; audience: Audience; cite: boolean }) {
  const [force, setForce] = useState(false);
  const [html, error] = useLoad(
    () => api.text(`/reports/${reportId}/render${qs({ audience, format: "html", cite, force: force || undefined })}`),
    [reportId, audience, cite, force],
  );
  if (error instanceof ApiError && error.status === 409) {
    const issues = ((error.detail as { issues?: LintIssue[] })?.issues || []) as LintIssue[];
    return (
      <div className="mx-auto max-w-2xl p-8">
        <p className="font-medium">This copy can't be rendered until {issues.length} problem{issues.length === 1 ? " is" : "s are"} fixed.</p>
        <p className="mt-1 text-sm text-muted">The problems are marked in the claims view. You can preview it anyway; the override is logged.</p>
        <button className="btn btn-danger mt-4" onClick={() => setForce(true)}>Preview anyway</button>
      </div>
    );
  }
  if (error) return <div className="p-5"><ErrorBox error={error} /></div>;
  if (html === null) return <Loading>Rendering the {audience} copy</Loading>;
  return (
    <div className="h-full bg-raised/40 p-4">
      <iframe title="Report preview" sandbox="" srcDoc={html}
        className="mx-auto block h-full min-h-[70vh] w-full max-w-4xl rounded-md border border-line bg-white shadow-float" />
    </div>
  );
}
