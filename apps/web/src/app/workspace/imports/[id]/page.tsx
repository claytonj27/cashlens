"use client";

import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";

import { ApiError } from "@/lib/api";
import { featuresConfigured } from "@/lib/features";
import { fetchImportLocation, type ImportLocation } from "@/lib/imports-api";
import { useSession } from "@/lib/use-session";
import { ImportReview } from "@/components/import-review";
import { ScaffoldNotice } from "@/components/scaffold-notice";
import { buttonClass, Notice } from "@/components/ui";

export default function ImportReviewPage() {
  const params = useParams<{ id: string }>();
  const search = useSearchParams();
  const { loading, session } = useSession();
  const token = session?.access_token ?? null;
  // The workspace comes from the import record itself, never from the link, so a
  // query parameter cannot move an import or point its confirmation elsewhere.
  const [location, setLocation] = useState<ImportLocation | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!featuresConfigured || !token) return;
    let active = true;
    fetchImportLocation(params.id, token)
      .then((found) => {
        if (!active) return;
        setLocation(found);
        setError(null);
      })
      .catch((caught: unknown) => {
        if (!active) return;
        if (caught instanceof ApiError && caught.status === 404) {
          setUnavailable(true);
          return;
        }
        setError(caught instanceof ApiError ? caught.describe() : "Could not load this import.");
      });
    return () => {
      active = false;
    };
  }, [params.id, token, attempt]);

  if (!featuresConfigured) {
    return (
      <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-10 sm:px-6">
        <ScaffoldNotice />
      </main>
    );
  }
  if (loading) return <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-10 sm:px-6 text-ink-muted">Checking your session…</main>;
  if (!session || !token) {
    return (
      <main className="mx-auto flex w-full max-w-6xl flex-1 flex-col gap-4 px-4 py-10 sm:px-6">
        <Notice tone="info">Log in to review this import.</Notice>
        <Link href="/login" className={buttonClass}>
          Log in
        </Link>
      </main>
    );
  }
  if (unavailable) {
    return (
      <main className="mx-auto flex w-full max-w-6xl flex-1 flex-col gap-4 px-4 py-10 sm:px-6">
        <Notice tone="error">This import is not available for this account. Check the link, or open your workspace below.</Notice>
        <div>
          <Link href="/workspace" className={buttonClass}>
            Open my workspace
          </Link>
        </div>
      </main>
    );
  }

  // Back to the section this review belongs to, still naming the same workspace.
  const backHref = location
    ? `/workspace/statements?workspace=${encodeURIComponent(location.workspace_id)}`
    : "/workspace/statements";
  return (
    <main className="mx-auto flex w-full max-w-6xl flex-1 flex-col gap-6 px-4 py-10 sm:px-6 sm:px-8">
      <Link href={backHref} className="text-sm text-plum-600 hover:underline">
        ← My workspace
      </Link>
      {error ? (
        <div className="flex flex-col gap-2">
          <Notice tone="error">Could not load this import: {error}. Nothing was changed.</Notice>
          <div>
            <button type="button" onClick={() => setAttempt((n) => n + 1)} className={buttonClass}>
              Try again
            </button>
          </div>
        </div>
      ) : null}
      {search.get("duplicate") ? <Notice tone="info">That file was already imported into this account, so the existing import is shown instead. Nothing was added twice.</Notice> : null}
      {location ? (
        <ImportReview
          workspaceId={location.workspace_id}
          importId={params.id}
          token={token}
          businessName={location.business.name}
          backHref={backHref}
        />
      ) : !error ? (
        <p className="text-ink-muted">Loading…</p>
      ) : null}
    </main>
  );
}
