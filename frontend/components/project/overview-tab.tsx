"use client";

import { useState } from "react";
import { useSession } from "@/components/session";
import { Badge, ErrorNotice, when } from "@/components/ui";
import { api, describeError } from "@/lib/api";
import { explain, STAGE } from "@/lib/explain";
import type { Generation, Project } from "@/lib/types";

const FORMATS = [
  { value: "json_schema", label: "JSON Schema (.json)" },
  { value: "field_export_csv", label: "Field export (.csv)" },
  { value: "html_form", label: "HTML intake form (.html)" },
];

export function OverviewTab({
  project,
  onChanged,
  goTo,
}: {
  project: Project;
  onChanged: () => Promise<void>;
  goTo: (tab: string) => void;
}) {
  const { can } = useSession();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const base = `/onboarding-projects/${project.id}`;

  async function run(label: string, action: () => Promise<string>) {
    setBusy(label);
    setError(null);
    setMessage(null);
    try {
      setMessage(await action());
      await onChanged();
    } catch (e) {
      setError(describeError(e));
    } finally {
      setBusy(null);
    }
  }

  async function upload(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const file = form.get("file");
    if (!(file instanceof File) || file.size === 0) {
      setError("Choose a file to upload.");
      return;
    }
    const content = await file.text();
    await run("upload", async () => {
      const result = await api<{ created: boolean; schema_version: { version: number; field_count: number } }>(
        `${base}/source-schema`,
        { method: "POST", body: { format: form.get("format"), filename: file.name, content } },
      );
      const v = result.schema_version;
      return result.created
        ? `Source schema v${v.version} registered with ${v.field_count} fields.`
        : `Identical to the current version (v${v.version}); nothing changed.`;
    });
  }

  return (
    <>
      <section className="panel">
        <h2>Where this project stands</h2>
        <p>
          <Badge value={project.stage} /> {explain(STAGE, project.stage)}
        </p>
        <table>
          <tbody>
            <tr>
              <th>Target CMS</th>
              <td>
                {project.connector.name} — <code>{project.connector.base_url}</code>
              </td>
            </tr>
            <tr>
              <th>Source schema</th>
              <td>
                {project.latest_source_schema
                  ? `v${project.latest_source_schema.version} · ${project.latest_source_schema.field_count} fields · ${project.latest_source_schema.format} · by ${project.latest_source_schema.created_by ?? "—"} · ${when(project.latest_source_schema.created_at)}`
                  : "not uploaded"}
              </td>
            </tr>
            <tr>
              <th>Target schema</th>
              <td>
                {project.latest_target_schema
                  ? `v${project.latest_target_schema.version} · ${project.latest_target_schema.field_count} fields · CMS revision ${project.latest_target_schema.target_config_revision ?? "—"} · ${when(project.latest_target_schema.created_at)}`
                  : "not inspected"}
              </td>
            </tr>
          </tbody>
        </table>
      </section>
      <ErrorNotice error={error} />
      {message && (
        <div className="notice ok" role="status">
          {message}
        </div>
      )}
      {can("source_schema:write") && (
        <section className="panel">
          <h2>1. Upload the firm&apos;s intake schema</h2>
          <form onSubmit={upload}>
            <label htmlFor="format">Format</label>
            <select id="format" name="format">
              {FORMATS.map((f) => (
                <option key={f.value} value={f.value}>
                  {f.label}
                </option>
              ))}
            </select>
            <label htmlFor="file">File</label>
            <input id="file" name="file" type="file" accept=".json,.csv,.html,.htm" />
            <div style={{ marginTop: "0.75rem" }}>
              <button type="submit" disabled={busy !== null}>
                {busy === "upload" ? "Uploading…" : "Upload source schema"}
              </button>
            </div>
          </form>
        </section>
      )}
      {can("target:inspect") && (
        <section className="panel">
          <h2>2. Inspect the target CMS</h2>
          <p className="muted">Reads the CMS&apos;s fields through its API (read-only).</p>
          <button
            disabled={busy !== null}
            onClick={() =>
              run("inspect", async () => {
                const r = await api<{ created: boolean; schema_version: { version: number; field_count: number } }>(
                  `${base}/inspect-target`,
                  { method: "POST" },
                );
                return r.created
                  ? `Target schema v${r.schema_version.version} captured (${r.schema_version.field_count} fields).`
                  : "The CMS is unchanged since the last inspection.";
              })
            }
          >
            {busy === "inspect" ? "Inspecting…" : "Inspect target CMS"}
          </button>
        </section>
      )}
      {can("mappings:propose") && (
        <section className="panel">
          <h2>3. Ask the agent for mapping proposals</h2>
          <p className="muted">
            The LLM proposes; deterministic checks validate; nothing changes until a person
            approves a version.
          </p>
          <button
            disabled={busy !== null || project.stage !== "ready_for_mapping"}
            onClick={() =>
              run("propose", async () => {
                const g = await api<Generation>(`${base}/propose-mappings`, { method: "POST" });
                goTo("Mappings");
                return `${g.proposals.length} proposals from ${g.provider}/${g.model}.`;
              })
            }
          >
            {busy === "propose" ? "The agent is working…" : "Propose mappings"}
          </button>
        </section>
      )}
    </>
  );
}
