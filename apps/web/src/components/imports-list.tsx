"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { ApiError } from "@/lib/api";
import { listImports, statusLabel, type ImportSummary } from "@/lib/imports-api";
import { formatTimestamp } from "@/lib/workspace-api";

/** Recent statement imports for the private workspace, with a way back into an unfinished review. */
export function ImportsList({ workspaceId, token }: { workspaceId: string; token: string }) {
  const [imports, setImports] = useState<ImportSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    listImports(workspaceId, token)
      .then((list) => {
        if (active) setImports(list);
      })
      .catch((caught: unknown) => {
        if (active) setError(caught instanceof ApiError ? caught.message : "Could not load imports.");
      });
    return () => {
      active = false;
    };
  }, [workspaceId, token]);
  if (error) return <p className="text-sm text-amber-900">{error}</p>;
  if (!imports) return <p className="text-sm text-ink-muted">Loading imports…</p>;
  if (imports.length === 0) return null;
  return (
    <ul className="mt-3 divide-y divide-lavender-100 text-sm">
      {imports.map((i) => (
        <li key={i.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
          <span className="text-ink">
            {i.file_label} <span className="text-ink-muted">· {statusLabel[i.status]} · {formatTimestamp(i.created_at)}</span>
            {i.summary?.result ? <span className="text-ink-muted"> · {i.summary.result.created} added</span> : null}
          </span>
          <Link href={`/workspace/imports/${i.id}?workspace=${encodeURIComponent(workspaceId)}`} className="text-plum-600 hover:underline">
            {i.status === "staged" ? "Resume review" : "Details"}
          </Link>
        </li>
      ))}
    </ul>
  );
}
