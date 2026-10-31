import { useMemo, useState } from "react";
import {
  Boxes, ChevronRight, CircleCheck, Cpu, FileSearch, Network, RefreshCw,
  ScanSearch, ShieldCheck, Sparkles,
} from "lucide-react";
import { api, ApiError } from "../api";
import type { ModelCatalog, ModelInfo } from "../types";
import { ErrorBox, Loading, Pill, Spinner, useLoad } from "./ui";

// Icon per agent task (keys from the backend catalogue, app/agent/catalog.py).
const TASK_ICON: Record<string, typeof Cpu> = {
  collection: ScanSearch,
  deep: Network,
  worker: Boxes,
  verify: ShieldCheck,
  report: FileSearch,
  review: CircleCheck,
};

// The pipeline the nodes are laid out as. "deep" fans out to "worker".
const PIPELINE = ["collection", "verify", "report", "review"];
const DEEP = ["deep", "worker"];

function providerLabel(p: string): string {
  return p === "anthropic" ? "Claude" : p === "venice" ? "Venice" : p;
}

/** The <select> of models for one task. Claude and Venice are grouped; models
 *  that can't call tools can't drive an agent, so they're shown disabled. */
function ModelSelect({
  value, models, onChange,
}: {
  value: string;
  models: ModelInfo[];
  onChange: (id: string) => void;
}) {
  const groups = useMemo(() => {
    const by: Record<string, ModelInfo[]> = {};
    for (const m of models) {
      if (m.hidden) continue;
      (by[m.provider] ||= []).push(m);
    }
    return by;
  }, [models]);
  const known = models.some((m) => m.id === value);

  return (
    <select className="field font-mono text-[13px]" value={value} onChange={(e) => onChange(e.target.value)}>
      {!known && value && <option value={value}>{value} (unknown)</option>}
      {Object.entries(groups).map(([provider, list]) => (
        <optgroup key={provider} label={providerLabel(provider)}>
          {list.map((m) => (
            <option key={m.id} value={m.id} disabled={m.tools === false}>
              {m.label}
              {m.tools === false ? " — no tool use" : ""}
              {m.available === false ? " — key missing" : ""}
            </option>
          ))}
        </optgroup>
      ))}
    </select>
  );
}

function TaskNode({
  taskKey, label, detail, value, defaultId, models, onChange,
}: {
  taskKey: string;
  label: string;
  detail: string;
  value: string;
  defaultId: string;
  models: ModelInfo[];
  onChange: (id: string) => void;
}) {
  const Icon = TASK_ICON[taskKey] ?? Cpu;
  const chosen = models.find((m) => m.id === value);
  const provider = chosen?.provider ?? (value.startsWith("claude-") ? "anthropic" : "venice");
  const unusable = chosen && chosen.available === false;
  return (
    <div className="flex w-[260px] shrink-0 flex-col rounded-lg border border-line bg-panel p-3 shadow-sm">
      <div className="flex items-center gap-2">
        <span className="grid h-7 w-7 place-items-center rounded-md bg-raised text-accent-text">
          <Icon className="h-4 w-4" aria-hidden />
        </span>
        <span className="min-w-0 flex-1 truncate text-sm font-semibold" title={label}>{label}</span>
        <Pill tone={provider === "venice" ? "accent" : "muted"}>{providerLabel(provider)}</Pill>
      </div>
      <p className="mt-1.5 min-h-[32px] text-xs leading-snug text-muted">{detail}</p>
      <ModelSelect value={value} models={models} onChange={onChange} />
      <div className="mt-1.5 flex items-center gap-1.5 text-2xs text-muted">
        {chosen?.reasoning && <Pill tone="muted">reasoning</Pill>}
        {chosen?.uncensored && <Pill tone="warn">uncensored</Pill>}
        {value === defaultId ? <span>default</span> : <span className="text-accent-text">changed from {defaultId}</span>}
        {unusable && <span className="text-danger">key missing on server</span>}
      </div>
    </div>
  );
}

function Arrow() {
  return <ChevronRight className="mx-0.5 hidden h-5 w-5 shrink-0 self-center text-muted md:block" aria-hidden />;
}

export function ModelsView() {
  const [cat, error, loading, reload] = useLoad(() => api.get<ModelCatalog>("/models"), []);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<unknown>(null);
  const [saved, setSaved] = useState(false);

  const assignments = cat ? { ...cat.assignments, ...edits } : {};
  const dirty = Object.keys(edits).some((k) => cat && edits[k] !== cat.assignments[k]);

  const set = (key: string, id: string) => {
    setSaved(false);
    setEdits((e) => ({ ...e, [key]: id }));
  };

  const save = async () => {
    setSaving(true);
    setSaveError(null);
    try {
      await api.put("/models/assignments", { assignments: edits });
      setEdits({});
      setSaved(true);
      reload();
    } catch (e) {
      setSaveError(e instanceof ApiError ? e : new Error(String(e)));
    } finally {
      setSaving(false);
    }
  };

  if (loading && !cat) return <Loading>Loading models</Loading>;

  const venice = cat?.providers.venice;
  const tasksByKey = Object.fromEntries((cat?.tasks ?? []).map((t) => [t.key, t]));
  const node = (key: string) => {
    const t = tasksByKey[key];
    if (!t || !cat) return null;
    return (
      <TaskNode
        key={key} taskKey={key} label={t.label} detail={t.detail}
        value={assignments[key] ?? cat.defaults[key]} defaultId={cat.defaults[key]}
        models={cat.models} onChange={(id) => set(key, id)}
      />
    );
  };

  return (
    <div className="scroll-thin min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-6xl px-6 py-8">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="flex items-center gap-2 text-[26px] font-semibold leading-tight tracking-tight">
              <Sparkles className="h-6 w-6 text-accent-text" aria-hidden /> Agent models
            </h1>
            <p className="mt-2 max-w-prose text-muted">
              Choose which model runs each step of the agent pipeline. Changes apply to the next run;
              a running agent keeps its model. A declined turn still ends the run — nothing switches
              models automatically.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button className="btn btn-sm" onClick={reload} disabled={loading}>
              <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} aria-hidden /> Refresh
            </button>
            <button className="btn btn-primary btn-sm" onClick={save} disabled={!dirty || saving}>
              {saving ? <Spinner /> : <CircleCheck className="h-4 w-4" aria-hidden />}
              {saving ? "Saving" : "Save assignments"}
            </button>
          </div>
        </div>

        {error ? <div className="mt-4"><ErrorBox error={error} /></div> : null}
        {saveError ? <div className="mt-4"><ErrorBox error={saveError} onDismiss={() => setSaveError(null)} /></div> : null}
        {saved && !dirty && (
          <p className="mt-4 flex items-center gap-1.5 text-sm text-ok">
            <CircleCheck className="h-4 w-4" aria-hidden /> Saved. New runs will use these models.
          </p>
        )}

        {/* Provider availability */}
        <div className="mt-6 flex flex-wrap gap-2">
          <Pill tone={cat?.providers.anthropic.available ? "ok" : "muted"}>
            Claude {cat?.providers.anthropic.available ? "ready" : "no API key"}
          </Pill>
          <Pill tone={venice?.available ? "ok" : "muted"}>
            Venice {venice?.available ? "ready" : "no API key"}
          </Pill>
          {venice && !venice.configured_list && (
            <span className="text-xs text-muted">
              Set <code className="font-mono">VENICE_INFERENCE_KEY</code> on the server to list and run Venice models.
            </span>
          )}
        </div>

        {/* The pipeline */}
        <div className="mt-8">
          <h2 className="mb-3 text-xs font-semibold uppercase tracking-wide text-muted">Collection &amp; report pipeline</h2>
          <div className="flex flex-col gap-3 md:flex-row md:flex-wrap md:items-stretch">
            {PIPELINE.map((key, i) => (
              <div key={key} className="flex md:items-stretch">
                {i > 0 && <Arrow />}
                {node(key)}
              </div>
            ))}
          </div>
        </div>

        <div className="mt-8">
          <h2 className="mb-3 text-xs font-semibold uppercase tracking-wide text-muted">Deep analysis</h2>
          <div className="flex flex-col gap-3 md:flex-row md:items-stretch">
            {DEEP.map((key, i) => (
              <div key={key} className="flex md:items-stretch">
                {i > 0 && <Arrow />}
                {node(key)}
              </div>
            ))}
            <p className="max-w-[240px] self-center text-xs text-muted">
              The orchestrator plans the investigation and dispatches the workers; they then hand off to the
              report pipeline above.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
