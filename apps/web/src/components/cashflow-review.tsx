"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ApiError } from "@/lib/api";
import {
  ALL_CATEGORIES,
  categoriesFor,
  confidenceLabel,
  correctRow,
  downloadClassifiedCsv,
  fetchCashflowRows,
  fetchCashflowSummary,
  runClassification,
  stoppedLabel,
  type CashflowSummary,
  type ClassifiedRow,
  type Confidence,
  type Direction,
  type Filters,
} from "@/lib/cashflow-api";
import { formatDate, formatSigned } from "@/lib/workspace-api";
import {
  buttonClass,
  Card,
  Chip,
  Empty,
  Field,
  inputClass,
  Notice,
  ProgressBar,
  secondaryButtonClass,
  selectClass,
} from "@/components/ui";

const PAGE = 50;

type Props = {
  workspaceId: string;
  importId: string;
  token: string;
  /** The uploaded file's own name, used for the export when the browser hides the header. */
  fileLabel: string;
  /** Rows staged for this import, so the panel can say what there is to do before it runs. */
  rowCount: number;
};

/**
 * Sort every row of an upload into a cash-flow bucket, review the lot, correct what is wrong,
 * and export the result.
 *
 * Two things this screen is careful about, because they are the two a bookkeeper would check.
 *
 * Direction is never editable and never waits on the model. It is arithmetic on the amount, so
 * it is shown from the first render, and the category dropdown offers only the buckets that
 * belong to it. A correction cannot move a payment into a revenue bucket, here or in the API.
 *
 * A long run is made of many bounded calls rather than one long one. Each returns how far it
 * got, this component shows that and calls again, and a run that stops on a spend cap says so
 * and resumes from where it stopped when asked. Nothing is left running when the tab closes.
 */
export function CashflowReview({ workspaceId, importId, token, fileLabel, rowCount }: Props) {
  const [summary, setSummary] = useState<CashflowSummary | null>(null);
  const [rows, setRows] = useState<ClassifiedRow[]>([]);
  const [nextAfter, setNextAfter] = useState<number | null>(null);
  const [matched, setMatched] = useState(0);
  const [filters, setFilters] = useState<Filters>({ direction: "", category: "", confidence: "", search: "" });
  const [search, setSearch] = useState("");
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState<{ classified: number; total: number } | null>(null);
  const [stopped, setStopped] = useState<string | null>(null);
  const [busyRow, setBusyRow] = useState<number | null>(null);
  const [expanded, setExpanded] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  // Set when the component unmounts, so a run in progress stops asking for another batch rather
  // than looping on against a screen nobody is looking at.
  const gone = useRef(false);
  useEffect(() => () => { gone.current = true; }, []);

  // Bumped by anything that changes what the server holds, which is how a finished run or a
  // correction asks for fresh rows without a second copy of the fetch.
  const [generation, setGeneration] = useState(0);
  const reload = useCallback(() => setGeneration((n) => n + 1), []);

  useEffect(() => {
    // `live` guards against a slow answer for old filters landing after a fast one for new
    // filters, which would otherwise show rows the current filters exclude.
    let live = true;
    void (async () => {
      try {
        const [page, totals] = await Promise.all([
          fetchCashflowRows(workspaceId, importId, token, { ...filters, limit: PAGE }),
          fetchCashflowSummary(workspaceId, importId, token),
        ]);
        if (!live) return;
        setRows(page.items);
        setNextAfter(page.next_after);
        setMatched(page.matched);
        setSummary(totals);
      } catch (err) {
        if (live) setError(err instanceof ApiError ? err.describe() : "The classification could not be read.");
      }
    })();
    return () => {
      live = false;
    };
  }, [workspaceId, importId, token, filters, generation]);

  const loadMore = useCallback(async () => {
    if (nextAfter === null) return;
    try {
      const page = await fetchCashflowRows(workspaceId, importId, token, { ...filters, after: nextAfter, limit: PAGE });
      setRows((current) => [...current, ...page.items]);
      setNextAfter(page.next_after);
    } catch (err) {
      setError(err instanceof ApiError ? err.describe() : "More rows could not be read.");
    }
  }, [workspaceId, importId, token, filters, nextAfter]);

  /** Keep asking for another bounded pass until the import is done or something stops it. */
  const classify = useCallback(async () => {
    setRunning(true);
    setError(null);
    setStopped(null);
    try {
      for (;;) {
        const result = await runClassification(workspaceId, importId, token);
        if (gone.current) return;
        setProgress({ classified: result.classified, total: result.total });
        if (result.stopped) {
          setStopped(result.stopped);
          break;
        }
        if (result.done) break;
        // A pass that classified nothing and was not stopped would otherwise spin.
        if (result.batches === 0) break;
      }
      reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.describe() : "The classification could not be started.");
    } finally {
      if (!gone.current) setRunning(false);
    }
  }, [workspaceId, importId, token, reload]);

  const correct = useCallback(
    async (row: ClassifiedRow, category: string) => {
      setBusyRow(row.row_index);
      setError(null);
      try {
        const updated = await correctRow(workspaceId, importId, row.row_index, token, category);
        setRows((current) => current.map((r) => (r.row_index === updated.row_index ? updated : r)));
        setSummary(await fetchCashflowSummary(workspaceId, importId, token));
      } catch (err) {
        setError(err instanceof ApiError ? err.describe() : "That change could not be saved.");
      } finally {
        setBusyRow(null);
      }
    },
    [workspaceId, importId, token],
  );

  const exportCsv = useCallback(async () => {
    setExporting(true);
    setError(null);
    try {
      await downloadClassifiedCsv(workspaceId, importId, token, fileLabel);
    } catch (err) {
      setError(err instanceof ApiError ? err.describe() : "The export could not be downloaded.");
    } finally {
      setExporting(false);
    }
  }, [workspaceId, importId, token, fileLabel]);

  const anyClassified = (summary?.classified ?? 0) > 0;
  const total = summary?.total_rows ?? rowCount;
  const filtered = useMemo(() => Boolean(filters.direction || filters.category || filters.confidence || filters.search), [filters]);

  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="t-section">Cash flow</h2>
          <p className="mt-1 text-sm text-ink-muted">
            Every row sorted into one of eleven buckets. Direction is worked out from the amount, not asked of the model, so it is right before anything is classified.
          </p>
        </div>
        <div className="flex shrink-0 flex-wrap gap-2">
          <button type="button" onClick={classify} className={buttonClass} disabled={running || total === 0}>
            {running ? "Classifying…" : anyClassified && summary?.unclassified === 0 ? "Classify again" : anyClassified ? "Continue" : "Classify transactions"}
          </button>
          <button type="button" onClick={exportCsv} className={secondaryButtonClass} disabled={exporting || !anyClassified}>
            {exporting ? "Preparing…" : "Export CSV"}
          </button>
        </div>
      </div>

      {error ? (
        <div className="mt-3">
          <Notice tone="error">{error}</Notice>
        </div>
      ) : null}

      {running || (progress && !summary?.unclassified) ? (
        <RunProgress classified={progress?.classified ?? summary?.classified ?? 0} total={progress?.total ?? total} running={running} />
      ) : null}

      {stopped ? (
        <div className="mt-3">
          <Notice tone="info">
            {stoppedLabel[stopped] ?? "The run stopped early. Nothing already classified was lost."}{" "}
            {summary && summary.unclassified > 0 ? `${summary.unclassified.toLocaleString()} of ${total.toLocaleString()} rows are still waiting.` : ""}
          </Notice>
        </div>
      ) : null}

      {summary && anyClassified ? <SummaryPanel summary={summary} /> : null}

      {anyClassified ? (
        <>
          <Filterbar
            filters={filters}
            search={search}
            onSearch={setSearch}
            onChange={(next) => {
              setFilters(next);
              setExpanded(null);
            }}
            matched={matched}
            total={total}
            filtered={filtered}
          />
          {rows.length === 0 ? (
            <div className="mt-3">
              <Empty title="No rows match these filters" icon="search">
                Widen the filters to see more of the file.
              </Empty>
            </div>
          ) : (
            <RowTable
              rows={rows}
              expanded={expanded}
              busyRow={busyRow}
              onToggle={(index) => setExpanded((current) => (current === index ? null : index))}
              onCorrect={correct}
            />
          )}
          {nextAfter !== null ? (
            <div className="mt-3 flex justify-center">
              <button type="button" onClick={loadMore} className={secondaryButtonClass}>
                Show more ({rows.length.toLocaleString()} of {matched.toLocaleString()})
              </button>
            </div>
          ) : rows.length > 0 ? (
            <p className="mt-3 text-center text-xs text-ink-faint">
              All {matched.toLocaleString()} matching {matched === 1 ? "row" : "rows"} shown.
            </p>
          ) : null}
        </>
      ) : !running ? (
        <div className="mt-3">
          <Empty title="Not classified yet" icon="layers">
            {total.toLocaleString()} {total === 1 ? "row is" : "rows are"} ready. Classifying sends the date, name, merchant and account of each one to the model. The file&rsquo;s own category column is never sent, and the direction is worked out here rather than asked for.
          </Empty>
        </div>
      ) : null}
    </Card>
  );
}

function RunProgress({ classified, total, running }: { classified: number; total: number; running: boolean }) {
  const percent = total > 0 ? Math.round((classified / total) * 100) : 0;
  return (
    <div className="mt-4">
      <div className="flex items-baseline justify-between text-xs">
        <span className="font-medium text-ink">
          {classified.toLocaleString()} of {total.toLocaleString()} classified
        </span>
        <span className="text-ink-muted">{percent}%</span>
      </div>
      <ProgressBar value={classified} max={total} label="Rows classified" className="mt-1.5" />
      {running ? <p className="mt-1.5 text-xs text-ink-faint">Working through the file in batches. You can leave this page and come back; what is done stays done.</p> : null}
    </div>
  );
}

function SummaryPanel({ summary }: { summary: CashflowSummary }) {
  const inflow = summary.buckets.filter((b) => b.direction === "inflow");
  const outflow = summary.buckets.filter((b) => b.direction === "outflow");
  return (
    <div className="mt-4 grid gap-3">
      <dl className="grid grid-cols-2 gap-x-4 gap-y-3 rounded-xl border border-line bg-surface-soft p-4 sm:grid-cols-4">
        <Figure label="Money in" value={formatSigned(summary.inflow_minor)} note={`${summary.inflow_count.toLocaleString()} ${summary.inflow_count === 1 ? "row" : "rows"}`} tone="up" />
        <Figure label="Money out" value={formatSigned(-summary.outflow_minor)} note={`${summary.outflow_count.toLocaleString()} ${summary.outflow_count === 1 ? "row" : "rows"}`} tone="down" />
        <Figure label="Net" value={formatSigned(summary.net_minor)} note="Across the whole file" tone={summary.net_minor < 0 ? "down" : "up"} />
        <Figure
          label="Reviewed"
          value={`${summary.classified.toLocaleString()} / ${summary.total_rows.toLocaleString()}`}
          note={summary.manually_reviewed > 0 ? `${summary.manually_reviewed.toLocaleString()} corrected by hand` : summary.unclassified > 0 ? `${summary.unclassified.toLocaleString()} still to do` : "Every row classified"}
        />
      </dl>
      <div className="grid gap-3 sm:grid-cols-2">
        <BucketList title="Money in" buckets={inflow} sign={1} />
        <BucketList title="Money out" buckets={outflow} sign={-1} />
      </div>
      <p className="text-xs text-ink-faint">
        Confidence: {summary.by_confidence.high.toLocaleString()} high, {summary.by_confidence.medium.toLocaleString()} medium, {summary.by_confidence.low.toLocaleString()} low. A low-confidence row is the model saying it was choosing between two readings, not that it is wrong.
      </p>
    </div>
  );
}

function Figure({ label, value, note, tone }: { label: string; value: string; note?: string; tone?: "up" | "down" }) {
  const colour = tone === "up" ? "text-up" : tone === "down" ? "text-down" : "text-ink";
  return (
    <div className="min-w-0">
      <dt className="text-xs font-medium text-ink-muted">{label}</dt>
      <dd className={`t-figure mt-0.5 text-lg ${colour}`}>{value}</dd>
      {note ? <p className="mt-0.5 text-[11px] text-ink-faint">{note}</p> : null}
    </div>
  );
}

function BucketList({ title, buckets, sign }: { title: string; buckets: { category: string; count: number; total_minor: number }[]; sign: 1 | -1 }) {
  if (buckets.length === 0) return null;
  return (
    <div className="rounded-xl border border-line p-3">
      <h3 className="text-xs font-semibold text-ink-muted">{title}</h3>
      <ul className="mt-2 grid gap-1">
        {buckets.map((bucket) => (
          <li key={bucket.category} className="flex items-baseline justify-between gap-3 text-sm">
            <span className="min-w-0 truncate text-ink">{bucket.category}</span>
            <span className="shrink-0 tabular-nums text-ink-muted">
              <span className="text-[11px]">{bucket.count.toLocaleString()}</span>
              <span className="ml-2 text-ink">{formatSigned(sign * bucket.total_minor)}</span>
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Filterbar({
  filters,
  search,
  onSearch,
  onChange,
  matched,
  total,
  filtered,
}: {
  filters: Filters;
  search: string;
  onSearch: (value: string) => void;
  onChange: (next: Filters) => void;
  matched: number;
  total: number;
  filtered: boolean;
}) {
  // The category list narrows with the direction, so the two filters cannot contradict each other.
  const categories = filters.direction ? categoriesFor(filters.direction as Direction) : ALL_CATEGORIES;
  return (
    <div className="mt-5 grid gap-2 border-t border-line pt-4 sm:grid-cols-[repeat(3,minmax(0,1fr))_1.4fr_auto] sm:items-end">
      <Field label="Direction">
        <select
          value={filters.direction ?? ""}
          onChange={(ev) => {
            const direction = ev.target.value as Direction | "";
            const stillValid = !filters.category || !direction || categoriesFor(direction).includes(filters.category);
            onChange({ ...filters, direction, category: stillValid ? filters.category : "" });
          }}
          className={selectClass}
        >
          <option value="">Both</option>
          <option value="inflow">Money in</option>
          <option value="outflow">Money out</option>
        </select>
      </Field>
      <Field label="Category">
        <select value={filters.category ?? ""} onChange={(ev) => onChange({ ...filters, category: ev.target.value })} className={selectClass}>
          <option value="">All</option>
          {categories.map((category) => (
            <option key={category} value={category}>
              {category}
            </option>
          ))}
        </select>
      </Field>
      <Field label="Confidence">
        <select value={filters.confidence ?? ""} onChange={(ev) => onChange({ ...filters, confidence: ev.target.value as Confidence | "" })} className={selectClass}>
          <option value="">Any</option>
          <option value="high">High</option>
          <option value="medium">Medium</option>
          <option value="low">Low</option>
        </select>
      </Field>
      <Field label="Search">
        <form
          onSubmit={(ev) => {
            ev.preventDefault();
            onChange({ ...filters, search });
          }}
        >
          <input
            type="search"
            value={search}
            onChange={(ev) => {
              onSearch(ev.target.value);
              if (ev.target.value === "") onChange({ ...filters, search: "" });
            }}
            placeholder="Name, merchant or id"
            maxLength={120}
            className={inputClass}
            aria-label="Search transactions"
          />
        </form>
      </Field>
      <div className="flex items-center gap-2 pb-1 text-xs text-ink-muted sm:pb-2">
        <span className="tabular-nums">
          {matched.toLocaleString()}
          {filtered ? ` of ${total.toLocaleString()}` : ""}
        </span>
        {filtered ? (
          <button
            type="button"
            onClick={() => {
              onSearch("");
              onChange({ direction: "", category: "", confidence: "", search: "" });
            }}
            className="font-semibold text-plum-700 hover:text-plum-600"
          >
            Clear
          </button>
        ) : null}
      </div>
    </div>
  );
}

function RowTable({
  rows,
  expanded,
  busyRow,
  onToggle,
  onCorrect,
}: {
  rows: ClassifiedRow[];
  expanded: number | null;
  busyRow: number | null;
  onToggle: (rowIndex: number) => void;
  onCorrect: (row: ClassifiedRow, category: string) => void;
}) {
  return (
    <div className="mt-3 overflow-x-auto">
      <table className="w-full min-w-[46rem] border-collapse text-sm">
        <thead>
          <tr className="border-b border-line text-left text-xs text-ink-muted">
            <th scope="col" className="py-2 pr-3 font-medium">Date</th>
            <th scope="col" className="py-2 pr-3 font-medium">Transaction</th>
            <th scope="col" className="py-2 pr-3 font-medium">Merchant</th>
            <th scope="col" className="py-2 pr-3 text-right font-medium">Amount</th>
            <th scope="col" className="py-2 pr-3 font-medium">Direction</th>
            <th scope="col" className="py-2 pr-3 font-medium">Category</th>
            <th scope="col" className="py-2 pr-3 font-medium">Confidence</th>
            <th scope="col" className="py-2 font-medium"><span className="sr-only">Reason</span></th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <Row
              key={row.row_index}
              row={row}
              open={expanded === row.row_index}
              busy={busyRow === row.row_index}
              onToggle={() => onToggle(row.row_index)}
              onCorrect={(category) => onCorrect(row, category)}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Row({ row, open, busy, onToggle, onCorrect }: { row: ClassifiedRow; open: boolean; busy: boolean; onToggle: () => void; onCorrect: (category: string) => void }) {
  const direction = row.direction ?? "outflow";
  const allowed = categoriesFor(direction);
  return (
    <>
      <tr className="border-b border-line align-top">
        <td className="py-2.5 pr-3 whitespace-nowrap text-ink-muted">{row.date ? formatDate(row.date) : "—"}</td>
        <td className="max-w-[16rem] py-2.5 pr-3">
          <span className="block truncate text-ink" title={row.name ?? undefined}>{row.name ?? "—"}</span>
        </td>
        <td className="max-w-[10rem] py-2.5 pr-3">
          <span className="block truncate text-ink-muted" title={row.merchant ?? undefined}>{row.merchant ?? "—"}</span>
        </td>
        <td className={`py-2.5 pr-3 text-right tabular-nums whitespace-nowrap ${row.amount_minor < 0 ? "text-down" : "text-up"}`}>{formatSigned(row.amount_minor)}</td>
        <td className="py-2.5 pr-3">
          <Chip tone={direction === "inflow" ? "up" : "down"}>{direction === "inflow" ? "In" : "Out"}</Chip>
        </td>
        <td className="py-2.5 pr-3">
          {row.category ? (
            <label className="block">
              <span className="sr-only">Category for {row.name ?? `row ${row.row_index + 1}`}</span>
              <select
                value={row.category}
                onChange={(ev) => onCorrect(ev.target.value)}
                disabled={busy}
                className={`${selectClass} min-w-[11rem] py-1 text-xs`}
              >
                {/* Only this row's own direction, so a correction cannot contradict the arithmetic. */}
                {allowed.map((category) => (
                  <option key={category} value={category}>
                    {category}
                  </option>
                ))}
              </select>
            </label>
          ) : (
            <span className="text-xs text-ink-faint">Not classified</span>
          )}
        </td>
        <td className="py-2.5 pr-3 whitespace-nowrap">
          {row.source === "manual" ? (
            <Chip tone="accent" icon="check">Reviewed</Chip>
          ) : row.confidence ? (
            <Chip tone={row.confidence === "low" ? "warn" : "muted"}>{confidenceLabel[row.confidence]}</Chip>
          ) : (
            <span className="text-xs text-ink-faint">—</span>
          )}
        </td>
        <td className="py-2.5 text-right">
          {row.reason ? (
            <button type="button" onClick={onToggle} aria-expanded={open} className="text-xs font-semibold text-plum-700 hover:text-plum-600">
              {open ? "Hide" : "Why"}
            </button>
          ) : null}
        </td>
      </tr>
      {open && row.reason ? (
        <tr className="border-b border-line">
          <td colSpan={8} className="bg-surface-soft px-3 py-2.5 text-xs leading-relaxed text-ink-muted">
            <span className="font-medium text-ink">{row.source === "manual" ? "Set by you." : "Why the model chose this:"}</span> {row.reason}
            <span className="ml-2 text-ink-faint">Transaction {row.transaction_id}</span>
          </td>
        </tr>
      ) : null}
    </>
  );
}
