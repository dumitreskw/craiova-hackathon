// Focus mode: instead of the whole graph, the canvas shows a focus entity (or
// the targets), its direct neighbours, and the neighbours of the nodes the
// analyst opened. The layout is a radial tree: focus in the middle, each ring
// one hop further out, neighbours grouped by kind around the ring. It is
// deterministic, so the same focus always looks the same. Pure functions.

import type { EdgeView, EntityView } from "../types";
import { passes, type Filters, type View } from "./model";

export type Point = { x: number; y: number };

/** Kind order around a ring: who they are first, infrastructure last. */
const TYPE_RANK: Record<string, number> = {
  Person: 0, Organization: 1, Location: 2,
  SocialProfile: 3, Username: 4, Repository: 5,
  Email: 6, Phone: 7,
  Breach: 8, Vulnerability: 9,
  URL: 10, Document: 11,
  Domain: 12, IPAddress: 13, ASN: 14, Certificate: 15,
};

function rank(etype: string, status: string): number {
  // Unverified name matches sit together at the end of their kind.
  return (TYPE_RANK[etype] ?? 16) * 2 + (status === "candidate" ? 1 : 0);
}

/**
 * The entities the focus view shows: the roots, their neighbours, and the
 * neighbours of opened nodes that are themselves shown (opened chains reach
 * further out). Filters apply to everything except the roots.
 */
export function focusSubset(
  nodes: Map<string, EntityView>,
  edges: Map<string, EdgeView>,
  f: Filters,
  seeds: Set<string>,
  roots: string[],
  opened: Set<string>,
): Map<string, EntityView> {
  const rootSet = new Set(roots.filter((id) => nodes.has(id)));
  const ok = (id: string) => {
    const e = nodes.get(id);
    return !!e && (rootSet.has(id) || passes(e, f, seeds));
  };
  const adj = new Map<string, string[]>();
  for (const e of edges.values()) {
    if (e.source === e.target || !ok(e.source) || !ok(e.target)) continue;
    (adj.get(e.source) ?? adj.set(e.source, []).get(e.source)!).push(e.target);
    (adj.get(e.target) ?? adj.set(e.target, []).get(e.target)!).push(e.source);
  }
  const keep = new Set(rootSet);
  const grown = new Set<string>();
  const queue = [...rootSet];
  while (queue.length) {
    const id = queue.shift()!;
    if (grown.has(id)) continue;
    grown.add(id);
    for (const n of adj.get(id) || []) {
      keep.add(n);
      if (opened.has(n) && !grown.has(n)) queue.push(n);
    }
  }
  const out = new Map<string, EntityView>();
  for (const id of keep) out.set(id, nodes.get(id)!);
  return out;
}

export interface RadialLayout {
  pos: Map<string, Point>;
  /** node -> the node it hangs off (one hop closer to the focus) */
  parent: Map<string, string>;
  depth: Map<string, number>;
}

const SCALE = 100;

/** Radial tree over the view: roots in the middle, ring d = d hops out. */
export function radialLayout(view: View, roots: string[]): RadialLayout {
  const ids = [...view.nodes.keys()];
  const adj = new Map<string, string[]>();
  for (const id of ids) adj.set(id, []);
  for (const e of view.edges.values()) {
    adj.get(e.source)?.push(e.target);
    adj.get(e.target)?.push(e.source);
  }
  const key = (id: string) => {
    const n = view.nodes.get(id)!;
    return { r: rank(n.etype, n.status), label: (n.label || id).toLowerCase() };
  };
  const byKey = (a: string, b: string) => {
    const ka = key(a);
    const kb = key(b);
    return ka.r - kb.r || ka.label.localeCompare(kb.label) || a.localeCompare(b);
  };

  let rootIds = roots.filter((r) => view.nodes.has(r));
  if (!rootIds.length && ids.length) {
    let best = ids[0];
    for (const id of ids) if ((adj.get(id)?.length ?? 0) > (adj.get(best)?.length ?? 0)) best = id;
    rootIds = [best];
  }
  rootIds.sort(byKey);

  const depth = new Map<string, number>();
  const parent = new Map<string, string>();
  const children = new Map<string, string[]>();
  const order: string[] = [];
  const queue = [...rootIds];
  for (const r of rootIds) depth.set(r, 0);
  while (queue.length) {
    const id = queue.shift()!;
    order.push(id);
    const kids: string[] = [];
    for (const n of [...(adj.get(id) || [])].sort(byKey)) {
      if (depth.has(n)) continue;
      depth.set(n, depth.get(id)! + 1);
      parent.set(n, id);
      kids.push(n);
      queue.push(n);
    }
    children.set(id, kids);
  }

  // Angular weight: a leaf takes one slot; an opened node takes room for its
  // children, scaled down because they sit on a wider ring (same arc spacing).
  const multi = rootIds.length > 1;
  const radius = (d: number) => (multi ? 0.55 + d : d);
  const weight = new Map<string, number>();
  for (let i = order.length - 1; i >= 0; i--) {
    const id = order[i];
    const kids = children.get(id) || [];
    const d = depth.get(id)!;
    const sum = kids.reduce((s, c) => s + weight.get(c)!, 0);
    const shrink = radius(d) > 0 ? radius(d) / radius(d + 1) : 1;
    weight.set(id, Math.max(1, sum * shrink));
  }

  const pos = new Map<string, Point>();
  const START = -Math.PI / 2;
  const place = (id: string, a0: number, a1: number) => {
    const d = depth.get(id)!;
    const r = radius(d);
    const a = (a0 + a1) / 2;
    pos.set(id, { x: Math.cos(a) * r * SCALE, y: Math.sin(a) * r * SCALE });
    const kids = children.get(id) || [];
    const total = kids.reduce((s, c) => s + weight.get(c)!, 0) || 1;
    // A single root spreads its ring over the whole circle; deeper nodes keep
    // their children inside their own wedge.
    let from = a0;
    for (const c of kids) {
      const span = ((a1 - a0) * weight.get(c)!) / total;
      place(c, from, from + span);
      from += span;
    }
  };

  if (!multi && rootIds.length) {
    place(rootIds[0], START, START + Math.PI * 2);
    pos.set(rootIds[0], { x: 0, y: 0 });
  } else if (multi) {
    const total = rootIds.reduce((s, r) => s + weight.get(r)!, 0);
    let from = START;
    for (const r of rootIds) {
      const span = (Math.PI * 2 * weight.get(r)!) / total;
      place(r, from, from + span);
      from += span;
    }
  }

  // Anything the tree didn't reach goes on an outer ring.
  const rest = ids.filter((id) => !depth.has(id)).sort(byKey);
  if (rest.length) {
    const maxDepth = Math.max(0, ...depth.values());
    const r = radius(maxDepth + 1);
    rest.forEach((id, i) => {
      const a = START + (Math.PI * 2 * i) / rest.length;
      depth.set(id, maxDepth + 1);
      pos.set(id, { x: Math.cos(a) * r * SCALE, y: Math.sin(a) * r * SCALE });
    });
  }
  return { pos, parent, depth };
}

/** Bounding box of a layout with room around it, never degenerate. */
export function layoutBBox(pos: Map<string, Point>): { x: [number, number]; y: [number, number] } {
  let minX = -SCALE * 0.6;
  let maxX = SCALE * 0.6;
  let minY = -SCALE * 0.6;
  let maxY = SCALE * 0.6;
  for (const p of pos.values()) {
    minX = Math.min(minX, p.x);
    maxX = Math.max(maxX, p.x);
    minY = Math.min(minY, p.y);
    maxY = Math.max(maxY, p.y);
  }
  const pad = SCALE * 0.15;
  return { x: [minX - pad, maxX + pad], y: [minY - pad, maxY + pad] };
}
