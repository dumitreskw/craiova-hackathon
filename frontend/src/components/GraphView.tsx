import { useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import Graph from "graphology";
import Sigma from "sigma";
import type { NodeDisplayData, EdgeDisplayData, PartialButFor } from "sigma/types";
import type { Settings } from "sigma/settings";
import FA2Layout from "graphology-layout-forceatlas2/worker";
import { inferSettings } from "graphology-layout-forceatlas2";
import {
  Filter, Fullscreen, Layers, Minus, Pause, Play, Plus, Search, SlidersHorizontal, Workflow, X,
} from "lucide-react";
import { api } from "../api";
import { DEFAULT_HIDDEN, typeColor } from "../entityStyle";
import { clusterId, computeView, plural, type Filters, type View, type ViewNode } from "../graph/model";
import type { InvestigationStream } from "../stream";
import type { EdgeView, EntityView, Graph as GraphData } from "../types";
import { ErrorBox, Spinner } from "./ui";

interface NodeAttrs {
  x: number;
  y: number;
  size: number;
  color: string;
  label: string;
  etype: string;
  status: string;
  seed: boolean;
  count: number;
}

interface EdgeAttrs {
  size: number;
  color: string;
  etype: string;
  count: number;
}

type G = Graph<NodeAttrs, EdgeAttrs>;
type Point = { x: number; y: number };

// Survive tab switches: reopening the graph shows the same picture at once.
const positionCache = new Map<string, Map<string, Point>>();
const expandedCache = new Map<string, Set<string>>();

interface Palette {
  ink: string;
  muted: string;
  panel: string;
  line: string;
  canvas: string;
  accent: string;
  danger: string;
  edge: string;
  faded: string;
}

function cssRgb(name: string, alpha = 1): string {
  const raw = getComputedStyle(document.documentElement).getPropertyValue(`--${name}`).trim();
  const [r, g, b] = raw.split(/\s+/).map(Number);
  return alpha >= 1 ? `rgb(${r}, ${g}, ${b})` : `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

function readPalette(): Palette {
  const light = cssRgb("canvas").startsWith("rgb(2");
  return {
    ink: cssRgb("ink"),
    muted: cssRgb("muted"),
    panel: cssRgb("panel"),
    line: cssRgb("line"),
    canvas: cssRgb("canvas"),
    accent: cssRgb("accent"),
    danger: cssRgb("danger"),
    // Blue-grey edges at low opacity: structure without noise.
    edge: light ? "rgba(96, 116, 130, 0.30)" : "rgba(144, 164, 174, 0.26)",
    faded: light ? "rgba(180, 190, 200, 0.55)" : "rgba(70, 84, 98, 0.55)",
  };
}

function withAlpha(hex: string, a: number): string {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${a})`;
}

function compact(n: number): string {
  return n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k` : String(n);
}

type LabelData = PartialButFor<NodeDisplayData, "x" | "y" | "size" | "label" | "color">;
type DrawFn = Settings<NodeAttrs, EdgeAttrs>["defaultDrawNodeLabel"];

function roundRect(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

/** Labels get a halo in the canvas color so they stay legible over edges. */
function makeDrawLabel(pal: { current: Palette }): DrawFn {
  return (ctx, data: LabelData, settings) => {
    if (!data.label) return;
    const size = settings.labelSize;
    ctx.font = `${settings.labelWeight} ${size}px ${settings.labelFont}`;
    const x = data.x + data.size + 4;
    const y = data.y + size / 3;
    ctx.lineJoin = "round";
    ctx.lineWidth = 3.5;
    ctx.strokeStyle = pal.current.canvas;
    ctx.strokeText(data.label, x, y);
    ctx.fillStyle = pal.current.ink;
    ctx.fillText(data.label, x, y);
  };
}

/** Hovered node: a panel-colored tag with the full label. */
function makeDrawHover(pal: { current: Palette }): DrawFn {
  return (ctx, data: LabelData, settings) => {
    const size = settings.labelSize;
    ctx.font = `600 ${size}px ${settings.labelFont}`;
    const label = data.label || "";
    const w = ctx.measureText(label).width;
    const pad = 7;
    const h = size + pad * 1.4;
    const x = data.x + data.size + 6;
    const y = data.y - h / 2;
    ctx.shadowColor = "rgba(0,0,0,0.35)";
    ctx.shadowBlur = 10;
    ctx.fillStyle = pal.current.panel;
    roundRect(ctx, x, y, w + pad * 2, h, 5);
    ctx.fill();
    ctx.shadowBlur = 0;
    ctx.strokeStyle = pal.current.line;
    ctx.lineWidth = 1;
    ctx.stroke();
    ctx.fillStyle = pal.current.ink;
    ctx.fillText(label, x + pad, data.y + size / 3);
    ctx.beginPath();
    ctx.arc(data.x, data.y, data.size + 2.5, 0, Math.PI * 2);
    ctx.strokeStyle = pal.current.accent;
    ctx.lineWidth = 2;
    ctx.stroke();
  };
}

interface Props {
  investigationId: string;
  seedIds: string[];
  stream: InvestigationStream;
  selected: string | null;
  onSelect: (id: string | null) => void;
}

export function GraphView({ investigationId, seedIds, stream, selected, onSelect }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const graphRef = useRef<G>(new Graph<NodeAttrs, EdgeAttrs>({ multi: true, type: "directed", allowSelfLoops: false }));
  const sigmaRef = useRef<Sigma<NodeAttrs, EdgeAttrs> | null>(null);
  const layoutRef = useRef<FA2Layout | null>(null);
  const layoutTimer = useRef<number | null>(null);
  const raw = useRef({ nodes: new Map<string, EntityView>(), edges: new Map<string, EdgeView>() });
  const pendingEdges = useRef(new Map<string, EdgeView>());
  const fetching = useRef(new Set<string>());
  const viewRef = useRef<View | null>(null);
  const hoverRef = useRef<{ node: string | null; neighbors: Set<string> }>({ node: null, neighbors: new Set() });
  const special = useRef<{ candidates: string[]; clusters: string[]; seeds: string[] }>({ candidates: [], clusters: [], seeds: [] });
  const palette = useRef<Palette>(readPalette());
  const selectedRef = useRef(selected);
  selectedRef.current = selected;
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;
  const focusAfterSync = useRef<string | null>(null);
  const firstSync = useRef(true);

  const [ready, setReady] = useState(false);
  const [loadError, setLoadError] = useState<unknown>(null);
  const [version, setVersion] = useState(0);
  const [hidden, setHidden] = useState<Set<string>>(() => new Set(DEFAULT_HIDDEN));
  const [minConfidence, setMinConfidence] = useState(0);
  const [showCandidates, setShowCandidates] = useState(true);
  const [showRejected, setShowRejected] = useState(false);
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set(expandedCache.get(investigationId) || []));
  const [arranging, setArranging] = useState(false);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [legendOpen, setLegendOpen] = useState(true);
  const [shown, setShown] = useState({ nodes: 0, groups: 0 });

  const seedKey = seedIds.join(",");
  const seeds = useMemo(() => new Set(seedIds), [seedKey]); // eslint-disable-line react-hooks/exhaustive-deps

  // ---- renderer -----------------------------------------------------------
  useEffect(() => {
    const container = containerRef.current!;
    const graph = graphRef.current;
    const pal = palette;
    const renderer = new Sigma<NodeAttrs, EdgeAttrs>(graph, container, {
      allowInvalidContainer: true,
      renderEdgeLabels: false,
      labelRenderedSizeThreshold: 9,
      labelDensity: 0.6,
      labelGridCellSize: 120,
      labelFont: '"IBM Plex Sans", system-ui, sans-serif',
      labelSize: 13,
      labelWeight: "500",
      labelColor: { color: pal.current.ink },
      zIndex: true,
      stagePadding: 48,
      minCameraRatio: 0.02,
      maxCameraRatio: 5,
      defaultDrawNodeLabel: makeDrawLabel(pal),
      defaultDrawNodeHover: makeDrawHover(pal),
      nodeReducer: (node, data) => {
        const res: Partial<NodeDisplayData> = { ...data };
        const hov = hoverRef.current;
        if (hov.node) {
          if (node === hov.node || hov.neighbors.has(node)) {
            res.forceLabel = true;
            res.zIndex = 2;
          } else {
            res.color = pal.current.faded;
            res.label = "";
            res.zIndex = 0;
          }
        }
        if (node === selectedRef.current) {
          res.forceLabel = true;
          res.highlighted = true;
          res.zIndex = 3;
        }
        return res;
      },
      edgeReducer: (edge, data) => {
        const hov = hoverRef.current;
        if (!hov.node) return data;
        const ends = graph.extremities(edge);
        if (ends[0] === hov.node || ends[1] === hov.node) {
          return { ...data, color: "#F59E0B", size: Math.max(data.size, 1.6), zIndex: 1 } as Partial<EdgeDisplayData>;
        }
        return { ...data, hidden: true };
      },
    });
    sigmaRef.current = renderer;

    renderer.on("enterNode", ({ node }) => {
      hoverRef.current = { node, neighbors: new Set(graph.neighbors(node)) };
      container.style.cursor = "pointer";
      renderer.refresh({ skipIndexation: true });
    });
    renderer.on("leaveNode", () => {
      hoverRef.current = { node: null, neighbors: new Set() };
      container.style.cursor = "";
      renderer.refresh({ skipIndexation: true });
    });
    renderer.on("clickNode", ({ node }) => {
      if (graph.getNodeAttribute(node, "count")) {
        setExpanded((prev) => new Set(prev).add(node));
      } else {
        onSelectRef.current(node);
      }
    });
    renderer.on("clickStage", () => onSelectRef.current(null));

    // Marks WebGL can't draw cheaply: dashed rings on unverified matches,
    // target rings, group counts and the selection ring. There are few of these.
    renderer.createCanvasContext("marks", { style: { pointerEvents: "none" } });
    renderer.resize(true); // size the new layer's pixel buffer
    const marks = renderer.getCanvases().marks.getContext("2d")!;
    renderer.on("afterRender", () => {
      const { width, height } = renderer.getDimensions();
      marks.clearRect(0, 0, width, height);
      const p = pal.current;
      const hov = hoverRef.current;
      const at = (id: string) => {
        const d = renderer.getNodeDisplayData(id);
        if (!d || d.hidden) return null;
        const v = renderer.framedGraphToViewport(d);
        if (v.x < -40 || v.y < -40 || v.x > width + 40 || v.y > height + 40) return null;
        marks.globalAlpha = hov.node && id !== hov.node && !hov.neighbors.has(id) ? 0.18 : 1;
        return { x: v.x, y: v.y, r: renderer.scaleSize(d.size), color: d.color };
      };
      const ring = (x: number, y: number, r: number, color: string, width: number, dash: number[] = []) => {
        marks.beginPath();
        marks.setLineDash(dash);
        marks.arc(x, y, r, 0, Math.PI * 2);
        marks.strokeStyle = color;
        marks.lineWidth = width;
        marks.stroke();
        marks.setLineDash([]);
      };
      const s = special.current;
      for (const id of s.clusters) {
        const n = at(id);
        if (!n) continue;
        ring(n.x, n.y, n.r + 3.5, n.color, 1.5);
        if (n.r >= 8) {
          const count = graph.getNodeAttribute(id, "count");
          marks.font = `600 ${Math.min(13, Math.max(10, n.r * 0.7))}px "IBM Plex Sans", sans-serif`;
          marks.textAlign = "center";
          marks.textBaseline = "middle";
          marks.fillStyle = "#0f1720";
          marks.fillText(compact(count), n.x, n.y + 0.5);
          marks.textAlign = "start";
          marks.textBaseline = "alphabetic";
        }
      }
      for (const id of s.seeds) {
        const n = at(id);
        if (n) ring(n.x, n.y, n.r + 4, p.accent, 2.5);
      }
      for (const id of s.candidates) {
        const n = at(id);
        if (n) ring(n.x, n.y, n.r + 3.5, p.danger, 1.75, [3.5, 2.5]);
      }
      const sel = selectedRef.current;
      if (sel && graph.hasNode(sel)) {
        const n = at(sel);
        if (n) {
          ring(n.x, n.y, n.r + 7, withAlphaRgb(p.accent, 0.35), 6);
          ring(n.x, n.y, n.r + 6, p.accent, 2);
        }
      }
      marks.globalAlpha = 1;
    });

    // Follow the theme.
    const onTheme = () => {
      palette.current = readPalette();
      renderer.setSetting("labelColor", { color: palette.current.ink });
      graph.updateEachEdgeAttributes((_, a) => ({ ...a, color: edgeColor(a.etype, palette.current) }));
      renderer.refresh();
    };
    const mo = new MutationObserver(onTheme);
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    const mq = window.matchMedia("(prefers-color-scheme: light)");
    mq.addEventListener("change", onTheme);

    return () => {
      mo.disconnect();
      mq.removeEventListener("change", onTheme);
      savePositions();
      if (layoutTimer.current) window.clearTimeout(layoutTimer.current);
      layoutRef.current?.kill();
      layoutRef.current = null;
      renderer.kill();
      sigmaRef.current = null;
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  function savePositions() {
    const g = graphRef.current;
    let cache = positionCache.get(investigationId);
    if (!cache) positionCache.set(investigationId, (cache = new Map()));
    g.forEachNode((id, a) => cache!.set(id, { x: a.x, y: a.y }));
  }

  // ---- data ---------------------------------------------------------------
  useEffect(() => {
    let live = true;
    api
      .get<GraphData>(`/investigations/${investigationId}/graph?include_candidates=true`)
      .then((g) => {
        if (!live) return;
        for (const n of g.nodes) raw.current.nodes.set(n.data.id, n.data);
        for (const e of g.edges) raw.current.edges.set(e.data.id, e.data);
        setReady(true);
        setVersion((v) => v + 1);
      })
      .catch((e) => live && setLoadError(e));
    return () => {
      live = false;
    };
  }, [investigationId]);

  function fetchNode(id: string) {
    if (fetching.current.has(id)) return;
    fetching.current.add(id);
    api
      .get<EntityView>(`/entities/${id}`)
      .then((ent) => {
        raw.current.nodes.set(ent.id, ent);
        settlePending();
        setVersion((v) => v + 1);
      })
      .catch(() => undefined)
      .finally(() => fetching.current.delete(id));
  }

  function settlePending() {
    for (const [id, e] of pendingEdges.current) {
      if (raw.current.nodes.has(e.source) && raw.current.nodes.has(e.target)) {
        raw.current.edges.set(id, e);
        pendingEdges.current.delete(id);
      }
    }
  }

  // Live additions, applied in batches so a busy scan doesn't re-render per event.
  useEffect(() => {
    let timer: number | null = null;
    const flush = () => {
      timer = null;
      settlePending();
      setVersion((v) => v + 1);
    };
    const unsubscribe = stream.subscribe(({ event, data }) => {
      if (event === "entity.created" || event === "entity.updated") {
        const ent = data.entity as EntityView;
        raw.current.nodes.set(ent.id, ent);
      } else if (event === "edge.created") {
        const e = data.edge as EdgeView;
        if (raw.current.nodes.has(e.source) && raw.current.nodes.has(e.target)) {
          raw.current.edges.set(e.id, e);
        } else {
          pendingEdges.current.set(e.id, e);
          for (const end of [e.source, e.target]) if (!raw.current.nodes.has(end)) fetchNode(end);
        }
      } else {
        return;
      }
      if (timer === null) timer = window.setTimeout(flush, 700);
    });
    return () => {
      unsubscribe();
      if (timer !== null) window.clearTimeout(timer);
    };
  }, [investigationId]); // eslint-disable-line react-hooks/exhaustive-deps

  // A target added after load may not be in the graph yet.
  useEffect(() => {
    if (!ready) return;
    for (const id of seedIds) if (!raw.current.nodes.has(id)) fetchNode(id);
  }, [ready, seedKey]); // eslint-disable-line react-hooks/exhaustive-deps

  // ---- view sync ----------------------------------------------------------
  useEffect(() => {
    if (!ready) return;
    const filters: Filters = { hidden, minConfidence, showCandidates, showRejected };
    const view = computeView(raw.current.nodes, raw.current.edges, filters, seeds, expanded);
    viewRef.current = view;
    const changed = syncGraph(view);
    expandedCache.set(investigationId, expanded);
    setShown({ nodes: view.nodes.size - view.clusters.size, groups: view.clusters.size });
    const renderer = sigmaRef.current;
    if (firstSync.current) {
      firstSync.current = false;
      const cached = positionCache.get(investigationId);
      // Cached positions are already arranged: only settle what is new.
      runLayout(cached && cached.size ? 1200 : undefined);
    } else if (changed) {
      runLayout(Math.min(4000, 1500 + changed * 20));
    } else {
      renderer?.refresh();
    }
    const focus = focusAfterSync.current;
    if (focus && graphRef.current.hasNode(focus)) {
      focusAfterSync.current = null;
      window.setTimeout(() => focusNode(focus), 60);
    }
  }, [ready, version, hidden, minConfidence, showCandidates, showRejected, expanded, seeds]); // eslint-disable-line react-hooks/exhaustive-deps

  /** Bring the graphology graph in line with the view; returns how many items changed. */
  function syncGraph(view: View): number {
    const g = graphRef.current;
    const pal = palette.current;
    let cache = positionCache.get(investigationId);
    if (!cache) positionCache.set(investigationId, (cache = new Map()));
    let changed = 0;

    const dropNodes: string[] = [];
    g.forEachNode((id, a) => {
      if (!view.nodes.has(id)) {
        cache!.set(id, { x: a.x, y: a.y });
        dropNodes.push(id);
      }
    });
    for (const id of dropNodes) g.dropNode(id);
    changed += dropNodes.length;

    const dropEdges: string[] = [];
    g.forEachEdge((id) => {
      if (!view.edges.has(id)) dropEdges.push(id);
    });
    for (const id of dropEdges) if (g.hasEdge(id)) g.dropEdge(id);

    const neighbours = new Map<string, string[]>();
    for (const e of view.edges.values()) {
      (neighbours.get(e.source) ?? neighbours.set(e.source, []).get(e.source)!).push(e.target);
      (neighbours.get(e.target) ?? neighbours.set(e.target, []).get(e.target)!).push(e.source);
    }
    const spread = Math.max(40, Math.sqrt(view.nodes.size) * 12);
    const placed = (id: string): Point | null => {
      if (g.hasNode(id)) return { x: g.getNodeAttribute(id, "x"), y: g.getNodeAttribute(id, "y") };
      return null;
    };
    const near = (p: Point, r: number): Point => {
      const a = Math.random() * Math.PI * 2;
      const d = r * (0.4 + Math.random() * 0.6);
      return { x: p.x + Math.cos(a) * d, y: p.y + Math.sin(a) * d };
    };

    let todo: string[] = [];
    for (const [id, n] of view.nodes) {
      const attrs = nodeAttrs(n);
      if (g.hasNode(id)) {
        g.mergeNodeAttributes(id, attrs);
        continue;
      }
      const c = cache.get(id);
      if (c) {
        g.addNode(id, { ...attrs, x: c.x, y: c.y });
        changed++;
      } else {
        todo.push(id);
      }
    }
    // Place new nodes next to the hub they hang off (or the group they came
    // out of), so expanding a group or a live addition doesn't scatter.
    for (let pass = 0; pass < 4 && todo.length; pass++) {
      const next: string[] = [];
      for (const id of todo) {
        const n = view.nodes.get(id)!;
        let anchor: Point | null = null;
        let radius = 12;
        if (n.owner) {
          const group = cache.get(clusterId(n.owner, n.etype));
          anchor = group ?? placed(n.owner) ?? cache.get(n.owner) ?? null;
          radius = group ? 18 : 14;
        }
        if (!anchor) for (const m of neighbours.get(id) || []) if ((anchor = placed(m))) break;
        if (!anchor && pass < 3) {
          next.push(id);
          continue;
        }
        const p = anchor ? near(anchor, radius) : near({ x: 0, y: 0 }, spread);
        g.addNode(id, { ...nodeAttrs(n), x: p.x, y: p.y });
        changed++;
      }
      todo = next;
    }

    for (const [id, e] of view.edges) {
      const attrs: EdgeAttrs = {
        size: e.count > 1 ? Math.min(4, 0.8 + Math.log2(e.count) * 0.5) : e.etype === "possible_match" ? 1.5 : 0.7,
        color: edgeColor(e.etype, pal),
        etype: e.etype,
        count: e.count,
      };
      if (g.hasEdge(id)) g.mergeEdgeAttributes(id, attrs);
      else if (g.hasNode(e.source) && g.hasNode(e.target)) {
        g.addDirectedEdgeWithKey(id, e.source, e.target, attrs);
        changed++;
      }
    }

    const s = { candidates: [] as string[], clusters: [] as string[], seeds: [] as string[] };
    for (const n of view.nodes.values()) {
      if (n.count) s.clusters.push(n.id);
      else if (n.status === "candidate") s.candidates.push(n.id);
      if (n.seed) s.seeds.push(n.id);
    }
    special.current = s;
    return changed;
  }

  // ---- layout -------------------------------------------------------------
  function runLayout(ms?: number) {
    const g = graphRef.current;
    if (g.order < 2) return;
    if (layoutTimer.current) window.clearTimeout(layoutTimer.current);
    if (!layoutRef.current || !layoutRef.current.isRunning()) {
      layoutRef.current?.kill();
      const settings = inferSettings(g);
      layoutRef.current = new FA2Layout(g, {
        settings: { ...settings, barnesHutOptimize: g.order > 300, slowDown: 1 + Math.log(g.order), gravity: 1 },
      });
      layoutRef.current.start();
    }
    setArranging(true);
    const budget = ms ?? Math.min(9000, Math.max(2500, 2000 + g.order * 2));
    layoutTimer.current = window.setTimeout(stopLayout, budget);
  }

  function stopLayout() {
    if (layoutTimer.current) window.clearTimeout(layoutTimer.current);
    layoutTimer.current = null;
    layoutRef.current?.stop();
    setArranging(false);
    savePositions();
  }

  // ---- camera & selection -------------------------------------------------
  function focusNode(id: string) {
    const r = sigmaRef.current;
    if (!r || !graphRef.current.hasNode(id)) return;
    const d = r.getNodeDisplayData(id);
    if (!d) return;
    const cam = r.getCamera();
    cam.animate({ x: d.x, y: d.y, ratio: Math.min(cam.ratio, 0.35) }, { duration: 450 });
  }

  // Selection made elsewhere (activity feed, entity links, search).
  useEffect(() => {
    sigmaRef.current?.refresh({ skipIndexation: true });
    if (!selected || !ready) return;
    const view = viewRef.current;
    const group = view?.folded.get(selected);
    if (group) {
      focusAfterSync.current = selected;
      setExpanded((prev) => new Set(prev).add(group));
      return;
    }
    if (graphRef.current.hasNode(selected)) focusNode(selected);
    else focusAfterSync.current = selected;
  }, [selected, ready]); // eslint-disable-line react-hooks/exhaustive-deps

  function reveal(ent: EntityView) {
    if (hidden.has(ent.type)) setHidden((prev) => {
      const next = new Set(prev);
      next.delete(ent.type);
      return next;
    });
    if (ent.status === "candidate") setShowCandidates(true);
    if (ent.status === "rejected") setShowRejected(true);
    if (ent.confidence < minConfidence) setMinConfidence(0);
    focusAfterSync.current = ent.id;
    onSelect(ent.id);
  }

  function zoom(by: number) {
    const cam = sigmaRef.current?.getCamera();
    if (cam) cam.animate({ ratio: cam.ratio / by }, { duration: 200 });
  }

  // ---- legend -------------------------------------------------------------
  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    let candidates = 0;
    let rejected = 0;
    for (const e of raw.current.nodes.values()) {
      if (e.status === "candidate") candidates++;
      if (e.status === "rejected") rejected++;
      c[e.type] = (c[e.type] || 0) + 1;
    }
    return { byType: c, candidates, rejected, total: raw.current.nodes.size };
  }, [version]);
  const typeList = useMemo(
    () => Object.keys(counts.byType).sort((a, b) => counts.byType[b] - counts.byType[a]),
    [counts],
  );

  function toggleType(t: string) {
    setHidden((prev) => {
      const next = new Set(prev);
      if (next.has(t)) next.delete(t);
      else next.add(t);
      return next;
    });
  }

  const filtersActive = minConfidence > 0 || !showCandidates || showRejected;

  return (
    <div className="graph-surface absolute inset-0 overflow-hidden">
      <div ref={containerRef} className="absolute inset-0" role="img"
        aria-label={`Entity graph: ${shown.nodes} entities and ${shown.groups} groups shown. Use the search box to find one; details open in the side panel.`} />

      {/* top-left: find and filter */}
      <div className="absolute left-3 top-3 z-10 flex max-w-[calc(100%-200px)] items-start gap-2">
        <GraphSearch nodes={raw.current.nodes} version={version} onPick={reveal} />
        <div className="relative">
          <button className={`btn h-10 gap-2 shadow-float ${filtersActive ? "border-accent/70" : ""}`}
            aria-expanded={filtersOpen} onClick={() => setFiltersOpen((o) => !o)}>
            <SlidersHorizontal className="h-4 w-4" aria-hidden />
            Filters
            {filtersActive && <span className="h-2 w-2 rounded-full bg-accent" aria-label="Filters active" />}
          </button>
          {filtersOpen && (
            <div className="floating absolute left-0 top-12 w-72 space-y-4 p-4" role="dialog" aria-label="Graph filters">
              <div>
                <div className="flex items-baseline justify-between">
                  <label className="label mb-0" htmlFor="min-conf">Minimum confidence</label>
                  <span className="font-mono text-xs text-muted">{minConfidence.toFixed(2)}</span>
                </div>
                <input id="min-conf" type="range" min={0} max={1} step={0.05} value={minConfidence}
                  onChange={(e) => setMinConfidence(Number(e.target.value))}
                  className="mt-2 w-full accent-[rgb(var(--accent))]" />
                <p className="hint mt-1">Targets always stay visible.</p>
              </div>
              <label className="flex items-start gap-2.5 text-sm">
                <input type="checkbox" className="mt-1 accent-[rgb(var(--accent))]" checked={showCandidates}
                  onChange={(e) => setShowCandidates(e.target.checked)} />
                <span>Unverified name matches <span className="text-muted">({counts.candidates})</span></span>
              </label>
              <label className="flex items-start gap-2.5 text-sm">
                <input type="checkbox" className="mt-1 accent-[rgb(var(--accent))]" checked={showRejected}
                  onChange={(e) => setShowRejected(e.target.checked)} />
                <span>Rejected entities <span className="text-muted">({counts.rejected})</span></span>
              </label>
              <p className="hint">Entity types are switched on and off in the legend.</p>
            </div>
          )}
        </div>
      </div>

      {/* top-right: camera and layout */}
      <div className="floating absolute right-3 top-3 z-10 flex flex-col p-1" role="toolbar" aria-label="Graph view">
        <button className="icon-btn" onClick={() => zoom(1.5)} aria-label="Zoom in" title="Zoom in"><Plus className="h-4 w-4" /></button>
        <button className="icon-btn" onClick={() => zoom(1 / 1.5)} aria-label="Zoom out" title="Zoom out"><Minus className="h-4 w-4" /></button>
        <button className="icon-btn" onClick={() => sigmaRef.current?.getCamera().animatedReset({ duration: 300 })}
          aria-label="Fit graph to screen" title="Fit to screen"><Fullscreen className="h-4 w-4" /></button>
        <div className="mx-1.5 my-1 border-t border-line" />
        {arranging ? (
          <button className="icon-btn text-accent-text" onClick={stopLayout} aria-label="Stop arranging" title="Stop arranging">
            <Pause className="h-4 w-4" />
          </button>
        ) : (
          <button className="icon-btn" onClick={() => runLayout(6000)} aria-label="Re-arrange layout" title="Re-arrange">
            <Play className="h-4 w-4" />
          </button>
        )}
        <button className="icon-btn" disabled={expanded.size === 0} onClick={() => setExpanded(new Set())}
          aria-label="Fold expanded groups again" title="Fold groups again"><Layers className="h-4 w-4" /></button>
      </div>

      {/* bottom-left: legend doubles as the type filter */}
      {ready && counts.total > 0 && (
        <div className="floating absolute bottom-3 left-3 z-10 w-60 max-w-[calc(100%-24px)]">
          <button className="flex w-full items-center justify-between gap-2 px-3 py-2 text-sm font-medium"
            aria-expanded={legendOpen} onClick={() => setLegendOpen((o) => !o)}>
            <span className="flex items-center gap-2"><Filter className="h-4 w-4 text-muted" aria-hidden />Entity types</span>
            <span className="text-xs text-muted">{legendOpen ? "Hide" : "Show"}</span>
          </button>
          {legendOpen && (
            <div className="border-t border-line px-1.5 pb-2 pt-1.5">
              <ul className="scroll-thin max-h-[30vh] overflow-y-auto">
                {typeList.map((t) => {
                  const on = !hidden.has(t);
                  return (
                    <li key={t}>
                      <button aria-pressed={on} onClick={() => toggleType(t)}
                        className={`flex w-full items-center gap-2.5 rounded px-1.5 py-1 text-left text-[13px] hover:bg-raised ${on ? "" : "text-muted"}`}>
                        <span className="h-3 w-3 shrink-0 rounded-full border-2"
                          style={{ background: on ? typeColor(t) : "transparent", borderColor: typeColor(t) }} aria-hidden />
                        <span className={`min-w-0 flex-1 truncate ${on ? "" : "line-through"}`}>{plural(t, 2)}</span>
                        <span className="font-mono text-xs text-muted">{counts.byType[t]}</span>
                      </button>
                    </li>
                  );
                })}
              </ul>
              <ul className="mt-2 space-y-1 border-t border-line px-1.5 pt-2 text-xs text-muted">
                <li className="flex items-center gap-2.5">
                  <span className="h-3 w-3 rounded-full border-2 border-accent" aria-hidden />Target you added
                </li>
                <li className="flex items-center gap-2.5">
                  <span className="h-3 w-3 rounded-full border-2 border-dashed border-danger" aria-hidden />Unverified name match
                </li>
                <li className="flex items-center gap-2.5">
                  <span className="relative h-3 w-3 rounded-full bg-muted ring-1 ring-muted ring-offset-1 ring-offset-panel" aria-hidden />Group, click to expand
                </li>
                <li className="flex items-center gap-2.5">
                  <span className="h-0 w-3 border-t-2 border-danger" aria-hidden />Possible match link
                </li>
              </ul>
            </div>
          )}
        </div>
      )}

      {/* bottom-right: what is on screen */}
      {ready && (
        <div className="floating absolute bottom-3 right-3 z-10 flex items-center gap-3 px-3 py-2 text-xs text-muted">
          {arranging && <span className="flex items-center gap-1.5 text-accent-text"><Workflow className="h-3.5 w-3.5" aria-hidden />Arranging</span>}
          <span>
            <span className="font-medium text-ink">{shown.nodes.toLocaleString()}</span> of {counts.total.toLocaleString()} entities
            {shown.groups > 0 && <>, {shown.groups} groups</>}
          </span>
        </div>
      )}

      {!ready && !loadError && (
        <div className="absolute inset-0 flex items-center justify-center">
          <p className="floating flex items-center gap-2 px-4 py-3 text-sm text-muted" role="status"><Spinner /> Loading graph</p>
        </div>
      )}
      {loadError ? (
        <div className="absolute inset-x-0 top-20 mx-auto w-full max-w-md px-4"><ErrorBox error={loadError} /></div>
      ) : null}
      {ready && counts.total === 0 && (
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center p-6">
          <div className="floating max-w-sm p-5 text-center">
            <p className="font-medium">Nothing in the graph yet</p>
            <p className="mt-1 text-sm text-muted">Add a target above, or start a Claude collection in the Agents tab. New entities appear here as each source answers.</p>
          </div>
        </div>
      )}
    </div>
  );
}

function nodeAttrs(n: ViewNode): Omit<NodeAttrs, "x" | "y"> {
  const base = typeColor(n.etype);
  let size: number;
  if (n.count) size = Math.min(24, 8 + Math.log2(n.count) * 2.4);
  else {
    size = Math.min(16, 3 + Math.sqrt(n.degree) * 1.5) * (0.75 + 0.35 * n.confidence);
    if (n.seed) size = Math.max(size, 11);
  }
  return {
    size,
    color: n.status === "rejected" ? withAlpha(base, 0.35) : base,
    label: n.label,
    etype: n.etype,
    status: n.status,
    seed: n.seed,
    count: n.count ?? 0,
  };
}

function edgeColor(etype: string, pal: Palette): string {
  return etype === "possible_match" ? pal.danger : pal.edge;
}

function withAlphaRgb(rgb: string, a: number): string {
  return rgb.replace(/^rgb\((.*)\)$/, `rgba($1, ${a})`);
}

function GraphSearch({ nodes, version, onPick }: {
  nodes: Map<string, EntityView>; version: number; onPick: (e: EntityView) => void;
}) {
  const [q, setQ] = useState("");
  const [active, setActive] = useState(0);
  const [open, setOpen] = useState(false);
  const results = useMemo(() => {
    const term = q.trim().toLowerCase();
    if (term.length < 2) return [];
    const hits: { e: EntityView; score: number }[] = [];
    for (const e of nodes.values()) {
      const label = (e.label || "").toLowerCase();
      const key = e.key.toLowerCase();
      const i = Math.min(...[label.indexOf(term), key.indexOf(term)].map((x) => (x < 0 ? 1e9 : x)));
      if (i === 1e9) continue;
      hits.push({ e, score: (i === 0 ? 0 : 1) * 1000 + label.length });
    }
    hits.sort((a, b) => a.score - b.score);
    return hits.slice(0, 8).map((h) => h.e);
  }, [q, nodes, version]);

  function pick(e: EntityView) {
    onPick(e);
    setOpen(false);
    setQ("");
  }

  function onKey(ev: ReactKeyboardEvent<HTMLInputElement>) {
    if (ev.key === "ArrowDown") {
      ev.preventDefault();
      setActive((a) => Math.min(results.length - 1, a + 1));
    } else if (ev.key === "ArrowUp") {
      ev.preventDefault();
      setActive((a) => Math.max(0, a - 1));
    } else if (ev.key === "Enter" && results[active]) {
      ev.preventDefault();
      pick(results[active]);
    } else if (ev.key === "Escape") {
      setOpen(false);
    }
  }

  return (
    <div className="relative w-80 max-w-full">
      <Search className="pointer-events-none absolute left-3 top-1/2 z-10 h-4 w-4 -translate-y-1/2 text-muted" aria-hidden />
      <input className="field h-10 rounded-lg bg-panel/95 pl-9 pr-9 shadow-float backdrop-blur" value={q}
        placeholder="Find an entity" aria-label="Find an entity in the graph" role="combobox"
        aria-expanded={open && results.length > 0} aria-controls="graph-search-results"
        onChange={(e) => {
          setQ(e.target.value);
          setActive(0);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)} onBlur={() => window.setTimeout(() => setOpen(false), 120)} onKeyDown={onKey} />
      {q && (
        <button className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-muted hover:text-ink"
          onClick={() => setQ("")} aria-label="Clear search"><X className="h-4 w-4" /></button>
      )}
      {open && q.trim().length >= 2 && (
        <ul id="graph-search-results" role="listbox" className="floating absolute left-0 right-0 top-12 overflow-hidden py-1">
          {results.length === 0 && <li className="px-3 py-2 text-sm text-muted">No entity matches "{q.trim()}".</li>}
          {results.map((e, i) => (
            <li key={e.id} role="option" aria-selected={i === active}>
              <button onMouseDown={(ev) => ev.preventDefault()} onClick={() => pick(e)} onMouseEnter={() => setActive(i)}
                className={`flex w-full items-center gap-2.5 px-3 py-2 text-left text-sm ${i === active ? "bg-raised" : ""}`}>
                <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${e.status === "candidate" ? "ring-1 ring-danger ring-offset-1 ring-offset-panel" : ""}`}
                  style={{ background: typeColor(e.type) }} aria-hidden />
                <span className="min-w-0 flex-1 truncate">{e.label || e.key}</span>
                <span className="shrink-0 text-xs text-muted">{e.type}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
