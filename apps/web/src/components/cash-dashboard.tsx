"use client";

import Link from "next/link";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";

import { ApiError } from "@/lib/api";
import { track } from "@/lib/analytics";
import {
  dayLabel,
  fetchAnalysis,
  money,
  monthLabel,
  runScenario,
  type CashAnalysis,
  type ForecastPath,
  type ScenarioInput,
} from "@/lib/analysis-api";
import {
  ActionRow,
  Card,
  CardTitle,
  Chip,
  Disclosure,
  Empty,
  Field,
  FieldGroup,
  Icon,
  inputClass,
  Notice,
  Stat,
  buttonClass,
  quietButtonClass,
  secondaryButtonClass,
} from "@/components/ui";
import { BriefCard } from "@/components/brief-card";
import { CfoPanel } from "@/components/cfo-panel";
import { ReviewCard } from "@/components/review-card";
import { ScenarioExplainer } from "@/components/scenario-explainer";

type Props = {
  workspaceId: string;
  token: string | null;
  /** Where a "show the transactions behind this" link should go — the Transactions section. */
  transactionsHref: string;
  /** The Analysis section, which holds the month-by-month history and the category detail. */
  analysisHref: string;
  /** The Statements section, for the import and review links an insight points at. */
  statementsHref?: string;
};

/**
 * Overview: where the business stands today, and what to do about it.
 *
 * It answers four questions and stops — how much cash there is, where it is heading, what is
 * worth a look, and anything the owner can ask. The month-by-month history, the full category
 * split, the month comparison and the repeating payments are a section of their own, because
 * a dashboard that also holds every report is neither.
 *
 * Wording rule followed throughout: the cards say what the money did, and anything about how a
 * figure was produced sits behind a "how this is calculated" control rather than in the headline.
 */
export function CashDashboard({
  workspaceId,
  token,
  transactionsHref,
  analysisHref,
  statementsHref,
}: Props) {
  const [data, setData] = useState<CashAnalysis | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [scenario, setScenario] = useState<ScenarioInput | null>(null);
  const [scenarioOpen, setScenarioOpen] = useState(false);
  // A scenario the conversation worked out, and a stamp that changes only when a new one
  // arrives. The form copies the assumptions across on that stamp and at no other time, so
  // nothing the owner typed is overwritten by a turn of the conversation.
  const [fromChat, setFromChat] = useState<{ input: ScenarioInput; stamp: number } | null>(null);
  const forecastRef = useRef<HTMLDivElement>(null);
  const briefRef = useRef<HTMLDivElement>(null);
  const reviewRef = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    const fresh = await fetchAnalysis(workspaceId, token);
    setData(fresh);
  }, [workspaceId, token]);

  useEffect(() => {
    let active = true;
    fetchAnalysis(workspaceId, token)
      .then((fresh) => {
        if (active) setData(fresh);
      })
      .catch((caught: unknown) => {
        if (active)
          setError(
            caught instanceof ApiError
              ? caught.describe()
              : "Could not read your figures.",
          );
      });
    return () => {
      active = false;
    };
  }, [workspaceId, token]);

  async function applyScenario(input: ScenarioInput) {
    setBusy(true);
    setError(null);
    try {
      const next = await runScenario(workspaceId, token, input);
      setData(next);
      setScenario(input);
      track("scenario_applied", { section: "overview", outcome: "applied" });
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.describe()
          : "Could not run that scenario.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function clearScenario() {
    setBusy(true);
    try {
      await load();
      setScenario(null);
    } finally {
      setBusy(false);
    }
  }

  /** A what-if the conversation applied: open the workspace and bring it into view. */
  const revealScenario = useCallback(() => {
    setScenarioOpen(true);
    // After the panel has expanded, so the chart and its assumptions are what lands on screen.
    requestAnimationFrame(() =>
      forecastRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }),
    );
  }, []);

  if (error && !data) return <Notice tone="error">{error}</Notice>;
  if (!data) {
    return (
      <Card>
        <p className="text-ink-muted">Working out your cash position…</p>
      </Card>
    );
  }
  if (data.coverage.posted_count === 0) {
    return (
      <Card>
        <CardTitle title="Your cash position" />
        <div className="mt-4">
          <Empty title="No transactions here yet">
            Import a statement and every figure on this page fills in
            automatically.
          </Empty>
        </div>
      </Card>
    );
  }

  return (
    <div className="flex flex-col gap-6">
      {error ? <Notice tone="error">{error}</Notice> : null}

      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="t-section">Your cash</h2>
        <p className="text-xs text-ink-muted">
          Figures through {dayLabel(data.as_of)}
          {data.data_through && data.data_through !== data.as_of
            ? ` · newest entry ${dayLabel(data.data_through)}`
            : ""}
        </p>
      </div>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)] lg:items-start">
        <div className="flex min-w-0 flex-col gap-5">
          <CashHero data={data} transactionsHref={transactionsHref} />
          <div ref={forecastRef} className="scroll-mt-20">
            <ForecastSection
              data={data}
              busy={busy}
              open={scenarioOpen}
              onOpen={() => setScenarioOpen(true)}
              onClose={() => setScenarioOpen(false)}
              onApply={applyScenario}
              onClear={clearScenario}
              active={scenario}
              fromChat={fromChat}
              workspaceId={workspaceId}
              token={token}
            />
          </div>
          <InsightsCard
            data={data}
            analysisHref={analysisHref}
            onReview={() =>
              reviewRef.current?.scrollIntoView({ behavior: "smooth", block: "start" })
            }
            onBrief={() =>
              briefRef.current?.scrollIntoView({ behavior: "smooth", block: "start" })
            }
          />
        </div>
        <div className="min-w-0 lg:sticky lg:top-[4.25rem]">
          <CfoPanel
            workspaceId={workspaceId}
            token={token}
            fingerprint={data.input_fingerprint}
            onScenario={(input) => {
              setFromChat({ input, stamp: Date.now() });
              void applyScenario(input);
            }}
            onProposal={() => {
              void load();
            }}
            onScenarioApplied={revealScenario}
          />
        </div>
      </div>

      <div ref={briefRef} className="scroll-mt-20">
        <BriefCard workspaceId={workspaceId} token={token} fingerprint={data.input_fingerprint} />
      </div>
      <div ref={reviewRef} className="scroll-mt-20">
        <ReviewCard
          workspaceId={workspaceId}
          token={token}
          fingerprint={data.input_fingerprint}
          onDecided={() => {
            void load();
          }}
        />
      </div>

      <p className="text-sm text-ink-muted">
        The month-by-month history, the full category split and what keeps repeating are in{" "}
        <Link href={analysisHref} className="text-plum-700 underline underline-offset-2">
          Analysis
        </Link>
        . Every transaction behind these figures is in{" "}
        <Link href={transactionsHref} className="text-plum-700 underline underline-offset-2">
          Transactions
        </Link>
        {statementsHref ? (
          <>
            , and the statements they came from are in{" "}
            <Link href={statementsHref} className="text-plum-700 underline underline-offset-2">
              Statements
            </Link>
          </>
        ) : null}
        .
      </p>
    </div>
  );
}

/**
 * The first thing on the page: how much cash there is, when that was true, and the month's
 * in / out / net beside it. The qualifications that matter — mixed dates, a statement figure
 * rather than a live one — are said in one chip and opened up in a disclosure, not written
 * as paragraphs above the number.
 */
function CashHero({ data, transactionsHref }: { data: CashAnalysis; transactionsHref: string }) {
  const b = data.balances;
  const live = b.rows.filter((r) => r.is_live);
  const statement = b.rows.filter((r) => r.kind === "statement_closing");
  const shown = live.length > 0 ? live : statement;
  const available = b.rows.filter((r) => r.kind === "available");
  const total = shown.reduce((sum, r) => sum + r.amount_minor, 0);
  const latest = data.months[data.months.length - 1];
  const asOf = shown.length ? shown.map((r) => r.as_of).sort().at(-1)! : data.as_of;
  const qualifier =
    shown.length === 0
      ? "No balance recorded yet"
      : `${live.length ? "Latest reported balances" : "Statement closing balances"}${b.mixed_dates ? " · mixed dates" : ` · as of ${dayLabel(asOf)}`}`;

  return (
    <Card>
      <div className="grid gap-6 md:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)] md:items-start">
        <div>
          <div className="flex items-center gap-2">
            <p className="text-sm font-medium text-ink-muted">Cash in the bank</p>
            <Chip tone={live.length ? "up" : "muted"} icon={live.length ? "bank" : "clock"}>
              {live.length ? "bank-reported" : "from your statements"}
            </Chip>
          </div>
          {shown.length === 0 ? (
            <p className="mt-2 text-2xl font-semibold text-ink-faint">Not recorded</p>
          ) : (
            <p className="t-figure mt-2 text-[2.75rem] sm:text-5xl">{money(total)}</p>
          )}
          <p className="mt-2 text-xs text-ink-muted">{qualifier}</p>
          {shown.length > 0 ? (
            <Disclosure label="By account, and how this is worked out" openLabel="Hide detail" className="mt-3">
              <ul className="divide-y divide-line">
                {shown.map((r) => (
                  <li key={`${r.account_id}-${r.kind}`} className="flex flex-wrap justify-between gap-2 py-1.5 text-ink">
                    <span>
                      {r.account_name}
                      {r.mask ? <span className="text-ink-faint"> ···{r.mask}</span> : null}
                    </span>
                    <span className="t-num">
                      {money(r.amount_minor)} <span className="text-ink-faint">as of {dayLabel(r.as_of)}</span>
                    </span>
                  </li>
                ))}
              </ul>
              {b.mixed_dates ? (
                <p className="mt-2">
                  These balances were last seen on different days, so this is the latest known figure for
                  each account added together rather than one moment in time.
                </p>
              ) : null}
              {live.length === 0 ? (
                <p className="mt-2">
                  This is the closing balance printed on your latest statement. That is history, not
                  today&apos;s balance.
                </p>
              ) : null}
              {available.length > 0 ? (
                <p className="mt-2">
                  Your bank also reports an available balance of {money(available.reduce((s, r) => s + r.amount_minor, 0))}.
                  Available and current describe the same money and are never added together.
                </p>
              ) : null}
            </Disclosure>
          ) : null}
        </div>

        {latest ? (
          <div className="rounded-xl border border-line bg-surface-soft p-4">
            <div className="flex items-baseline justify-between gap-2">
              <p className="text-sm font-semibold text-ink">
                {monthLabel(latest.month)}
                {latest.complete ? "" : <span className="font-normal text-ink-muted"> so far</span>}
              </p>
              <Link href={transactionsHref} className={quietButtonClass}>
                Transactions <Icon name="arrow" className="size-3.5" />
              </Link>
            </div>
            <dl className="mt-3 grid grid-cols-3 gap-x-3 gap-y-2">
              <Stat size="sm" label="Money in" value={money(latest.money_in_minor)} tone="up" />
              <Stat size="sm" label="Money out" value={money(latest.money_out_minor)} tone="down" />
              <Stat
                size="sm"
                label="Net cash"
                value={money(latest.net_minor, { sign: true })}
                tone={latest.net_minor >= 0 ? "up" : "down"}
              />
            </dl>
            <p className="mt-3 text-xs text-ink-faint">
              {latest.complete
                ? `A full month · ${latest.posted_count} transactions`
                : `Month to date through ${dayLabel(data.data_through ?? data.as_of)} · ${latest.posted_count} transactions`}
            </p>
          </div>
        ) : null}
      </div>
    </Card>
  );
}

/** Things worth a look, each with somewhere to go. Only what the analysis actually found. */
function InsightsCard({
  data,
  analysisHref,
  onReview,
  onBrief,
}: {
  data: CashAnalysis;
  analysisHref: string;
  onReview: () => void;
  onBrief: () => void;
}) {
  const c = data.comparison;
  const movers = (c?.changes ?? []).filter((x) => x.change_minor !== 0);
  const biggest = movers.length
    ? movers.reduce((a, b) => (Math.abs(b.change_minor) > Math.abs(a.change_minor) ? b : a))
    : null;
  const waiting = data.classification?.uncategorized_count ?? 0;
  // The next repeating payment still ahead of the analysis date, if the history shows one.
  const upcoming = data.recurring
    .filter((r) => r.typical_amount_minor < 0 && r.next_expected >= data.as_of)
    .sort((a, b) => a.next_expected.localeCompare(b.next_expected))[0];

  const items: React.ReactNode[] = [];
  if (biggest && c) {
    items.push(
      <ActionRow
        key="change"
        icon="swap"
        // Direction by size of the movement, not by sign: an outflow that fell from −$8,900 to
        // $0 is spending going DOWN, however the signed change reads.
        title={`${biggest.label} ${Math.abs(biggest.current_minor) < Math.abs(biggest.previous_minor) ? "down" : "up"} ${money(Math.abs(biggest.change_minor))}`}
        body={`${monthLabel(c.month)} against ${monthLabel(c.previous_month)} · ${money(biggest.previous_minor, { sign: true })} → ${money(biggest.current_minor, { sign: true })}`}
        href={analysisHref}
        cta="See what changed"
      />,
    );
  }
  if (waiting > 0) {
    items.push(
      <ActionRow
        key="review"
        icon="review"
        title={`${waiting} transaction${waiting === 1 ? "" : "s"} waiting for a category`}
        body="The rules left these alone rather than guess. Suggestions change nothing until you accept them."
        onClick={onReview}
        cta="Review"
      />,
    );
  }
  if (upcoming) {
    items.push(
      <ActionRow
        key="recurring"
        icon="repeat"
        title={`${money(upcoming.typical_amount_minor)} usually leaves around ${dayLabel(upcoming.next_expected)}`}
        body={`${upcoming.label} has come round every ${upcoming.cadence_days} days. An estimate from that pattern, not a bill.`}
        href={analysisHref}
        cta="See the pattern"
      />,
    );
  }
  items.push(
    <ActionRow
      key="brief"
      icon="doc"
      title="Your cash, in words"
      body="A checked brief of what changed and what it means."
      onClick={onBrief}
      cta="Read"
    />,
  );
  return (
    <Card padding="tight">
      <CardTitle icon="spark" title="Worth a look" />
      <ul className="mt-1 divide-y divide-line">{items.slice(0, 4)}</ul>
    </Card>
  );
}

/** The active what-if in the owner's own words, so a chart never stands without its inputs. */
function describeScenario(input: ScenarioInput): string[] {
  const lines: string[] = [];
  if (input.receipts_change_percent) {
    const pct = input.receipts_change_percent;
    lines.push(
      `Customer receipts ${pct < 0 ? "down" : "up"} ${Math.abs(pct)}% across the window`,
    );
  }
  if (input.expected_payment_minor && input.expected_payment_date) {
    lines.push(
      `A payment of ${money(input.expected_payment_minor)} expected on ` +
        `${dayLabel(input.expected_payment_date)}, arriving ${input.delay_days ?? 0} ` +
        `day${(input.delay_days ?? 0) === 1 ? "" : "s"} late`,
    );
  }
  if (input.one_time_expense_minor && input.one_time_expense_date) {
    lines.push(
      `${input.one_time_expense_label || "A one-off cost"} of ` +
        `${money(input.one_time_expense_minor)} on ${dayLabel(input.one_time_expense_date)}`,
    );
  }
  return lines.length ? lines : ["No change to the baseline"];
}

function PathChart({
  baseline,
  scenario,
  compact = false,
}: {
  baseline: ForecastPath;
  scenario: ForecastPath | null;
  compact?: boolean;
}) {
  const all = [...baseline.points, ...(scenario?.points ?? [])];
  const lowest = Math.min(...all.map((p) => p.balance_minor));
  const highest = Math.max(...all.map((p) => p.balance_minor));
  // Pad the band a little so a flat path does not sit on the axis, and so the top label is
  // not clipped. The ticks are read off the padded band, so every label is a real position.
  const pad = Math.max(Math.round((highest - lowest) * 0.08), 1000);
  const lo = lowest - pad;
  const hi = highest + pad;
  const span = Math.max(hi - lo, 1);
  // Room on the left for the dollar labels: without them the chart shows a shape and no
  // sense of scale, which is the one thing a cash projection has to convey.
  const AXIS = 58;
  const W = 560;
  const H = compact ? 120 : 168;
  const PLOT = W - AXIS;
  const y = (minor: number) => H - 14 - ((minor - lo) / span) * (H - 28);
  const ticks = [hi, (hi + lo) / 2, lo];
  const toPath = (points: typeof baseline.points) =>
    points
      .map((p, i) => {
        const x = AXIS + (i / Math.max(points.length - 1, 1)) * PLOT;
        return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y(p.balance_minor).toFixed(1)}`;
      })
      .join(" ");
  return (
    <div className="mt-4 overflow-x-auto">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className={compact ? "h-28 w-full min-w-[280px]" : "h-44 w-full min-w-[320px]"}
        role="img"
        aria-label={`Projected cash balance over the next 30 days, between ${money(lowest)} and ${money(highest)}`}
      >
        {ticks.map((value) => (
          <g key={value}>
            <line
              x1={AXIS}
              y1={y(value)}
              x2={W}
              y2={y(value)}
              stroke="#e5e7eb"
              strokeWidth="1"
            />
            <text
              x={AXIS - 8}
              y={y(value) + 4}
              textAnchor="end"
              className="fill-ink-muted"
              fontSize="11"
            >
              {money(Math.round(value), { compact: true })}
            </text>
          </g>
        ))}
        {/* The fill belongs to the series it is the colour of. With a what-if on screen that
            is the scenario, not the baseline — shading under the grey dashed line in violet
            read as if the two had swapped places. */}
        <path
          d={`${toPath((scenario ?? baseline).points)} L${W},${H - 14} L${AXIS},${H - 14} Z`}
          fill="url(#cl-area)"
          stroke="none"
        />
        <defs>
          <linearGradient id="cl-area" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="#4422ff" stopOpacity="0.16" />
            <stop offset="1" stopColor="#4422ff" stopOpacity="0" />
          </linearGradient>
        </defs>
        <path
          d={toPath(baseline.points)}
          fill="none"
          stroke={scenario ? "#7c8698" : "#4422ff"}
          strokeWidth="2.25"
          strokeLinecap="round"
          strokeDasharray={scenario ? "5 4" : undefined}
        />
        {scenario ? (
          <path
            d={toPath(scenario.points)}
            fill="none"
            stroke="#4422ff"
            strokeWidth="2.75"
            strokeLinecap="round"
          />
        ) : null}
      </svg>
      <div
        className="flex flex-wrap justify-between gap-2 text-xs text-ink-muted"
        style={{ paddingLeft: `${(AXIS / W) * 100}%` }}
      >
        <span>{dayLabel(baseline.points[0].date)}</span>
        <span>
          {dayLabel(baseline.points[baseline.points.length - 1].date)}
        </span>
      </div>
      <div className="mt-2 flex flex-wrap gap-4 text-xs text-ink-muted">
        <span className="flex items-center gap-1">
          <span
            className="inline-block h-1 w-4 rounded"
            style={{ background: scenario ? "#7c8698" : "#4422ff" }}
          />{" "}
          as things stand
        </span>
        {scenario ? (
          <span className="flex items-center gap-1">
            <span className="inline-block h-1 w-4 rounded" style={{ background: "#4422ff" }} /> with
            your changes
          </span>
        ) : null}
      </div>
    </div>
  );
}

/**
 * The projection, and the scenario workspace it opens into.
 *
 * Closed, this is a glance: one chart, two figures. Open, the same chart grows, the figures
 * gain the comparison, and the controls appear underneath — so the chart and the results a
 * change produces are never on a different part of the page from the change itself. There is
 * only ever one forecast on this page; the controls are what expands, not a second copy.
 *
 * The typed values live here rather than in the expanded body, so closing the workspace and
 * opening it again finds them exactly as they were left.
 */
function ForecastSection({
  data,
  busy,
  open,
  onOpen,
  onClose,
  onApply,
  onClear,
  active,
  fromChat,
  workspaceId,
  token,
}: {
  data: CashAnalysis;
  busy: boolean;
  open: boolean;
  onOpen: () => void;
  onClose: () => void;
  onApply: (input: ScenarioInput) => void;
  onClear: () => void;
  active: ScenarioInput | null;
  fromChat: { input: ScenarioInput; stamp: number } | null;
  workspaceId: string;
  token: string | null;
}) {
  const [receipts, setReceipts] = useState("0");
  const [payAmount, setPayAmount] = useState("");
  const [payDate, setPayDate] = useState("");
  const [delay, setDelay] = useState("14");
  const [costAmount, setCostAmount] = useState("");
  const [costDate, setCostDate] = useState("");
  const [costLabel, setCostLabel] = useState("");
  const f = data.forecast;

  // Only when a NEW scenario arrives from the conversation. Adjusted during render against the
  // last stamp seen, so the owner's own typing is never rewritten by a re-render, and never by
  // their own Show me — only by a turn that actually worked out a different what-if.
  const [seenStamp, setSeenStamp] = useState(0);
  if (fromChat && fromChat.stamp !== seenStamp) {
    const input = fromChat.input;
    setSeenStamp(fromChat.stamp);
    setReceipts(String(input.receipts_change_percent ?? 0));
    setPayAmount(input.expected_payment_minor ? String(input.expected_payment_minor / 100) : "");
    setPayDate(input.expected_payment_date ?? "");
    setDelay(String(input.delay_days ?? 0));
    setCostAmount(input.one_time_expense_minor ? String(input.one_time_expense_minor / 100) : "");
    setCostDate(input.one_time_expense_date ?? "");
    setCostLabel(input.one_time_expense_label ?? "");
  }

  if (!f.available) {
    return (
      <Card>
        <CardTitle title="Next 30 days" />
        <div className="mt-3" />
        <Notice tone="info">
          There is not enough to project from yet.
          <ul className="mt-2 list-disc pl-5">
            {f.reasons.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
          Import another statement and this appears on its own.
        </Notice>
      </Card>
    );
  }
  if (!f.baseline) return null;

  function submit(event: FormEvent) {
    event.preventDefault();
    const input: ScenarioInput = {
      receipts_change_percent: Number(receipts) || 0,
    };
    if (payAmount && payDate) {
      input.expected_payment_minor = Math.round(Number(payAmount) * 100);
      input.expected_payment_date = payDate;
      input.delay_days = Number(delay) || 0;
    }
    if (costAmount && costDate) {
      input.one_time_expense_minor = Math.round(Number(costAmount) * 100);
      input.one_time_expense_date = costDate;
      input.one_time_expense_label = costLabel || null;
    }
    onApply(input);
  }

  function reset() {
    setReceipts("0");
    setPayAmount("");
    setPayDate("");
    setDelay("14");
    setCostAmount("");
    setCostDate("");
    setCostLabel("");
    onClear();
  }

  const shown = f.scenario ?? f.baseline;

  return (
    <Card padding={open ? "normal" : "tight"}>
      <CardTitle
        icon="chart"
        title="Next 30 days"
        hint={
          open
            ? "A projection, not a statement of fact. It starts from your latest known balance and carries on what your history has been doing."
            : active
              ? "Showing your what-if against the baseline."
              : "A projection from your own history, not a promise."
        }
        action={
          open ? (
            <button type="button" onClick={onClose} className={quietButtonClass}>
              <Icon name="close" className="size-3.5" /> Close
            </button>
          ) : (
            <button type="button" onClick={onOpen} className={quietButtonClass}>
              {active ? "See assumptions" : "Explore a scenario"}{" "}
              <Icon name="arrow" className="size-3.5" />
            </button>
          )
        }
      />

      {open && active ? (
        <div className="mt-4 rounded-xl border border-lavender-200 bg-brand-tint p-3">
          <p className="text-xs font-semibold text-plum-800">
            Showing a what-if, on these assumptions
          </p>
          <ul className="mt-1 flex flex-col gap-0.5 text-xs text-ink">
            {describeScenario(active).map((line) => (
              <li key={line}>· {line}</li>
            ))}
          </ul>
          <p className="mt-1 text-xs text-ink-muted">
            Your transactions and balances are untouched; only this chart changed.
          </p>
        </div>
      ) : null}

      {/* Chart and the figures it produced, always adjacent: a change to the controls below
          redraws both together. */}
      <div className={open ? "" : "grid gap-4 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center"}>
        <div className="min-w-0">
          <PathChart baseline={f.baseline} scenario={f.scenario} compact={!open} />
        </div>
        {open ? (
          <dl className="mt-4 grid gap-4 sm:grid-cols-3">
            <Stat label="Cash in 30 days" value={money(shown.ending_minor)} note="estimated" />
            <Stat
              label="Lowest point"
              value={money(shown.lowest_minor)}
              note={`on ${dayLabel(shown.lowest_date)}`}
            />
            {f.scenario && f.difference_minor !== null ? (
              <Stat
                label="Difference from as things stand"
                value={money(f.difference_minor, { sign: true })}
                tone={f.difference_minor < 0 ? "down" : "up"}
                note={
                  f.lowest_difference_minor !== null &&
                  f.lowest_difference_minor !== f.difference_minor
                    ? `lowest point ${money(f.lowest_difference_minor, { sign: true })}`
                    : undefined
                }
              />
            ) : (
              <Stat
                label="Starting from"
                value={money(f.anchor?.amount_minor ?? 0)}
                note={`${f.anchor?.is_live ? "bank-reported" : "latest statement"} on ${dayLabel(f.anchor?.as_of ?? data.as_of)}`}
              />
            )}
          </dl>
        ) : (
          <dl className="grid grid-cols-2 gap-4 sm:w-44 sm:grid-cols-1">
            <Stat label="Cash in 30 days" value={money(shown.ending_minor)} note="estimated" />
            <Stat
              label="Lowest point"
              value={money(shown.lowest_minor)}
              note={`around ${dayLabel(shown.lowest_date)}`}
            />
          </dl>
        )}
      </div>

      {open ? (
        <>
          <form onSubmit={submit} className="mt-6 flex flex-col gap-4 border-t border-line pt-5">
            <div>
              <h4 className="text-sm font-semibold text-ink">Try something out</h4>
              <p className="mt-0.5 text-xs text-ink-muted">
                Fill in whichever of the three you want to test. Leave the rest empty.
              </p>
            </div>

            <FieldGroup
              legend="Customer receipts"
              hint="Scales what comes in across the whole window. Leave at 0 for no change."
            >
              <div className="sm:max-w-xs">
                <Field label="Receipts change by (%)">
                  <input
                    type="number"
                    step="1"
                    min="-100"
                    max="500"
                    value={receipts}
                    onChange={(e) => setReceipts(e.target.value)}
                    className={inputClass}
                  />
                </Field>
              </div>
            </FieldGroup>

            <FieldGroup
              legend="A one-time expense"
              hint="A single cost on a single day, such as a repair, a purchase or a tax bill."
            >
              <div className="grid gap-3 sm:grid-cols-3">
                <Field label="Amount (USD)">
                  <input
                    type="number"
                    step="0.01"
                    min="0"
                    value={costAmount}
                    onChange={(e) => setCostAmount(e.target.value)}
                    className={inputClass}
                    placeholder="12000"
                  />
                </Field>
                <Field label="On this date">
                  <input
                    type="date"
                    value={costDate}
                    onChange={(e) => setCostDate(e.target.value)}
                    className={inputClass}
                  />
                </Field>
                <Field label="Called">
                  <input
                    type="text"
                    maxLength={80}
                    value={costLabel}
                    onChange={(e) => setCostLabel(e.target.value)}
                    className={inputClass}
                    placeholder="New oven"
                  />
                </Field>
              </div>
            </FieldGroup>

            <FieldGroup
              legend="A delayed expected payment"
              hint="Your bank history does not show unpaid invoices, so the amount and date are yours to state, not ours to find."
            >
              <div className="grid gap-3 sm:grid-cols-3">
                <Field label="Payment you expect (USD)">
                  <input
                    type="number"
                    step="0.01"
                    min="0"
                    value={payAmount}
                    onChange={(e) => setPayAmount(e.target.value)}
                    className={inputClass}
                    placeholder="5000"
                  />
                </Field>
                <Field label="Expected on">
                  <input
                    type="date"
                    value={payDate}
                    onChange={(e) => setPayDate(e.target.value)}
                    className={inputClass}
                  />
                </Field>
                <Field label="Arrives late by (days)">
                  <input
                    type="number"
                    min="0"
                    max="365"
                    value={delay}
                    onChange={(e) => setDelay(e.target.value)}
                    className={inputClass}
                  />
                </Field>
              </div>
            </FieldGroup>

            <div className="flex flex-wrap gap-3">
              <button type="submit" className={buttonClass} disabled={busy}>
                {busy ? "Working it out…" : "Show me"}
              </button>
              {active ? (
                <button
                  type="button"
                  onClick={reset}
                  className={secondaryButtonClass}
                  disabled={busy}
                >
                  Back to as things stand
                </button>
              ) : null}
            </div>
            <p className="text-xs text-ink-muted">
              Trying something out never changes your transactions or your balance. It only
              redraws this chart.
            </p>
          </form>

          {active ? (
            <ScenarioExplainer
              workspaceId={workspaceId}
              token={token}
              scenario={active}
              fingerprint={data.input_fingerprint}
            />
          ) : null}

          <Disclosure
            label="What this projection assumes"
            openLabel="Hide what this projection assumes"
            className="mt-4"
          >
            <ul className="list-disc pl-5">
              {f.assumptions.map((a) => (
                <li key={a.key}>
                  <span className="text-ink">{a.label}</span>: {a.detail}
                  {a.estimated ? " (an estimate)" : ""}
                </li>
              ))}
            </ul>
            <p className="mt-2">
              Every future point on this chart is estimated. Nothing here is a commitment from
              anyone, and a payment you expect is included only because you entered it.
            </p>
          </Disclosure>
        </>
      ) : (
        <p className="mt-3 text-xs text-ink-faint">
          Starts from {money(f.anchor?.amount_minor ?? 0)}, the{" "}
          {f.anchor?.is_live ? "bank-reported" : "latest statement"} balance on{" "}
          {dayLabel(f.anchor?.as_of ?? data.as_of)}
          {data.balances.mixed_dates
            ? ", covering only the accounts reported that day, so it is below the mixed-date total above"
            : ""}
          . Every point ahead is estimated.
        </p>
      )}
    </Card>
  );
}
