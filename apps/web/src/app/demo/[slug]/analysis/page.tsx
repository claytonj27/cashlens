"use client";

import { useParams } from "next/navigation";

import { AnalysisView } from "@/components/analysis-view";
import { DemoShell, demoSections } from "@/components/demo-shell";

export default function DemoAnalysisPage() {
  const params = useParams<{ slug: string }>();
  return (
    <DemoShell slug={params.slug} section="analysis">
      {(summary) => {
        const sections = demoSections(summary.business.slug);
        return (
          <AnalysisView
            workspaceId={summary.workspace_id}
            token={null}
            transactionsHref={sections.transactions}
            overviewHref={sections.overview}
          />
        );
      }}
    </DemoShell>
  );
}
