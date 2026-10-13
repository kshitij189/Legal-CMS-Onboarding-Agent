// Plain-language explanations for every status and code the backend reports. The dashboard's job
// is to explain *why* something succeeded, stopped or needs a person — not just show a code.

export const STAGE: Record<string, string> = {
  awaiting_source_schema: "Upload the firm's intake form or schema to begin.",
  awaiting_target_inspection: "Inspect the target CMS so the agent knows which fields exist.",
  ready_for_mapping: "Both schemas are known. Ask the agent to propose mappings.",
};

export const VALIDATION: Record<string, string> = {
  valid: "Passed every deterministic check. A person still has to approve it.",
  needs_review: "Plausible, but a person must look before it can be used.",
  invalid: "Breaks a rule; it cannot be approved as proposed. Edit or reject it.",
};

export const ISSUE: Record<string, string> = {
  unknown_source_field: "The source field does not exist in the uploaded schema.",
  duplicate_source_field: "The model proposed this source field twice.",
  unknown_target_field: "The CMS has no such field — the model invented it.",
  unauthorized_configuration_area: "This CMS field is managed by the CMS itself and may not be set by intake.",
  unsupported_transformation: "The CMS does not offer this transformation.",
  transformation_incompatible: "This transformation cannot produce values of the target field's type.",
  incompatible_types: "The source and target data types are incompatible.",
  unsupported_source_type: "The source field (e.g. a file or list) cannot be carried by an intake mapping.",
  unsupported_target_type: "The target field's type is not supported.",
  value_map_requires_enum_target: "Only fields with fixed options take a value map.",
  invalid_enum_option: "The value map points at options the CMS does not have.",
  duplicate_value_map_entry: "A source value appears twice in the value map.",
  unknown_source_option: "The value map names source options that do not exist.",
  unmapped_source_options: "Some source options have no CMS equivalent and would be rejected.",
  possible_truncation: "Long text going into a short text field may be cut off.",
  phone_not_normalized: "Phone numbers usually need normalising to pass the CMS's checks.",
  free_text_conversion: "Free text may not be a valid value for this field type.",
  free_text_to_enum: "Free text only works if people type exactly one of the allowed options.",
  low_confidence: "The model itself was unsure.",
  ambiguous_mapping: "More than one CMS field could fit; a person must choose.",
  unknown_candidate: "Some suggested candidates are not real CMS fields.",
  custom_field_creation: "This would create a new CMS field, which always needs approval.",
  invalid_custom_field: "The proposed custom field is incomplete or invalid.",
  custom_field_exists: "That custom field already exists; map to it instead.",
  unsupported_declared: "Marked unsupported although its type is supported.",
  required_source_unmapped: "The firm requires this field, but it would not reach the CMS.",
  untrusted_instruction_text: "The field's text contains instruction-like wording (possible prompt injection). Treated as data; review it.",
  duplicate_target: "Several source fields point at the same CMS field.",
  not_proposed: "The model skipped this field; decide it manually.",
};

export const RUN_STATUS: Record<string, string> = {
  running: "Executing in the background. This page refreshes automatically.",
  succeeded: "Every action was applied and the configuration read back from the CMS matches the approved plan.",
  failed: "The run stopped at the first failed action; later actions were not attempted.",
  blocked: "Nothing was written: the CMS changed since approval, so the plan may no longer be safe.",
  verification_failed: "The CMS reported success, but what it stored differs from the plan. Not treated as success.",
};

export const STEP_STATUS: Record<string, string> = {
  pending: "Not started yet.",
  applied: "Written to the CMS.",
  already_applied: "The CMS already held this value, so nothing was written.",
  failed: "Could not be applied.",
  skipped: "Not attempted because the run stopped earlier.",
};

export const ERROR_CATEGORY: Record<string, string> = {
  ui_drift: "A control in the CMS web page was missing or changed. The agent stopped instead of guessing.",
  validation: "The CMS rejected the values.",
  conflict: "The CMS already holds something this plan is not allowed to overwrite.",
  authorization: "The CMS refused the connector's credentials or permissions.",
  uncertain: "The outcome of a write could not be confirmed, even after checking.",
  transient: "The CMS was temporarily unreachable.",
  security: "The browser was sent outside the allowed CMS address and was stopped.",
  target_drift: "A field the plan writes to changed in the CMS after approval.",
  verification: "The stored configuration differs from the approved plan.",
  orchestration: "The orchestrator gave up after its retries.",
  orchestrator_unavailable: "The background orchestrator could not be reached; nothing was written.",
  configuration: "The connector is misconfigured (for example missing credentials).",
  contract: "The CMS answered in an unexpected way.",
  browser_error: "The browser session failed unexpectedly.",
  internal: "An unexpected error occurred in the executor.",
};

export const CHECK_STATUS: Record<string, string> = {
  match: "Stored exactly as approved.",
  mismatch: "Stored, but different from the approved value.",
  missing: "Not present in the CMS.",
};

export function explain(table: Record<string, string>, key: string | null | undefined): string {
  if (!key) return "";
  return table[key] ?? key;
}
