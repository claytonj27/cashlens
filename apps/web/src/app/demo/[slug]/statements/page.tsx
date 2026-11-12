"use client";

import Link from "next/link";
import { useParams } from "next/navigation";

import { AccountBalances } from "@/components/account-balances";
import { DemoShell } from "@/components/demo-shell";
import { Card, CardTitle, Empty } from "@/components/ui";

/**
 * A demo business has no statements to import: its transactions ship with the application.
 * Saying so is the honest content for this section, rather than hiding the tab and leaving a
 * reader to wonder whether demo and private businesses work differently in some deeper way.
 */
export default function DemoStatementsPage() {
  const params = useParams<{ slug: string }>();
  return (
    <DemoShell slug={params.slug} section="statements">
      {(summary) => (
        <>
          <Card>
            <CardTitle
              title="Statements"
              hint="Where the figures in this workspace came from."
            />
            <div className="mt-4">
              <Empty title="Nothing to import here">
                This is a demo business. Its {summary.posted_transaction_count} transactions
                are generated synthetic data that ships with CashLens, so there are no bank
                statements behind them and nothing to upload.{" "}
                <Link
                  href="/workspace"
                  className="text-plum-700 underline underline-offset-2"
                >
                  Your own business
                </Link>{" "}
                is where statements are imported.
              </Empty>
            </div>
          </Card>
          <AccountBalances workspaceId={summary.workspace_id} token={null} />
        </>
      )}
    </DemoShell>
  );
}
