"use client";

import { inputClass, textareaClass } from "@/components/ui";
import { type Question } from "@/lib/discovery-api";

/**
 * The input for one non-scenario question, editing a draft the section owns.
 *
 * Drafts are the input's own shape (text in a box, a chosen value, a list of values), not the
 * stored shape; `toRequest` in the section turns one into the other at save time, and the
 * server decides whether it is an answer.
 */
export type Draft = string | boolean | string[] | null;

export function QuestionField({
  question,
  draft,
  onChange,
  disabled,
}: {
  question: Question;
  draft: Draft;
  onChange: (draft: Draft) => void;
  disabled: boolean;
}) {
  const name = `q-${question.id}`;
  switch (question.question_type) {
    case "short_text":
      return (
        <input
          type="text"
          value={typeof draft === "string" ? draft : ""}
          onChange={(event) => onChange(event.target.value)}
          className={inputClass}
          maxLength={500}
          disabled={disabled}
          aria-label={question.question_text}
        />
      );
    case "long_text":
      return (
        <textarea
          value={typeof draft === "string" ? draft : ""}
          onChange={(event) => onChange(event.target.value)}
          className={`${textareaClass} min-h-[7rem]`}
          maxLength={10000}
          disabled={disabled}
          aria-label={question.question_text}
        />
      );
    case "boolean":
      return (
        <Choices
          name={name}
          multiple={false}
          disabled={disabled}
          options={[
            { value: "true", label: "Yes" },
            { value: "false", label: "No" },
          ]}
          selected={draft === true ? ["true"] : draft === false ? ["false"] : []}
          onSelect={(values) => onChange(values[0] === "true" ? true : values[0] === "false" ? false : null)}
        />
      );
    case "single_select":
      return (
        <Choices
          name={name}
          multiple={false}
          disabled={disabled}
          options={question.options}
          selected={typeof draft === "string" ? [draft] : []}
          onSelect={(values) => onChange(values[0] ?? null)}
        />
      );
    case "multi_select":
      return (
        <Choices
          name={name}
          multiple
          disabled={disabled}
          options={question.options}
          selected={Array.isArray(draft) ? draft : []}
          onSelect={(values) => onChange(values)}
        />
      );
    case "currency":
      return (
        <div className="relative">
          <span className="pointer-events-none absolute inset-y-0 left-3 flex items-center text-sm text-ink-muted">$</span>
          <input
            type="text"
            inputMode="decimal"
            value={typeof draft === "string" ? draft : ""}
            onChange={(event) => onChange(event.target.value)}
            className={`${inputClass} pl-7`}
            placeholder="0.00"
            disabled={disabled}
            aria-label={question.question_text}
          />
        </div>
      );
    case "number":
    case "percentage":
    case "scale":
      return (
        <div className="relative">
          <input
            type="number"
            inputMode="decimal"
            value={typeof draft === "string" ? draft : ""}
            onChange={(event) => onChange(event.target.value)}
            className={`${inputClass} ${question.question_type === "percentage" ? "pr-8" : ""}`}
            min={question.question_type === "scale" ? 0 : undefined}
            max={question.question_type === "scale" ? 10 : question.question_type === "percentage" ? 100 : undefined}
            step={question.question_type === "scale" ? 1 : "any"}
            disabled={disabled}
            aria-label={question.question_text}
          />
          {question.question_type === "percentage" ? (
            <span className="pointer-events-none absolute inset-y-0 right-3 flex items-center text-sm text-ink-muted">%</span>
          ) : null}
        </div>
      );
    case "ranking":
      return (
        <Ranking
          options={question.options}
          ranked={Array.isArray(draft) ? draft : []}
          onChange={onChange}
          disabled={disabled}
        />
      );
    default:
      return null;
  }
}

/**
 * Click to rank: the options that apply, most important first, and the rest left out. The
 * server accepts any non-empty ordering of offered options, so a person is never made to rank
 * something they would not have named.
 */
export function Ranking({
  options,
  ranked,
  onChange,
  disabled,
}: {
  options: { value: string; label: string }[];
  ranked: string[];
  onChange: (draft: string[]) => void;
  disabled: boolean;
}) {
  const labelOf = new Map(options.map((o) => [o.value, o.label]));
  const pool = options.filter((o) => !ranked.includes(o.value));
  const move = (index: number, delta: number) => {
    const next = [...ranked];
    const target = index + delta;
    if (target < 0 || target >= next.length) return;
    [next[index], next[target]] = [next[target], next[index]];
    onChange(next);
  };
  const small = "rounded-lg border border-line bg-surface px-2 py-1 text-xs text-ink-muted transition hover:border-line-strong disabled:cursor-not-allowed disabled:opacity-50";
  return (
    <div className="flex flex-col gap-3">
      <p className="text-xs text-ink-muted">Click the options in order, most important first. Leave out anything that does not apply.</p>
      {ranked.length > 0 ? (
        <ol className="flex flex-col gap-2" aria-label="Your ranking">
          {ranked.map((value, index) => (
            <li key={value} className="flex items-center gap-2 rounded-xl border border-plum-500 bg-lavender-100 px-3 py-2 text-sm text-plum-800">
              <span className="w-5 shrink-0 text-xs font-semibold">{index + 1}.</span>
              <span className="min-w-0 flex-1">{labelOf.get(value) ?? value}</span>
              <button type="button" onClick={() => move(index, -1)} disabled={disabled || index === 0} className={small} aria-label="Move up">
                ▲
              </button>
              <button type="button" onClick={() => move(index, 1)} disabled={disabled || index === ranked.length - 1} className={small} aria-label="Move down">
                ▼
              </button>
              <button type="button" onClick={() => onChange(ranked.filter((v) => v !== value))} disabled={disabled} className={small} aria-label="Remove">
                ✕
              </button>
            </li>
          ))}
        </ol>
      ) : null}
      {pool.length > 0 ? (
        <div className="flex flex-col gap-2" role="group" aria-label="Not yet ranked">
          {pool.map((option) => (
            <button
              key={option.value}
              type="button"
              onClick={() => onChange([...ranked, option.value])}
              disabled={disabled}
              className="flex items-center gap-3 rounded-xl border border-line bg-surface px-3 py-2.5 text-left text-sm text-ink transition hover:border-line-strong disabled:cursor-not-allowed disabled:opacity-60"
            >
              <span className="text-xs text-ink-faint">+</span>
              <span>{option.label}</span>
            </button>
          ))}
        </div>
      ) : null}
      {ranked.length > 0 ? (
        <div>
          <button type="button" onClick={() => onChange([])} disabled={disabled} className={small}>
            Clear ranking
          </button>
        </div>
      ) : null}
    </div>
  );
}

/** Radio buttons or checkboxes drawn the same way, so a choice looks like a choice. */
export function Choices({
  name,
  options,
  selected,
  onSelect,
  multiple,
  disabled,
}: {
  name: string;
  options: { value: string; label: string }[];
  selected: string[];
  onSelect: (values: string[]) => void;
  multiple: boolean;
  disabled: boolean;
}) {
  return (
    <div className="flex flex-col gap-2" role={multiple ? "group" : "radiogroup"}>
      {options.map((option) => {
        const checked = selected.includes(option.value);
        return (
          <label
            key={option.value}
            className={`flex cursor-pointer items-center gap-3 rounded-xl border px-3 py-2.5 text-sm transition ${
              checked ? "border-plum-500 bg-lavender-100 text-plum-800" : "border-line bg-surface text-ink hover:border-line-strong"
            } ${disabled ? "cursor-not-allowed opacity-60" : ""}`}
          >
            <input
              type={multiple ? "checkbox" : "radio"}
              name={name}
              value={option.value}
              checked={checked}
              disabled={disabled}
              onChange={() => {
                if (!multiple) {
                  onSelect([option.value]);
                } else {
                  onSelect(checked ? selected.filter((v) => v !== option.value) : [...selected, option.value]);
                }
              }}
              className="size-4 accent-plum-600"
            />
            <span>{option.label}</span>
          </label>
        );
      })}
    </div>
  );
}
