"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { api, ApiError } from "@/lib/api";
import { useSession } from "@/lib/use-session";
import { CashDashboard } from "@/components/cash-dashboard";
import { DeleteBusiness } from "@/components/delete-business";
import { WorkspaceShell, workspaceSections } from "@/components/workspace-shell";
import { buttonClass, Card, CardTitle, Field, inputClass, Notice } from "@/components/ui";

export default function WorkspaceOverviewPage() {
  const router = useRouter();
  const { session } = useSession();
  const token = session?.access_token ?? null;

  return (
    <WorkspaceShell
      section="overview"
      onboarding={(_me, reload) => (
        <FirstBusiness token={token} onCreated={reload} />
      )}
    >
      {({ workspace, selected }) => {
        const sections = workspaceSections(selected);
        return (
          <>
            <div id="cash-overview" className="scroll-mt-4">
              <CashDashboard
                workspaceId={workspace.workspace_id}
                token={token}
                transactionsHref={sections.transactions}
                analysisHref={sections.analysis}
                statementsHref={sections.statements}
              />
            </div>
            <Notice tone="info">
              Demo businesses are separate from this workspace; their synthetic transactions
              are never attached to your business.
            </Notice>
            {token ? (
              <DeleteBusiness
                businessId={workspace.business.id}
                businessName={workspace.business.name}
                token={token}
                onFinished={() => {
                  // Never land back on the deleted workspace: drop any selection so the page
                  // resumes another business or shows first-business onboarding.
                  router.replace("/workspace");
                  router.refresh();
                }}
              />
            ) : null}
          </>
        );
      }}
    </WorkspaceShell>
  );
}

function FirstBusiness({
  token,
  onCreated,
}: {
  token: string | null;
  onCreated: () => void;
}) {
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function provision(event: FormEvent) {
    event.preventDefault();
    if (!token) return;
    setBusy(true);
    setError(null);
    try {
      await api("/api/private/provision", {
        method: "POST",
        token,
        body: { business_name: name },
      });
      onCreated();
    } catch (caught) {
      setError(
        caught instanceof ApiError ? caught.message : "Could not set up your workspace.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="max-w-xl">
      <CardTitle
        title="Set up your business"
        hint="Name it to create a private workspace. Only your own data will ever appear here."
      />
      <form onSubmit={provision} className="mt-4 flex flex-col gap-4">
        {error ? <Notice tone="error">{error}</Notice> : null}
        <Field label="Business name">
          <input
            type="text"
            required
            maxLength={120}
            value={name}
            onChange={(e) => setName(e.target.value)}
            className={inputClass}
          />
        </Field>
        <div>
          <button type="submit" disabled={busy} className={buttonClass}>
            {busy ? "Creating…" : "Create workspace"}
          </button>
        </div>
      </form>
    </Card>
  );
}
