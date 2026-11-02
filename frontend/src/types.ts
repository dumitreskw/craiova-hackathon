// Shapes returned by the backend (backend/app/api, docs/05-api.md).

export type Mode = "passive" | "active";

export interface InvestigationSettings {
  max_depth: number;
  connectors: string[];
  exclude_connectors: string[];
}

/** What the brief says about who a person target is (backend app/identity.py). */
export interface SubjectProfile {
  aliases?: string[];
  birth_year?: number | null;
  birth_date?: string | null;
  nationalities?: string[];
  locations?: string[];
  employers?: string[];
  roles?: string[];
  education?: string[];
  relatives?: string[];
  emails?: string[];
  usernames?: string[];
  phones?: string[];
  profile_urls?: string[];
  notes?: string;
}

export interface Target {
  id: string;
  value: string;
  type: string;
  entity_id: string;
  added_by: string;
  created_at: string;
  profile?: SubjectProfile;
}

/** One distinct person among same-name results, grouped by the verification agent. */
export interface IdentityCluster {
  id: string;
  label: string;
  summary: string;
  distinguishing: string;
  entity_ids: string[];
  is_subject: number | boolean;
  chosen: number | boolean;
  run_id: string | null;
  created_at: string;
}

export interface IdentityOverview {
  targets: {
    target_id: string;
    entity_id: string;
    value: string;
    profile: SubjectProfile;
    namesakes: number;
    list_matches: number;
    common_name: boolean;
  }[];
  clusters: IdentityCluster[];
}

export interface Investigation {
  id: string;
  name: string;
  purpose: string;
  mode: Mode;
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
  /** The latest review verdict, if any (graph and entity endpoints). */
  verdict?: Verdict | null;
  verdict_reason?: string | null;
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

export type Verdict = "confirmed" | "rejected" | "uncertain";

/** Is this entity really about the subject? By Claude's verification agent or an analyst. */
export interface EntityReview {
  verdict: Verdict;
  reason: string;
  reviewer: string;
  by_agent: number | boolean;
  created_at: string;
}

export interface EntityDetail extends EntityView {
  attribute_sources: Record<string, { value: unknown; sources: string[] }[]>;
  sources: EntitySource[];
  edges: EntityEdge[];
  notes: Note[];
  reviews?: EntityReview[];
}

export interface ReviewSummary {
  confirmed: number;
  rejected: number;
  uncertain: number;
  pending: number;
  uncertain_items: { entity_id: string; reason: string; type: string; key: string }[];
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
  stats: Record<string, unknown>;
  model?: string | null;
  started_at: string;
  finished_at: string | null;
}

// --- Model assignments (the Models settings page, GET /models) --------------

/** One agent role a model can be assigned to. */
export interface ModelTask {
  key: string;
  label: string;
  detail: string;
}

/** A selectable model (a Claude shortlist entry or a live Venice model). */
export interface ModelInfo {
  id: string;
  label: string;
  provider: "anthropic" | "venice";
  note?: string;
  tools?: boolean; // can drive an agent (function calling)
  reasoning?: boolean;
  uncensored?: boolean;
  context?: number | null;
  available?: boolean; // the provider's key is on the server
  hidden?: boolean;
}

export interface ModelCatalog {
  tasks: ModelTask[];
  assignments: Record<string, string>;
  defaults: Record<string, string>;
  providers: {
    anthropic: { available: boolean };
    venice: { available: boolean; configured_list: boolean };
  };
  models: ModelInfo[];
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

/** GET /credits: one row per paid or metered source. Any number may be null. */
export interface CreditEntry {
  source: string;
  label: string;
  status: "ok" | "unavailable" | "error";
  used: number | null;
  limit: number | null;
  remaining: number | null;
  unit: string | null;
  period: string | null;
  resets: string | null;
  note: string | null;
  dashboard_url: string | null;
  platform_used_30d?: number | null;
  checked_at?: string;
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

// --- Social personas & capture (app/social.py) -----------------------------

export type PersonaStatus = "ok" | "needs_login" | "disabled";

export interface Persona {
  id: string;
  name: string;
  platform: string;
  region: string;
  status: PersonaStatus;
  note: string | null;
  created_by: string;
  created_at: string;
  status_at: string | null;
  last_used: string | null;
  captures_last_hour?: number;
}

export interface SocialStatus {
  online: boolean;
  last_seen: string | null;
  version?: string;
  vpn?: { connected?: boolean; country?: string; city?: string };
  egress?: { ip?: string; city?: string; country?: string; mullvad?: boolean };
  busy?: boolean;
  queued: number;
  personas: Persona[];
}

export type SocialJobKind = "capture" | "login" | "check" | "search" | "interactions";
export type SocialJobStatus = "queued" | "running" | "done" | "failed" | "expired";

export interface SocialJob {
  id: string;
  kind: SocialJobKind;
  status: SocialJobStatus;
  persona: string | null;
  mode: "auto" | "persona" | "anonymous";
  url: string | null;
  investigation_id: string | null;
  entity_id: string | null;
  watch_id: string | null;
  requested_by: string;
  run_by: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  finding_id: string | null;
  result: { login_wall?: boolean; persona_used?: string | null; session?: string };
}

export interface SocialWatch {
  id: string;
  investigation_id: string;
  entity_id: string | null;
  url: string;
  persona: string | null;
  interval_hours: number;
  active: boolean;
  created_by: string;
  created_at: string;
  last_run_at: string | null;
  last_finding_id: string | null;
  last_error: string | null;
}

export interface SocialCaptureResult {
  finding: Finding | null;
  raw: {
    final_url?: string;
    platform?: string;
    login_wall?: boolean;
    logged_in?: boolean;
    persona?: string | null;
    blocked?: boolean;
    profile?: Record<string, string | number>;
    changes?: { count?: number; since?: string; fields?: Record<string, [unknown, unknown]>; new_lines?: string[]; removed_lines?: string[] };
  } | null;
  note: string | null;
}
