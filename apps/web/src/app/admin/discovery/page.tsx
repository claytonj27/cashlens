"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { type ExportName, downloadExport, fetchDiscoveryAdminSession } from "@/lib/discovery-admin-api";
import { TABS, type TabKey, hrefFor, tabFromParam } from "@/lib/discovery-admin-rules";
import { useSession } from "@/lib/use-session";
import { ResponsesTab } from "@/components/discovery-admin/responses-tab";
import { Drawer, useDrawer } from "@/components/discovery-admin/shared";
import {
  BusinessTab,
  CalibrationTab,
  ComparisonTab,
  EngagementTab,
  EngineeringTab,
  OpenQuestionsTab,
  OverviewTab,
  ScenariosTab,
  UnderwritingTab,
} from "@/components/discovery-admin/tabs";
import { useAdminData } from "@/components/discovery-admin/use-admin-data";
import { Notice, PageHeader, buttonClass, secondaryButtonClass } from "@/components/ui";

/**
 * The Discovery admin dashboard: what the respondents actually said, read-only.
 *
 * Respondent → questions → answers → analysis. The server decides whether this page may be
 * read (the admin profile role, checked on every request); the page asks `/session` only to
 * render a refusal instead of an error. Every tab is one read of one analysis route, and every
 * number on it is a deterministic result of `app.discovery.analysis`; nothing here is
 * generated, inferred or editable.
 */
export default function DiscoveryAdminPage() {
  return (
    <Suspense fallback={null}>
      <Dashboard />
    </Suspense>
  );
}

function Dashboard() {
  const { loading, session } = useSession();
  const token = session?.access_token ?? null;
  const params = useSearchParams();
  const tab: TabKey = tabFromParam(params.get("tab"));
  const gate = useAdminData(fetchDiscoveryAdminSession, token);
  const drawer = useDrawer();
  const [exporting, setExporting] = useState<ExportName | null>(null);
  const [exportError, setExportError] = useState<string | null>(null);

  async function exportFile(name: ExportName) {
    setExporting(name);
    setExportError(null);
    try {
      await downloadExport(name, token);
    } catch (caught) {
      setExportError(caught instanceof Error ? caught.message : "Export failed.");
    } finally {
      setExporting(null);
    }
  }

  if (loading) return <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-8"><p className="text-ink-muted">Checking your session…</p></main>;
  if (!session || !token)
    return (
      <main className="mx-auto flex w-full max-w-6xl flex-1 flex-col gap-3 px-4 py-8">
        <Notice tone="info">Log in to open the Discovery admin.</Notice>
        <div><Link href="/login" className={buttonClass}>Log in</Link></div>
      </main>
    );
  if (gate.data && !gate.data.is_discovery_admin)
    return (
      <main className="mx-auto flex w-full max-w-6xl flex-1 flex-col gap-3 px-4 py-8">
        <Notice tone="error">This account is not a Discovery admin.</Notice>
        <div><Link href="/workspace" className={secondaryButtonClass}>Open CashLens</Link></div>
      </main>
    );

  return (
    <main className="mx-auto flex w-full max-w-6xl flex-1 flex-col gap-5 px-4 py-8 sm:px-6 lg:px-8">
      <PageHeader eyebrow="Discovery · admin" title="What did they actually tell me?" lead="Every answer from Lead, Partner and Engineer, compared deterministically. Read-only." />
      <div className="flex flex-wrap items-center justify-between gap-2">
        <nav className="flex flex-wrap gap-1" aria-label="Views">
          {TABS.map((t) => (
            <Link key={t.key} href={hrefFor(t.key)} aria-current={t.key === tab ? "page" : undefined} className={`rounded-lg px-2.5 py-1.5 text-sm font-semibold transition ${t.key === tab ? "bg-plum-600 text-white" : "text-ink-soft hover:bg-lavender-100 hover:text-ink"}`}>
              {t.label}
            </Link>
          ))}
        </nav>
        <div className="flex flex-wrap items-center gap-1 text-xs">
          <span className="text-ink-muted">Export:</span>
          {(["founder-comparison", "calibration", "scenarios", "progress", "dataset"] as ExportName[]).map((name) => (
            <button key={name} type="button" disabled={exporting !== null} onClick={() => exportFile(name)} className="rounded-md border border-line-strong px-2 py-1 font-semibold text-ink-soft hover:border-plum-400 hover:text-plum-700 disabled:opacity-50">
              {exporting === name ? "…" : name === "dataset" ? "JSON dataset" : `${name}.csv`}
            </button>
          ))}
        </div>
      </div>
      {exportError ? <Notice tone="error">{exportError}</Notice> : null}
      {tab === "overview" ? <OverviewTab token={token} open={drawer.open} /> : null}
      {tab === "responses" ? <ResponsesTab token={token} open={drawer.open} /> : null}
      {tab === "comparison" ? <ComparisonTab token={token} open={drawer.open} /> : null}
      {tab === "business" ? <BusinessTab token={token} open={drawer.open} /> : null}
      {tab === "underwriting" ? <UnderwritingTab token={token} open={drawer.open} /> : null}
      {tab === "calibration" ? <CalibrationTab token={token} open={drawer.open} /> : null}
      {tab === "scenarios" ? <ScenariosTab token={token} open={drawer.open} /> : null}
      {tab === "engineering" ? <EngineeringTab token={token} open={drawer.open} /> : null}
      {tab === "engagement" ? <EngagementTab token={token} open={drawer.open} /> : null}
      {tab === "open" ? <OpenQuestionsTab token={token} open={drawer.open} /> : null}
      <Drawer target={drawer.target} onClose={drawer.close} token={token} />
    </main>
  );
}
