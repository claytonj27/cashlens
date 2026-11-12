"use client";

import { useParams } from "next/navigation";

import { CashDashboard } from "@/components/cash-dashboard";
import { DemoShell, demoSections } from "@/components/demo-shell";
import { Notice } from "@/components/ui";

export default function DemoOverviewPage() {
  const params = useParams<{ slug: string }>();
  return (
    <DemoShell slug={params.slug} section="overview">
      {(summary) => {
        const sections = demoSections(summary.business.slug);
        return (
          <>
            <div id="cash-overview" className="scroll-mt-4">
              <CashDashboard
                workspaceId={summary.workspace_id}
                token={null}
                transactionsHref={sections.transactions}
                analysisHref={sections.analysis}
                statementsHref={sections.statements}
              />
            </div>
            <Notice tone="info">{summary.note}</Notice>
          </>
        );
      }}
    </DemoShell>
  );
}
