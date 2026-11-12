"use client";

import { formatMinor } from "@/lib/api";
import { type Question } from "@/lib/discovery-api";
import { Choices } from "@/components/question-field";
import { Field, inputClass, textareaClass } from "@/components/ui";

/** The draft of a scenario answer, in the inputs' own shapes. */
export type ScenarioDraft = {
  decision: "approve" | "deny" | "manual_review" | null;
  confidence: string;
  reason_codes: string[];
  maximum_exposure: string;
  additional_information_needed: string;
  what_would_change_my_decision: string;
  notes: string;
};

export const emptyScenario: ScenarioDraft = {
  decision: null,
  confidence: "",
  reason_codes: [],
  maximum_exposure: "",
  additional_information_needed: "",
  what_would_change_my_decision: "",
  notes: "",
};

const DECISIONS = [
  { value: "approve", label: "Approve" },
  { value: "manual_review", label: "Manual review" },
  { value: "deny", label: "Deny" },
];

const CONFIDENCE = ["1", "2", "3", "4", "5"].map((value) => ({
  value,
  label: `${value}${value === "1" ? " — a guess" : value === "5" ? " — certain" : ""}`,
}));

/**
 * A synthetic underwriting case and the decision on it. The brief is drawn as the server sent
 * it: every line of it is fixture data, identical for everyone who sees it, and never a real
 * applicant. Money in it is minor units and is shown as money.
 */
export function ScenarioField({
  question,
  draft,
  onChange,
  disabled,
}: {
  question: Question;
  draft: ScenarioDraft;
  onChange: (draft: ScenarioDraft) => void;
  disabled: boolean;
}) {
  const set = (patch: Partial<ScenarioDraft>) => onChange({ ...draft, ...patch });
  return (
    <div className="flex flex-col gap-5">
      {question.scenario_brief ? <Brief brief={question.scenario_brief} /> : null}

      <fieldset className="flex flex-col gap-2">
        <legend className="mb-1 text-xs font-medium text-ink-soft">Your decision</legend>
        <Choices
          name={`decision-${question.id}`}
          options={DECISIONS}
          selected={draft.decision ? [draft.decision] : []}
          onSelect={(values) => set({ decision: (values[0] as ScenarioDraft["decision"]) ?? null })}
          multiple={false}
          disabled={disabled}
        />
      </fieldset>

      <fieldset className="flex flex-col gap-2">
        <legend className="mb-1 text-xs font-medium text-ink-soft">How confident are you? (1 to 5)</legend>
        <div className="flex flex-wrap gap-2">
          {CONFIDENCE.map((option) => {
            const checked = draft.confidence === option.value;
            return (
              <label
                key={option.value}
                className={`flex cursor-pointer items-center gap-2 rounded-xl border px-3 py-2 text-sm transition ${
                  checked ? "border-plum-500 bg-lavender-100 text-plum-800" : "border-line bg-surface text-ink hover:border-line-strong"
                } ${disabled ? "cursor-not-allowed opacity-60" : ""}`}
              >
                <input
                  type="radio"
                  name={`confidence-${question.id}`}
                  value={option.value}
                  checked={checked}
                  disabled={disabled}
                  onChange={() => set({ confidence: option.value })}
                  className="size-4 accent-plum-600"
                />
                {option.label}
              </label>
            );
          })}
        </div>
      </fieldset>

      {question.options.length > 0 ? (
        <fieldset className="flex flex-col gap-2">
          <legend className="mb-1 text-xs font-medium text-ink-soft">What drove the decision? (choose any)</legend>
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

      <Field label="Maximum exposure you would accept (optional)" hint="A dollar figure, if you have one in mind.">
        <div className="relative">
          <span className="pointer-events-none absolute inset-y-0 left-3 flex items-center text-sm text-ink-muted">$</span>
          <input
            type="text"
            inputMode="decimal"
            value={draft.maximum_exposure}
            onChange={(event) => set({ maximum_exposure: event.target.value })}
            className={`${inputClass} pl-7`}
            placeholder="0.00"
            disabled={disabled}
          />
        </div>
      </Field>

      <Field label="What else would you want to know? (optional)">
        <textarea
          value={draft.additional_information_needed}
          onChange={(event) => set({ additional_information_needed: event.target.value })}
          className={textareaClass}
          maxLength={2000}
          disabled={disabled}
        />
      </Field>
      <Field label="What would change your decision? (optional)">
        <textarea
          value={draft.what_would_change_my_decision}
          onChange={(event) => set({ what_would_change_my_decision: event.target.value })}
          className={textareaClass}
          maxLength={2000}
          disabled={disabled}
        />
      </Field>
      <Field label="Notes (optional)">
        <textarea
          value={draft.notes}
          onChange={(event) => set({ notes: event.target.value })}
          className={textareaClass}
          maxLength={2000}
          disabled={disabled}
        />
      </Field>
    </div>
  );
}

/** The brief as a list of facts. Keys are humanised; `_minor` values are shown as money. */
function Brief({ brief }: { brief: Record<string, unknown> }) {
  const rows = Object.entries(brief).filter(([key]) => key !== "synthetic");
  return (
    <div className="rounded-xl border border-line bg-surface-soft p-4">
      <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-muted">
        The case {brief.synthetic === true ? "(synthetic — not a real applicant)" : ""}
      </p>
      <dl className="grid grid-cols-1 gap-x-6 gap-y-2 sm:grid-cols-2">
        {rows.map(([key, value]) => (
          <div key={key} className="min-w-0">
            <dt className="text-xs text-ink-muted">{humanise(key)}</dt>
            <dd className="text-sm text-ink">{show(key, value)}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function humanise(key: string): string {
  return key
    .replace(/_minor$/, "")
    .replace(/_/g, " ")
    .replace(/^./, (c) => c.toUpperCase());
}

function show(key: string, value: unknown): string {
  if (key.endsWith("_minor") && typeof value === "number") return formatMinor(value);
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (value === null || value === undefined) return "—";
  return typeof value === "string" ? value : JSON.stringify(value);
}
