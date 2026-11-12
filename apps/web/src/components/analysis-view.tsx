"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { ApiError } from "@/lib/api";
import {
  dayLabel,
  fetchAnalysis,
  fetchCategoryRules,
  money,
  monthLabel,
  type CashAnalysis,
  type CategoryRow,
  type MonthRow,
  type RecurringRow,
} from "@/lib/analysis-api";
import { track } from "@/lib/analytics";
import { TransactionDetail } from "@/components/transaction-detail";
import {
  Card,
  CardTitle,
  Chip,
  Disclosure,
  Empty,
  Icon,
  Notice,
  quietButtonClass,
  secondaryButtonClass,
} from "@/components/ui";

type Props = {
  workspaceId: string;
  token: string | null;
  /** The Transactions section, so every figure here can be traced to the rows behind it. */
  transactionsHref: string;
  /** The Overview section, for the projection and the assistant. */
  overviewHref: string;
};

const GROUP_LABEL: Record<string, string> = {
  operating: "Running the business",
  financing: "Loans and owner money",
  internal_transfer: "Between your own accounts",
};

const GROUP_ORDER = ["operating", "financing", "internal_transfer"];

/** Money moved by a category: what came in, less what went out. Never one side alone. */
const netOf = (row: CategoryRow) => row.inflow_minor - row.outflow_minor;

/**
 * Analysis: the history behind the Overview.
 *
 * Overview answers "where do I stand"; this section answers "what has been happening". The
 * four displays are paired the way a reader compares them — the monthly shape beside what
 * changed, the category split beside what repeats — rather than stacked full width.
 */
export function AnalysisView({ workspaceId, token, transactionsHref, overviewHref }: Props) {
  const [data, setData] = useState<CashAnalysis | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    fetchAnalysis(workspaceId, token)
      .then((fresh) => {
        if (active) setData(fresh);
      })
      .catch((caught: unknown) => {
        if (active)
          setError(
            caught instanceof ApiError ? caught.describe() : "Could not read your figures.",
          );
      });
    return () => {
      active = false;
    };
  }, [workspaceId, token]);

  // One event when the section has something to show, not on every render.
  const ready = Boolean(data);
  useEffect(() => {
    if (ready) track("analysis_viewed", { section: "analysis" });
  }, [ready]);

  if (error) return <Notice tone="error">{error}</Notice>;
  if (!data)
    return (
      <Card>
        <p className="text-ink-muted">Reading your history…</p>
      </Card>
    );
  if (data.coverage.posted_count === 0)
    return (
      <Card>
        <CardTitle title="Analysis" />
        <div className="mt-4">
          <Empty title="Nothing to analyse yet">
            Import a statement and the months, categories and repeating payments on this page
            fill in automatically.
          </Empty>
        </div>
      </Card>
    );

  const months = data.months.slice(-12);
  const comparison = data.comparison;

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="t-section">Your history</h2>
        <p className="text-xs text-ink-muted">
          Figures through {dayLabel(data.as_of)}
          {data.data_through && data.data_through !== data.as_of
            ? ` · newest entry ${dayLabel(data.data_through)}`
            : ""}
        </p>
      </div>

      {/* Two balanced columns on desktop. Each pair is two things a reader holds side by side;
          on a narrow screen they stack in the same order. */}
      <div className="grid grid-cols-[minmax(0,1fr)] items-start gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <MonthlyHistory months={months} data={data} />
        {comparison && comparison.month_totals && comparison.previous_totals ? (
          <MonthComparison data={data} />
        ) : (
          <Card>
            <CardTitle icon="swap" title="What changed" />
            <div className="mt-4">
              <Empty title="Not two months yet" icon="clock">
                A month needs one before it to be compared with. This appears once there is a
                second month of history.
              </Empty>
            </div>
          </Card>
        )}
      </div>

      <div className="grid grid-cols-[minmax(0,1fr)] items-start gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <CategoryBreakdown
          data={data}
          workspaceId={workspaceId}
          token={token}
          transactionsHref={transactionsHref}
        />
        <RecurringActivity
          data={data}
          workspaceId={workspaceId}
          token={token}
          transactionsHref={transactionsHref}
        />
      </div>

      <p className="text-sm text-ink-muted">
        Your cash position, the projection and the assistant are on{" "}
        <Link href={overviewHref} className="text-plum-700 underline underline-offset-2">
          Overview
        </Link>
        . Every transaction behind these figures is in{" "}
        <Link href={transactionsHref} className="text-plum-700 underline underline-offset-2">
          Transactions
        </Link>
        .
      </p>
    </div>
  );
}

// ------------------------------------------------------------- monthly history --

/** "Jun", and the year too wherever the year changes, so a 12-month run stays readable. */
function axisMonth(month: string, previous: string | undefined): string {
  const [year, mon] = month.split("-");
  const name = new Date(Number(year), Number(mon) - 1, 1).toLocaleDateString("en-US", {
    month: "short",
  });
  const changed = !previous || previous.slice(0, 4) !== year;
  return changed ? `${name} ’${year.slice(2)}` : name;
}

/**
 * Money in and out, month by month, on one shared dollar scale.
 *
 * Drawn as an SVG rather than sized divs so the bars can be given a real axis: without one a
 * bar chart of money shows a shape and no sense of how much. Exact figures sit in a table
 * underneath, which is also what a screen reader is given — the chart itself is one image.
 */
function MonthlyHistory({ months, data }: { months: MonthRow[]; data: CashAnalysis }) {
  const peak = Math.max(1, ...months.map((m) => Math.max(m.money_in_minor, m.money_out_minor)));
  const W = 640;
  const H = 236;
  const AXIS = 62;
  const TOP = 34; // headroom for the net figure above the taller bar
  const BASE = H - 44; // room under the baseline for the month and part-month labels
  const plot = W - AXIS - 10;
  const slot = plot / Math.max(months.length, 1);
  const barW = Math.min(30, Math.max(9, slot * 0.3));
  const gap = Math.max(3, barW * 0.18);
  const height = (minor: number) => Math.max((minor / peak) * (BASE - TOP), minor > 0 ? 2 : 0);
  const ticks = [peak, peak / 2, 0];

  return (
    <Card>
      <CardTitle
        icon="chart"
        title="Money in and out, month by month"
        hint="Cash moving through the bank. Not profit: it leaves out anything owed or owing but unpaid."
      />

      <div className="mt-4 overflow-x-auto">
        <svg
          viewBox={`0 0 ${W} ${H}`}
          className="h-60 w-full min-w-[340px]"
          role="img"
          aria-label={`Money in and out for ${months.length} month${months.length === 1 ? "" : "s"}, from ${monthLabel(months[0].month)} to ${monthLabel(months[months.length - 1].month)}. Exact figures are in the table below.`}
        >
          {ticks.map((value) => {
            const ty = BASE - (value / peak) * (BASE - TOP);
            return (
              <g key={value}>
                <line
                  x1={AXIS}
                  y1={ty}
                  x2={W - 4}
                  y2={ty}
                  stroke={value === 0 ? "#d1d5db" : "#e5e7eb"}
                  strokeWidth="1"
                />
                <text
                  x={AXIS - 8}
                  y={ty + 4}
                  textAnchor="end"
                  fontSize="11"
                  className="fill-ink-muted"
                >
                  {value === 0 ? "$0" : money(Math.round(value), { compact: true })}
                </text>
              </g>
            );
          })}

          {months.map((m, i) => {
            const centre = AXIS + slot * (i + 0.5);
            const inH = height(m.money_in_minor);
            const outH = height(m.money_out_minor);
            const tallest = Math.max(inH, outH);
            return (
              <g key={m.month}>
                <title>
                  {`${monthLabel(m.month)}${m.complete ? "" : " (part month)"}: ${money(m.money_in_minor)} in, ${money(m.money_out_minor)} out, ${money(m.net_minor, { sign: true })} net`}
                </title>
                <rect
                  x={centre - barW - gap / 2}
                  y={BASE - inH}
                  width={barW}
                  height={inH}
                  rx="2.5"
                  fill="#0f6e56"
                />
                <rect
                  x={centre + gap / 2}
                  y={BASE - outH}
                  width={barW}
                  height={outH}
                  rx="2.5"
                  fill="#4422ff"
                />
                <text
                  x={centre}
                  y={BASE - tallest - 7}
                  textAnchor="middle"
                  fontSize="11"
                  fontWeight="600"
                  fill={m.net_minor < 0 ? "#9a3412" : "#0f6e56"}
                >
                  {money(m.net_minor, { sign: true, compact: true })}
                </text>
                <text
                  x={centre}
                  y={BASE + 16}
                  textAnchor="middle"
                  fontSize="11"
                  className="fill-ink-soft"
                >
                  {axisMonth(m.month, months[i - 1]?.month)}
                </text>
                {!m.complete ? (
                  <text
                    x={centre}
                    y={BASE + 30}
                    textAnchor="middle"
                    fontSize="10"
                    fill="#92400e"
                  >
                    part month
                  </text>
                ) : null}
              </g>
            );
          })}
        </svg>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-ink-muted">
        <span className="flex items-center gap-1.5">
          <span className="inline-block size-2.5 rounded-sm bg-up" aria-hidden /> money in
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block size-2.5 rounded-sm bg-plum-600" aria-hidden /> money out
        </span>
        <span>net above each month</span>
      </div>

      <Disclosure
        label={`Exact figures for all ${months.length} month${months.length === 1 ? "" : "s"}`}
        openLabel="Hide the exact figures"
        className="mt-4"
      >
        <div className="overflow-x-auto">
          <table className="w-full min-w-[380px] text-left text-xs">
            <caption className="sr-only">
              Money in, money out and net cash for each month
            </caption>
            <thead>
              <tr className="border-b border-line text-[10px] font-semibold uppercase tracking-wider text-ink-muted">
                <th scope="col" className="py-1.5 pr-3">Month</th>
                <th scope="col" className="py-1.5 pr-3 text-right">In</th>
                <th scope="col" className="py-1.5 pr-3 text-right">Out</th>
                <th scope="col" className="py-1.5 pr-3 text-right">Net</th>
                <th scope="col" className="py-1.5 text-right">Entries</th>
              </tr>
            </thead>
            <tbody>
              {[...months].reverse().map((m) => (
                <tr key={m.month} className="border-b border-line last:border-0">
                  <th scope="row" className="py-1.5 pr-3 font-medium text-ink">
                    {monthLabel(m.month)}
                    {m.complete ? null : (
                      <span className="font-normal text-warn"> · part month</span>
                    )}
                  </th>
                  <td className="t-num py-1.5 pr-3 text-right text-up">
                    {money(m.money_in_minor)}
                  </td>
                  <td className="t-num py-1.5 pr-3 text-right text-ink">
                    {money(m.money_out_minor)}
                  </td>
                  <td
                    className={`t-num py-1.5 pr-3 text-right font-semibold ${m.net_minor < 0 ? "text-down" : "text-up"}`}
                  >
                    {money(m.net_minor, { sign: true })}
                  </td>
                  <td className="t-num py-1.5 text-right text-ink-muted">{m.posted_count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Disclosure>

      <Disclosure label="How this is calculated" openLabel="Hide how this is calculated" className="mt-2">
        Each month adds up the transactions that settled in that calendar month. Anything still
        pending is left out ({data.pending_excluded} pending right now), and money moved between
        two of your own accounts is removed from both sides so it does not read as income and
        spending.
        {months.some((m) => !m.complete)
          ? " A month that has not finished is marked as a part month; it covers fewer days than a full one."
          : ""}
      </Disclosure>
    </Card>
  );
}

// ------------------------------------------------------------ month comparison --

/** One month against the one before it, category by category. */
function MonthComparison({ data }: { data: CashAnalysis }) {
  const [all, setAll] = useState(false);
  const c = data.comparison;
  if (!c || !c.month_totals || !c.previous_totals) return null;
  const movers = c.changes.filter((x) => x.change_minor !== 0);
  const shown = all ? movers : movers.slice(0, 6);
  const swing = c.month_totals.net_minor - c.previous_totals.net_minor;

  return (
    <Card>
      <CardTitle
        icon="swap"
        title={`${monthLabel(c.month)} against ${monthLabel(c.previous_month)}`}
        hint={
          <>
            Net cash {swing >= 0 ? "improved by" : "fell by"}{" "}
            <span className="font-semibold text-ink">{money(Math.abs(swing))}</span>.
          </>
        }
      />

      {!c.month_totals.complete ? (
        <div className="mt-3">
          <Notice tone="info">
            {monthLabel(c.month)} has not finished. It covers the month up to{" "}
            {dayLabel(data.data_through ?? data.as_of)} against all of{" "}
            {monthLabel(c.previous_month)}, so the two periods cover different numbers of days.
          </Notice>
        </div>
      ) : null}

      {movers.length === 0 ? (
        <p className="mt-4 text-sm text-ink-muted">No category moved between these months.</p>
      ) : (
        <div className="mt-4 overflow-x-auto">
          <table className="w-full text-left text-[13px]">
            <caption className="sr-only">
              Each category&apos;s net effect on cash in {monthLabel(c.month)} against{" "}
              {monthLabel(c.previous_month)}
            </caption>
            <thead>
              <tr className="border-b border-line text-[10px] font-semibold uppercase tracking-wider text-ink-muted">
                <th scope="col" className="py-2 pr-3">Category</th>
                <th scope="col" className="py-2 pr-3 text-right">
                  {monthLabel(c.previous_month)}
                </th>
                <th scope="col" className="py-2 pr-3 text-right">{monthLabel(c.month)}</th>
                <th scope="col" className="py-2 text-right">Change in cash</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((m) => (
                <tr key={m.key} className="border-b border-line last:border-0">
                  <th scope="row" className="min-w-0 py-2 pr-3 font-medium text-ink">
                    {m.label}
                  </th>
                  <td className="t-num py-2 pr-3 text-right whitespace-nowrap text-ink-muted">
                    {money(m.previous_minor, { sign: true })}
                  </td>
                  <td className="t-num py-2 pr-3 text-right whitespace-nowrap text-ink">
                    {money(m.current_minor, { sign: true })}
                  </td>
                  <td
                    className={`t-num py-2 text-right font-semibold whitespace-nowrap ${m.change_minor < 0 ? "text-down" : "text-up"}`}
                  >
                    {money(m.change_minor, { sign: true })}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {movers.length > 6 ? (
        <button type="button" onClick={() => setAll((v) => !v)} className={`${quietButtonClass} mt-3`}>
          {all ? "Show fewer" : `Show all ${movers.length} categories`}
        </button>
      ) : null}

      <Disclosure label="How this is calculated" openLabel="Hide how this is calculated" className="mt-3">
        Each row is that category&apos;s net effect on cash, meaning what came in less what
        went out, in one month against the month before. A negative change means the category took more
        cash out, or brought less in, than it did previously.
      </Disclosure>
    </Card>
  );
}

// ---------------------------------------------------------- category breakdown --

/** Every category, ranked by how much cash it moved, with the gross sides kept visible. */
function CategoryBreakdown({
  data,
  workspaceId,
  token,
  transactionsHref,
}: {
  data: CashAnalysis;
  workspaceId: string;
  token: string | null;
  transactionsHref: string;
}) {
  const [all, setAll] = useState(false);
  const [rules, setRules] = useState<Awaited<ReturnType<typeof fetchCategoryRules>> | null>(null);
  const [showRules, setShowRules] = useState(false);

  const grouped = useMemo(() => {
    const byGroup: Record<string, CategoryRow[]> = {};
    for (const row of data.categories) (byGroup[row.group] ||= []).push(row);
    for (const key of Object.keys(byGroup))
      byGroup[key].sort((a, b) => Math.abs(netOf(b)) - Math.abs(netOf(a)));
    return byGroup;
  }, [data.categories]);

  const biggest = Math.max(1, ...data.categories.map((row) => Math.abs(netOf(row))));
  const LIMIT = 6;
  const hidden = GROUP_ORDER.reduce(
    (sum, key) => sum + Math.max((grouped[key]?.length ?? 0) - LIMIT, 0),
    0,
  );

  async function toggleRules() {
    setShowRules((v) => !v);
    if (!rules) setRules(await fetchCategoryRules(workspaceId, token).catch(() => null));
  }

  return (
    <Card>
      <CardTitle
        icon="layers"
        title="Where the money sits, by category"
        hint="Everything on record. Each figure is what that category did to cash: money in less money out."
      />

      {GROUP_ORDER.filter((key) => grouped[key]?.length).map((key) => {
        const rows = all ? grouped[key] : grouped[key].slice(0, LIMIT);
        return (
          <div key={key} className="mt-5 first:mt-4">
            <h4 className="text-xs font-semibold uppercase tracking-wider text-ink-muted">
              {GROUP_LABEL[key] ?? key}
            </h4>
            <ul className="mt-2.5 flex flex-col gap-2.5">
              {rows.map((row) => {
                const net = netOf(row);
                const both = row.inflow_minor > 0 && row.outflow_minor > 0;
                const width = Math.max(Math.round((Math.abs(net) / biggest) * 100), 1);
                return (
                  <li key={row.key}>
                    <div className="flex items-baseline justify-between gap-3">
                      <span className="min-w-0 truncate text-[13px] text-ink">
                        {row.label}
                        <span className="text-ink-faint">
                          {" "}
                          · {row.count} {row.count === 1 ? "entry" : "entries"}
                        </span>
                        {row.key === "uncategorized" ? (
                          <span className="ml-1.5 align-middle">
                            <Chip tone="warn">needs review</Chip>
                          </span>
                        ) : null}
                      </span>
                      <span
                        className={`t-num shrink-0 text-[13px] font-semibold ${net > 0 ? "text-up" : "text-ink"}`}
                      >
                        {money(net, { sign: true })}
                      </span>
                    </div>
                    <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-line">
                      <div
                        className={`h-1.5 rounded-full ${net > 0 ? "bg-up" : "bg-plum-500"}`}
                        style={{ width: `${width}%` }}
                      />
                    </div>
                    {both ? (
                      <p className="mt-1 text-[11px] text-ink-faint">
                        {money(row.inflow_minor)} in · {money(row.outflow_minor)} out
                      </p>
                    ) : null}
                    {row.key === "uncategorized" ? (
                      <p className="mt-1 text-[11px] text-warn">
                        These matched no rule, so they are left alone rather than guessed at.
                      </p>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          </div>
        );
      })}

      <div className="mt-5 flex flex-wrap items-center gap-3">
        {hidden > 0 ? (
          <button type="button" onClick={() => setAll((v) => !v)} className={secondaryButtonClass}>
            {all ? "Show fewer" : `Show all (${hidden} more)`}
          </button>
        ) : null}
        <Link href={transactionsHref} className={secondaryButtonClass}>
          See every transaction
        </Link>
        <button type="button" onClick={toggleRules} className={quietButtonClass}>
          {showRules ? "Hide the grouping rules" : "What decides these groups?"}
        </button>
      </div>

      {showRules && rules ? (
        <div className="mt-3 rounded-xl border border-line bg-surface-soft p-3 text-xs leading-relaxed text-ink-muted">
          <p>{rules.note}</p>
          {rules.counterparty_note ? <p className="mt-2">{rules.counterparty_note}</p> : null}
          {rules.instrument_words?.length ? (
            <p className="mt-1">
              Words treated as describing how money moved rather than who it moved to or from:{" "}
              {rules.instrument_words.join(", ")}.
            </p>
          ) : null}
          <ul className="mt-2 flex flex-col gap-1">
            {rules.rules.map((rule) => (
              <li key={rule.key}>
                <span className="text-ink">{rule.label}</span>
                {rule.keywords.length ? `: ${rule.keywords.join(", ")}` : ": anything left over"}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <Disclosure label="How this is calculated" openLabel="Hide how this is calculated" className="mt-3">
        A category&apos;s figure is everything that came in under it less everything that went
        out, so a category with both sides is not reported as one of them. Where both sides
        exist, the gross totals are shown under the bar. Categories are ranked by how much cash
        they moved, and the bar is drawn to that same figure.
      </Disclosure>
    </Card>
  );
}

// ---------------------------------------------------------- recurring activity --

const ACRONYMS = new Set([
  "ACH", "LLC", "INC", "LTD", "LLP", "PLC", "POS", "ATM", "DBA", "CO", "US", "USA",
  "NSF", "IRS", "EFT", "ID", "PMT", "TX", "CC", "HQ",
]);

/**
 * A bank description in title case, for reading. The raw line is always shown on expansion,
 * because this is a cosmetic rewrite of the bank's own text and not a better source than it.
 */
function readableName(raw: string): string {
  return raw
    .split(/\s+/)
    .map((word) => {
      const bare = word.replace(/[^A-Z0-9]/gi, "");
      if (ACRONYMS.has(bare.toUpperCase())) return word.toUpperCase();
      if (/^\d+$/.test(bare)) return word;
      return word.charAt(0).toUpperCase() + word.slice(1).toLowerCase();
    })
    .join(" ");
}

function RecurringActivity({
  data,
  workspaceId,
  token,
  transactionsHref,
}: {
  data: CashAnalysis;
  workspaceId: string;
  token: string | null;
  transactionsHref: string;
}) {
  const [all, setAll] = useState(false);
  const [evidence, setEvidence] = useState<string | null>(null);
  const rows = data.recurring;
  const shown = all ? rows : rows.slice(0, 6);

  if (!rows.length)
    return (
      <Card>
        <CardTitle icon="repeat" title="What keeps coming back" />
        <div className="mt-4">
          <Empty title="No steady pattern yet" icon="clock">
            A description has to appear at least three times, at a regular spacing, before it is
            reported as repeating.
          </Empty>
        </div>
      </Card>
    );

  return (
    <Card>
      <CardTitle
        icon="repeat"
        title="What keeps coming back"
        hint="Seen often enough to look regular. Every next date is estimated from that pattern, not a bill anyone has told us about."
      />

      <div className="mt-4 overflow-x-auto">
        <table className="w-full text-left text-[13px]">
          <caption className="sr-only">
            Repeating payments and receipts, with their estimated frequency, typical amount and
            estimated next date
          </caption>
          <thead>
            <tr className="border-b border-line text-[10px] font-semibold uppercase tracking-wider text-ink-muted">
              <th scope="col" className="py-2 pr-3">Counterparty</th>
              {/* Four columns do not fit a phone. Below `sm` the frequency moves under the
                  name rather than squeezing every column until none of them reads. */}
              <th scope="col" className="hidden py-2 pr-3 whitespace-nowrap sm:table-cell">
                About every
              </th>
              <th scope="col" className="py-2 pr-3 text-right">Typical amount</th>
              <th scope="col" className="py-2 text-right">Next, estimated</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((row) => (
              <RecurringRowView
                key={row.label}
                row={row}
                transactionsHref={transactionsHref}
                onEvidence={setEvidence}
              />
            ))}
          </tbody>
        </table>
      </div>

      {rows.length > 6 ? (
        <button type="button" onClick={() => setAll((v) => !v)} className={`${quietButtonClass} mt-3`}>
          {all ? "Show fewer" : `Show all ${rows.length}`}
        </button>
      ) : null}

      {evidence ? (
        <div className="mt-4">
          <TransactionDetail
            workspaceId={workspaceId}
            transactionId={evidence}
            token={token}
            onClose={() => setEvidence(null)}
          />
        </div>
      ) : null}

      <Disclosure label="How this is calculated" openLabel="Hide how this is calculated" className="mt-3">
        A description counts as repeating once it has appeared at least three times with a
        steady gap between appearances. The amount shown is the average of those appearances,
        and the next date simply adds the usual gap to the last one. Both are estimates read off
        your own history; neither is a scheduled obligation.
      </Disclosure>
    </Card>
  );
}

/** One repeating line, with the bank's own text and its transactions a disclosure away. */
function RecurringRowView({
  row,
  transactionsHref,
  onEvidence,
}: {
  row: RecurringRow;
  transactionsHref: string;
  onEvidence: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const incoming = row.typical_amount_minor > 0;
  const latest = row.transaction_ids[row.transaction_ids.length - 1];

  return (
    <>
      <tr className="border-b border-line">
        <th scope="row" className="w-full min-w-0 max-w-0 py-2 pr-3 font-medium text-ink">
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
            className="flex w-full min-w-0 items-center gap-1 rounded text-left hover:text-plum-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-plum-600"
          >
            <Icon
              name="chevron"
              className={`size-3 shrink-0 text-ink-faint transition ${open ? "rotate-90" : ""}`}
            />
            <span className="min-w-0 flex-1">
              <span className="block truncate" title={row.label}>
                {readableName(row.label)}
              </span>
              <span className="block text-[11px] font-normal text-ink-muted sm:hidden">
                about every {row.cadence_days} days
              </span>
            </span>
          </button>
        </th>
        <td className="hidden py-2 pr-3 whitespace-nowrap text-ink-muted sm:table-cell">
          {row.cadence_days} days
        </td>
        <td
          className={`t-num py-2 pr-3 text-right font-semibold whitespace-nowrap ${incoming ? "text-up" : "text-ink"}`}
        >
          {money(row.typical_amount_minor, { sign: true })}
        </td>
        <td className="t-num py-2 text-right whitespace-nowrap text-ink-muted">
          ~{dayLabel(row.next_expected)}
        </td>
      </tr>
      {open ? (
        <tr className="border-b border-line bg-surface-soft">
          <td colSpan={4} className="px-1 py-2.5 text-xs leading-relaxed text-ink-muted">
            <p>
              <span className="text-ink-faint">As it appears on your statement:</span>{" "}
              <span className="font-mono text-[11px] text-ink">{row.label}</span>
            </p>
            <p className="mt-1">
              {incoming ? "Money in" : "Money out"} · seen {row.occurrences} times · last on{" "}
              {dayLabel(row.last_seen)} · estimated next {dayLabel(row.next_expected)}, which is
              this pattern continuing and not a known bill.
            </p>
            <div className="mt-2 flex flex-wrap items-center gap-3">
              {latest ? (
                <button
                  type="button"
                  onClick={() => onEvidence(latest)}
                  className="text-plum-700 underline underline-offset-2 hover:text-plum-600"
                >
                  See the most recent one
                </button>
              ) : null}
              <Link
                href={`${transactionsHref}${transactionsHref.includes("?") ? "&" : "?"}q=${encodeURIComponent(row.label)}`}
                className="text-plum-700 underline underline-offset-2 hover:text-plum-600"
              >
                Find all {row.occurrences} in Transactions
              </Link>
            </div>
          </td>
        </tr>
      ) : null}
    </>
  );
}
