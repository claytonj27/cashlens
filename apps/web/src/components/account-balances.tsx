"use client";

import { useEffect, useState } from "react";

import { ApiError } from "@/lib/api";
import { accountTypeLabel, balanceKindLabel, fetchAccounts, formatTimestamp, provenanceLabel, readable, type Account } from "@/lib/workspace-api";
import { formatMinor } from "@/lib/api";
import { Card, Notice } from "@/components/ui";

export function AccountBalances({ workspaceId, token, onAccounts }: { workspaceId: string; token: string | null; onAccounts?: (accounts: Account[]) => void }) {
  const [accounts, setAccounts] = useState<Account[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    fetchAccounts(workspaceId, token)
      .then((data) => {
        if (!active) return;
        setAccounts(data);
        onAccounts?.(data);
      })
      .catch((caught: unknown) => {
        if (active) setError(caught instanceof ApiError ? caught.message : "Could not load accounts.");
      });
    return () => {
      active = false;
    };
    // onAccounts is a stable callback from the parent; re-fetching on identity change is unnecessary.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceId, token]);

  return (
    <Card>
      <h2 className="t-section">Accounts</h2>
      {error ? <Notice tone="error">{error}</Notice> : null}
      {accounts === null && !error ? <p className="mt-2 text-sm text-ink-muted">Loading accounts…</p> : null}
      {accounts && accounts.length === 0 ? <p className="mt-2 text-sm text-ink-muted">No accounts yet.</p> : null}
      {accounts && accounts.length > 0 ? (
        <ul className="mt-3 grid gap-3 sm:grid-cols-2">
          {accounts.map((a) => (
            <li key={a.id} className="rounded-xl border border-line-strong bg-lavender-50 p-3">
              <p className="text-sm font-medium text-ink">
                {a.name} {a.mask ? `···${a.mask}` : ""} <span className="font-normal text-ink-muted">· {readable(accountTypeLabel, a.account_type)}</span>
              </p>
              {a.balances.length === 0 ? (
                <p className="mt-1 text-sm text-ink-muted">No balance snapshot recorded.</p>
              ) : (
                <dl className="mt-1 space-y-1">
                  {a.balances.map((b) => (
                    <div key={`${b.kind}-${b.provenance}`} className="flex flex-wrap items-baseline justify-between gap-x-3">
                      <dt className="text-xs text-ink-muted">
                        {readable(balanceKindLabel, b.kind)} · {readable(provenanceLabel, b.provenance)} · as of {formatTimestamp(b.as_of)}
                      </dt>
                      <dd className="text-base font-semibold text-ink">{formatMinor(b.amount_minor)}</dd>
                    </div>
                  ))}
                </dl>
              )}
            </li>
          ))}
        </ul>
      ) : null}
    </Card>
  );
}
