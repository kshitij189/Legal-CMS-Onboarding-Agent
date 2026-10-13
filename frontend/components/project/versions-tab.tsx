"use client";

import { useEffect, useState } from "react";
import { useSession } from "@/components/session";
import { Badge, ErrorNotice, when } from "@/components/ui";
import { api, describeError } from "@/lib/api";
import type { MappingVersion, Project, Run } from "@/lib/types";

export function VersionsTab({ project, goTo }: { project: Project; goTo: (tab: string) => void }) {
  const { can } = useSession();
  const [versions, setVersions] = useState<MappingVersion[]>([]);
  const [channel, setChannel] = useState<"browser" | "api">("browser");
  const [error, setError] = useState<string | null>(null);
  const base = `/onboarding-projects/${project.id}`;

  useEffect(() => {
    api<MappingVersion[]>(`${base}/mapping-versions`)
      .then(setVersions)
      .catch((e) => setError(describeError(e)));
  }, [base]);

  async function execute(version: MappingVersion) {
    setError(null);
    try {
      await api<Run>(`${base}/execute`, {
        method: "POST",
        body: { mapping_version_id: version.id, channel },
      });
      goTo("Runs");
    } catch (e) {
      setError(describeError(e));
    }
  }

  return (
    <section className="panel">
      <h2>Approved mapping versions</h2>
      <ErrorNotice error={error} />
      {versions.length === 0 && <p className="muted">Nothing approved yet.</p>}
      {can("runs:execute") && versions.some((v) => v.status === "approved") && (
        <div className="row">
          <label htmlFor="channel" style={{ margin: 0 }}>
            Apply through
          </label>
          <select id="channel" value={channel} onChange={(e) => setChannel(e.target.value as "browser" | "api")} style={{ maxWidth: "16rem" }}>
            <option value="browser">the CMS web UI (Playwright)</option>
            <option value="api">the CMS API</option>
          </select>
        </div>
      )}
      {[...versions].reverse().map((v) => (
        <div key={v.id} className="panel" style={{ marginTop: "0.75rem" }}>
          <div className="row">
            <strong>Version {v.version}</strong>
            <Badge value={v.status} />
            {!v.complete && <Badge value="incomplete" title="A required CMS field has no source" />}
            <span className="muted">
              approved by {v.approved_by}, {when(v.approved_at)} · fingerprint <code>{v.fingerprint.slice(0, 12)}</code>
            </span>
          </div>
          {v.coverage_gaps.map((g) => (
            <div key={g.target_field} className="notice warn">
              Onboarding incomplete: {g.message}
            </div>
          ))}
          <table aria-label={`Changes in version ${v.version}`}>
            <thead>
              <tr>
                <th>Source field</th>
                <th>Change</th>
              </tr>
            </thead>
            <tbody>
              {v.items.map((i) => (
                <tr key={i.proposal_id}>
                  <td>
                    <code>{i.mapping.source_field}</code>
                  </td>
                  <td>
                    {i.mapping.decision === "requires_custom_field"
                      ? `create ${i.mapping.custom_field_entity}.custom.${i.mapping.custom_field_name}, then map to it`
                      : `→ ${i.mapping.target_field} (${i.mapping.transformation})`}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {can("runs:execute") && v.status === "approved" && (
            <div style={{ marginTop: "0.75rem" }}>
              <button onClick={() => execute(v)}>Execute version {v.version}</button>
            </div>
          )}
        </div>
      ))}
    </section>
  );
}
