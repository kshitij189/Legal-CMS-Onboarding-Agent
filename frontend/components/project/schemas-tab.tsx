"use client";

import { useEffect, useState } from "react";
import { ErrorNotice } from "@/components/ui";
import { api, describeError } from "@/lib/api";
import type { Project, SchemaDetail } from "@/lib/types";

function FieldsTable({ schema, label }: { schema: SchemaDetail; label: string }) {
  const target = schema.kind === "target";
  return (
    <table aria-label={label}>
      <thead>
        <tr>
          <th>Key</th>
          <th>Label</th>
          <th>Type</th>
          <th>Required</th>
          <th>Options</th>
          {target && <th>Canonical concept</th>}
        </tr>
      </thead>
      <tbody>
        {schema.fields.map((f) => (
          <tr key={f.key}>
            <td>
              <code>{f.key}</code>
            </td>
            <td>{f.label}</td>
            <td>{f.data_type}</td>
            <td>{f.required ? "yes" : "no"}</td>
            <td>{f.options?.join(", ") ?? ""}</td>
            {target && (
              <td>{f.canonical_ref ?? <span className="muted">vendor-specific</span>}</td>
            )}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function SchemasTab({ project }: { project: Project }) {
  const [source, setSource] = useState<SchemaDetail | null>(null);
  const [target, setTarget] = useState<SchemaDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const base = `/onboarding-projects/${project.id}/schemas`;
    const load = async () => {
      try {
        if (project.latest_source_schema)
          setSource(await api<SchemaDetail>(`${base}/${project.latest_source_schema.id}`));
        if (project.latest_target_schema)
          setTarget(await api<SchemaDetail>(`${base}/${project.latest_target_schema.id}`));
      } catch (e) {
        setError(describeError(e));
      }
    };
    void load();
  }, [project]);

  return (
    <>
      <ErrorNotice error={error} />
      <section className="panel">
        <h2>Source schema {source ? `v${source.version} (${source.format})` : ""}</h2>
        {source ? <FieldsTable schema={source} label="Source fields" /> : <p className="muted">Not uploaded.</p>}
      </section>
      <section className="panel">
        <h2>Target CMS schema {target ? `v${target.version}` : ""}</h2>
        {target ? <FieldsTable schema={target} label="Target fields" /> : <p className="muted">Not inspected.</p>}
      </section>
    </>
  );
}
