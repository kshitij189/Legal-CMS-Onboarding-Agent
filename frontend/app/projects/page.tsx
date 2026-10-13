"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { useSession } from "@/components/session";
import { Badge, ErrorNotice, when } from "@/components/ui";
import { api, describeError } from "@/lib/api";
import { explain, STAGE } from "@/lib/explain";
import type { Connector, Project } from "@/lib/types";

export default function ProjectsPage() {
  const { me, loading, can } = useSession();
  const router = useRouter();
  const [projects, setProjects] = useState<Project[]>([]);
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setProjects(await api<Project[]>("/onboarding-projects"));
      setConnectors(await api<Connector[]>("/connectors"));
    } catch (e) {
      setError(describeError(e));
    }
  }, []);

  useEffect(() => {
    if (!loading && !me) router.replace("/");
    if (me) void load();
  }, [me, loading, router, load]);

  async function create(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      const project = await api<Project>("/onboarding-projects", {
        method: "POST",
        body: { name: String(form.get("name")), connector_id: Number(form.get("connector_id")) },
      });
      router.push(`/projects/${project.id}`);
    } catch (e) {
      setError(describeError(e));
    }
  }

  if (!me) return null;
  return (
    <>
      <h1>Onboarding projects — {me.firm.name}</h1>
      <ErrorNotice error={error} />
      <section className="panel">
        <table aria-label="Projects">
          <thead>
            <tr>
              <th>Project</th>
              <th>Stage</th>
              <th>Source schema</th>
              <th>Target schema</th>
              <th>Created</th>
            </tr>
          </thead>
          <tbody>
            {projects.map((p) => (
              <tr key={p.id}>
                <td>
                  <Link href={`/projects/${p.id}`}>{p.name}</Link>
                </td>
                <td>
                  <Badge value={p.stage} title={explain(STAGE, p.stage)} />
                </td>
                <td>{p.latest_source_schema ? `v${p.latest_source_schema.version}` : "—"}</td>
                <td>{p.latest_target_schema ? `v${p.latest_target_schema.version}` : "—"}</td>
                <td>{when(p.created_at)}</td>
              </tr>
            ))}
            {projects.length === 0 && (
              <tr>
                <td colSpan={5} className="muted">
                  No projects yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </section>
      {can("projects:create") && (
        <section className="panel">
          <h2>New onboarding project</h2>
          {connectors.length === 0 ? (
            <p className="muted">Add a CMS connector for this firm first (API: POST /connectors).</p>
          ) : (
            <form onSubmit={create}>
              <label htmlFor="name">Project name</label>
              <input id="name" name="name" required />
              <label htmlFor="connector_id">Target CMS connector</label>
              <select id="connector_id" name="connector_id">
                {connectors.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name} ({c.base_url})
                  </option>
                ))}
              </select>
              <div style={{ marginTop: "1rem" }}>
                <button type="submit">Create project</button>
              </div>
            </form>
          )}
        </section>
      )}
    </>
  );
}
