"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState, type ReactNode } from "react";

import { api, ApiError } from "@/lib/api";
import { featuresConfigured } from "@/lib/features";
import { useSession } from "@/lib/use-session";
import { readWorkspaceSelection, withWorkspace } from "@/lib/workspace-selection";
import { ScaffoldNotice } from "@/components/scaffold-notice";
import { WorkspaceNav, type BusinessOption, type Section } from "@/components/workspace-nav";
import { buttonClass, Notice } from "@/components/ui";

export type Me = {
  user_id: string;
  email: string | null;
  business: { id: string; name: string } | null;
  workspace_id: string | null;
  businesses: { business: { id: string; name: string }; workspace_id: string | null }[];
};

export type Workspace = {
  business: { id: string; name: string };
  workspace_id: string;
  account_count: number;
  transaction_count: number;
  statements: {
    count: number;
    uploads_available: boolean;
    message: string;
    active_import_id: string | null;
    active_import_status: string | null;
  };
};

/**
 * Section links for a private workspace, carrying the selection.
 *
 * `selected` is the workspace the link asked for, or null when the reader is on their default
 * business. Passing it through keeps a bookmarked `?workspace=…` link naming the same business
 * on every section, and an absent one stays absent rather than being filled in with a guess.
 */
export function workspaceSections(selected: string | null): Record<Section, string> {
  return {
    overview: withWorkspace("/workspace", selected),
    analysis: withWorkspace("/workspace/analysis", selected),
    transactions: withWorkspace("/workspace/transactions", selected),
    statements: withWorkspace("/workspace/statements", selected),
  };
}

type Loaded = { me: Me | null; workspace: Workspace; selected: string | null };

export function WorkspaceShell({
  section,
  children,
  /** Rendered instead of the sections when the account has no business yet. */
  onboarding,
}: {
  section: Section;
  children: (loaded: Loaded) => ReactNode;
  onboarding?: (me: Me | null, reload: () => void) => ReactNode;
}) {
  return (
    <Suspense
      fallback={
        <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-10 text-ink-muted sm:px-6 lg:px-8">
          Loading…
        </main>
      }
    >
      <Inner section={section} onboarding={onboarding}>
        {children}
      </Inner>
    </Suspense>
  );
}

function Inner({
  section,
  children,
  onboarding,
}: {
  section: Section;
  children: (loaded: Loaded) => ReactNode;
  onboarding?: (me: Me | null, reload: () => void) => ReactNode;
}) {
  const search = useSearchParams();
  // A workspace named in the link is resolved by the API through the same authorized resolver
  // the scoped routes use. It is never replaced by another business, and a link that names
  // nothing usable is reported as unavailable rather than silently ignored.
  const { present: asked, id: selected } = readWorkspaceSelection(search);
  const unusable = asked && !selected;
  const { loading, session } = useSession();
  const token = session?.access_token ?? null;
  const [me, setMe] = useState<Me | null>(null);
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    if (!featuresConfigured || !token || unusable) return;
    let active = true;
    const load = async () => {
      const mine = await api<Me>("/api/me", { token });
      if (!active) return;
      setMe(mine);
      if (selected) {
        const ws = await api<Workspace>(
          `/api/private/workspace?workspace=${encodeURIComponent(selected)}`,
          { token },
        );
        if (active) setWorkspace(ws);
        return;
      }
      if (mine.business) {
        const ws = await api<Workspace>("/api/private/workspace", { token });
        if (active) setWorkspace(ws);
      }
    };
    load().catch((caught: unknown) => {
      if (!active) return;
      if (
        selected &&
        caught instanceof ApiError &&
        (caught.status === 404 || caught.status === 422)
      ) {
        setUnavailable(true);
        return;
      }
      setError(
        caught instanceof ApiError ? caught.describe() : "Could not load your workspace.",
      );
    });
    return () => {
      active = false;
    };
  }, [token, selected, unusable, reloadKey]);

  const shell = (inner: ReactNode) => (
    <main className="mx-auto flex w-full max-w-6xl flex-1 flex-col gap-6 px-4 py-8 sm:px-6 lg:px-8">
      {inner}
    </main>
  );

  if (!featuresConfigured) return shell(<ScaffoldNotice />);
  if (loading) return shell(<p className="text-ink-muted">Checking your session…</p>);
  if (!session)
    return shell(
      <>
        <Notice tone="info">Log in to open your private workspace.</Notice>
        <div>
          <Link href="/login" className={buttonClass}>
            Log in
          </Link>
        </div>
      </>,
    );
  if (unavailable || unusable)
    return shell(
      <>
        <Notice tone="error">
          That workspace is not available for this account. Check the link, or open your own
          workspace below.
        </Notice>
        <div>
          <Link href="/workspace" className={buttonClass}>
            Open my workspace
          </Link>
        </div>
      </>,
    );

  if (!workspace) {
    if (me && !me.business && onboarding)
      return shell(
        <>
          {error ? <Notice tone="error">{error}</Notice> : null}
          {onboarding(me, () => setReloadKey((v) => v + 1))}
        </>,
      );
    return shell(
      <>
        {error ? <Notice tone="error">{error}</Notice> : null}
        {!error ? <p className="text-ink-muted">Loading…</p> : null}
      </>,
    );
  }

  // Only businesses this account already holds, from /api/me. Each link carries that
  // business's own workspace id, so switching is a move between authorized workspaces and
  // never a guess at an id.
  const options: BusinessOption[] = (me?.businesses ?? [])
    .filter((row) => row.workspace_id)
    .map((row) => ({
      key: row.workspace_id as string,
      name: row.business.name,
      href: workspaceSections(row.workspace_id as string)[section],
    }));
  const currentKey = workspace.workspace_id;

  return shell(
    <>
      {error ? <Notice tone="error">{error}</Notice> : null}
      <WorkspaceNav
        section={section}
        mode="private"
        businessName={workspace.business.name}
        sections={workspaceSections(selected)}
        options={options}
        currentKey={currentKey}
        summary={`${workspace.account_count} account${workspace.account_count === 1 ? "" : "s"} · ${workspace.transaction_count} transaction${workspace.transaction_count === 1 ? "" : "s"} · ${workspace.statements.count} confirmed import${workspace.statements.count === 1 ? "" : "s"}`}
      />
      {children({ me, workspace, selected })}
    </>,
  );
}
