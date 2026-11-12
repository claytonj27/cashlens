"use client";

import { useSession } from "@/lib/use-session";
import { AnalysisView } from "@/components/analysis-view";
import { WorkspaceShell, workspaceSections } from "@/components/workspace-shell";

export default function WorkspaceAnalysisPage() {
  const { session } = useSession();
  const token = session?.access_token ?? null;

  return (
    <WorkspaceShell section="analysis">
      {({ workspace, selected }) => {
        const sections = workspaceSections(selected);
        return (
          <AnalysisView
            workspaceId={workspace.workspace_id}
            token={token}
            transactionsHref={sections.transactions}
            overviewHref={sections.overview}
          />
        );
      }}
    </WorkspaceShell>
  );
}
