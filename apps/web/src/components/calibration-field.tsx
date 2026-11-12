"use client";

import { formatMinor } from "@/lib/api";
import { type Question, type TransactionCase } from "@/lib/discovery-api";
import { HANDLINGS, IMPACTS, MATERIALITIES, SIGNALS, timingLabel, type CalibrationDraft } from "@/lib/discovery-rules";
import { Choices } from "@/components/question-field";
import { Chip, Disclosure, Field, textareaClass } from "@/components/ui";

const LABELS: Record<string, string> = {
  immaterial: "Immaterial",
  low: "Low",
  medium: "Medium",
  high: "High",
  critical: "Critical",
  positive: "Positive",
  neutral: "Neutral",
  negative: "Negative",
  unknown: "Can't tell",
  none: "No effect",
  contributes_positive: "Counts in favour",
  contributes_negative: "Counts against",
  manual_review: "Sends it to manual review",
  potential_hard_decline: "Could be a hard decline",
  auto_classify: "Classify automatically",
  ai_review: "Let the model propose, a person confirms",
  human_review: "A person decides",
  ask_borrower: "Ask the borrower",
};

const RAILS: Record<string, string> = { ach: "ACH", card: "card", zelle: "Zelle", wire: "wire", check: "check", cash: "cash", internal: "internal transfer", other: "other" };

/**
 * One synthetic bank row and the two judgements about it: what it is, and how much it matters.
 *
 * The row is drawn like a statement line, because reading one is what the respondent is expert
 * at. Four things are asked up front; everything else sits behind one disclosure, opened for
 * the cases where the extra detail is worth the time. Nothing here shows a prediction, another
 * person's answer, or anything that would link this case to a variant of it.
 */
export function CalibrationField({
  question,
  draft,
  onChange,
  disabled,
}: {
  question: Question;
  draft: CalibrationDraft;
  onChange: (draft: CalibrationDraft) => void;
  disabled: boolean;
}) {
  const set = (patch: Partial<CalibrationDraft>) => onChange({ ...draft, ...patch });
  const c = question.transaction_case;
  const interesting = draft.underwriting_materiality !== null && draft.underwriting_materiality !== "immaterial" && draft.underwriting_materiality !== "low";
  const categories = (c?.categories ?? []).map((value) => ({ value, label: value }));

  return (
    <div className="flex flex-col gap-5">
      {c ? <CaseCard c={c} /> : null}

      <fieldset className="flex flex-col gap-2">
        <legend className="mb-1 text-xs font-medium text-ink-soft">What is this transaction?</legend>
        <Choices
          name={`category-${question.id}`}
          options={categories}
          selected={draft.classification_category ? [draft.classification_category] : []}
          onSelect={(values) => set({ classification_category: values[0] ?? null })}
          multiple={false}
          disabled={disabled}
        />
      </fieldset>

      <Scale
        legend="How confident are you? (1 is a guess, 5 is certain)"
        name={`confidence-${question.id}`}
        options={["1", "2", "3", "4", "5"]}
        value={draft.classification_confidence || null}
        onChange={(value) => set({ classification_confidence: value ?? "" })}
        disabled={disabled}
      />
      <Scale
        legend="How much would this matter when deciding whether to lend?"
        name={`materiality-${question.id}`}
        options={[...MATERIALITIES]}
        value={draft.underwriting_materiality}
        onChange={(value) => set({ underwriting_materiality: value })}
        disabled={disabled}
      />
      <Scale
        legend="Which way does it point?"
        name={`signal-${question.id}`}
        options={[...SIGNALS]}
        value={draft.underwriting_signal}
        onChange={(value) => set({ underwriting_signal: value })}
        disabled={disabled}
      />

      <Disclosure label={interesting ? "More detail (worth it for this one)" : "More detail (optional)"} openLabel="Less detail">
        <div className="flex flex-col gap-4 text-sm text-ink">
          <Scale
            legend="What would it do to the decision?"
            name={`impact-${question.id}`}
            options={[...IMPACTS]}
            value={draft.decision_impact}
            onChange={(value) => set({ decision_impact: value })}
            disabled={disabled}
            clearable
          />
          <Scale
            legend="What should the system do with a line like this?"
            name={`handling-${question.id}`}
            options={[...HANDLINGS]}
            value={draft.handling_recommendation}
            onChange={(value) => set({ handling_recommendation: value })}
            disabled={disabled}
            clearable
          />
          {question.options.length > 0 ? (
            <fieldset className="flex flex-col gap-2">
              <legend className="mb-1 text-xs font-medium text-ink-soft">What drove the reading? (choose any)</legend>
              <Choices
                name={`reasons-${question.id}`}
                options={question.options}
                selected={draft.reason_codes}
                onSelect={(values) => set({ reason_codes: values })}
                multiple
                disabled={disabled}
              />
            </fieldset>
          ) : null}
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={draft.needs_more_context}
              disabled={disabled}
              onChange={(event) => set({ needs_more_context: event.target.checked, context_needed: event.target.checked ? draft.context_needed : "" })}
              className="size-4 accent-plum-600"
            />
            I would need to see more before I was sure
          </label>
          {draft.needs_more_context ? (
            <Field label="What would you need to know?">
              <textarea
                value={draft.context_needed}
                onChange={(event) => set({ context_needed: event.target.value, needs_more_context: true })}
                className={textareaClass}
                maxLength={2000}
                disabled={disabled}
              />
            </Field>
          ) : null}
          <Field label="Your reasoning (optional)" hint="A sentence is plenty. This is what turns a reading into a rule.">
            <textarea
              value={draft.reasoning}
              onChange={(event) => set({ reasoning: event.target.value })}
              className={textareaClass}
              maxLength={2000}
              disabled={disabled}
            />
          </Field>
        </div>
      </Disclosure>
    </div>
  );
}

/** The synthetic row, drawn like a line on a statement, with the pattern beneath it. */
function CaseCard({ c }: { c: TransactionCase }) {
  const timing = timingLabel(c.day_of_week, c.time_of_day);
  const pattern: string[] = [];
  if (c.frequency_30d !== null) pattern.push(`seen ${c.frequency_30d}× in 30 days`);
  if (c.frequency_90d !== null) pattern.push(`${c.frequency_90d}× in 90 days`);
  if (c.amount_min_minor !== null && c.amount_max_minor !== null) pattern.push(`${formatMinor(Math.abs(c.amount_min_minor))}–${formatMinor(Math.abs(c.amount_max_minor))}`);
  if (c.average_amount_minor !== null) pattern.push(`avg ${formatMinor(Math.abs(c.average_amount_minor))}`);
  if (c.recurrence_pattern && c.recurrence_pattern !== "none") pattern.push(c.recurrence_pattern);
  const contextRows = Object.entries(c.context);
  return (
    <div className="rounded-xl border border-line bg-surface-soft p-4">
      <div className="flex flex-wrap items-center gap-2">
        <Chip tone="accent">Synthetic case</Chip>
        {c.business_vertical ? <Chip tone="muted">{c.business_vertical}</Chip> : null}
        {c.account_type ? <Chip tone="muted">{c.account_type}</Chip> : null}
      </div>
      {c.business_description ? <p className="mt-2 text-sm text-ink-muted">{c.business_description}</p> : null}
      <div className="mt-3 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 rounded-lg border border-line bg-surface px-3 py-2">
        <div className="min-w-0">
          <p className="t-figure truncate text-sm font-semibold text-ink">{c.descriptor}</p>
          <p className="text-xs text-ink-muted">
            {[c.merchant, c.counterparty, c.payment_rail ? RAILS[c.payment_rail] ?? c.payment_rail : null, c.posted_date, timing].filter(Boolean).join(" · ")}
          </p>
          {c.memo ? <p className="text-xs text-ink-faint">{c.memo}</p> : null}
        </div>
        <p className={`t-figure text-lg font-semibold ${c.direction === "inflow" ? "text-up" : "text-ink"}`}>
          {c.direction === "inflow" ? "+" : "−"}
          {formatMinor(Math.abs(c.amount_minor))}
        </p>
      </div>
      {pattern.length > 0 ? <p className="mt-2 text-xs text-ink-muted">{pattern.join(" · ")}</p> : null}
      {c.related_transactions.length > 0 ? (
        <Disclosure label={`Related rows (${c.related_transactions.length})`} className="mt-2">
          <ul className="flex flex-col gap-1">
            {c.related_transactions.map((row, index) => (
              <li key={index} className="flex flex-wrap justify-between gap-x-3">
                <span>
                  {String(row.descriptor ?? "")}
                  {row.posted_date ? ` · ${String(row.posted_date)}` : ""}
                  {row.note ? ` · ${String(row.note)}` : ""}
                </span>
                {typeof row.amount_minor === "number" ? <span className="t-figure">{row.amount_minor < 0 ? "−" : "+"}{formatMinor(Math.abs(row.amount_minor))}</span> : null}
              </li>
            ))}
          </ul>
        </Disclosure>
      ) : null}
      {contextRows.length > 0 ? (
        <dl className="mt-2 flex flex-col gap-1 text-xs text-ink-muted">
          {contextRows.map(([key, value]) => (
            <div key={key}>
              <dt className="inline font-medium text-ink-soft">{key.replace(/_/g, " ")}: </dt>
              <dd className="inline">{typeof value === "string" ? value : JSON.stringify(value)}</dd>
            </div>
          ))}
        </dl>
      ) : null}
    </div>
  );
}

/** A row of pill radios for a short ordered scale; `clearable` lets an optional one be unset. */
function Scale({
  legend,
  name,
  options,
  value,
  onChange,
  disabled,
  clearable = false,
}: {
  legend: string;
  name: string;
  options: string[];
  value: string | null;
  onChange: (value: string | null) => void;
  disabled: boolean;
  clearable?: boolean;
}) {
  return (
    <fieldset className="flex flex-col gap-2">
      <legend className="mb-1 text-xs font-medium text-ink-soft">{legend}</legend>
      <div className="flex flex-wrap gap-2">
        {options.map((option) => {
          const checked = value === option;
          return (
            <label
              key={option}
              className={`flex cursor-pointer items-center gap-2 rounded-xl border px-3 py-2 text-sm transition ${
                checked ? "border-plum-500 bg-lavender-100 text-plum-800" : "border-line bg-surface text-ink hover:border-line-strong"
              } ${disabled ? "cursor-not-allowed opacity-60" : ""}`}
            >
              <input
                type="radio"
                name={name}
                value={option}
                checked={checked}
                disabled={disabled}
                onChange={() => onChange(option)}
                onClick={() => {
                  if (clearable && checked) onChange(null);
                }}
                className="size-4 accent-plum-600"
              />
              {LABELS[option] ?? option}
            </label>
          );
        })}
      </div>
    </fieldset>
  );
}
