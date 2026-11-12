"use client";

import { useSession } from "@/lib/use-session";
import { WorkspaceData } from "@/components/transaction-view";
import { WorkspaceShell } from "@/components/workspace-shell";

export default function WorkspaceTransactionsPage() {
  const { session } = useSession();
  const token = session?.access_token ?? null;
  return (
    <WorkspaceShell section="transactions">
      {({ workspace }) => (
        <WorkspaceData
          workspaceId={workspace.workspace_id}
          token={token}
          emptyMessage="No transactions yet. Import a statement to add them."
        />
      )}
    </WorkspaceShell>
  );
}
