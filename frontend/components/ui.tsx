// Small presentational helpers shared by the pages.

const TONE: Record<string, "ok" | "warn" | "bad" | "info"> = {
  valid: "ok",
  succeeded: "ok",
  applied: "ok",
  already_applied: "ok",
  approved: "ok",
  match: "ok",
  ready_for_mapping: "ok",
  needs_review: "warn",
  pending: "info",
  running: "info",
  needs_clarification: "warn",
  edited: "info",
  superseded: "info",
  skipped: "warn",
  blocked: "warn",
  invalid: "bad",
  failed: "bad",
  rejected: "bad",
  mismatch: "bad",
  missing: "bad",
  verification_failed: "bad",
};

export function Badge({ value, title }: { value: string; title?: string }) {
  return (
    <span className={`badge ${TONE[value] ?? "info"}`} title={title}>
      {value.replaceAll("_", " ")}
    </span>
  );
}

export function ErrorNotice({ error }: { error: string | null }) {
  if (!error) return null;
  return (
    <div className="notice bad" role="alert">
      {error}
    </div>
  );
}

export function when(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString() : "—";
}
