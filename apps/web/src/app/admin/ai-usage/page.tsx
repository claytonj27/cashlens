"use client";

import { useCallback, useEffect, useState } from "react";

import { ApiError } from "@/lib/api";
import { useSession } from "@/lib/use-session";
import {
  dollars,
  duration,
  fetchRuns,
  fetchUsage,
  modeLabel,
  stamp,
  type Runs,
  type Usage,
  type WindowName,
} from "@/lib/admin-api";
import {
  Card,
  CardTitle,
  Chip,
  Disclosure,
  Notice,
  Stat,
  secondaryButtonClass,
} from "@/components/ui";

const WINDOWS: { key: WindowName; label: string }[] = [
  { key: "today", label: "Today" },
  { key: "7d", label: "Last 7 days" },
  { key: "all", label: "All time" },
];

/**
 * AI usage, for whoever runs this deployment.
 *
 * The server decides whether this page may be read; a 403 is rendered as a refusal rather than
 * as an error, because a signed-in person who is not an operator is not a fault. Nothing here
 * is a second source of truth: the cumulative figure is the same function the cap is enforced
 * with, and a metric the records do not carry says so rather than showing a zero.
 */
export default function AiUsagePage() {
  const { loading, session } = useSession();
  const token = session?.access_token ?? null;
  const [window, setWindow] = useState<WindowName>("7d");
  const [nonce, setNonce] = useState(0);
  // The key identifies the read on screen. Loading is derived from whether the result in hand
  // belongs to it, so nothing sets state synchronously inside the effect.
  const requestKey = `${window}|${nonce}|${token ? "signed-in" : "anonymous"}`;
  const [result, setResult] = useState<{
    key: string;
    usage: Usage | null;
    runs: Runs | null;
    error: string | null;
    refused: boolean;
  } | null>(null);
  const current = result && result.key === requestKey ? result : null;
  const usage = current?.usage ?? null;
  const runs = current?.runs ?? null;
  const error = current?.error ?? null;
  const refused = current?.refused ?? false;
  const busy = current === null;

  const refresh = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    if (loading || !token) return;
    let live = true;
    const key = requestKey;
    Promise.all([fetchUsage(window, token), fetchRuns(window, token)])
      .then(([u, r]) => {
        if (live) setResult({ key, usage: u, runs: r, error: null, refused: false });
      })
      .catch((caught: unknown) => {
        if (!live) return;
        const denied = caught instanceof ApiError && caught.status === 403;
        setResult({
          key,
          usage: null,
          runs: null,
          refused: denied,
          error: denied
            ? null
            : caught instanceof ApiError
              ? caught.describe()
              : "Could not read usage.",
        });
      });
    return () => {
      live = false;
    };
  }, [requestKey, window, token, loading]);

  const shell = (inner: React.ReactNode) => (
    <main className="mx-auto flex w-full max-w-6xl flex-1 flex-col gap-6 px-4 py-8 sm:px-6 lg:px-8">
      {inner}
    </main>
  );

  if (loading) return shell(<p className="text-ink-muted">Checking your session…</p>);
  if (!session) return shell(<Notice tone="info">Log in to read this page.</Notice>);
  if (refused)
    return shell(
      <Notice tone="error">
        This account is not an operator of this deployment. Nothing on this page is available to
        it.
      </Notice>,
    );

  const b = usage?.budget;

  return shell(
    <>
      <div className="flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
        <div>
          <p className="t-eyebrow mb-1.5">Operations</p>
          <h1 className="t-page">AI usage</h1>
          <p className="mt-1.5 max-w-prose text-sm text-ink-muted">
            Spend and activity across every business on this deployment. Accounting only: no
            question, answer or transaction is readable here.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div role="group" aria-label="Activity window" className="flex rounded-xl border border-line bg-surface p-0.5">
            {WINDOWS.map((w) => (
              <button
                key={w.key}
                type="button"
                aria-pressed={window === w.key}
                onClick={() => setWindow(w.key)}
                className={`rounded-lg px-3 py-1.5 text-sm font-medium transition focus:outline-none focus-visible:ring-2 focus-visible:ring-plum-600 ${
                  window === w.key ? "bg-plum-600 text-white" : "text-ink-soft hover:bg-lavender-100"
                }`}
              >
                {w.label}
              </button>
            ))}
          </div>
          <button type="button" onClick={refresh} disabled={busy} className={secondaryButtonClass}>
            {busy ? "Reading…" : "Refresh"}
          </button>
        </div>
      </div>

      {error ? <Notice tone="error">{error}</Notice> : null}
      {!usage && !error ? <p className="text-ink-muted">Reading the ledger…</p> : null}

      {usage && b ? (
        <>
          {/* ---------------------------------------------------------- budget -- */}
          <Card>
            <CardTitle
              icon="wallet"
              title="Budget"
              hint="From the same ledger the database enforces the caps against."
            />
            <dl className="mt-4 grid gap-5 sm:grid-cols-2 lg:grid-cols-4">
              <Stat
                label="Cumulative spend"
                value={dollars(b.total_micro)}
                note={`of ${dollars(b.milestone_cap_micro)} milestone cap`}
              />
              <Stat
                label="Remaining"
                value={dollars(b.remaining_micro)}
                tone={b.remaining_micro > 0 ? "up" : "down"}
                note="no time window; counts everything ever held"
              />
              <Stat
                label="Rolling 24 hours"
                value={dollars(b.rolling_24h_micro)}
                note={`against ${dollars(b.rolling_24h_global_cap_micro)} deployment cap`}
              />
              <Stat
                label="Per identity, 24 hours"
                value={dollars(b.rolling_24h_identity_cap_micro)}
                note="the configured cap, not a total"
              />
            </dl>
            <p className="mt-4 text-xs text-ink-faint">
              The rolling 24-hour figure is a moving window, not today. It is deliberately not the
              same thing as the Today filter above, which is a calendar day.
              {b.rolling_24h_truncated
                ? " More holds exist in that window than this page reads, so the figure is a floor."
                : ""}
            </p>

            <div className="mt-5 grid gap-5 border-t border-line pt-5 sm:grid-cols-3">
              <Stat
                label="Settled"
                value={dollars(b.settled_micro)}
                note="what calls actually cost"
              />
              <Stat
                label="Held, not settled"
                value={dollars(b.held_micro)}
                note={`${b.active_reservations} active reservation${b.active_reservations === 1 ? "" : "s"}`}
              />
              <Stat
                label="Uncertain holds"
                value={String(b.uncertain_holds)}
                tone={b.uncertain_holds > 0 ? "down" : "default"}
                note="a timed-out call keeps its estimate"
              />
            </div>

            {b.external_probe_micro > 0 && !b.external_probe_in_ledger ? (
              <div className="mt-5 rounded-xl border border-line bg-surface-soft p-3">
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <p className="text-sm font-semibold text-ink">Spend outside this ledger</p>
                  <span className="t-num text-sm font-semibold text-ink">
                    {dollars(b.external_probe_micro, { precise: true })}
                  </span>
                </div>
                <p className="mt-1 text-xs text-ink-muted">
                  A direct provider probe that never passed through a reservation. The database
                  caps do not bound it and cannot see it. Counted once in the overall figure
                  below and nowhere else.
                </p>
                <p className="mt-2 text-sm text-ink">
                  Overall, including it:{" "}
                  <span className="t-num font-semibold">
                    {dollars(b.overall_total_micro, { precise: true })}
                  </span>
                </p>
              </div>
            ) : null}
          </Card>

          {/* -------------------------------------------------------- activity -- */}
          <div className="grid grid-cols-[minmax(0,1fr)] items-start gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
            <Card>
              <CardTitle icon="spark" title="Requests by capability" hint={windowNote(usage)} />
              {usage.by_capability.length === 0 ? (
                <p className="mt-4 text-sm text-ink-muted">No AI requests in this window.</p>
              ) : (
                <ul className="mt-4 divide-y divide-line">
                  {usage.by_capability.map((row) => (
                    <li key={row.key} className="flex items-baseline justify-between gap-3 py-2">
                      <span className="text-sm text-ink">{row.label}</span>
                      <span className="t-num text-sm font-semibold text-ink">{row.count}</span>
                    </li>
                  ))}
                </ul>
              )}
              <div className="mt-4 border-t border-line pt-4">
                <dl className="grid grid-cols-2 gap-4">
                  <Stat
                    label="Average latency"
                    value={duration(usage.average_latency_ms)}
                    note={
                      usage.latency_sample > 0
                        ? `over ${usage.latency_sample} run${usage.latency_sample === 1 ? "" : "s"} that recorded timing`
                        : "no run in this window recorded a timing"
                    }
                  />
                  <Stat label="Most recent activity" value={stamp(usage.last_activity_at)} />
                </dl>
              </div>
            </Card>

            <Card>
              <CardTitle icon="review" title="How requests ended" hint={windowNote(usage)} />
              {usage.by_status.length === 0 ? (
                <p className="mt-4 text-sm text-ink-muted">No AI requests in this window.</p>
              ) : (
                <ul className="mt-4 divide-y divide-line">
                  {usage.by_status.map((row) => (
                    <li key={row.key} className="flex items-baseline justify-between gap-3 py-2">
                      <span className="text-sm text-ink">{row.label}</span>
                      <span
                        className={`t-num text-sm font-semibold ${row.key === "succeeded" ? "text-up" : row.key === "invalid_output" || row.key === "provider_error" || row.key === "timeout" ? "text-down" : "text-ink"}`}
                      >
                        {row.count}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
              <p className="mt-4 border-t border-line pt-4 text-xs text-ink-muted">
                {usage.claims_withheld_recorded ? (
                  <>
                    <span className="font-semibold text-ink">{usage.claims_withheld}</span> claim
                    {usage.claims_withheld === 1 ? " was" : "s were"} withheld from otherwise
                    successful answers in this window. A partly withheld answer is a success that
                    said less, not a failure.
                  </>
                ) : (
                  "Withheld-claim counts are not recorded for the runs in this window."
                )}
              </p>
            </Card>
          </div>

          <Card>
            <CardTitle
              icon="layers"
              title="Provider and model"
              hint="Live, replayed and simulated activity counted apart, from the run's own mode."
            />
            {usage.by_provider.length === 0 ? (
              <p className="mt-4 text-sm text-ink-muted">No AI requests in this window.</p>
            ) : (
              <div className="mt-4 overflow-x-auto">
                <table className="w-full text-left text-[13px]">
                  <thead>
                    <tr className="border-b border-line text-[10px] font-semibold uppercase tracking-wider text-ink-muted">
                      <th scope="col" className="py-2 pr-3">Provider</th>
                      <th scope="col" className="py-2 pr-3">Model</th>
                      <th scope="col" className="py-2 pr-3">Mode</th>
                      <th scope="col" className="py-2 pr-3 text-right">Requests</th>
                      <th scope="col" className="py-2 pr-3 text-right">Cost</th>
                      <th scope="col" className="py-2 text-right">Avg latency</th>
                    </tr>
                  </thead>
                  <tbody>
                    {usage.by_provider.map((row) => (
                      <tr key={`${row.provider}-${row.model}-${row.mode}`} className="border-b border-line last:border-0">
                        <th scope="row" className="py-2 pr-3 font-medium text-ink">{row.provider}</th>
                        <td className="py-2 pr-3 text-ink-muted">{row.model || "Not recorded"}</td>
                        <td className="py-2 pr-3">
                          <Chip tone={row.mode === "live" ? "up" : "muted"}>{modeLabel(row.mode)}</Chip>
                        </td>
                        <td className="t-num py-2 pr-3 text-right text-ink">{row.count}</td>
                        <td className="t-num py-2 pr-3 text-right text-ink">{dollars(row.cost_micro)}</td>
                        <td className="t-num py-2 text-right text-ink-muted">
                          {duration(row.average_latency_ms)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <p className="mt-3 text-xs text-ink-faint">
              All time on this deployment: {usage.live_runs} live run
              {usage.live_runs === 1 ? "" : "s"}, {usage.non_live_runs} simulated or replayed.
            </p>
          </Card>

          {/* ------------------------------------------------------ recent runs -- */}
          <Card>
            <CardTitle icon="clock" title="Recent runs" hint={windowNote(usage)} />
            {!runs || runs.items.length === 0 ? (
              <p className="mt-4 text-sm text-ink-muted">Nothing in this window.</p>
            ) : (
              <div className="mt-4 overflow-x-auto">
                <table className="w-full text-left text-[13px]">
                  <thead>
                    <tr className="border-b border-line text-[10px] font-semibold uppercase tracking-wider text-ink-muted">
                      <th scope="col" className="py-2 pr-3">When</th>
                      <th scope="col" className="py-2 pr-3">Capability</th>
                      <th scope="col" className="py-2 pr-3">Provider and model</th>
                      <th scope="col" className="py-2 pr-3">Status</th>
                      <th scope="col" className="py-2 pr-3 text-right">Duration</th>
                      <th scope="col" className="py-2 pr-3 text-right">Cost</th>
                      <th scope="col" className="py-2 text-right">Rounds</th>
                    </tr>
                  </thead>
                  <tbody>
                    {runs.items.map((run) => (
                      <tr key={run.id} className="border-b border-line last:border-0">
                        <td className="t-num py-2 pr-3 whitespace-nowrap text-ink-muted">
                          {stamp(run.created_at)}
                        </td>
                        <td className="py-2 pr-3 text-ink">{run.capability}</td>
                        <td className="py-2 pr-3 text-ink-muted">
                          {run.provider}
                          {run.model ? ` · ${run.model}` : ""}
                          {run.mode !== "live" ? (
                            <span className="ml-1.5 align-middle">
                              <Chip>{modeLabel(run.mode)}</Chip>
                            </span>
                          ) : null}
                        </td>
                        <td className="py-2 pr-3">
                          <span
                            className={
                              run.status === "succeeded" ? "text-up" : "text-down"
                            }
                          >
                            {run.status}
                          </span>
                          {run.error_code && run.error_code !== run.status ? (
                            <span className="text-ink-faint"> · {run.error_code}</span>
                          ) : null}
                        </td>
                        <td className="t-num py-2 pr-3 text-right text-ink-muted">
                          {duration(run.latency_ms)}
                        </td>
                        <td className="t-num py-2 pr-3 text-right whitespace-nowrap text-ink">
                          {dollars(run.cost_micro)}
                          {run.cost_is_estimate ? (
                            <span className="block text-[10px] font-normal text-warn">
                              retained estimate
                            </span>
                          ) : null}
                        </td>
                        <td className="t-num py-2 text-right text-ink-muted">
                          {run.rounds ?? run.attempts ?? "Not recorded"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {runs?.truncated ? (
              <p className="mt-3 text-xs text-ink-muted">
                Showing the most recent {runs.items.length}. Older runs are not loaded.
              </p>
            ) : null}
          </Card>

          <Disclosure label="Where these figures come from" openLabel="Hide where these come from">
            <p>
              Cumulative spend, what is settled and what is held all come from
              `ai_milestone_spend()`, the function the cumulative cap is enforced with, so this
              page cannot disagree with the budget the database applies. The rolling 24-hour
              figure is counted the same way the reservation function counts it: a settled hold
              costs what it cost, an unsettled hold that has not expired costs its estimate, and
              an expired unsettled hold counts nothing. Nothing here releases, alters or deletes
              a reservation.
            </p>
            <p className="mt-2">
              Activity counts come from the run records, one row per generation attempt, read
              with a bound of 1,000 rows per window. A figure the records do not carry is shown
              as &quot;Not recorded&quot; rather than as zero.
              {usage.runs_truncated
                ? " This window hit the read bound, so its counts are a floor."
                : ""}
            </p>
            <p className="mt-2">
              Read at {stamp(usage.generated_at)}
              {usage.window_start ? ` · window from ${stamp(usage.window_start)}` : " · all time"}.
            </p>
          </Disclosure>
        </>
      ) : null}
    </>,
  );
}

function windowNote(usage: Usage): string {
  const count = usage.runs_in_window;
  const label =
    usage.window === "today" ? "today" : usage.window === "7d" ? "in the last 7 days" : "all time";
  return `${count} run${count === 1 ? "" : "s"} ${label}.`;
}
