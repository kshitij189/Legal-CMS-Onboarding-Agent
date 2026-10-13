"use client";

import { useCallback, useEffect, useState } from "react";
import { Badge, ErrorNotice, when } from "@/components/ui";
import { api, describeError } from "@/lib/api";
import { CHECK_STATUS, ERROR_CATEGORY, explain, RUN_STATUS, STEP_STATUS } from "@/lib/explain";
import type { Project, Run, Step } from "@/lib/types";

function stepNote(step: Step): string {
  const e = step.evidence;
  if (e.after_interruption) return "The worker was interrupted after writing; the CMS was checked and the write was not repeated.";
  if (e.resolved_by_read_back) return "The CMS response was lost; reading the CMS confirmed the change, so it was not written again.";
  if (step.error_category) return `${explain(ERROR_CATEGORY, step.error_category)} ${step.error_message ?? ""}`;
  if (step.attempts > 1) return `Succeeded on attempt ${step.attempts} after checking the CMS.`;
  return explain(STEP_STATUS, step.status);
}

function RunDetail({ run }: { run: Run }) {
  const tone = run.status === "succeeded" ? "ok" : run.status === "running" ? "info" : run.status === "blocked" ? "warn" : "bad";
  return (
    <div>
      <div className={`notice ${tone}`} role="status">
        <strong>
          Run {run.id}: <Badge value={run.status} />
        </strong>{" "}
        {explain(RUN_STATUS, run.status)}
        {run.error_category && (
          <div>
            Why: {explain(ERROR_CATEGORY, run.error_category)}
            {run.error_message && <span className="muted"> ({run.error_message})</span>}
          </div>
        )}
      </div>
      <p className="muted">
        Version {run.mapping_version_id} via {run.channel === "browser" ? "the CMS web UI" : "the CMS API"} · started by{" "}
        {run.created_by} {when(run.started_at)} · finished {when(run.finished_at)} · CMS revision{" "}
        {run.config_revision_before ?? "—"} → {run.config_revision_after ?? "—"}
        {run.artifact_dir && (
          <>
            {" "}
            · browser trace and screenshot saved in <code>artifacts/{run.artifact_dir}</code>
          </>
        )}
      </p>
      {run.drift_issues.length > 0 && (
        <>
          <h3>Differences found in the CMS before writing</h3>
          <ul>
            {run.drift_issues.map((d) => (
              <li key={d.field + d.code}>
                <Badge value={d.blocking ? "blocked" : "pending"} title={d.blocking ? "blocking" : "reported only"} /> {d.message}
              </li>
            ))}
          </ul>
        </>
      )}
      <h3>Actions</h3>
      <table aria-label={`Actions of run ${run.id}`}>
        <thead>
          <tr>
            <th>#</th>
            <th>Action</th>
            <th>Status</th>
            <th>What happened</th>
          </tr>
        </thead>
        <tbody>
          {run.steps.map((s) => (
            <tr key={s.sequence}>
              <td>{s.sequence}</td>
              <td>
                {s.action_type === "create_custom_field" ? "create field " : "map "}
                <code>{s.subject}</code>
              </td>
              <td>
                <Badge value={s.status} />
              </td>
              <td>{stepNote(s)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {run.verification.length > 0 && (
        <>
          <h3>Verification (read back from the CMS)</h3>
          <table aria-label={`Verification of run ${run.id}`}>
            <thead>
              <tr>
                <th>Item</th>
                <th>Result</th>
                <th>Explanation</th>
              </tr>
            </thead>
            <tbody>
              {run.verification.map((c) => (
                <tr key={c.check_type + c.subject}>
                  <td>
                    <code>{c.subject}</code>
                  </td>
                  <td>
                    <Badge value={c.status} />
                  </td>
                  <td>
                    {explain(CHECK_STATUS, c.status)}
                    {c.status !== "match" && <span className="muted"> {c.reason}</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}

export function RunsTab({ project }: { project: Project }) {
  const [runs, setRuns] = useState<Run[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setRuns(await api<Run[]>(`/onboarding-projects/${project.id}/runs`));
    } catch (e) {
      setError(describeError(e));
    }
  }, [project.id]);

  useEffect(() => {
    void load();
  }, [load]);

  // Runs execute in the background (Temporal); refresh while any is still running.
  useEffect(() => {
    if (!runs.some((r) => r.status === "running")) return;
    const timer = setInterval(() => void load(), 2000);
    return () => clearInterval(timer);
  }, [runs, load]);

  return (
    <section className="panel">
      <h2>Runs</h2>
      <ErrorNotice error={error} />
      {runs.length === 0 && <p className="muted">No runs yet. Execute an approved version.</p>}
      {runs.map((r) => (
        <div key={r.id} className="panel">
          <RunDetail run={r} />
        </div>
      ))}
    </section>
  );
}
