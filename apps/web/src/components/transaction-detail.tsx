"use client";

import { useEffect, useRef, useState } from "react";

import { ApiError } from "@/lib/api";
import { fetchTransactionDetail, formatDate, formatSigned, formatTimestamp, readable, sourceTypeLabel, type TransactionDetail as Detail } from "@/lib/workspace-api";
import { Notice, secondaryButtonClass } from "@/components/ui";

export function TransactionDetail({ workspaceId, transactionId, token, onClose }: { workspaceId: string; transactionId: string; token: string | null; onClose: () => void }) {
  const [detail, setDetail] = useState<Detail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    let active = true;
    fetchTransactionDetail(workspaceId, transactionId, token)
      .then((data) => {
        if (active) setDetail(data);
      })
      .catch((caught: unknown) => {
        if (active) setError(caught instanceof ApiError ? caught.message : "Could not load this transaction.");
      });
    return () => {
      active = false;
    };
  }, [workspaceId, transactionId, token]);

  useEffect(() => {
    heading.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const t = detail?.transaction;
  return (
    <aside role="dialog" aria-labelledby="transaction-detail-heading" className="rounded-2xl border border-line bg-white p-5 shadow-card">
      <div className="flex items-start justify-between gap-3">
        <h3 id="transaction-detail-heading" ref={heading} tabIndex={-1} className="t-section focus:outline-none">
          Transaction evidence
        </h3>
        <button type="button" onClick={onClose} className={secondaryButtonClass}>
          Close
        </button>
      </div>
      {error ? <Notice tone="error">{error}</Notice> : null}
      {!detail && !error ? <p className="mt-2 text-sm text-ink-muted">Loading…</p> : null}
      {detail && t ? (
        <div className="mt-3 space-y-4 text-sm">
          <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1">
            <dt className="text-ink-muted">Description</dt>
            <dd className="text-ink">{t.description}</dd>
            <dt className="text-ink-muted">Amount</dt>
            <dd className="text-ink">{formatSigned(t.amount_minor)} {t.currency}</dd>
            <dt className="text-ink-muted">Posted date</dt>
            <dd className="text-ink">{formatDate(t.posted_date)}</dd>
            <dt className="text-ink-muted">Account</dt>
            <dd className="text-ink">
              {t.account.name} {t.account.mask ? `···${t.account.mask}` : ""}
            </dd>
            <dt className="text-ink-muted">Status</dt>
            <dd className="text-ink">
              {t.status}
              {t.replaces_pending && detail.pending_transaction
                ? ` (replaced pending ${formatSigned(detail.pending_transaction.amount_minor)} authorized ${formatDate(detail.pending_transaction.posted_date)})`
                : ""}
            </dd>
            <dt className="text-ink-muted">Source</dt>
            <dd className="text-ink">
              {readable(sourceTypeLabel, t.source_type)} · revision {t.revision}
              {detail.external_id ? ` · provider id ${detail.external_id}` : ""}
            </dd>
          </dl>
          <div>
            <h4 className="font-medium text-ink">Provenance</h4>
            {detail.sources.length === 0 ? (
              <p className="text-ink-muted">No source events recorded.</p>
            ) : (
              <ul className="mt-1 space-y-1">
                {detail.sources.map((s, index) => (
                  <li key={index} className="rounded-xl border border-line-strong bg-lavender-50 px-3 py-2 text-ink">
                    {s.kind === "import_row" ? (
                      <>
                        Statement <span className="font-medium">{s.file_label}</span> ({s.format.toUpperCase()}), row {s.row_index + 1}
                        {s.page_number ? `, page ${s.page_number}` : ""} ({s.source_locator})
                        {s.imported_at ? ` · confirmed ${formatTimestamp(s.imported_at)}` : ""}
                        <span className="block text-xs text-ink-muted">
                          As extracted: {s.extracted.posted_date ?? "no date"} · {s.extracted.description ?? "no description"} ·{" "}
                          {typeof s.extracted.amount_minor === "number" ? formatSigned(s.extracted.amount_minor) : "no amount"}
                          {s.extracted.external_ref ? ` · ref ${s.extracted.external_ref}` : ""}
                          {s.reviewed ? " · corrected by owner during review" : ""}
                        </span>
                      </>
                    ) : (
                      <>
                        Provider event <span className="font-medium">{s.operation}</span> received {formatTimestamp(s.received_at)}
                        {s.batch_start_cursor ? ` · sync batch ${s.batch_start_cursor} → ${s.batch_end_cursor}` : ""}
                      </>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      ) : null}
    </aside>
  );
}
