"use client";

import { useSearchParams } from "next/navigation";
import { type FormEvent, Suspense, useCallback, useEffect, useState } from "react";

import { ApiError } from "@/lib/api";
import { track } from "@/lib/analytics";
import { defaultFilters, fetchTransactions, formatDate, formatSigned, type Account, type FilterInput, type TransactionPage, type TransactionRow } from "@/lib/workspace-api";
import { AccountBalances } from "@/components/account-balances";
import { TransactionDetail } from "@/components/transaction-detail";
import { buttonClass, Card, Field, inputClass, Notice, secondaryButtonClass } from "@/components/ui";

type Props = { workspaceId: string; token: string | null; emptyMessage: string };

const sameFilters = (a: FilterInput, b: FilterInput) =>
  a.accountId === b.accountId && a.from === b.from && a.to === b.to && a.status === b.status && a.q === b.q;

/**
 * Accounts with balances plus a filterable, keyset-paginated transaction table
 * and an evidence panel. Used unchanged by the demo and the private workspace.
 *
 * `?q=` seeds the description filter so a figure elsewhere can link to the rows behind it.
 * Reading it needs a boundary, so the suspense wrapper lives here rather than at each of the
 * two call sites.
 */
export function WorkspaceData(props: Props) {
  return (
    <Suspense
      fallback={
        <Card>
          <p className="text-ink-muted">Loading transactions…</p>
        </Card>
      }
    >
      <Inner {...props} />
    </Suspense>
  );
}

function Inner({ workspaceId, token, emptyMessage }: Props) {
  const search = useSearchParams();
  // A description to filter by, handed over from a figure that was computed from those rows.
  // It is only ever a starting point for the form the reader can then change.
  const linked = (search.get("q") ?? "").slice(0, 80);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [draft, setDraft] = useState<FilterInput>({ ...defaultFilters, q: linked });
  const [applied, setApplied] = useState<FilterInput>({ ...defaultFilters, q: linked });

  // The request key identifies the current query; loading is derived from whether
  // the resolved result belongs to it, so no state is set synchronously in effects.
  const requestKey = JSON.stringify({ workspaceId, authed: token !== null, applied });
  const [result, setResult] = useState<{ key: string; rows: TransactionRow[]; page: TransactionPage | null; error: string | null } | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);

  // Arriving at this section again on a different link, without having left it in between.
  // Adjusted during render rather than in an effect, so no request is made for the old filter
  // first and then thrown away.
  const [seenLink, setSeenLink] = useState(linked);
  if (linked !== seenLink) {
    setSeenLink(linked);
    setDraft({ ...defaultFilters, q: linked });
    setApplied({ ...defaultFilters, q: linked });
    setSelected(null);
  }
  const current = result && result.key === requestKey ? result : null;
  const rows = current?.rows ?? [];
  const page = current?.page ?? null;
  const error = current?.error ?? null;
  const loading = current === null || loadingMore;

  const onAccounts = useCallback((list: Account[]) => setAccounts(list), []);

  useEffect(() => {
    let active = true;
    const key = requestKey;
    fetchTransactions(workspaceId, applied, null, token)
      .then((data) => {
        if (active) setResult({ key, rows: data.items, page: data, error: null });
      })
      .catch((caught: unknown) => {
        if (active) setResult({ key, rows: [], page: null, error: caught instanceof ApiError ? caught.message : "Could not load transactions." });
      });
    return () => {
      active = false;
    };
  }, [workspaceId, token, applied, requestKey]);

  async function loadMore() {
    if (!current?.page?.next_cursor) return;
    setLoadingMore(true);
    try {
      const next = await fetchTransactions(workspaceId, applied, current.page.next_cursor, token);
      setResult({ key: requestKey, rows: [...current.rows, ...next.items], page: next, error: null });
    } catch (caught) {
      setResult({ ...current, error: caught instanceof ApiError ? caught.message : "Could not load more transactions." });
    } finally {
      setLoadingMore(false);
    }
  }

  function applyFilters(event: FormEvent) {
    event.preventDefault();
    setSelected(null);
    setApplied({ ...draft });
  }

  function resetFilters() {
    setDraft(defaultFilters);
    setApplied(defaultFilters);
    setSelected(null);
  }

  const close = useCallback(() => setSelected(null), []);

  return (
    <div className="flex flex-col gap-6">
      <AccountBalances workspaceId={workspaceId} token={token} onAccounts={onAccounts} />

      <Card className="min-w-0">
        <h2 className="t-section">Transactions</h2>
        {linked && applied.q === linked ? (
          <p className="mt-2 text-sm text-ink-muted">
            Filtered to descriptions containing{" "}
            <span className="font-medium text-ink">{linked}</span>, from a figure that was
            worked out from these rows. Reset to see everything.
          </p>
        ) : null}
        <form onSubmit={applyFilters} className="mt-3 grid gap-3 sm:grid-cols-5" aria-label="Transaction filters">
          <Field label="Account">
            <select value={draft.accountId} onChange={(e) => setDraft({ ...draft, accountId: e.target.value })} className={inputClass}>
              <option value="">All accounts</option>
              {accounts.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}
                  {a.mask ? ` ···${a.mask}` : ""}
                </option>
              ))}
            </select>
          </Field>
          <Field label="From">
            <input type="date" value={draft.from} onChange={(e) => setDraft({ ...draft, from: e.target.value })} className={inputClass} />
          </Field>
          <Field label="To">
            <input type="date" value={draft.to} onChange={(e) => setDraft({ ...draft, to: e.target.value })} className={inputClass} />
          </Field>
          <Field label="Status">
            <select value={draft.status} onChange={(e) => setDraft({ ...draft, status: e.target.value as FilterInput["status"] })} className={inputClass}>
              <option value="posted">Posted</option>
              <option value="pending">Pending</option>
              <option value="all">Posted and pending</option>
            </select>
          </Field>
          <Field label="Description contains">
            <input type="search" maxLength={80} value={draft.q} onChange={(e) => setDraft({ ...draft, q: e.target.value })} className={inputClass} />
          </Field>
          <div className="flex gap-2 sm:col-span-5">
            <button type="submit" className={buttonClass} disabled={loading}>
              Apply filters
            </button>
            <button type="button" onClick={resetFilters} className={secondaryButtonClass} disabled={loading}>
              Reset
            </button>
          </div>
        </form>

        {error ? (
          <div className="mt-4">
            <Notice tone="error">{error}</Notice>
          </div>
        ) : null}

        {page && rows.length === 0 && !loading ? <p className="mt-4 text-sm text-ink-muted">{page.total === 0 && sameFilters(applied, defaultFilters) ? emptyMessage : "No transactions match these filters."}</p> : null}

        {rows.length > 0 ? (
          <div className="mt-4 overflow-x-auto">
            <table className="w-full min-w-[640px] text-sm">
              <caption className="sr-only">Transactions ordered by posted date, newest first</caption>
              <thead>
                <tr className="border-b border-line text-left text-[11px] font-semibold uppercase tracking-wider text-ink-muted">
                  <th scope="col" className="py-2.5 pr-3">Date</th>
                  <th scope="col" className="py-2.5 pr-3">Description</th>
                  <th scope="col" className="py-2.5 pr-3">Account</th>
                  <th scope="col" className="py-2 pr-3 text-right">Amount</th>
                  <th scope="col" className="py-2.5 pr-3">Status</th>
                  <th scope="col" className="py-2">
                    <span className="sr-only">Evidence</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.id} className={`border-b border-line transition hover:bg-surface-soft ${selected === r.id ? "bg-lavender-50" : ""}`}>
                    <td className="t-num py-2.5 pr-3 whitespace-nowrap text-ink-muted">{formatDate(r.posted_date)}</td>
                    <td className="py-2.5 pr-3 font-medium text-ink">{r.description}</td>
                    <td className="py-2 pr-3 whitespace-nowrap text-ink-muted">
                      {r.account.name}
                      {r.account.mask ? ` ···${r.account.mask}` : ""}
                    </td>
                    <td className={`t-num py-2.5 pr-3 whitespace-nowrap text-right font-semibold ${r.amount_minor < 0 ? "text-ink" : "text-up"}`}>{formatSigned(r.amount_minor)}</td>
                    <td className="py-2 pr-3">
                      {r.status === "pending" ? <span className="rounded-full bg-amber-100 px-2 py-0.5 text-xs text-amber-900">pending</span> : <span className="text-xs text-ink-muted">posted{r.replaces_pending ? " · replaced pending" : ""}</span>}
                    </td>
                    <td className="py-2 text-right">
                      <button type="button" onClick={() => {
                        setSelected(r.id);
                        track("evidence_opened", { section: "transactions" });
                      }} className="rounded-lg px-2 py-1 text-xs font-medium text-plum-700 hover:bg-lavender-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-plum-600" aria-label={`Evidence for ${r.description} on ${formatDate(r.posted_date)}`}>
                        Evidence
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}

        <div className="mt-4 flex flex-wrap items-center gap-3 text-sm text-ink-muted">
          {loading ? <span>Loading…</span> : null}
          {page ? (
            <span>
              Showing {rows.length} of {page.total}
              {page.read_at ? ` · read ${page.read_at.slice(11, 16)} UTC` : ""}
            </span>
          ) : null}
          {page?.next_cursor ? (
            <button type="button" onClick={loadMore} disabled={loading} className={secondaryButtonClass}>
              Load more
            </button>
          ) : null}
        </div>
        <p className="mt-2 text-xs text-ink-muted">Pages read live data; entries that change between pages can shift. Removed provider records are never shown.</p>
      </Card>

      {selected ? <TransactionDetail workspaceId={workspaceId} transactionId={selected} token={token} onClose={close} /> : null}
    </div>
  );
}
