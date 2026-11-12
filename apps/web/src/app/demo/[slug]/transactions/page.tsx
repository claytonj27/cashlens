"use client";

import { useParams } from "next/navigation";

import { DemoShell } from "@/components/demo-shell";
import { WorkspaceData } from "@/components/transaction-view";

export default function DemoTransactionsPage() {
  const params = useParams<{ slug: string }>();
  return (
    <DemoShell slug={params.slug} section="transactions">
      {(summary) => (
        <WorkspaceData
          workspaceId={summary.workspace_id}
          token={null}
          emptyMessage="No transactions in this demo business."
        />
      )}
    </DemoShell>
  );
}
