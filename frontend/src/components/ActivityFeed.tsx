import { useMemo, useState, type ReactNode } from "react";
import { typeColor } from "../entityStyle";
import type { EntityView, StreamEvent } from "../types";
import { timeOnly } from "./ui";

type Tone = "muted" | "ink" | "danger" | "ok" | "accent";

interface Line {
  tone: Tone;
  body: ReactNode;
}

const TONE: Record<Tone, string> = {
  muted: "text-muted",
  ink: "text-ink",
  danger: "text-danger",
  ok: "text-ok",
  accent: "text-accent",
};

function arg(data: Record<string, unknown>): string {
  const input = data.input as { arg?: string } | undefined;
  return input?.arg ? String(input.arg) : "";
}

function describe(e: StreamEvent, onSelect: (id: string) => void): Line | null {
  const d = e.data;
  switch (e.event) {
    case "tool.started":
      return { tone: "muted", body: <>Asking <b>{String(d.connector)}</b> about <span className="font-mono">{arg(d)}</span></> };
    case "tool.finished": {
      const n = Number(d.new_entities ?? 0);
      return {
        tone: "ink",
        body: (
          <>
            <b>{String(d.connector)}</b> answered: {String(d.found ?? 0)} results, {n} new
            {d.note ? <span className="text-muted">. {String(d.note)}</span> : null}
          </>
        ),
      };
    }
    case "tool.failed":
      return { tone: "danger", body: <><b>{String(d.connector)}</b> failed: {String(d.error)}</> };
    case "tool.refused":
      return { tone: "danger", body: <>Blocked <b>{String(d.connector)}</b>: {String(d.reason)}</> };
    case "entity.created": {
      const ent = d.entity as EntityView;
      return {
        tone: ent.status === "candidate" ? "danger" : "ink",
        body: (
          <button className="text-left hover:underline" onClick={() => onSelect(ent.id)}>
            <span className="mr-1.5 inline-block h-2 w-2 rounded-full" style={{ background: typeColor(ent.type) }} />
            {ent.status === "candidate" ? "Unverified match " : "Found "}
            {ent.type} <span className="font-mono">{ent.label || ent.key}</span>
          </button>
        ),
      };
    }
    case "entity.updated": {
      const ent = d.entity as EntityView;
      return {
        tone: "accent",
        body: (
          <button className="text-left hover:underline" onClick={() => onSelect(ent.id)}>
            {ent.label || ent.key} is now {ent.status}
          </button>
        ),
      };
    }
    case "scan.finished":
      return { tone: d.status === "finished" ? "ok" : "danger", body: <>Scan {String(d.status)}: {String(d.summary ?? "")}</> };
    case "scan.failed":
      return { tone: "danger", body: <>Scan failed: {String(d.error)}</> };
    case "agent.tool":
      return { tone: "muted", body: <>Claude called <b>{String(d.connector ?? d.name ?? "a tool")}</b> {arg(d) && <span className="font-mono">{arg(d)}</span>}</> };
    case "agent.finished":
      return { tone: "ok", body: <>Claude finished: {String(d.summary ?? "")}</> };
    case "agent.refused":
      return { tone: "danger", body: <>Claude declined: {String(d.category ?? d.message ?? "")}</> };
    case "agent.failed":
      return { tone: "danger", body: <>Claude run failed: {String(d.error ?? "")}</> };
    case "agent.text":
    case "edge.created":
      return null;
    default:
      return {
        tone: "muted",
        body: (
          <>
            <span className="font-mono">{e.event}</span>{" "}
            <span className="font-mono text-2xs">{JSON.stringify(d).slice(0, 160)}</span>
          </>
        ),
      };
  }
}

export function ActivityFeed({ events, onSelect }: { events: StreamEvent[]; onSelect: (id: string) => void }) {
  const [onlyProblems, setOnlyProblems] = useState(false);
  const lines = useMemo(() => {
    const out: { key: number; at: number; line: Line }[] = [];
    for (let i = events.length - 1; i >= 0; i--) {
      const line = describe(events[i], onSelect);
      if (!line) continue;
      if (onlyProblems && line.tone !== "danger") continue;
      out.push({ key: i, at: events[i].at, line });
      if (out.length >= 300) break;
    }
    return out;
  }, [events, onlyProblems, onSelect]);

  return (
    <div>
      <div className="sticky top-0 flex items-center justify-between border-b border-line bg-panel px-4 py-2 text-xs">
        <span className="text-muted">Newest first, since this page opened</span>
        <label className="flex items-center gap-1.5">
          <input type="checkbox" checked={onlyProblems} onChange={(e) => setOnlyProblems(e.target.checked)}
            className="accent-[rgb(var(--accent))]" />
          Problems only
        </label>
      </div>
      {lines.length === 0 ? (
        <p className="p-4 text-sm text-muted">
          {onlyProblems ? "No failures or blocked calls so far." : "Quiet. Add a target or start a Claude run to see each lookup here."}
        </p>
      ) : (
        <ol className="divide-y divide-line/60">
          {lines.map(({ key, at, line }) => (
            <li key={key} className={`flex gap-2 px-4 py-1.5 text-[13px] ${TONE[line.tone]}`}>
              <span className="shrink-0 pt-px font-mono text-2xs text-muted">{timeOnly(at)}</span>
              <span className="min-w-0 break-words">{line.body}</span>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
