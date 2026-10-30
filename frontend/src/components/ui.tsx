import { useEffect, useRef, useState, type ReactNode } from "react";
import { AlertTriangle, Loader2, X } from "lucide-react";
import { ApiError } from "../api";

export function ErrorBox({ error, onDismiss }: { error: unknown; onDismiss?: () => void }) {
  if (!error) return null;
  const message =
    error instanceof ApiError ? error.message : error instanceof Error ? error.message : String(error);
  const status = error instanceof ApiError ? error.status : null;
  return (
    <div role="alert" className="flex items-start gap-2.5 rounded-md border border-danger/50 bg-danger/10 px-3 py-2.5 text-sm">
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-danger" aria-hidden />
      <div className="min-w-0 flex-1 whitespace-pre-wrap text-ink">
        {status === 422 && <span className="font-medium">Check these fields: </span>}
        {message}
      </div>
      {onDismiss && (
        <button className="-m-1 rounded p-1 text-muted hover:text-ink" onClick={onDismiss} aria-label="Dismiss error">
          <X className="h-4 w-4" />
        </button>
      )}
    </div>
  );
}

export function Empty({ title, icon, children, action }: {
  title: string; icon?: ReactNode; children?: ReactNode; action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-14 text-center">
      {icon && <div className="mb-1 text-muted">{icon}</div>}
      <p className="text-base font-medium text-ink">{title}</p>
      {children && <div className="max-w-md text-sm text-muted">{children}</div>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

export function Spinner({ className = "h-4 w-4" }: { className?: string }) {
  return <Loader2 className={`animate-spin ${className}`} aria-hidden />;
}

export function Loading({ children = "Loading" }: { children?: ReactNode }) {
  return (
    <p className="flex items-center gap-2 p-5 text-sm text-muted" role="status">
      <Spinner /> {children}
    </p>
  );
}

export type Tone = "ok" | "danger" | "warn" | "accent" | "muted";

const TONE_CLASS: Record<Tone, string> = {
  ok: "border-ok/45 bg-ok/10 text-ok",
  danger: "border-danger/50 bg-danger/10 text-danger",
  warn: "border-warn/50 bg-warn/10 text-warn",
  accent: "border-accent/55 bg-accent/10 text-accent-text",
  muted: "border-line bg-raised/60 text-muted",
};

const STATUS: Record<string, { tone: Tone; label?: string; dashed?: boolean; strike?: boolean }> = {
  running: { tone: "accent", label: "Running" },
  finished: { tone: "ok", label: "Finished" },
  failed: { tone: "danger", label: "Failed" },
  cancelled: { tone: "muted", label: "Stopped" },
  refused: { tone: "danger", label: "Declined" },
  ok: { tone: "ok", label: "OK" },
  error: { tone: "danger", label: "Error" },
  verified: { tone: "ok", label: "Verified" },
  unverified: { tone: "danger", label: "Unverified", dashed: true },
  candidate: { tone: "danger", label: "Unverified match", dashed: true },
  leaked: { tone: "warn", label: "Leaked data" },
  inference: { tone: "muted", label: "Inference" },
  confirmed: { tone: "accent", label: "Confirmed" },
  rejected: { tone: "muted", label: "Rejected", strike: true },
  active: { tone: "warn", label: "Active" },
  passive: { tone: "muted", label: "Passive" },
  warning: { tone: "warn", label: "Warning" },
  high: { tone: "danger", label: "High" },
  medium: { tone: "warn", label: "Medium" },
  low: { tone: "muted", label: "Low" },
  info: { tone: "muted", label: "Info" },
  sensitive: { tone: "warn", label: "Sensitive" },
};

/** A small status label. Shape (dashed, struck) repeats what the color says. */
export function Badge({ value, title, label }: { value: string; title?: string; label?: string }) {
  const s = STATUS[value] || { tone: "muted" as Tone };
  return (
    <span
      title={title}
      className={`inline-flex shrink-0 items-center gap-1 rounded-full border px-2 py-px text-2xs font-medium ${
        TONE_CLASS[s.tone]} ${s.dashed ? "border-dashed" : ""} ${s.strike ? "line-through" : ""}`}
    >
      {value === "running" && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-current" aria-hidden />}
      {label ?? s.label ?? value}
    </span>
  );
}

export function Pill({ tone = "muted", children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  return (
    <span title={title} className={`inline-flex shrink-0 items-center gap-1 rounded-full border px-2 py-px text-2xs font-medium ${TONE_CLASS[tone]}`}>
      {children}
    </span>
  );
}

export function Mono({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <span className={`font-mono text-[13px] ${className}`}>{children}</span>;
}

type SegOption<T> = readonly [T, ReactNode] | readonly [T, ReactNode, string];

/** Segmented control: one choice out of a few, all visible. */
export function Segmented<T extends string>({ value, options, onChange, label, size = "sm" }: {
  value: T; options: readonly SegOption<T>[]; onChange: (v: T) => void; label: string; size?: "sm" | "md";
}) {
  return (
    <div className="seg" role="group" aria-label={label}>
      {options.map(([key, text, title]) => (
        <button key={key} type="button" aria-pressed={value === key} title={title} onClick={() => onChange(key)}
          className={`seg-item ${size === "md" ? "min-h-[32px] px-3 text-sm" : ""}`}>
          {text}
        </button>
      ))}
    </div>
  );
}

/** A horizontal meter; `fraction` is 0..1. */
export function Meter({ fraction, tone = "accent", label }: { fraction: number; tone?: Tone; label: string }) {
  const pct = Math.max(0, Math.min(1, fraction)) * 100;
  const bar = { ok: "bg-ok", danger: "bg-danger", warn: "bg-warn", accent: "bg-accent", muted: "bg-muted" }[tone];
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-raised" role="meter" aria-label={label}
      aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(pct)}>
      <div className={`h-full rounded-full ${bar}`} style={{ width: `${pct}%` }} />
    </div>
  );
}

export function shortHash(h: string | null | undefined, n = 10): string {
  return h ? `${h.slice(0, n)}…` : "—";
}

export function when(ts: string | null | undefined): string {
  if (!ts) return "—";
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts;
  return d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function ago(ts: string | number | null | undefined): string {
  if (!ts) return "—";
  const t = typeof ts === "number" ? ts : new Date(ts).getTime();
  if (Number.isNaN(t)) return String(ts);
  const s = Math.round((Date.now() - t) / 1000);
  if (s < 45) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return new Date(t).toLocaleDateString(undefined, { dateStyle: "medium" });
}

export function timeOnly(ms: number): string {
  return new Date(ms).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function num(n: number | null | undefined): string {
  return n === null || n === undefined ? "—" : n.toLocaleString();
}

/** Load data, re-run when deps change; returns [data, error, loading, reload]. */
export function useLoad<T>(fn: () => Promise<T>, deps: unknown[]): [T | null, unknown, boolean, () => void] {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let live = true;
    setLoading(true);
    fn()
      .then((d) => {
        if (live) {
          setData(d);
          setError(null);
        }
      })
      .catch((e) => live && setError(e))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);
  return [data, error, loading, () => setTick((t) => t + 1)];
}

/** Returns a trigger that runs `fn` once, `ms` after the first call in a burst. */
export function useDebounced(fn: () => void, ms: number): () => void {
  const timer = useRef<number | null>(null);
  const latest = useRef(fn);
  latest.current = fn;
  useEffect(() => () => {
    if (timer.current) window.clearTimeout(timer.current);
  }, []);
  return () => {
    if (timer.current) return;
    timer.current = window.setTimeout(() => {
      timer.current = null;
      latest.current();
    }, ms);
  };
}

export function JsonView({ value }: { value: unknown }) {
  return (
    <pre className="scroll-thin max-h-[60vh] overflow-auto rounded-md border border-line bg-ground p-3 font-mono text-[13px] leading-5 text-ink">
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}

export function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    ref.current?.focus();
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/50 p-4 pt-[8vh] backdrop-blur-sm" onClick={onClose}>
      <div ref={ref} tabIndex={-1} role="dialog" aria-modal="true" aria-label={title}
        className="panel w-full max-w-3xl shadow-2xl outline-none" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between gap-3 border-b border-line px-5 py-3">
          <h2 className="min-w-0 truncate text-base font-semibold">{title}</h2>
          <button className="icon-btn" onClick={onClose} aria-label="Close">
            <X className="h-5 w-5" />
          </button>
        </div>
        <div className="scroll-thin max-h-[75vh] overflow-y-auto p-5">{children}</div>
      </div>
    </div>
  );
}
