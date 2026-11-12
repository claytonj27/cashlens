"use client";

import Link from "next/link";
import { useEffect, useState, type ReactNode } from "react";

import { api, ApiError } from "@/lib/api";
import { demoAvailable } from "@/lib/features";
import { ScaffoldNotice } from "@/components/scaffold-notice";
import { WorkspaceNav, type BusinessOption, type Section } from "@/components/workspace-nav";
import { Notice } from "@/components/ui";
import { isOfferedDemo } from "@/lib/demo-catalogue";

export type DemoSummary = {
  workspace_id: string;
  business: { slug: string; name: string; industry: string };
  accounts: {
    name: string;
    account_type: string;
    mask: string | null;
    current_balance_minor: number | null;
    balance_as_of: string | null;
  }[];
  posted_transaction_count: number;
  note: string;
};

type Business = { slug: string; name: string; industry: string };

export function demoSections(slug: string): Record<Section, string> {
  return {
    overview: `/demo/${slug}`,
    analysis: `/demo/${slug}/analysis`,
    transactions: `/demo/${slug}/transactions`,
    statements: `/demo/${slug}/statements`,
  };
}

/**
 * Everything a demo section needs before it can render: the workspace, the other demo
 * businesses for the selector, and the header that says which one this is.
 *
 * The slug in the URL is the selection. Switching business goes to the same section of that
 * business, so a reader never loses their place — and never lands on a different business's
 * data while the heading still says the one they chose.
 */
export function DemoShell({
  slug,
  section,
  children,
}: {
  slug: string;
  section: Section;
  children: (summary: DemoSummary) => ReactNode;
}) {
  const [summary, setSummary] = useState<DemoSummary | null>(null);
  const [businesses, setBusinesses] = useState<Business[]>([]);
  const [error, setError] = useState<{ status: number; message: string } | null>(null);

  useEffect(() => {
    if (!demoAvailable) return;
    let active = true;
    api<DemoSummary>(`/api/demo/workspaces/${slug}`)
      .then((data) => {
        if (active) setSummary(data);
      })
      .catch((caught: unknown) => {
        if (!active) return;
        setError(
          caught instanceof ApiError
            ? { status: caught.status, message: caught.message }
            : { status: 0, message: "unavailable" },
        );
      });
    api<Business[]>("/api/demo/businesses")
      .then((rows) => {
        if (active) setBusinesses(rows);
      })
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, [slug]);

  if (!demoAvailable) {
    return (
      <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-10 sm:px-6 lg:px-8">
        <ScaffoldNotice />
      </main>
    );
  }

  const options: BusinessOption[] = businesses.filter((b) => isOfferedDemo(b.slug)).map((b) => ({
    key: b.slug,
    name: b.name,
    href: demoSections(b.slug)[section],
  }));

  return (
    <main className="mx-auto flex w-full max-w-6xl flex-1 flex-col gap-6 px-4 py-8 sm:px-6 lg:px-8">
      <Link
        href="/demo"
        className="text-sm text-plum-700 underline underline-offset-2 hover:text-plum-600"
      >
        ← All demo businesses
      </Link>

      {error ? (
        <Notice tone="error">
          {error.status === 401 || error.status === 404
            ? "No demo session for this business in this browser yet. Go back and open it from the demo list."
            : `Could not load this workspace (${error.message}).`}
        </Notice>
      ) : null}
      {!summary && !error ? <p className="text-ink-muted">Loading workspace…</p> : null}

      {summary ? (
        <>
          <WorkspaceNav
            section={section}
            mode="demo"
            businessName={summary.business.name}
            sections={demoSections(summary.business.slug)}
            options={options}
            currentKey={summary.business.slug}
            summary={`${summary.business.industry} · ${summary.posted_transaction_count} transactions · ${summary.accounts.length} account${summary.accounts.length === 1 ? "" : "s"}`}
          />
          {children(summary)}
        </>
      ) : null}
    </main>
  );
}
