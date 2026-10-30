import { useEffect, useState, type ReactNode } from "react";
import { ApiError } from "../api";

export function ErrorBox({ error, onDismiss }: { error: unknown; onDismiss?: () => void }) {
  if (!error) return null;
  const message =
    error instanceof ApiError ? error.message : error instanceof Error ? error.message : String(error);
  const status = error instanceof ApiError ? error.status : null;
  return (
    <div role="alert" className="flex items-start gap-2 rounded-md border border-danger/50 bg-danger/10 px-3 py-2 text-sm">
      <div className="min-w-0 flex-1 whitespace-pre-wrap text-ink">
        {status === 422 && <span className="font-medium">Check these fields: </span>}
        {message}
      </div>
      {onDismiss && (
        <button className="text-muted hover:text-ink" onClick={onDismiss} aria-label="Dismiss error">
          ×
        </button>
      )}
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-1 px-6 py-10 text-center">
      <p className="font-medium text-ink">{title}</p>
      {children && <div className="max-w-sm text-sm text-muted">{children}</div>}
    </div>
  );
}

const STATUS_STYLES: Record<string, string> = {
  running: "border-accent/60 text-accent",
  finished: "border-ok/50 text-ok",
  failed: "border-danger/60 text-danger",
  cancelled: "border-line text-muted",
  refused: "border-danger/60 text-danger",
  ok: "border-ok/50 text-ok",
  error: "border-danger/60 text-danger",
  verified: "border-ok/50 text-ok",
  unverified: "border-danger/60 text-danger border-dashed",
  candidate: "border-danger/60 text-danger border-dashed",
  leaked: "border-warn/60 text-warn",
  inference: "border-line text-muted italic",
  confirmed: "border-accent/70 text-accent",
  rejected: "border-line text-muted line-through",
  active: "border-line text-muted",
  passive: "border-line text-muted",
  warning: "border-warn/60 text-warn",
};

export function Badge({ value, title }: { value: string; title?: string }) {
  return (
    <span
      title={title}
      className={`inline-flex shrink-0 items-center rounded border px-1.5 py-px text-2xs font-medium ${
        STATUS_STYLES[value] || "border-line text-muted"
      }`}
    >
      {value}
    </span>
  );
}

export function Mono({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <span className={`font-mono text-[12px] ${className}`}>{children}</span>;
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

export function timeOnly(ms: number): string {
  return new Date(ms).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
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

export function JsonView({ value }: { value: unknown }) {
  return (
    <pre className="scroll-thin max-h-[60vh] overflow-auto rounded-md border border-line bg-ground p-3 font-mono text-[12px] leading-5 text-ink">
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}

export function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-ground/70 p-4 pt-[8vh] backdrop-blur-sm" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className="panel w-full max-w-3xl shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-line px-4 py-2.5">
          <h2 className="font-semibold">{title}</h2>
          <button className="btn btn-quiet px-2 py-0.5" onClick={onClose} aria-label="Close">
            Close
          </button>
        </div>
        <div className="p-4">{children}</div>
      </div>
    </div>
  );
}
