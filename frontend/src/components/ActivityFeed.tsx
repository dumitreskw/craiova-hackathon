import { useMemo, useState, type ReactNode } from "react";
import {
  AlertTriangle, Ban, Bot, CheckCircle2, CircleDot, Radio, Search, Sparkles, type LucideIcon,
} from "lucide-react";
import { typeColor } from "../entityStyle";
import type { EntityView, StreamEvent } from "../types";
import { Empty, Segmented, timeOnly } from "./ui";

type Tone = "muted" | "ink" | "danger" | "ok" | "accent";
type Kind = "lookup" | "entity" | "claude" | "scan";

interface Line {
  tone: Tone;
  kind: Kind;
  icon: LucideIcon;
  body: ReactNode;
}

const TONE: Record<Tone, string> = {
  muted: "text-muted",
  ink: "text-ink",
  danger: "text-danger",
  ok: "text-ok",
  accent: "text-accent-text",
};

function arg(data: Record<string, unknown>): string {
  const input = data.input as { arg?: string } | undefined;
  return input?.arg ? String(input.arg) : "";
}

function describe(e: StreamEvent, onSelect: (id: string) => void): Line | null {
  const d = e.data;
  switch (e.event) {
    case "tool.started":
      return { tone: "muted", kind: "lookup", icon: Search,
        body: <>Asking <b className="font-medium text-ink">{String(d.connector)}</b> about <span className="font-mono text-[13px]">{arg(d)}</span></> };
    case "tool.finished": {
      const n = Number(d.new_entities ?? 0);
      return {
        tone: "ink", kind: "lookup", icon: CheckCircle2,
        body: (
          <>
            <b className="font-medium">{String(d.connector)}</b> answered with {String(d.found ?? 0)} results, {n} new
            {d.note ? <span className="text-muted">. {String(d.note)}</span> : null}
          </>
        ),
      };
    }
    case "tool.failed":
      return { tone: "danger", kind: "lookup", icon: AlertTriangle, body: <><b className="font-medium">{String(d.connector)}</b> failed: {String(d.error)}</> };
    case "tool.refused":
      return { tone: "danger", kind: "lookup", icon: Ban, body: <>Blocked <b className="font-medium">{String(d.connector)}</b>: {String(d.reason)}</> };
    case "entity.created": {
      const ent = d.entity as EntityView;
      return {
        tone: ent.status === "candidate" ? "danger" : "ink", kind: "entity", icon: CircleDot,
        body: (
          <button className="text-left hover:underline" onClick={() => onSelect(ent.id)}>
            <span className="mr-2 inline-block h-2.5 w-2.5 rounded-full align-middle" style={{ background: typeColor(ent.type) }} aria-hidden />
            {ent.status === "candidate" ? "Unverified match: " : "Found "}
            {ent.type} <span className="font-mono text-[13px]">{ent.label || ent.key}</span>
          </button>
        ),
      };
    }
    case "entity.updated": {
      const ent = d.entity as EntityView;
      return {
        tone: "accent", kind: "entity", icon: CircleDot,
        body: (
          <button className="text-left hover:underline" onClick={() => onSelect(ent.id)}>
            {ent.label || ent.key} is now {ent.status}
          </button>
        ),
      };
    }
    case "scan.finished":
      return { tone: d.status === "finished" ? "ok" : "danger", kind: "scan", icon: Radio, body: <>Scan {String(d.status)}: {String(d.summary ?? "")}</> };
    case "scan.failed":
      return { tone: "danger", kind: "scan", icon: AlertTriangle, body: <>Scan failed: {String(d.error)}</> };
    case "agent.started":
      return { tone: "accent", kind: "claude", icon: Sparkles,
        body: <>Claude started {d.worker ? <>the <b className="font-medium">{String(d.worker)}</b> worker</> : `a ${String(d.kind ?? "")} run`}{d.goal ? <span className="text-muted">: {String(d.goal)}</span> : null}</> };
    case "agent.tool":
      return { tone: "muted", kind: "claude", icon: Bot,
        body: <>Claude called <b className="font-medium text-ink">{String(d.connector ?? d.name ?? "a tool")}</b> {arg(d) && <span className="font-mono text-[13px]">{arg(d)}</span>}</> };
    case "agent.finished":
      return { tone: "ok", kind: "claude", icon: CheckCircle2, body: <>Claude finished: {String(d.summary ?? "")}</> };
    case "agent.refused":
      return { tone: "danger", kind: "claude", icon: Ban, body: <>Claude declined: {String(d.category ?? d.message ?? "")}</> };
    case "agent.failed":
      return { tone: "danger", kind: "claude", icon: AlertTriangle, body: <>Claude run failed: {String(d.error ?? "")}</> };
    default:
      // Streaming text, raw tool results and edges are too chatty for this list.
      return null;
  }
}

type FilterKey = "all" | Kind | "problems";

export function ActivityFeed({ events, onSelect }: { events: StreamEvent[]; onSelect: (id: string) => void }) {
  const [filter, setFilter] = useState<FilterKey>("all");
  const lines = useMemo(() => {
    const out: { key: number; at: number; line: Line }[] = [];
    for (let i = events.length - 1; i >= 0; i--) {
      const line = describe(events[i], onSelect);
      if (!line) continue;
      if (filter === "problems" ? line.tone !== "danger" : filter !== "all" && line.kind !== filter) continue;
      out.push({ key: i, at: events[i].at, line });
      if (out.length >= 400) break;
    }
    return out;
  }, [events, filter, onSelect]);

  return (
    <div className="scroll-thin min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-5xl px-6 py-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-sm text-muted">Every lookup and finding since this page opened, newest first.</p>
          <Segmented label="Show" value={filter} onChange={setFilter} options={[
            ["all", "Everything"], ["lookup", "Lookups"], ["entity", "Entities"], ["claude", "Claude"],
            ["scan", "Scans"], ["problems", "Problems"],
          ]} />
        </div>
        {lines.length === 0 ? (
          <Empty title={filter === "problems" ? "No failures or blocked calls" : "Quiet for now"} icon={<Radio className="h-8 w-8" />}>
            {filter === "problems"
              ? "Every lookup so far has answered."
              : "Add a target or start a Claude run; each lookup shows up here as it happens."}
          </Empty>
        ) : (
          <ol className="mt-4 divide-y divide-line/60 rounded-lg border border-line bg-panel" aria-live="polite">
            {lines.map(({ key, at, line }) => {
              const Icon = line.icon;
              return (
                <li key={key} className={`flex items-start gap-3 px-4 py-2.5 text-sm ${TONE[line.tone]}`}>
                  <span className="w-[5.5rem] shrink-0 pt-px font-mono text-xs text-muted">{timeOnly(at)}</span>
                  <Icon className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
                  <span className="min-w-0 break-words">{line.body}</span>
                </li>
              );
            })}
          </ol>
        )}
      </div>
    </div>
  );
}
