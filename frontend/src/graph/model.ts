// Turns the raw entity graph into what the canvas shows: filters applied,
// and big homogeneous groups (e.g. 300 subdomains under one apex) folded into
// one cluster node that expands on click. Pure functions, no rendering.

import type { EdgeView, EntityView } from "../types";

export const CLUSTER_MIN = 25; // more members than this collapse into a group
const LEAF_MAX_DEGREE = 3; // only leaf-ish nodes are grouped; hubs stay visible

export interface Filters {
  hidden: Set<string>;
  minConfidence: number;
  showCandidates: boolean;
  showRejected: boolean;
}

export interface ViewNode {
  id: string;
  label: string;
  etype: string;
  status: string;
  confidence: number;
  degree: number;
  seed: boolean;
  /** set on cluster nodes: how many entities are folded inside */
  count?: number;
  owner?: string;
}

export interface ViewEdge {
  id: string;
  source: string;
  target: string;
  etype: string;
  count: number;
}

export interface View {
  nodes: Map<string, ViewNode>;
  edges: Map<string, ViewEdge>;
  /** cluster id -> member entity ids */
  clusters: Map<string, string[]>;
  /** entity id -> cluster id, for entities currently folded */
  folded: Map<string, string>;
  /** entity id -> owner entity id (the hub it hangs off) */
  owners: Map<string, string>;
  /** entities passing the filters, before folding */
  visibleCount: number;
}

export function clusterId(owner: string, type: string): string {
  return `cluster:${owner}:${type}`;
}

export function passes(e: EntityView, f: Filters, seeds: Set<string>): boolean {
  if (seeds.has(e.id)) return true;
  if (f.hidden.has(e.type)) return false;
  if (e.status === "candidate" && !f.showCandidates) return false;
  if (e.status === "rejected" && !f.showRejected) return false;
  return e.confidence >= f.minConfidence;
}

export function computeView(
  nodes: Map<string, EntityView>,
  edges: Map<string, EdgeView>,
  f: Filters,
  seeds: Set<string>,
  expanded: Set<string>,
): View {
  const visible = new Map<string, EntityView>();
  for (const e of nodes.values()) if (passes(e, f, seeds)) visible.set(e.id, e);

  const adj = new Map<string, Set<string>>();
  for (const id of visible.keys()) adj.set(id, new Set());
  const liveEdges: EdgeView[] = [];
  for (const e of edges.values()) {
    if (e.source === e.target) continue;
    const a = adj.get(e.source);
    const b = adj.get(e.target);
    if (!a || !b) continue;
    a.add(e.target);
    b.add(e.source);
    liveEdges.push(e);
  }
  const degree = (id: string) => adj.get(id)?.size ?? 0;

  // Each leaf-ish entity hangs off its best-connected neighbour.
  const owners = new Map<string, string>();
  const groups = new Map<string, string[]>();
  for (const [id, ent] of visible) {
    const d = degree(id);
    if (d === 0 || d > LEAF_MAX_DEGREE || seeds.has(id) || ent.status === "candidate") continue;
    let best: string | null = null;
    let bestScore = -1;
    for (const n of adj.get(id)!) {
      const score = degree(n) + (seeds.has(n) ? 1e6 : 0);
      if (score > bestScore || (score === bestScore && best !== null && n < best)) {
        best = n;
        bestScore = score;
      }
    }
    if (!best || degree(best) <= LEAF_MAX_DEGREE) continue;
    owners.set(id, best);
    const key = clusterId(best, ent.type);
    const list = groups.get(key);
    if (list) list.push(id);
    else groups.set(key, [id]);
  }

  const clusters = new Map<string, string[]>();
  const folded = new Map<string, string>();
  for (const [key, members] of groups) {
    if (members.length <= CLUSTER_MIN || expanded.has(key)) continue;
    clusters.set(key, members);
    for (const m of members) folded.set(m, key);
  }

  const out = new Map<string, ViewNode>();
  for (const [id, ent] of visible) {
    if (folded.has(id)) continue;
    out.set(id, {
      id,
      label: ent.label || ent.key,
      etype: ent.type,
      status: ent.status,
      confidence: ent.confidence,
      degree: degree(id),
      seed: seeds.has(id),
      owner: owners.get(id),
    });
  }
  for (const [key, members] of clusters) {
    const sample = visible.get(members[0])!;
    const owner = owners.get(members[0])!;
    out.set(key, {
      id: key,
      label: `${members.length} ${plural(sample.type, members.length)}`,
      etype: sample.type,
      status: "cluster",
      confidence: 1,
      degree: 0,
      seed: false,
      count: members.length,
      owner,
    });
  }

  const viewEdges = new Map<string, ViewEdge>();
  for (const e of liveEdges) {
    const s = folded.get(e.source) ?? e.source;
    const t = folded.get(e.target) ?? e.target;
    if (s === t) continue;
    if (s === e.source && t === e.target) {
      viewEdges.set(e.id, { id: e.id, source: s, target: t, etype: e.type, count: 1 });
      continue;
    }
    const id = `agg:${s}>${t}`;
    const prev = viewEdges.get(id);
    if (prev) prev.count += 1;
    else viewEdges.set(id, { id, source: s, target: t, etype: e.type, count: 1 });
  }
  for (const e of viewEdges.values()) {
    const s = out.get(e.source);
    const t = out.get(e.target);
    if (s?.count) s.degree += 1;
    if (t?.count) t.degree += 1;
  }

  return { nodes: out, edges: viewEdges, clusters, folded, owners, visibleCount: visible.size };
}

const PLURALS: Record<string, [string, string]> = {
  Domain: ["domain", "domains"],
  IPAddress: ["IP address", "IP addresses"],
  ASN: ["network", "networks"],
  Organization: ["organization", "organizations"],
  Person: ["person", "people"],
  Email: ["email address", "email addresses"],
  Username: ["username", "usernames"],
  Phone: ["phone number", "phone numbers"],
  SocialProfile: ["social profile", "social profiles"],
  URL: ["URL", "URLs"],
  Document: ["document", "documents"],
  Certificate: ["certificate", "certificates"],
  Location: ["location", "locations"],
  Breach: ["breach", "breaches"],
  Repository: ["repository", "repositories"],
  Vulnerability: ["vulnerability", "vulnerabilities"],
};

export function plural(type: string, n: number): string {
  const p = PLURALS[type];
  if (!p) return n === 1 ? type : `${type}s`;
  return n === 1 ? p[0] : p[1];
}
