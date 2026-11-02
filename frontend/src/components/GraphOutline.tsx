import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, ChevronRight, PanelLeftClose, Search, X } from "lucide-react";
import { typeColor } from "../entityStyle";
import type { EdgeView, EntityView } from "../types";
import { Badge } from "./ui";

// The side list: every entity that passes the graph filters, grouped by what
// it means for the case rather than by where it sits on the canvas. Picking a
// row selects the entity and focuses the graph on it.

export type Category =
  | "identity" | "accounts" | "contacts" | "flags" | "mentions" | "infra" | "namesakes" | "other" | "rejected";

const CATEGORIES: { key: Category; title: string }[] = [
  { key: "identity", title: "Identity" },
  { key: "accounts", title: "Accounts" },
  { key: "contacts", title: "Contacts" },
  { key: "flags", title: "Red flags" },
  { key: "namesakes", title: "Namesakes" },
  { key: "mentions", title: "Web mentions" },
  { key: "infra", title: "Infrastructure" },
  { key: "other", title: "Other" },
  { key: "rejected", title: "Rejected" },
];

const BY_TYPE: Record<string, Category> = {
  Person: "identity", Organization: "identity", Location: "identity",
  SocialProfile: "accounts", Username: "accounts", Repository: "accounts",
  Email: "contacts", Phone: "contacts",
  Breach: "flags", Vulnerability: "flags",
  URL: "mentions", Document: "mentions",
  Domain: "infra", IPAddress: "infra", ASN: "infra", Certificate: "infra",
};

const ROW_LIMIT = 50;

/**
 * Person entities that are namesakes: flagged so by the resolver
 * (attributes.namesake), or tied to a target only through possible_match.
 */
export function namesakeIds(
  nodes: Map<string, EntityView>, edges: Map<string, EdgeView>, seeds: Set<string>,
): Set<string> {
  const toSeed = new Map<string, { possible: number; other: number }>();
  for (const e of edges.values()) {
    for (const [me, other] of [[e.source, e.target], [e.target, e.source]] as const) {
      if (!seeds.has(other) || seeds.has(me)) continue;
      const c = toSeed.get(me) ?? toSeed.set(me, { possible: 0, other: 0 }).get(me)!;
      if (e.type === "possible_match") c.possible++;
      else c.other++;
    }
  }
  const out = new Set<string>();
  for (const n of nodes.values()) {
    if (n.type !== "Person" || seeds.has(n.id) || n.status === "candidate") continue;
    const c = toSeed.get(n.id);
    if (n.attributes?.namesake === true || (c && c.possible > 0 && c.other === 0)) out.add(n.id);
  }
  return out;
}

export function categoryOf(e: EntityView, namesakes: Set<string>): Category {
  if (e.status === "rejected") return "rejected";
  if (namesakes.has(e.id)) return "namesakes";
  if (e.status === "candidate") return "flags";
  return BY_TYPE[e.type] ?? "other";
}

interface Props {
  entities: EntityView[];
  namesakes: Set<string>;
  seeds: Set<string>;
  selected: string | null;
  onPick: (e: EntityView) => void;
  onClose: () => void;
}

export function GraphOutline({ entities, namesakes, seeds, selected, onPick, onClose }: Props) {
  const [q, setQ] = useState("");
  const [collapsed, setCollapsed] = useState<Set<Category>>(() => new Set(["infra", "rejected"]));
  const [showAll, setShowAll] = useState<Set<Category>>(() => new Set());
  const listRef = useRef<HTMLDivElement>(null);

  const groups = useMemo(() => {
    const term = q.trim().toLowerCase();
    const map = new Map<Category, EntityView[]>();
    for (const e of entities) {
      if (term && !(e.label || "").toLowerCase().includes(term) && !e.key.toLowerCase().includes(term)) continue;
      const c = categoryOf(e, namesakes);
      (map.get(c) ?? map.set(c, []).get(c)!).push(e);
    }
    for (const list of map.values()) {
      list.sort((a, b) =>
        Number(seeds.has(b.id)) - Number(seeds.has(a.id))
        || b.confidence - a.confidence
        || (a.label || a.key).localeCompare(b.label || b.key));
    }
    return CATEGORIES.filter((c) => map.has(c.key)).map((c) => ({ ...c, items: map.get(c.key)! }));
  }, [entities, namesakes, seeds, q]);

  const searching = q.trim().length > 0;

  // Keep the selected row in view when the selection comes from the canvas.
  useEffect(() => {
    if (!selected) return;
    const el = listRef.current?.querySelector<HTMLElement>(`[data-entity="${CSS.escape(selected)}"]`);
    el?.scrollIntoView({ block: "nearest" });
  }, [selected]);

  function toggle(set: Set<Category>, c: Category): Set<Category> {
    const next = new Set(set);
    if (next.has(c)) next.delete(c);
    else next.add(c);
    return next;
  }

  return (
    <aside className="flex h-full w-72 shrink-0 flex-col border-r border-line bg-panel max-md:absolute max-md:inset-y-0 max-md:left-0 max-md:z-20 max-md:shadow-float"
      aria-label="Entities by category">
      <div className="flex items-center justify-between gap-2 border-b border-line px-3 py-2">
        <span className="text-sm font-medium">Entities</span>
        <button className="icon-btn h-8 w-8" onClick={onClose} aria-label="Hide the entity list" title="Hide list">
          <PanelLeftClose className="h-4 w-4" />
        </button>
      </div>
      <div className="relative border-b border-line px-3 py-2">
        <Search className="pointer-events-none absolute left-5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted" aria-hidden />
        <input className="field h-9 pl-8 pr-8" value={q} onChange={(e) => setQ(e.target.value)}
          placeholder="Filter the list" aria-label="Filter the entity list" />
        {q && (
          <button className="absolute right-4 top-1/2 -translate-y-1/2 rounded p-1 text-muted hover:text-ink"
            onClick={() => setQ("")} aria-label="Clear the list filter"><X className="h-4 w-4" /></button>
        )}
      </div>
      <div ref={listRef} className="scroll-thin min-h-0 flex-1 overflow-y-auto py-1">
        {groups.length === 0 && (
          <p className="px-4 py-3 text-sm text-muted">{searching ? `Nothing matches "${q.trim()}".` : "No entities yet."}</p>
        )}
        {groups.map((g) => {
          const open = searching || !collapsed.has(g.key);
          const all = showAll.has(g.key);
          const rows = all ? g.items : g.items.slice(0, ROW_LIMIT);
          return (
            <section key={g.key} aria-label={g.title}>
              <h3>
                <button className="flex w-full items-center gap-1.5 px-3 py-1.5 text-left text-xs font-semibold uppercase tracking-wide text-muted hover:text-ink"
                  aria-expanded={open} onClick={() => setCollapsed((s) => toggle(s, g.key))}>
                  {open ? <ChevronDown className="h-3.5 w-3.5" aria-hidden /> : <ChevronRight className="h-3.5 w-3.5" aria-hidden />}
                  <span className={`flex-1 ${g.key === "flags" ? "text-danger" : ""}`}>{g.title}</span>
                  <span className="font-mono font-normal normal-case">{g.items.length}</span>
                </button>
              </h3>
              {open && (
                <ul className="pb-1">
                  {rows.map((e) => {
                    const sel = e.id === selected;
                    return (
                      <li key={e.id}>
                        <button data-entity={e.id} onClick={() => onPick(e)} aria-current={sel ? "true" : undefined}
                          title={`${e.type}: ${e.key}`}
                          className={`flex w-full items-center gap-2 border-l-2 px-3 py-1.5 text-left text-[13px] ${
                            sel ? "border-accent bg-accent/10" : "border-transparent hover:bg-raised"}`}>
                          <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: typeColor(e.type) }} aria-hidden />
                          <span className={`min-w-0 flex-1 truncate ${e.status === "rejected" ? "text-muted line-through" : ""}`}>
                            {e.label || e.key}
                          </span>
                          {seeds.has(e.id) && <Badge value="target" label="Target" />}
                          <StatusChip e={e} />
                          <span className="w-8 shrink-0 text-right font-mono text-2xs text-muted" title="Confidence">
                            {e.confidence.toFixed(2)}
                          </span>
                        </button>
                      </li>
                    );
                  })}
                  {g.items.length > ROW_LIMIT && (
                    <li>
                      <button className="w-full px-3 py-1 text-left text-xs text-accent-text hover:underline"
                        onClick={() => setShowAll((s) => toggle(s, g.key))}>
                        {all ? "Show fewer" : `Show all ${g.items.length}`}
                      </button>
                    </li>
                  )}
                </ul>
              )}
            </section>
          );
        })}
      </div>
    </aside>
  );
}

function StatusChip({ e }: { e: EntityView }) {
  if (e.status === "confirmed" || e.status === "rejected" || e.status === "candidate") {
    return <Badge value={e.status} label={e.status === "candidate" ? "Unverified" : undefined} />;
  }
  if (e.verdict === "uncertain") {
    return <Badge value="warning" label="Uncertain" title={e.verdict_reason ?? "Verification could not settle this"} />;
  }
  return null;
}
