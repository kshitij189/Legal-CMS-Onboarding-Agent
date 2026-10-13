// Shapes returned by the backend API (/api/v1). Only the fields the dashboard uses.

export interface Firm {
  id: number;
  slug: string;
  name: string;
}

export interface UserOut {
  id: number;
  username: string;
  display_name: string;
  role: "firm_admin" | "integration_specialist";
}

export interface Me {
  user: UserOut;
  firm: Firm;
  permissions: string[];
}

export interface Connector {
  id: number;
  name: string;
  base_url: string;
  credential_ref: string;
}

export interface SchemaSummary {
  id: number;
  kind: "source" | "target";
  version: number;
  format: string;
  title: string | null;
  field_count: number;
  target_config_revision: number | null;
  original_filename: string | null;
  created_by: string | null;
  created_at: string;
}

export interface NormalizedField {
  key: string;
  label: string;
  description: string;
  data_type: string;
  required: boolean;
  options: string[] | null;
  entity: string | null;
  canonical_ref: string | null;
}

export interface SchemaDetail extends SchemaSummary {
  fields: NormalizedField[];
}

export interface Project {
  id: number;
  name: string;
  created_by: string | null;
  stage: "awaiting_source_schema" | "awaiting_target_inspection" | "ready_for_mapping";
  connector: Connector;
  latest_source_schema: SchemaSummary | null;
  latest_target_schema: SchemaSummary | null;
  created_at: string;
}

export interface ValueMapEntry {
  source_value: string;
  target_value: string;
}

export interface ProposedMapping {
  source_field: string;
  decision: "map" | "ambiguous" | "no_target" | "unsupported" | "requires_custom_field";
  target_field: string;
  transformation: string;
  value_map: ValueMapEntry[];
  candidates: string[];
  custom_field_entity: string;
  custom_field_name: string;
  custom_field_data_type: string;
  custom_field_options: string[];
  confidence: number;
  rationale: string;
  open_questions: string[];
}

export interface ValidationIssue {
  code: string;
  message: string;
  severity: "needs_review" | "invalid";
}

export interface Proposal {
  id: number;
  origin: "llm" | "reviewer" | "system";
  supersedes_id: number | null;
  source_field: string;
  decision: string;
  mapping: ProposedMapping;
  validation_status: "valid" | "needs_review" | "invalid";
  validation_issues: ValidationIssue[];
  review_status: "pending" | "approved" | "rejected" | "edited" | "needs_clarification";
  review_comment: string | null;
  reviewed_by: string | null;
}

export interface Generation {
  id: number;
  status: string;
  provider: string | null;
  model: string | null;
  prompt_version: string;
  fallbacks: string[][];
  coverage_gaps: { target_field: string; code: string; message: string }[];
  unproposed_source_fields: string[];
  untrusted_source_fields: string[];
  created_by: string;
  created_at: string;
  proposals: Proposal[];
}

export interface MappingVersion {
  id: number;
  version: number;
  status: "approved" | "superseded";
  fingerprint: string;
  complete: boolean;
  items: { proposal_id: number; mapping: ProposedMapping }[];
  coverage_gaps: { target_field: string; message: string }[];
  approved_by: string;
  approved_at: string;
}

export interface Step {
  sequence: number;
  action_type: string;
  subject: string;
  status: "pending" | "applied" | "already_applied" | "failed" | "skipped";
  attempts: number;
  error_category: string | null;
  error_message: string | null;
  evidence: Record<string, unknown>;
}

export interface Check {
  subject: string;
  check_type: string;
  status: "match" | "mismatch" | "missing";
  reason: string;
  expected: Record<string, unknown>;
  actual: Record<string, unknown> | null;
}

export interface Run {
  id: number;
  workflow_id: string | null;
  mapping_version_id: number;
  channel: "api" | "browser";
  status: "running" | "succeeded" | "failed" | "blocked" | "verification_failed";
  error_category: string | null;
  error_message: string | null;
  drift_issues: { field: string; code: string; message: string; blocking: boolean }[];
  config_revision_before: number | null;
  config_revision_after: number | null;
  artifact_dir: string | null;
  created_by: string;
  started_at: string;
  finished_at: string | null;
  summary: { steps: Record<string, number>; verification: Record<string, number> };
  steps: Step[];
  verification: Check[];
}

export interface AuditEvent {
  id: number;
  action: string;
  entity_type: string;
  entity_id: number | null;
  actor: string | null;
  details: Record<string, unknown>;
  created_at: string;
}
