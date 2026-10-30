// Shapes returned by the backend (backend/app/api, docs/05-api.md).

export type LegalBasis =
  | "consent"
  | "contract"
  | "legal_obligation"
  | "vital_interests"
  | "public_task"
  | "legitimate_interest";

export type Mode = "passive" | "active";

export interface InvestigationSettings {
  max_depth: number;
  connectors: string[];
  exclude_connectors: string[];
}

export interface Target {
  id: string;
  value: string;
  type: string;
  entity_id: string;
  added_by: string;
  created_at: string;
}

export interface Investigation {
  id: string;
  name: string;
  purpose: string;
  legal_basis: LegalBasis;
  mode: Mode;
  scope: string[];
  authorization: string | null;
  settings: InvestigationSettings;
  retention_days: number;
  legal_hold: number;
  created_by: string;
  created_at: string;
  stats?: { entities: number; edges: number; findings: number; targets: number };
  targets?: Target[];
}

export type EntityStatus = "active" | "candidate" | "confirmed" | "rejected";

export interface EntityView {
  id: string;
  type: string;
  key: string;
  label: string;
  attributes: Record<string, unknown>;
  conflicts: string[];
  confidence: number;
  status: EntityStatus;
  first_seen: string;
  last_seen: string;
}

export interface EdgeView {
  id: string;
  type: string;
  source: string;
  target: string;
  attributes: Record<string, unknown>;
  confidence: number;
}

export interface Graph {
  nodes: { data: EntityView }[];
  edges: { data: EdgeView }[];
}

export interface EntitySource {
  id: string;
  connector: string;
  input: Record<string, unknown>;
  fetched_at: string;
  sha256: string | null;
  reliability: number;
  match_quality: number;
}

export interface Note {
  id: string;
  entity_id: string;
  author: string;
  body: string;
  created_at: string;
}

export interface EntityEdge extends EdgeView {
  direction?: "in" | "out";
  other?: { id: string; type: string; key: string; label: string; status: EntityStatus };
}

export interface EntityDetail extends EntityView {
  attribute_sources: Record<string, { value: unknown; sources: string[] }[]>;
  sources: EntitySource[];
  edges: EntityEdge[];
  notes: Note[];
}

export interface Finding {
  id: string;
  investigation_id: string;
  connector: string;
  input: { arg?: string; [k: string]: unknown };
  entity_ref: string | null;
  run_by: string;
  passive: number;
  fetched_at: string;
  duration_ms: number;
  sha256: string | null;
  raw_path: string | null;
  note: string | null;
  error: string | null;
  entity_count: number;
  payload?: unknown;
}

export interface Run {
  id: string;
  investigation_id: string;
  kind: string;
  goal: string | null;
  status: "running" | "finished" | "failed" | "cancelled" | string;
  summary: string | null;
  stats: Record<string, number>;
  started_at: string;
  finished_at: string | null;
}

export interface Connector {
  name: string;
  accepts: string;
  passive: boolean;
  description: string;
  requires_key: string | null;
  key_configured: boolean | null;
  available?: boolean;
  cost?: "free" | "metered" | "paid";
  seed_only?: boolean;
  quota?: number | null;
  quota_used?: number | null;
}

export type CostTier = "none" | "metered" | "all";

export type ClaimStatus = "verified" | "unverified" | "leaked" | "inference";

export interface Claim {
  id: string;
  text: string;
  evidence?: string[];
  entities?: string[];
  status?: ClaimStatus;
  sensitive?: boolean;
  severity?: "info" | "low" | "medium" | "high";
}

export interface ReportBody {
  title?: string;
  summary?: string;
  sections: { title: string; claims: Claim[] }[];
  outstanding?: string[];
  method?: string;
}

export interface LintIssue {
  level: "error" | "warning";
  rule: string;
  claim: string | null;
  message: string;
}

export interface Report {
  id: string;
  investigation_id: string;
  title: string;
  body: ReportBody;
  agent_run_id: string | null;
  created_by: string;
  created_at: string;
  updated_at: string;
  lint?: LintIssue[];
}

export interface ReportSummary {
  id: string;
  title: string;
  created_by: string;
  created_at: string;
  updated_at: string;
}

export interface AuditEvent {
  id: number;
  ts: string;
  user: string;
  action: string;
  investigation_id: string | null;
  connector: string | null;
  target: string | null;
  outcome: "ok" | "refused" | "error";
  detail: Record<string, unknown>;
}

export interface StreamEvent {
  event: string;
  data: Record<string, unknown>;
  at: number;
}

export type MaterialKind = "file" | "text" | "link";

/** Analyst-supplied material: a file, note or link, converted to markdown. */
export interface Material {
  id: string;
  investigation_id: string;
  kind: MaterialKind;
  name: string;
  format: string;
  size_bytes: number;
  sha256: string | null;
  chars: number;
  finding_id: string | null;
  note: string | null;
  digest: {
    outline?: string[];
    preview?: string;
    emails?: string[];
    company_identifiers?: string[];
    urls?: string[];
    url?: string;
  };
  added_by: string;
  created_at: string;
  duplicate?: boolean;
  markdown?: string;
}
