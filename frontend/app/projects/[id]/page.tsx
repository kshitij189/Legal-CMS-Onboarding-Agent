"use client";

import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { AuditTab } from "@/components/project/audit-tab";
import { MappingsTab } from "@/components/project/mappings-tab";
import { OverviewTab } from "@/components/project/overview-tab";
import { RunsTab } from "@/components/project/runs-tab";
import { SchemasTab } from "@/components/project/schemas-tab";
import { VersionsTab } from "@/components/project/versions-tab";
import { useSession } from "@/components/session";
import { ErrorNotice } from "@/components/ui";
import { api, describeError } from "@/lib/api";
import type { Project } from "@/lib/types";

const TABS = ["Overview", "Schemas", "Mappings", "Versions", "Runs", "Audit log"] as const;
type Tab = (typeof TABS)[number];

export default function ProjectPage() {
  const { id } = useParams<{ id: string }>();
  const { me, loading } = useSession();
  const router = useRouter();
  const [project, setProject] = useState<Project | null>(null);
  const [tab, setTab] = useState<Tab>("Overview");
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      setProject(await api<Project>(`/onboarding-projects/${id}`));
    } catch (e) {
      setError(describeError(e));
    }
  }, [id]);

  useEffect(() => {
    if (!loading && !me) router.replace("/");
    if (me) void reload();
  }, [me, loading, router, reload]);

  if (!me) return null;
  return (
    <>
      <h1>{project?.name ?? "Project"}</h1>
      <ErrorNotice error={error} />
      {project && (
        <>
          <nav className="tabs" role="tablist" aria-label="Project sections">
            {TABS.map((t) => (
              <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)}>
                {t}
              </button>
            ))}
          </nav>
          {tab === "Overview" && (
            <OverviewTab project={project} onChanged={reload} goTo={(t) => setTab(t as Tab)} />
          )}
          {tab === "Schemas" && <SchemasTab project={project} />}
          {tab === "Mappings" && <MappingsTab project={project} goTo={(t) => setTab(t as Tab)} />}
          {tab === "Versions" && <VersionsTab project={project} goTo={(t) => setTab(t as Tab)} />}
          {tab === "Runs" && <RunsTab project={project} />}
          {tab === "Audit log" && <AuditTab project={project} />}
        </>
      )}
    </>
  );
}
