"use client";

import { useEffect, useState } from "react";
import { ErrorNotice, when } from "@/components/ui";
import { api, describeError } from "@/lib/api";
import type { AuditEvent, Project } from "@/lib/types";

export function AuditTab({ project }: { project: Project }) {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<AuditEvent[]>(`/onboarding-projects/${project.id}/audit-events`)
      .then(setEvents)
      .catch((e) => setError(describeError(e)));
  }, [project.id]);

  return (
    <section className="panel">
      <h2>Audit log</h2>
      <p className="muted">Append-only record of who proposed, reviewed, approved and executed what.</p>
      <ErrorNotice error={error} />
      <table aria-label="Audit events">
        <thead>
          <tr>
            <th>When</th>
            <th>Who</th>
            <th>Action</th>
            <th>Details</th>
          </tr>
        </thead>
        <tbody>
          {[...events].reverse().map((e) => (
            <tr key={e.id}>
              <td>{when(e.created_at)}</td>
              <td>{e.actor ?? "system"}</td>
              <td>
                <code>{e.action}</code>
              </td>
              <td className="muted">
                <code>{JSON.stringify(e.details)}</code>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
