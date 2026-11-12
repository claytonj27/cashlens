"use client";

import { useMemo, useState } from "react";

import type { ComparisonRow } from "@/lib/discovery-admin-api";
import { COMPARISON_FILTERS, type ComparisonFilter, matchesComparisonFilter, matchesSearch } from "@/lib/discovery-admin-rules";
import { AnswerCell, type DrawerTarget, FilterBar, LevelChip, SearchBox, StatusChip } from "@/components/discovery-admin/shared";

/**
 * The two-founder table: question, both answers, status. Used by the Founder comparison,
 * Business and Underwriting tabs. Filtering and search are client-side over the rows in hand.
 */
export function ComparisonTable({
  rows,
  labels,
  open,
  filters = COMPARISON_FILTERS,
  showFlags = true,
  assignments = {},
  initialQuery = "",
  initialFilter = null,
}: {
  rows: ComparisonRow[];
  labels: [string, string];
  open: (target: DrawerTarget) => void;
  filters?: { key: ComparisonFilter; label: string }[];
  showFlags?: boolean;
  assignments?: Record<string, string | null>;
  initialQuery?: string;
  initialFilter?: string | null;
}) {
  const [filter, setFilter] = useState<ComparisonFilter>((filters.find((f) => f.key === initialFilter)?.key ?? "all") as ComparisonFilter);
  const [query, setQuery] = useState(initialQuery);
  const shown = useMemo(() => rows.filter((r) => matchesComparisonFilter(r, filter) && matchesSearch(r.question, query)), [rows, filter, query]);
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-3">
        <SearchBox value={query} onChange={setQuery} />
        <span className="text-xs text-ink-muted">{shown.length} of {rows.length}</span>
      </div>
      <FilterBar value={filter} options={filters} onChange={setFilter} />
      <div className="overflow-x-auto rounded-xl border border-line">
        <table className="w-full min-w-[56rem] text-sm">
          <thead className="bg-lavender-50 text-left text-xs uppercase tracking-wide text-ink-muted">
            <tr>
              <th className="px-3 py-2">Question</th>
              <th className="px-3 py-2">{labels[0]}</th>
              <th className="px-3 py-2">{labels[1]}</th>
              <th className="px-3 py-2">Status</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((r) => (
              <tr
                key={r.question.id}
                className="cursor-pointer border-t border-line align-top hover:bg-lavender-50"
                onClick={() =>
                  open({
                    question: r.question,
                    answers: [
                      { label: labels[0], response: r.lead, assignment_id: assignments[labels[0]] ?? undefined },
                      { label: labels[1], response: r.partner, assignment_id: assignments[labels[1]] ?? undefined },
                    ],
                  })
                }
              >
                <td className="max-w-md px-3 py-2">
                  <div className="flex flex-col gap-1">
                    <span>{r.question.question_text}</span>
                    <span className="flex flex-wrap items-center gap-1 text-[11px] text-ink-faint">
                      <LevelChip level={r.question.level} core={r.question.required_core} />
                      <span>{r.question.topic}</span>
                      <span>· {r.question.section_slug}</span>
                      {showFlags && r.needs_reconciliation ? <StatusChip status="NEEDS_RECONCILIATION" /> : null}
                    </span>
                  </div>
                </td>
                <td className="max-w-xs px-3 py-2"><AnswerCell response={r.lead} /></td>
                <td className="max-w-xs px-3 py-2"><AnswerCell response={r.partner} /></td>
                <td className="px-3 py-2">
                  <div className="flex flex-col gap-1">
                    <StatusChip status={r.status} />
                    {r.detail ? <span className="text-[11px] text-ink-faint">{r.detail}</span> : null}
                  </div>
                </td>
              </tr>
            ))}
            {shown.length === 0 ? (
              <tr><td colSpan={4} className="px-3 py-6 text-center text-ink-muted">Nothing matches.</td></tr>
            ) : null}
          </tbody>
        </table>
      </div>
    </div>
  );
}
