import cytoscape from "cytoscape";
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { DEFAULT_HIDDEN, TYPE_COLORS, typeColor } from "../entityStyle";
import type { InvestigationStream } from "../stream";
import type { EdgeView, EntityView, Graph } from "../types";
import { ErrorBox } from "./ui";

function cssRgb(name: string, fallback: string): string {
  const v = getComputedStyle(document.documentElement).getPropertyValue(`--${name}`).trim();
  return v ? `rgb(${v.split(/\s+/).join(",")})` : fallback;
}

function shortLabel(e: EntityView): string {
  const base = e.label || e.key;
  return base.length > 30 ? `${base.slice(0, 28)}…` : base;
}

function nodeData(e: EntityView, seeds: Set<string>) {
  return {
    ...e,
    id: e.id,
    short: shortLabel(e),
    color: typeColor(e.type),
    weight: seeds.has(e.id) ? 1.4 : 0.6 + e.confidence * 0.6,
  };
}

function nodeClasses(e: EntityView, seeds: Set<string>): string {
  return [e.status, seeds.has(e.id) ? "seed" : ""].filter(Boolean).join(" ");
}

function styles(): cytoscape.Stylesheet[] {
  const ink = cssRgb("ink", "#e4e9f1");
  const muted = cssRgb("muted", "#92a0b8");
  const line = cssRgb("line", "#34425a");
  const ground = cssRgb("ground", "#171e2b");
  const accent = cssRgb("accent", "#f2c94c");
  const danger = cssRgb("danger", "#e5484d");
  return [
    {
      selector: "node",
      style: {
        "background-color": "data(color)",
        width: "mapData(weight, 0.6, 1.4, 8, 30)",
        height: "mapData(weight, 0.6, 1.4, 8, 30)",
        label: "data(short)",
        "font-family": "Public Sans, system-ui, sans-serif",
        "font-size": 9,
        color: muted,
        "text-valign": "bottom",
        "text-margin-y": 3,
        "text-outline-color": ground,
        "text-outline-width": 2,
        "border-width": 0,
        // Labels appear once zoomed in enough to read them without overlap.
        "min-zoomed-font-size": 13,
      },
    },
    {
      selector: "node.seed",
      style: {
        "border-width": 3, "border-color": ink, "border-style": "double", color: ink,
        "font-size": 12, "font-weight": 600, "min-zoomed-font-size": 0,
      },
    },
    {
      selector: "node.candidate",
      style: {
        "background-opacity": 0.15,
        "border-width": 2,
        "border-style": "dashed",
        "border-color": danger,
        color: danger,
      },
    },
    { selector: "node.confirmed", style: { "border-width": 3, "border-color": accent } },
    {
      selector: "node:selected, node.near",
      style: { "border-width": 3, "border-color": accent, "border-style": "solid", color: ink, "min-zoomed-font-size": 0, "z-index": 10 },
    },
    { selector: ".hidden", style: { display: "none" } },
    {
      selector: "edge",
      style: {
        width: "mapData(confidence, 0, 1, 0.6, 3)",
        "line-color": line,
        "target-arrow-color": line,
        "target-arrow-shape": "triangle",
        "arrow-scale": 0.6,
        "curve-style": "bezier",
        opacity: 0.85,
      },
    },
    {
      selector: "edge.possible_match",
      style: { "line-style": "dashed", "line-color": danger, "target-arrow-color": danger, opacity: 1 },
    },
    {
      selector: "edge:selected, edge.near",
      style: {
        label: "data(type)",
        "font-size": 8,
        color: ink,
        "text-rotation": "autorotate",
        "text-outline-color": ground,
        "text-outline-width": 2,
        "line-color": accent,
        "target-arrow-color": accent,
      },
    },
  ] as cytoscape.Stylesheet[];
}

interface Props {
  investigationId: string;
  seedIds: string[];
  stream: InvestigationStream;
  selected: string | null;
  onSelect: (id: string | null) => void;
}

export function GraphView({ investigationId, seedIds, stream, selected, onSelect }: Props) {
  const box = useRef<HTMLDivElement>(null);
  const cyRef = useRef<cytoscape.Core | null>(null);
  const seeds = useRef(new Set<string>());
  const pendingEdges = useRef(new Map<string, EdgeView>());
  const layoutTimer = useRef<number | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [minConfidence, setMinConfidence] = useState(0);
  const [hidden, setHidden] = useState<Set<string>>(() => new Set(DEFAULT_HIDDEN));
  const [showCandidates, setShowCandidates] = useState(true);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [visible, setVisible] = useState(0);
  const filterRef = useRef({ minConfidence, hidden, showCandidates });
  filterRef.current = { minConfidence, hidden, showCandidates };
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;
  const fetching = useRef(new Set<string>());

  function passes(n: cytoscape.NodeSingular): boolean {
    const f = filterRef.current;
    const d = n.data() as EntityView;
    if (f.hidden.has(d.type)) return false;
    if (d.status === "candidate" && !f.showCandidates) return false;
    if (d.status === "rejected") return false;
    if (d.confidence < f.minConfidence && !seeds.current.has(d.id)) return false;
    return true;
  }

  function applyFilters() {
    const cy = cyRef.current;
    if (!cy) return;
    cy.batch(() => {
      cy.nodes().forEach((n) => {
        n.toggleClass("hidden", !passes(n));
      });
      cy.edges().forEach((e) => {
        e.toggleClass("hidden", e.source().hasClass("hidden") || e.target().hasClass("hidden"));
      });
    });
    const c: Record<string, number> = {};
    cy.nodes().forEach((n) => {
      const t = n.data("type") as string;
      c[t] = (c[t] || 0) + 1;
    });
    setCounts(c);
    setVisible(cy.nodes().not(".hidden").length);
  }

  function relayout(fit = false) {
    const cy = cyRef.current;
    if (!cy) return;
    const shown = cy.elements().not(".hidden");
    shown
      .layout({
        name: "cose",
        animate: false,
        randomize: false,
        fit,
        padding: 40,
        nodeRepulsion: () => 14000,
        idealEdgeLength: () => 60,
        nodeOverlap: 8,
        numIter: 1500,
      } as cytoscape.LayoutOptions)
      .run();
  }

  function scheduleLayout() {
    if (layoutTimer.current) window.clearTimeout(layoutTimer.current);
    layoutTimer.current = window.setTimeout(() => {
      applyFilters();
      relayout(false);
    }, 700);
  }

  function addEdge(cy: cytoscape.Core, e: EdgeView): boolean {
    if (cy.getElementById(e.id).nonempty()) return true;
    if (cy.getElementById(e.source).empty() || cy.getElementById(e.target).empty()) return false;
    cy.add({ group: "edges", data: { ...e }, classes: e.type });
    return true;
  }

  // Build the graph once per investigation.
  useEffect(() => {
    let live = true;
    const cy = cytoscape({
      container: box.current,
      style: styles(),
      wheelSensitivity: 0.25,
      minZoom: 0.1,
      maxZoom: 3,
    });
    cyRef.current = cy;
    cy.on("tap", "node", (evt) => onSelectRef.current(evt.target.id()));
    cy.on("tap", (evt) => {
      if (evt.target === cy) onSelectRef.current(null);
    });
    cy.on("mouseover", "node", (evt) => evt.target.addClass("near").connectedEdges().addClass("near"));
    cy.on("mouseout", "node", (evt) => evt.target.removeClass("near").connectedEdges().removeClass("near"));

    api
      .get<Graph>(`/investigations/${investigationId}/graph?include_candidates=true`)
      .then((g) => {
        if (!live) return;
        cy.batch(() => {
          cy.add(g.nodes.map((n) => ({ group: "nodes" as const, data: nodeData(n.data, seeds.current), classes: nodeClasses(n.data, seeds.current) })));
          cy.add(g.edges.map((e) => ({ group: "edges" as const, data: { ...e.data }, classes: e.data.type })));
        });
        applyFilters();
        relayout(true);
      })
      .catch((e) => live && setError(e));

    return () => {
      live = false;
      if (layoutTimer.current) window.clearTimeout(layoutTimer.current);
      cy.destroy();
      cyRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [investigationId]);

  // Seeds can arrive later (a new target); restyle when the list changes.
  useEffect(() => {
    seeds.current = new Set(seedIds);
    const cy = cyRef.current;
    if (!cy) return;
    cy.nodes().forEach((n) => {
      n.data(nodeData(n.data() as EntityView, seeds.current));
      n.toggleClass("seed", seeds.current.has(n.id()));
    });
    for (const id of seedIds) if (cy.getElementById(id).empty()) fetchNode(id);
  }, [seedIds.join(",")]); // eslint-disable-line react-hooks/exhaustive-deps

  function addNode(cy: cytoscape.Core, ent: EntityView) {
    cy.add({ group: "nodes", data: nodeData(ent, seeds.current), classes: nodeClasses(ent, seeds.current),
      position: { x: Math.random() * 400, y: Math.random() * 400 } });
    for (const [id, e] of pendingEdges.current) {
      if (addEdge(cy, e)) pendingEdges.current.delete(id);
    }
    scheduleLayout();
  }

  // An edge can reference an entity that existed before this page loaded
  // without an event (e.g. a freshly added target): fetch it.
  function fetchNode(id: string) {
    if (fetching.current.has(id)) return;
    fetching.current.add(id);
    api
      .get<EntityView>(`/entities/${id}`)
      .then((ent) => {
        const cy = cyRef.current;
        if (cy && cy.getElementById(ent.id).empty()) addNode(cy, ent);
      })
      .catch(() => undefined)
      .finally(() => fetching.current.delete(id));
  }

  // Apply live events.
  useEffect(
    () =>
      stream.subscribe(({ event, data }) => {
        const cy = cyRef.current;
        if (!cy) return;
        if (event === "entity.created" || event === "entity.updated") {
          const ent = data.entity as EntityView;
          const existing = cy.getElementById(ent.id);
          if (existing.nonempty()) {
            existing.data(nodeData(ent, seeds.current));
            existing.classes(nodeClasses(ent, seeds.current));
          } else {
            addNode(cy, ent);
          }
          scheduleLayout();
        } else if (event === "edge.created") {
          const e = data.edge as EdgeView;
          if (!addEdge(cy, e)) {
            pendingEdges.current.set(e.id, e);
            for (const end of [e.source, e.target]) if (cy.getElementById(end).empty()) fetchNode(end);
          }
          scheduleLayout();
        } else if (event === "scan.finished") {
          scheduleLayout();
        }
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [investigationId],
  );

  useEffect(applyFilters, [minConfidence, hidden, showCandidates]); // eslint-disable-line react-hooks/exhaustive-deps

  // Reflect selection made elsewhere (activity feed, entity panel links).
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.nodes(":selected").unselect();
    if (selected) {
      const n = cy.getElementById(selected);
      if (n.nonempty()) {
        n.select();
        if (!n.hasClass("hidden")) cy.animate({ center: { eles: n } }, { duration: 250 });
      }
    }
  }, [selected]);

  const typeList = useMemo(() => Object.keys(counts).sort((a, b) => (counts[b] || 0) - (counts[a] || 0)), [counts]);
  const total = Object.values(counts).reduce((a, b) => a + b, 0);

  function toggleType(t: string) {
    setHidden((prev) => {
      const next = new Set(prev);
      if (next.has(t)) next.delete(t);
      else next.add(t);
      return next;
    });
  }

  return (
    <div className="absolute inset-0">
      <div ref={box} className="absolute inset-0" aria-label="Entity graph" role="img" />

      <div className="pointer-events-none absolute left-3 top-3 flex max-w-[calc(100%-1.5rem)] flex-col gap-2">
        <div className="pointer-events-auto flex flex-wrap items-center gap-3 rounded-md border border-line bg-panel/95 px-3 py-2 text-xs shadow-lg">
          <label className="flex items-center gap-2">
            <span className="text-muted">Min confidence</span>
            <input type="range" min={0} max={1} step={0.05} value={minConfidence}
              onChange={(e) => setMinConfidence(Number(e.target.value))} className="w-28 accent-[rgb(var(--accent))]" />
            <span className="w-8 font-mono">{minConfidence.toFixed(2)}</span>
          </label>
          <label className="flex items-center gap-1.5">
            <input type="checkbox" checked={showCandidates} onChange={(e) => setShowCandidates(e.target.checked)}
              className="accent-[rgb(var(--accent))]" />
            <span>Unverified matches</span>
          </label>
          <button className="btn px-2 py-0.5 text-xs" onClick={() => cyRef.current?.fit(undefined, 40)}>Fit</button>
          <button className="btn px-2 py-0.5 text-xs" onClick={() => relayout(true)}>Re-arrange</button>
          <span className="text-muted">{visible} of {total} shown</span>
        </div>
        {error ? <div className="pointer-events-auto"><ErrorBox error={error} /></div> : null}
      </div>

      {total === 0 && !error && (
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
          <p className="max-w-sm text-center text-sm text-muted">
            Add a target above. Entities appear here as each source answers, linked to what revealed them.
          </p>
        </div>
      )}

      <div className="absolute bottom-3 left-3 max-w-[calc(100%-1.5rem)] rounded-md border border-line bg-panel/95 px-3 py-2 text-xs shadow-lg">
        <div className="flex flex-wrap gap-1">
          {typeList.map((t) => (
            <button key={t} onClick={() => toggleType(t)} aria-pressed={!hidden.has(t)}
              className={`flex items-center gap-1.5 rounded px-1.5 py-0.5 hover:bg-raised ${hidden.has(t) ? "opacity-40" : ""}`}
              title={hidden.has(t) ? `Show ${t}` : `Hide ${t}`}>
              <span className="h-2.5 w-2.5 rounded-full" style={{ background: TYPE_COLORS[t] || "#8a97ad" }} />
              {t} <span className="text-muted">{counts[t]}</span>
            </button>
          ))}
        </div>
        <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1 text-muted">
          <span className="flex items-center gap-1.5">
            <span className="inline-block h-0 w-5 border-t-2 border-muted" /> thicker line: more sources agree
          </span>
          <span className="flex items-center gap-1.5">
            <span className="inline-block h-0 w-5 border-t-2 border-dashed border-danger" /> unverified name match
          </span>
          <span className="flex items-center gap-1.5">
            <span className="inline-block h-3 w-3 rounded-full border-2 border-double border-ink" /> your target
          </span>
        </div>
      </div>
    </div>
  );
}
