"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, useCallback, useEffect, useState } from "react";

import { ApiError } from "@/lib/api";
import {
  cancelImport,
  confirmImport,
  decideRows,
  fetchImport,
  fetchImportRows,
  flagLabel,
  mappingFlagLabel,
  markUploaded,
  patchRow,
  reconciliationProblemLabel,
  setMapping,
  statusLabel,
  type ImportDetail,
  type ImportRow,
  type Mapping,
  type MappingColumns,
} from "@/lib/imports-api";
import { accountTypeLabel, formatDate, formatSigned, formatTimestamp, readable } from "@/lib/workspace-api";
import { CashflowReview } from "@/components/cashflow-review";
import {
  buttonClass,
  Card,
  Field,
  inputClass,
  Notice,
  secondaryButtonClass,
} from "@/components/ui";

type Props = {
  workspaceId: string;
  importId: string;
  token: string;
  businessName?: string;
  backHref?: string;
};

const DATE_FORMATS: [string, string][] = [
  ["iso", "2026-01-31 (year-month-day)"],
  ["iso_slash", "2026/01/31"],
  ["mdy", "01/31/2026 (month/day/year)"],
  ["dmy", "31/01/2026 (day/month/year)"],
  ["mdy_dash", "01-31-2026"],
  ["dmy_dash", "31-01-2026"],
  ["mdy_short", "01/31/26"],
  ["dmy_short", "31/01/26"],
  ["dmon_y", "31 Jan 2026"],
  ["mon_d_y", "Jan 31, 2026"],
  ["mon_d_y_nocomma", "Jan 31 2026"],
  ["compact", "20260131"],
];

/** Mapping, totals, warnings and row-by-row review for one staged import; confirmation is explicit. */
export function ImportReview({
  workspaceId,
  importId,
  token,
  businessName,
  backHref = "/workspace",
}: Props) {
  const router = useRouter();
  const [detail, setDetail] = useState<ImportDetail | null>(null);
  const [rows, setRows] = useState<ImportRow[]>([]);
  const [nextAfter, setNextAfter] = useState<number | null>(null);
  const [only, setOnly] = useState<"all" | "flagged">("all");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [mappingDraft, setMappingDraft] = useState<{
    columns: MappingColumns;
    date_format: string;
    sign_convention: Mapping["sign_convention"];
  } | null>(null);
  const [done, setDone] = useState<{
    created: number;
    linked: number;
    replaced: number;
    excluded: number;
  } | null>(null);
  // Bumped after a mapping change so the effect below re-reads the import and its rows.
  const [version, setVersion] = useState(0);

  useEffect(() => {
    let active = true;
    Promise.all([
      fetchImport(workspaceId, importId, token),
      fetchImportRows(workspaceId, importId, token, { only }),
    ])
      .then(([d, page]) => {
        if (!active) return;
        setDetail(d);
        setRows(page.items);
        setNextAfter(page.next_after);
        if (d.mapping)
          setMappingDraft({
            columns: { ...d.mapping.columns },
            date_format: d.mapping.date_format,
            sign_convention: d.mapping.sign_convention,
          });
      })
      .catch((caught: unknown) => {
        if (active)
          setError(
            caught instanceof ApiError
              ? caught.message
              : "Could not load this import.",
          );
      });
    return () => {
      active = false;
    };
  }, [workspaceId, importId, token, only, version]);

  const reload = useCallback(() => setVersion((v) => v + 1), []);

  async function run(label: string, action: () => Promise<void>) {
    setBusy(label);
    setError(null);
    try {
      await action();
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? describe(caught)
          : caught instanceof Error
            ? caught.message
            : "Something went wrong.",
      );
    } finally {
      setBusy(null);
    }
  }

  async function loadMore() {
    if (nextAfter === null) return;
    await run("more", async () => {
      const page = await fetchImportRows(workspaceId, importId, token, {
        only,
        after: nextAfter,
      });
      setRows((current) => [...current, ...page.items]);
      setNextAfter(page.next_after);
    });
  }

  async function applyMapping(event: FormEvent) {
    event.preventDefault();
    if (!mappingDraft) return;
    await run("mapping", async () => {
      await setMapping(workspaceId, importId, token, mappingDraft);
      reload();
    });
  }

  async function updateRow(
    rowIndex: number,
    patch: Parameters<typeof patchRow>[4],
  ) {
    await run(`row-${rowIndex}`, async () => {
      const updated = await patchRow(
        workspaceId,
        importId,
        rowIndex,
        token,
        patch,
      );
      setRows((current) =>
        current.map((r) => (r.row_index === rowIndex ? updated : r)),
      );
      const d = await fetchImport(workspaceId, importId, token);
      setDetail(d);
    });
  }

  async function confirm() {
    if (!detail) return;
    await run("confirm", async () => {
      const result = await confirmImport(
        workspaceId,
        importId,
        token,
        detail.draft_revision,
      );
      setDone({
        created: result.created,
        linked: result.linked,
        replaced: result.replaced,
        excluded: result.excluded,
      });
      setDetail((current) =>
        current ? { ...current, ...result.import } : current,
      );
    });
  }

  async function cancel() {
    await run("cancel", async () => {
      await cancelImport(workspaceId, importId, token);
      router.push(backHref);
    });
  }

  async function decideAll(
    decision: "keep" | "link_existing" | "exclude",
    scope: "undecided" | "candidates" | "conflicts" = "undecided",
  ) {
    await run("decide", async () => {
      await decideRows(workspaceId, importId, token, decision, scope);
      reload();
    });
  }

  async function retryReading() {
    await run("retry", async () => {
      const staged = await markUploaded(workspaceId, importId, token);
      if (staged.duplicate_of) {
        router.push(`/workspace/imports/${staged.duplicate_of}?duplicate=1`);
        return;
      }
      reload();
    });
  }

  if (error && !detail) return <Notice tone="error">{error}</Notice>;
  if (!detail) return <p className="text-ink-muted">Loading import…</p>;
  const stats = detail.summary;
  const editable = detail.status === "staged";
  const blockingCount = stats?.blocking_rows ?? 0;
  const stillBlocking = rows.filter((r) => r.blocking).length;
  const undecided = rows.filter(
    (r) =>
      r.needs_decision && r.decision_by !== "owner" && r.decision !== "exclude",
  ).length;
  const undecidedTotal = stats?.overlap?.undecided_rows ?? 0;

  return (
    <div className="flex flex-col gap-6">
      <div>
        <p className="text-sm font-medium uppercase tracking-wide text-plum-600">
          Statement import · {statusLabel[detail.status]}
        </p>
        <h1 className="text-3xl font-semibold tracking-tight text-ink">
          {detail.file_label}
        </h1>
        <p className="text-ink-muted">
          {businessName ? `${businessName} · ` : ""}
          {detail.account
            ? `${detail.account.name}${detail.account.mask ? ` ···${detail.account.mask}` : ""}`
            : detail.proposed_account
              ? `New account: ${detail.proposed_account.name} (${readable(accountTypeLabel, detail.proposed_account.account_type)})`
              : "No account"}
          {detail.byte_size
            ? ` · ${Math.round(detail.byte_size / 1024)} KB`
            : ""}
          {detail.parser_version ? ` · parser ${detail.parser_version}` : ""}
        </p>
      </div>

      {error ? <Notice tone="error">{error}</Notice> : null}
      {detail.status === "awaiting_upload" ? (
        <Notice tone="info">
          The file is uploaded but has not been read yet. Read it now, or cancel
          and upload again. Nothing has been added to your transactions.
        </Notice>
      ) : null}
      {detail.status === "failed" ? (
        <Notice tone="error">
          This file could not be imported:{" "}
          {detail.error_message ?? detail.error_code}. Nothing was added.
        </Notice>
      ) : null}
      {detail.status === "cancelled" ? (
        <Notice tone="info">
          This import was cancelled
          {detail.error_code === "duplicate_file"
            ? " because the same file was already imported"
            : ""}
          . Nothing was added.
        </Notice>
      ) : null}
      {done ? (
        <Notice tone="success">
          Import confirmed: {done.created} new transaction
          {done.created === 1 ? "" : "s"}
          {done.linked ? `, ${done.linked} linked to existing` : ""}
          {done.replaced ? `, ${done.replaced} replaced` : ""}
          {done.excluded ? `, ${done.excluded} excluded` : ""}.{" "}
          <Link href={backHref} className="underline">
            See them in your workspace.
          </Link>
        </Notice>
      ) : detail.status === "confirmed" ? (
        <Notice tone="success">
          Confirmed{" "}
          {detail.confirmed_at ? formatTimestamp(detail.confirmed_at) : ""}:{" "}
          {stats?.result?.created ?? 0} new transactions.{" "}
          <Link href={backHref} className="underline">
            Open your workspace.
          </Link>
        </Notice>
      ) : null}

      {stats?.statement &&
      (stats.statement.opening_minor !== undefined ||
        stats.statement.period_start) ? (
        <Card>
          <h2 className="t-section">
            What the statement itself says
          </h2>
          <dl className="mt-3 grid gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
            {stats.statement.period_start && stats.statement.period_end ? (
              <>
                <dt className="text-ink-muted">Printed period</dt>
                <dd className="text-ink">
                  {formatDate(stats.statement.period_start)} to{" "}
                  {formatDate(stats.statement.period_end)}
                </dd>
              </>
            ) : null}
            {typeof stats.statement.opening_minor === "number" ? (
              <>
                <dt className="text-ink-muted">Printed opening balance</dt>
                <dd className="text-ink">
                  {formatSigned(stats.statement.opening_minor)}
                </dd>
              </>
            ) : null}
            {typeof stats.statement.closing_minor === "number" ? (
              <>
                <dt className="text-ink-muted">Printed closing balance</dt>
                <dd className="text-ink">
                  {formatSigned(stats.statement.closing_minor)}
                </dd>
              </>
            ) : null}
            {typeof stats.statement.transaction_count === "number" ? (
              <>
                <dt className="text-ink-muted">Printed transaction count</dt>
                <dd className="text-ink">
                  {stats.statement.transaction_count}
                </dd>
              </>
            ) : null}
            {stats.statement.account_last4 ? (
              <>
                <dt className="text-ink-muted">Account digits printed</dt>
                <dd className="text-ink">···{stats.statement.account_last4}</dd>
              </>
            ) : null}
            {stats.statement.page_count ? (
              <>
                <dt className="text-ink-muted">Pages</dt>
                <dd className="text-ink">{stats.statement.page_count}</dd>
              </>
            ) : null}
          </dl>
          <p className="mt-2 text-xs text-ink-muted">
            Historical figures as printed on the statement; never today&apos;s
            available cash.
          </p>
        </Card>
      ) : null}

      {stats?.reconciliation ? (
        <Notice
          tone={
            stats.reconciliation.status === "matched"
              ? "success"
              : stats.reconciliation.status === "mismatch"
                ? "error"
                : "info"
          }
        >
          {stats.reconciliation.status === "matched"
            ? `Reconciles: the rows bridge the ${stats.reconciliation.source === "statement_totals" ? "statement's printed balances" : "running balance column"}.`
            : stats.reconciliation.status === "mismatch"
              ? `Does not reconcile: ${stats.reconciliation.problems.map((p) => reconciliationProblemLabel[p] ?? p).join("; ")}. Check for missing or misread rows before confirming; nothing is invented to make it balance.`
              : "Reconciliation unavailable: the file carries no balances or totals to check the rows against."}
        </Notice>
      ) : null}

      {stats?.overlap?.likely_duplicate_statement && editable ? (
        <Notice tone="info">
          Every row of this file matches a transaction already in this account
          for the same period, so it is probably the same statement in another
          format. Nothing has been linked or dropped: choose below whether to
          link all matched rows to the existing transactions (adds this file as
          evidence, adds no transactions) or keep them as new.
        </Notice>
      ) : null}

      {stats ? (
        <Card>
          <h2 className="t-section">
            Totals as read from the file
          </h2>
          <dl className="mt-3 grid gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
            <dt className="text-ink-muted">Rows</dt>
            <dd className="text-ink">
              {stats.row_count} ({blockingCount} need attention)
            </dd>
            <dt className="text-ink-muted">Statement period</dt>
            <dd className="text-ink">
              {stats.period_start && stats.period_end
                ? `${formatDate(stats.period_start)} to ${formatDate(stats.period_end)}`
                : "unknown"}
            </dd>
            <dt className="text-ink-muted">Money in</dt>
            <dd className="text-emerald-800">
              {formatSigned(stats.credits_minor)}
            </dd>
            <dt className="text-ink-muted">Money out</dt>
            <dd className="text-ink">{formatSigned(-stats.debits_minor)}</dd>
            <dt className="text-ink-muted">Net</dt>
            <dd className="text-ink">{formatSigned(stats.net_minor)}</dd>
            <dt className="text-ink-muted">Statement balances</dt>
            <dd className="text-ink">
              {stats.balance ? (
                <>
                  opening{" "}
                  {stats.balance.opening_minor !== null
                    ? formatSigned(stats.balance.opening_minor)
                    : "unknown"}{" "}
                  · closing {formatSigned(stats.balance.closing_minor)} as of{" "}
                  {formatDate(stats.balance.period_end)}
                  {stats.balance.reconciles
                    ? " · reconciles"
                    : ` · does not reconcile (${stats.balance.breaks} break${stats.balance.breaks === 1 ? "" : "s"})`}
                  <span className="block text-xs text-ink-muted">
                    Historical statement figures, not today&apos;s available
                    cash.
                  </span>
                </>
              ) : (
                "none in this file (no running balance column)"
              )}
            </dd>
            {typeof stats.existing_in_period === "number" &&
            stats.existing_in_period > 0 ? (
              <>
                <dt className="text-ink-muted">Overlap</dt>
                <dd className="text-amber-900">
                  {stats.existing_in_period} existing transaction
                  {stats.existing_in_period === 1 ? "" : "s"} already fall
                  inside this period. Review the flagged rows below.
                </dd>
              </>
            ) : null}
          </dl>
          {Object.keys(stats.flag_counts).length > 0 ? (
            <ul className="mt-3 flex flex-wrap gap-2 text-xs">
              {Object.entries(stats.flag_counts).map(([flag, count]) => (
                <li
                  key={flag}
                  className="rounded-full bg-amber-100 px-2 py-0.5 text-amber-900"
                >
                  {count} × {flagLabel[flag] ?? flag}
                </li>
              ))}
            </ul>
          ) : null}
        </Card>
      ) : null}

      {editable && detail.mapping && mappingDraft ? (
        <Card>
          <h2 className="t-section">
            Column mapping
          </h2>
          {detail.mapping.flags.map((flag) => (
            <p key={flag} className="mt-2 text-sm text-amber-900">
              {mappingFlagLabel[flag] ?? flag}
            </p>
          ))}
          <form
            onSubmit={applyMapping}
            className="mt-3 grid gap-3 sm:grid-cols-3"
          >
            {(
              [
                "date",
                "description",
                "amount",
                "debit",
                "credit",
                "balance",
                "reference",
              ] as const
            ).map((role) => (
              <Field
                key={role}
                label={
                  role === "reference"
                    ? "Source reference (optional)"
                    : role === "balance"
                      ? "Running balance (optional)"
                      : role[0].toUpperCase() + role.slice(1)
                }
              >
                <select
                  value={mappingDraft.columns[role] ?? ""}
                  onChange={(e) =>
                    setMappingDraft({
                      ...mappingDraft,
                      columns: {
                        ...mappingDraft.columns,
                        [role]: e.target.value || null,
                      },
                    })
                  }
                  className={inputClass}
                >
                  <option value="">Not mapped</option>
                  {detail.columns.map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                </select>
              </Field>
            ))}
            <Field label="Date format">
              <select
                value={mappingDraft.date_format}
                onChange={(e) =>
                  setMappingDraft({
                    ...mappingDraft,
                    date_format: e.target.value,
                  })
                }
                className={inputClass}
              >
                {DATE_FORMATS.map(([key, label]) => (
                  <option key={key} value={key}>
                    {label}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Amount sign">
              <select
                value={mappingDraft.sign_convention}
                onChange={(e) =>
                  setMappingDraft({
                    ...mappingDraft,
                    sign_convention: e.target
                      .value as Mapping["sign_convention"],
                  })
                }
                className={inputClass}
              >
                <option value="signed_inflow_positive">
                  One amount column: positive = money in
                </option>
                <option value="signed_outflow_positive">
                  One amount column: positive = money out
                </option>
                <option value="debit_credit">
                  Separate debit and credit columns
                </option>
              </select>
            </Field>
            <div className="flex items-end">
              <button
                type="submit"
                className={secondaryButtonClass}
                disabled={busy !== null}
              >
                {busy === "mapping"
                  ? "Re-reading…"
                  : "Apply mapping and re-read rows"}
              </button>
            </div>
          </form>
          {detail.sample.length > 0 ? (
            <div className="mt-3 overflow-x-auto">
              <table className="w-full text-xs">
                <caption className="sr-only">First rows of the file</caption>
                <thead>
                  <tr className="text-left text-ink-muted">
                    {detail.columns.map((c) => (
                      <th key={c} scope="col" className="py-1 pr-3">
                        {c}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {detail.sample.map((row, i) => (
                    <tr
                      key={i}
                      className="border-t border-lavender-100 text-ink"
                    >
                      {detail.columns.map((c) => (
                        <td key={c} className="py-1 pr-3 whitespace-nowrap">
                          {row[c]}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
        </Card>
      ) : null}

      {detail.status !== "awaiting_upload" ? (
        <Card>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 className="t-section">Rows</h2>
            <label className="text-sm text-ink">
              Show{" "}
              <select
                value={only}
                onChange={(e) => setOnly(e.target.value as "all" | "flagged")}
                className={inputClass}
              >
                <option value="all">all rows</option>
                <option value="flagged">flagged rows only</option>
              </select>
            </label>
          </div>
          {rows.length === 0 ? (
            <p className="mt-3 text-sm text-ink-muted">
              {only === "flagged" ? "No flagged rows." : "No rows."}
            </p>
          ) : null}
          <ul className="mt-3 divide-y divide-lavender-100">
            {rows.map((row) => (
              <RowItem
                key={row.row_index}
                row={row}
                editable={editable}
                busy={busy === `row-${row.row_index}`}
                onPatch={(patch) => updateRow(row.row_index, patch)}
              />
            ))}
          </ul>
          {nextAfter !== null ? (
            <button
              type="button"
              onClick={loadMore}
              className={`${secondaryButtonClass} mt-3`}
              disabled={busy !== null}
            >
              Load more rows
            </button>
          ) : null}
        </Card>
      ) : null}

      {detail.status === "awaiting_upload" || detail.status === "failed" ? (
        <Card>
          <div className="flex flex-wrap gap-3">
            {detail.status === "awaiting_upload" ? (
              <button
                type="button"
                onClick={retryReading}
                className={buttonClass}
                disabled={busy !== null}
              >
                {busy === "retry" ? "Reading…" : "Read the uploaded file"}
              </button>
            ) : null}
            <button
              type="button"
              onClick={cancel}
              className={secondaryButtonClass}
              disabled={busy !== null}
            >
              {busy === "cancel" ? "Cancelling…" : "Cancel import"}
            </button>
          </div>
        </Card>
      ) : null}

      {/* Cash-flow buckets sit between reviewing the rows and confirming them, because that is
          where they are useful: the owner has seen what came out of the file and can now sort it,
          correct it and export it, whether or not these rows ever become transactions. */}
      {detail.status === "staged" || detail.status === "confirmed" ? (
        <CashflowReview
          workspaceId={workspaceId}
          importId={importId}
          token={token}
          fileLabel={detail.file_label}
          rowCount={stats?.row_count ?? rows.length}
        />
      ) : null}

      {editable ? (
        <Card>
          <h2 className="t-section">Confirm</h2>
          <p className="mt-2 text-sm text-ink">
            Confirming adds the kept rows to your transactions in one step.
            Nothing is added until then, and a failed confirmation adds nothing.
            Rows still needing attention:{" "}
            {stillBlocking > 0 ? stillBlocking : blockingCount}
            {undecidedTotal > 0 || undecided > 0
              ? `, of which ${Math.max(undecided, undecidedTotal)} need your decision (keep as new or link to the existing transaction).`
              : "."}
          </p>
          {undecidedTotal > 0 || undecided > 0 ? (
            <div className="mt-3 flex flex-wrap gap-3">
              <button
                type="button"
                onClick={() => decideAll("link_existing")}
                className={secondaryButtonClass}
                disabled={busy !== null}
              >
                Link all matched rows to existing transactions
              </button>
              <button
                type="button"
                onClick={() => decideAll("keep", "candidates")}
                className={secondaryButtonClass}
                disabled={busy !== null}
              >
                Keep all look-alike rows as new transactions
              </button>
            </div>
          ) : null}
          <div className="mt-3 flex flex-wrap gap-3">
            <button
              type="button"
              onClick={confirm}
              className={buttonClass}
              disabled={busy !== null || blockingCount > 0 || stillBlocking > 0}
            >
              {busy === "confirm" ? "Confirming…" : "Confirm import"}
            </button>
            <button
              type="button"
              onClick={cancel}
              className={secondaryButtonClass}
              disabled={busy !== null}
            >
              {busy === "cancel" ? "Cancelling…" : "Cancel import"}
            </button>
          </div>
        </Card>
      ) : null}
    </div>
  );
}

function describe(error: ApiError): string {
  return error.describe();
}

function RowItem({
  row,
  editable,
  busy,
  onPatch,
}: {
  row: ImportRow;
  editable: boolean;
  busy: boolean;
  onPatch: (patch: Parameters<typeof patchRow>[4]) => void;
}) {
  const [editing, setEditing] = useState(false);
  // A row that carries a reference the account already holds cannot become a second
  // transaction: the database refuses it, so "keep as new" is not offered for it.
  const keepAllowed = !row.flags.some(
    (f) => f === "conflicting_revision" || f === "already_imported",
  );
  const [date, setDate] = useState(row.effective.posted_date ?? "");
  const [amount, setAmount] = useState(
    typeof row.effective.amount_minor === "number"
      ? (row.effective.amount_minor / 100).toFixed(2)
      : "",
  );
  const [description, setDescription] = useState(
    row.effective.description ?? "",
  );
  const e = row.effective;

  function save(event: FormEvent) {
    event.preventDefault();
    const patch: Parameters<typeof patchRow>[4] = {};
    if (date && date !== row.effective.posted_date) patch.posted_date = date;
    const cents = Math.round(Number(amount) * 100);
    if (
      amount !== "" &&
      Number.isFinite(cents) &&
      cents !== row.effective.amount_minor
    )
      patch.amount_minor = cents;
    if (description.trim() && description.trim() !== row.effective.description)
      patch.description = description.trim();
    if (Object.keys(patch).length > 0) onPatch(patch);
    setEditing(false);
  }

  return (
    <li className={`py-3 text-sm ${row.blocking ? "bg-amber-50" : ""}`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <p className="text-ink">
            <span className="text-ink-muted">
              #{row.row_index + 1} · {row.source_locator} ·{" "}
            </span>
            {row.needs_decision &&
            row.decision_by !== "owner" &&
            row.decision !== "exclude" ? (
              <span className="rounded-full bg-amber-100 px-2 py-0.5 text-xs text-amber-900">
                decision needed
              </span>
            ) : null}{" "}
            {e.posted_date ? (
              formatDate(e.posted_date)
            ) : (
              <span className="text-amber-900">no date</span>
            )}{" "}
            ·{" "}
            {e.description ?? (
              <span className="text-amber-900">no description</span>
            )}{" "}
            ·{" "}
            <span
              className={
                typeof e.amount_minor === "number" && e.amount_minor < 0
                  ? "text-ink"
                  : "text-emerald-800"
              }
            >
              {typeof e.amount_minor === "number" ? (
                formatSigned(e.amount_minor)
              ) : (
                <span className="text-amber-900">no amount</span>
              )}
            </span>
            {typeof e.balance_minor === "number" ? (
              <span className="text-ink-muted">
                {" "}
                · balance {formatSigned(e.balance_minor)}
              </span>
            ) : null}
            {e.external_ref ? (
              <span className="text-ink-muted"> · ref {e.external_ref}</span>
            ) : null}
          </p>
          {row.flags.length > 0 ? (
            <p className="mt-1 text-xs text-amber-900">
              {row.flags.map((f) => flagLabel[f] ?? f).join(" · ")}
              {row.conflict
                ? `. Existing: ${formatDate(row.conflict.posted_date)} ${row.conflict.description} ${formatSigned(row.conflict.amount_minor)}${row.conflict.other_candidates ? ` (+${row.conflict.other_candidates} more like it)` : ""}`
                : ""}
            </p>
          ) : null}
          {row.reviewed ? (
            <p className="mt-1 text-xs text-ink-muted">
              Corrected by you; original kept for provenance.
            </p>
          ) : null}
          {row.decision_by === "owner" ? (
            <p className="mt-1 text-xs text-ink-muted">
              Decision made by you: {row.decision.replace("_", " ")}.
            </p>
          ) : null}
        </div>
        {editable ? (
          <div className="flex flex-wrap items-center gap-2">
            <select
              value={row.decision}
              onChange={(ev) =>
                onPatch({
                  decision: ev.target.value as ImportRow["decision"],
                  ...(row.linked_transaction_id || row.conflict
                    ? {
                        linked_transaction_id:
                          row.linked_transaction_id ??
                          row.conflict?.transaction_id,
                      }
                    : {}),
                })
              }
              className={inputClass}
              disabled={busy}
              aria-label={`Decision for row ${row.row_index + 1}`}
            >
              {keepAllowed ? <option value="keep">Keep as new</option> : null}
              <option value="exclude">Exclude</option>
              {row.conflict || row.linked_transaction_id ? (
                <option value="link_existing">Link to existing</option>
              ) : null}
              {row.conflict || row.linked_transaction_id ? (
                <option value="replace_existing">
                  Replace existing values
                </option>
              ) : null}
            </select>
            <button
              type="button"
              onClick={() => setEditing((v) => !v)}
              className={secondaryButtonClass}
              disabled={busy}
            >
              {editing ? "Close" : "Edit"}
            </button>
          </div>
        ) : (
          <span className="text-xs text-ink-muted">
            {row.decision.replace("_", " ")}
          </span>
        )}
      </div>
      {editing && editable ? (
        <form onSubmit={save} className="mt-2 grid gap-2 sm:grid-cols-4">
          <Field label="Date">
            <input
              type="date"
              value={date}
              onChange={(ev) => setDate(ev.target.value)}
              className={inputClass}
            />
          </Field>
          <Field label="Amount (USD, negative = money out)">
            <input
              type="text"
              inputMode="decimal"
              value={amount}
              onChange={(ev) => setAmount(ev.target.value)}
              className={inputClass}
            />
          </Field>
          <Field label="Description">
            <input
              type="text"
              maxLength={500}
              value={description}
              onChange={(ev) => setDescription(ev.target.value)}
              className={inputClass}
            />
          </Field>
          <div className="flex items-end">
            <button type="submit" className={buttonClass} disabled={busy}>
              Save
            </button>
          </div>
        </form>
      ) : null}
    </li>
  );
}
