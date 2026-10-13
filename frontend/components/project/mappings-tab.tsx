"use client";

import { useCallback, useEffect, useState } from "react";
import { useSession } from "@/components/session";
import { Badge, ErrorNotice } from "@/components/ui";
import { ApiError, api, describeError } from "@/lib/api";
import { explain, ISSUE, VALIDATION } from "@/lib/explain";
import type { Generation, MappingVersion, Project, Proposal, ProposedMapping, SchemaDetail } from "@/lib/types";

function describeMapping(m: ProposedMapping): React.ReactNode {
  switch (m.decision) {
    case "map":
      return (
        <>
          → <code>{m.target_field}</code>
          {m.transformation !== "none" && <> · {m.transformation}</>}
          {m.value_map.length > 0 && (
            <div className="muted">
              {m.value_map.map((v) => `${v.source_value} → ${v.target_value}`).join(", ")}
            </div>
          )}
        </>
      );
    case "requires_custom_field":
      return (
        <>
          create <code>{`${m.custom_field_entity}.custom.${m.custom_field_name}`}</code> (
          {m.custom_field_data_type}
          {m.custom_field_options.length ? `: ${m.custom_field_options.join(", ")}` : ""})
        </>
      );
    case "ambiguous":
      return <>ambiguous: {m.candidates.join(" or ") || "no clear candidate"}</>;
    default:
      return <>{m.decision.replaceAll("_", " ")}</>;
  }
}

function EditForm({
  proposal,
  targets,
  onSave,
  onCancel,
}: {
  proposal: Proposal;
  targets: SchemaDetail | null;
  onSave: (mapping: ProposedMapping, comment: string) => void;
  onCancel: () => void;
}) {
  const [decision, setDecision] = useState<ProposedMapping["decision"]>(
    proposal.mapping.decision === "ambiguous" ? "map" : proposal.mapping.decision,
  );
  const [target, setTarget] = useState(proposal.mapping.target_field || proposal.mapping.candidates[0] || "");
  const [transformation, setTransformation] = useState(proposal.mapping.transformation);
  const [comment, setComment] = useState("");
  const field = proposal.source_field;
  return (
    <div className="notice info">
      <label htmlFor={`decision-${field}`}>Decision for {field}</label>
      <select id={`decision-${field}`} value={decision} onChange={(e) => setDecision(e.target.value as ProposedMapping["decision"])}>
        <option value="map">map to a CMS field</option>
        <option value="no_target">do not send to the CMS</option>
        <option value="unsupported">unsupported</option>
      </select>
      {decision === "map" && (
        <>
          <label htmlFor={`target-${field}`}>Target field for {field}</label>
          <select id={`target-${field}`} value={target} onChange={(e) => setTarget(e.target.value)}>
            <option value="">Choose…</option>
            {targets?.fields.map((f) => (
              <option key={f.key} value={f.key}>
                {f.label} ({f.key})
              </option>
            ))}
          </select>
          <label htmlFor={`transformation-${field}`}>Transformation for {field}</label>
          <select id={`transformation-${field}`} value={transformation} onChange={(e) => setTransformation(e.target.value)}>
            {["none", "trim", "lowercase", "normalize_phone"].map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </>
      )}
      <label htmlFor={`edit-comment-${field}`}>Reason for the edit</label>
      <input id={`edit-comment-${field}`} value={comment} onChange={(e) => setComment(e.target.value)} />
      <div className="row" style={{ marginTop: "0.5rem" }}>
        <button
          onClick={() =>
            onSave(
              {
                ...proposal.mapping,
                decision,
                target_field: decision === "map" ? target : "",
                transformation: decision === "map" ? transformation : "none",
                value_map: decision === "map" ? proposal.mapping.value_map : [],
                candidates: [],
                confidence: 1,
                rationale: comment || "Edited by reviewer.",
              },
              comment,
            )
          }
        >
          Save edit
        </button>
        <button className="secondary" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>
  );
}

export function MappingsTab({ project, goTo }: { project: Project; goTo: (tab: string) => void }) {
  const { can } = useSession();
  const [generation, setGeneration] = useState<Generation | null>(null);
  const [targets, setTargets] = useState<SchemaDetail | null>(null);
  const [comments, setComments] = useState<Record<number, string>>({});
  const [editing, setEditing] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const base = `/onboarding-projects/${project.id}`;

  const load = useCallback(async () => {
    try {
      setGeneration(await api<Generation>(`${base}/mappings`));
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 404)) setError(describeError(e));
    }
    if (project.latest_target_schema)
      setTargets(await api<SchemaDetail>(`${base}/schemas/${project.latest_target_schema.id}`));
  }, [base, project.latest_target_schema]);

  useEffect(() => {
    void load();
  }, [load]);

  async function review(p: Proposal, action: string, extra: Record<string, unknown> = {}) {
    setError(null);
    setMessage(null);
    try {
      await api(`${base}/mappings/${p.id}/review`, {
        method: "POST",
        body: { action, comment: comments[p.id] || null, ...extra },
      });
      setEditing(null);
      await load();
    } catch (e) {
      setError(describeError(e));
    }
  }

  async function approveVersion() {
    if (!generation) return;
    setError(null);
    try {
      const v = await api<MappingVersion>(`${base}/approve-mappings`, {
        method: "POST",
        body: { generation_id: generation.id },
      });
      setMessage(
        `Approved as mapping version ${v.version} (${v.items.length} changes${v.complete ? "" : ", onboarding incomplete"}).`,
      );
      goTo("Versions");
    } catch (e) {
      setError(describeError(e));
    }
  }

  if (!generation)
    return (
      <section className="panel">
        <ErrorNotice error={error} />
        <p className="muted">No proposals yet. Use “Propose mappings” on the Overview tab.</p>
      </section>
    );

  const current = generation.proposals.filter((p) => p.review_status !== "edited");
  const open = current.filter((p) => p.review_status === "pending" || p.review_status === "needs_clarification");
  return (
    <>
      <section className="panel">
        <h2>Proposals</h2>
        <p className="muted">
          Generated by {generation.provider}/{generation.model} (prompt {generation.prompt_version}) for{" "}
          {generation.created_by}.
          {generation.fallbacks.length > 0 &&
            ` Fallback used after: ${generation.fallbacks.map((f) => `${f[0]} (${f[1]})`).join(", ")}.`}
        </p>
        {generation.untrusted_source_fields.length > 0 && (
          <div className="notice warn">
            Instruction-like text was found in {generation.untrusted_source_fields.join(", ")}. It was
            treated as data only, and those fields must be reviewed.
          </div>
        )}
        {generation.coverage_gaps.map((g) => (
          <div key={g.target_field} className="notice warn">
            {g.message}
          </div>
        ))}
        <ErrorNotice error={error} />
        {message && (
          <div className="notice ok" role="status">
            {message}
          </div>
        )}
        <table aria-label="Mapping proposals">
          <thead>
            <tr>
              <th>Source field</th>
              <th>Proposal</th>
              <th>Checks</th>
              <th>Why</th>
              <th>Review</th>
            </tr>
          </thead>
          <tbody>
            {current.map((p) => (
              <tr key={p.id} data-testid={`proposal-${p.source_field}`}>
                <td>
                  <code>{p.source_field}</code>
                  {p.origin !== "llm" && <div className="muted">{p.origin === "reviewer" ? "edited by reviewer" : "added by system"}</div>}
                </td>
                <td>
                  {describeMapping(p.mapping)}
                  <div className="muted">confidence {p.mapping.confidence.toFixed(2)}</div>
                </td>
                <td>
                  <Badge value={p.validation_status} title={explain(VALIDATION, p.validation_status)} />
                  <ul className="issues">
                    {p.validation_issues.map((i) => (
                      <li key={i.code} className="muted">
                        {explain(ISSUE, i.code)}
                      </li>
                    ))}
                  </ul>
                </td>
                <td>
                  {p.mapping.rationale}
                  {p.mapping.open_questions.map((q) => (
                    <div key={q} className="muted">
                      ? {q}
                    </div>
                  ))}
                </td>
                <td>
                  <Badge value={p.review_status} />
                  {p.review_comment && <div className="muted">“{p.review_comment}” — {p.reviewed_by}</div>}
                  {can("mappings:review") && (
                    <>
                      <input
                        aria-label={`Comment for ${p.source_field}`}
                        placeholder="Comment"
                        value={comments[p.id] ?? ""}
                        onChange={(e) => setComments({ ...comments, [p.id]: e.target.value })}
                      />
                      <div className="row" style={{ marginTop: "0.3rem" }}>
                        <button aria-label={`Approve ${p.source_field}`} onClick={() => review(p, "approve")}>
                          Approve
                        </button>
                        <button className="secondary" aria-label={`Reject ${p.source_field}`} onClick={() => review(p, "reject")}>
                          Reject
                        </button>
                        <button className="secondary" aria-label={`Edit ${p.source_field}`} onClick={() => setEditing(editing === p.id ? null : p.id)}>
                          Edit
                        </button>
                        <button
                          className="secondary"
                          aria-label={`Ask about ${p.source_field}`}
                          onClick={() => review(p, "request_clarification")}
                        >
                          Ask
                        </button>
                      </div>
                      {editing === p.id && (
                        <EditForm
                          proposal={p}
                          targets={targets}
                          onCancel={() => setEditing(null)}
                          onSave={(mapping, comment) => {
                            setComments({ ...comments, [p.id]: comment });
                            void review(p, "edit", { mapping, comment: comment || null });
                          }}
                        />
                      )}
                    </>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      {can("mappings:approve") && (
        <section className="panel">
          <h2>Approve a mapping version</h2>
          <p className="muted">
            Freezes every approved proposal into an immutable, versioned plan. Only an approved version can
            be executed against the CMS.
          </p>
          {open.length > 0 && (
            <p className="notice warn">{open.length} proposal(s) still need a decision: {open.map((p) => p.source_field).join(", ")}.</p>
          )}
          <button disabled={open.length > 0} onClick={approveVersion}>
            Approve mapping version
          </button>
        </section>
      )}
    </>
  );
}
