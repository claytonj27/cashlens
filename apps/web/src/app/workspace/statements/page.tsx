"use client";

import Link from "next/link";

import { useSession } from "@/lib/use-session";
import { withWorkspace } from "@/lib/workspace-selection";
import { AccountBalances } from "@/components/account-balances";
import { ImportsList } from "@/components/imports-list";
import { WorkspaceShell } from "@/components/workspace-shell";
import { buttonClass, Card, CardTitle } from "@/components/ui";

export default function WorkspaceStatementsPage() {
  const { session } = useSession();
  const token = session?.access_token ?? null;

  return (
    <WorkspaceShell section="statements">
      {({ workspace, selected }) => (
        <>
          <Card>
            <CardTitle
              title="Statements"
              hint={workspace.statements.message}
              action={
                workspace.statements.active_import_id ? (
                  <Link
                    href={withWorkspace(
                      `/workspace/imports/${workspace.statements.active_import_id}`,
                      selected,
                    )}
                    className={buttonClass}
                  >
                    Resume import review
                  </Link>
                ) : (
                  <Link
                    href={withWorkspace("/workspace/import", selected)}
                    className={buttonClass}
                  >
                    Import statement
                  </Link>
                )
              }
            />
            {token ? (
              <ImportsList workspaceId={workspace.workspace_id} token={token} />
            ) : null}
          </Card>
          <AccountBalances workspaceId={workspace.workspace_id} token={token} />
        </>
      )}
    </WorkspaceShell>
  );
}
