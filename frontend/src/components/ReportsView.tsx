import { useEffect, useState } from "react";
import { api, ApiError, download, qs } from "../api";
import type { LintIssue, Report, ReportSummary } from "../types";
import { FindingModal } from "./EntityPanel";
import { Badge, Empty, ErrorBox, Mono, useLoad, when } from "./ui";

type Audience = "internal" | "client";

const AUDIENCE_HINT: Record<Audience, string> = {
  internal: "Shows finding ids, hashes, file paths and how the data was collected.",
  client: "Shows each source and its retrieval date only. No workspace details.",
};

export function ReportsView({ investigationId }: { investigationId: string }) {
  const [list, listError, , reloadList] = useLoad(
    () => api.get<ReportSummary[]>(`/investigations/${investigationId}/reports`),
    [investigationId],
  );
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    if (!selected && list && list.length) setSelected(list[0].id);
  }, [list, selected]);

  async function createDraft() {
    setCreating(true);
    setError(null);
    try {
      const r = await api.post<Report>(`/investigations/${investigationId}/reports`, {});
      reloadList();
      setSelected(r.id);
    } catch (e) {
      setError(e);
    } finally {
      setCreating(false);
    }
  }

  return (
    <div className="flex min-h-0 flex-1">
      <aside className="scroll-thin w-64 shrink-0 overflow-y-auto border-r border-line bg-panel p-3">
        <button className="btn btn-primary w-full justify-center" onClick={createDraft} disabled={creating}>
          {creating ? "Drafting…" : "Draft from graph"}
        </button>
        <p className="mt-2 text-2xs text-muted">
          A plain draft listing what the sources returned, every line cited. Use "Write report" in the Claude
          panel for a curated one.
        </p>
        <ErrorBox error={error || listError} />
        <ul className="mt-3 space-y-0.5">
          {list?.map((r) => (
            <li key={r.id}>
              <button onClick={() => setSelected(r.id)}
                className={`w-full rounded-md px-2.5 py-2 text-left hover:bg-raised ${selected === r.id ? "bg-raised ring-1 ring-line" : ""}`}>
                <div className="truncate text-sm font-medium">{r.title}</div>
                <div className="text-2xs text-muted">{r.created_by}, {when(r.updated_at)}</div>
              </button>
            </li>
          ))}
        </ul>
      </aside>
      <div className="min-w-0 flex-1">
        {selected ? (
          <ReportDetail key={selected} reportId={selected} />
        ) : (
          <Empty title="No reports yet">Draft one from the graph once a scan has found something.</Empty>
        )}
      </div>
    </div>
  );
}

function ReportDetail({ reportId }: { reportId: string }) {
  const [report, error] = useLoad(() => api.get<Report>(`/reports/${reportId}`), [reportId]);
  const [audience, setAudience] = useState<Audience>("internal");
  const [cite, setCite] = useState(true);
  const [view, setView] = useState<"claims" | "preview">("claims");
  const [lint] = useLoad(
    () => api.get<{ ok: boolean; issues: LintIssue[] }>(`/reports/${reportId}/lint${qs({ audience })}`),
    [reportId, audience],
  );
  const [finding, setFinding] = useState<string | null>(null);
  const [exportError, setExportError] = useState<unknown>(null);

  if (error) return <div className="p-5"><ErrorBox error={error} /></div>;
  if (!report) return <p className="p-5 text-muted">Loading report…</p>;

  const issues = lint?.issues || [];
  const errors = issues.filter((i) => i.level === "error");
  const byClaim = new Map<string, LintIssue[]>();
  for (const i of issues) if (i.claim) byClaim.set(i.claim, [...(byClaim.get(i.claim) || []), i]);
  const general = issues.filter((i) => !i.claim);

  const [exporting, setExporting] = useState<ExportFormat | null>(null);

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
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex flex-wrap items-center gap-3 border-b border-line px-5 py-3">
        <div className="mr-auto min-w-0">
          <h2 className="truncate text-base font-semibold">{report.title}</h2>
          <p className="text-xs text-muted">Updated {when(report.updated_at)} by {report.created_by}</p>
        </div>
        <div className="flex rounded-md border border-line p-0.5 text-xs" role="group" aria-label="Audience">
          {(["internal", "client"] as Audience[]).map((a) => (
            <button key={a} onClick={() => setAudience(a)} aria-pressed={audience === a} title={AUDIENCE_HINT[a]}
              className={`rounded px-2.5 py-1 capitalize ${audience === a ? "bg-raised text-ink" : "text-muted hover:text-ink"}`}>
              {a}
            </button>
          ))}
        </div>
        <label className="flex items-center gap-1.5 text-xs">
          <input type="checkbox" checked={cite} onChange={(e) => setCite(e.target.checked)} className="accent-[rgb(var(--accent))]" />
          Citations
        </label>
        <div className="flex rounded-md border border-line p-0.5 text-xs" role="group" aria-label="View">
          {(["claims", "preview"] as const).map((v) => (
            <button key={v} onClick={() => setView(v)} aria-pressed={view === v}
              className={`rounded px-2.5 py-1 capitalize ${view === v ? "bg-raised text-ink" : "text-muted hover:text-ink"}`}>
              {v}
            </button>
          ))}
        </div>
        {EXPORT_FORMATS.map(([format, label]) => (
          <button key={format} className="btn px-2.5 py-1 text-xs" disabled={exporting !== null}
            onClick={() => exportAs(format)}>
            {exporting === format ? "Preparing…" : `Export ${label}`}
          </button>
        ))}
      </div>

      <div className={`border-b px-5 py-2 text-sm ${errors.length ? "border-danger/40 bg-danger/5" : "border-line"}`}>
        {lint ? (
          errors.length ? (
            <span className="text-danger">
              {errors.length} problem{errors.length === 1 ? "" : "s"} block export for the {audience} copy. Fix the
              marked claims, or export anyway (the override is logged).
            </span>
          ) : (
            <span className="text-ok">
              Every claim is cited and passes the checks for the {audience} copy
              {issues.length ? `; ${issues.length} warning${issues.length === 1 ? "" : "s"} to review` : ""}.
            </span>
          )
        ) : (
          <span className="text-muted">Checking the report…</span>
        )}
        {general.length > 0 && (
          <ul className="mt-1 space-y-0.5 text-xs">
            {general.map((i, n) => <li key={n} className={i.level === "error" ? "text-danger" : "text-warn"}>{i.message}</li>)}
          </ul>
        )}
      </div>

      {exportError ? (
        <div className="px-5 pt-3">
          <LintBlocked error={exportError} onForce={(f) => exportAs(f, true)} />
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

type ExportFormat = "docx" | "pdf" | "html" | "md";
const EXPORT_FORMATS: [ExportFormat, string][] = [["docx", "Word"], ["pdf", "PDF"], ["html", "HTML"], ["md", "Markdown"]];

function LintBlocked({ error, onForce }: { error: unknown; onForce: (format: ExportFormat) => void }) {
  if (error instanceof ApiError && error.status === 409) {
    const issues = ((error.detail as { issues?: LintIssue[] })?.issues || []) as LintIssue[];
    return (
      <div className="rounded-md border border-danger/50 bg-danger/10 p-3 text-sm">
        <p className="font-medium">Export blocked by {issues.length} problem{issues.length === 1 ? "" : "s"}</p>
        <ul className="mt-1 list-disc space-y-0.5 pl-5 text-xs">
          {issues.slice(0, 8).map((i, n) => (
            <li key={n}>{i.claim ? <Mono>{i.claim}</Mono> : null} {i.message}</li>
          ))}
        </ul>
        <div className="mt-2 flex flex-wrap gap-2">
          {EXPORT_FORMATS.map(([format, label]) => (
            <button key={format} className="btn btn-danger px-2.5 py-1 text-xs" onClick={() => onForce(format)}>
              {label} anyway
            </button>
          ))}
        </div>
      </div>
    );
  }
  return <ErrorBox error={error} />;
}

function ClaimsView({ report, byClaim, onFinding }: {
  report: Report; byClaim: Map<string, LintIssue[]>; onFinding: (id: string) => void;
}) {
  const b = report.body;
  return (
    <div className="mx-auto max-w-4xl px-5 py-5">
      {b.summary && <p className="max-w-prose text-[15px] leading-relaxed">{b.summary}</p>}
      {b.sections.map((s, si) => (
        <section key={si} className="mt-6">
          <h3 className="mb-2 text-sm font-semibold">{s.title}</h3>
          <ul className="divide-y divide-line/60 border-y border-line/60">
            {s.claims.map((c) => {
              const problems = byClaim.get(c.id) || [];
              const blocked = problems.some((p) => p.level === "error");
              return (
                <li key={c.id} className={`py-2 ${blocked ? "bg-danger/5" : ""}`}>
                  <div className="flex items-start gap-3">
                    <Mono className="w-8 shrink-0 pt-0.5 text-muted">{c.id}</Mono>
                    <div className="min-w-0 flex-1">
                      <p className={c.status === "unverified" ? "text-ink" : ""}>{c.text}</p>
                      <div className="mt-1 flex flex-wrap items-center gap-1.5">
                        <Badge value={c.status || "verified"} />
                        {c.severity && <Badge value={c.severity} />}
                        {c.sensitive && <Badge value="sensitive" title="GDPR Art. 9 data, kept on purpose" />}
                        {(c.evidence || []).map((f) => (
                          <button key={f} onClick={() => onFinding(f)}
                            className="rounded border border-line px-1.5 py-px font-mono text-2xs text-muted hover:border-accent hover:text-ink">
                            {f}
                          </button>
                        ))}
                        {!(c.evidence || []).length && c.status !== "inference" && (
                          <span className="text-2xs text-danger">no evidence</span>
                        )}
                      </div>
                      {problems.map((p, n) => (
                        <p key={n} className={`mt-1 text-xs ${p.level === "error" ? "text-danger" : "text-warn"}`}>
                          {p.message}
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
        <section className="mt-6">
          <h3 className="mb-2 text-sm font-semibold">Outstanding steps</h3>
          <ul className="list-disc space-y-1 pl-5 text-sm">{b.outstanding.map((o, i) => <li key={i}>{o}</li>)}</ul>
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
      <div className="mx-auto max-w-2xl p-6">
        <p className="font-medium">This copy can't be rendered until {issues.length} problem{issues.length === 1 ? " is" : "s are"} fixed.</p>
        <p className="mt-1 text-sm text-muted">The problems are marked in the claims view. You can preview it anyway; the override is logged.</p>
        <button className="btn btn-danger mt-3" onClick={() => setForce(true)}>Preview anyway</button>
      </div>
    );
  }
  if (error) return <div className="p-5"><ErrorBox error={error} /></div>;
  if (html === null) return <p className="p-5 text-muted">Rendering…</p>;
  return (
    <iframe title="Report preview" sandbox="" srcDoc={html} className="h-full min-h-[70vh] w-full bg-white" />
  );
}
