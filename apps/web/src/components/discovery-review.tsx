"use client";

import { useState, useSyncExternalStore } from "react";

import { type Question } from "@/lib/discovery-api";
import {
  REVIEW_FLAG_KEY,
  REVIEW_ISSUES,
  REVIEW_PLACEMENTS,
  REVIEW_STORAGE_KEY,
  REVIEW_TRAINING_VALUES,
  REVIEW_VERDICTS,
  buildExport,
  emptyReview,
  isReviewActive,
  isReviewed,
  parseReviews,
  reviewMark,
  type QuestionReview,
  type ReviewStore,
} from "@/lib/discovery-review";
import { Chip, quietButtonClass, secondaryButtonClass, textareaClass } from "@/components/ui";

// ------------------------------------------------------------ activation --
function subscribe(onChange: () => void): () => void {
  window.addEventListener("storage", onChange);
  return () => window.removeEventListener("storage", onChange);
}

function storedFlag(): string | null {
  try {
    return window.localStorage.getItem(REVIEW_FLAG_KEY);
  } catch {
    return null;
  }
}

function snapshot(): boolean {
  const active = isReviewActive(
    window.location.search,
    storedFlag(),
    process.env.NODE_ENV,
    process.env.NEXT_PUBLIC_DISCOVERY_REVIEW_MODE,
  );
  // Remember the switch so it survives the portal's own navigation; `?review=0` forgets it.
  const param = new URLSearchParams(window.location.search).get("review");
  try {
    if (param === "1") window.localStorage.setItem(REVIEW_FLAG_KEY, "1");
    if (param === "0") window.localStorage.removeItem(REVIEW_FLAG_KEY);
  } catch {
    // Without storage the mode lasts this page only.
  }
  return active;
}

/** Whether reviewer controls are drawn. False on the server and on any production build. */
export function useReviewMode(): boolean {
  return useSyncExternalStore(subscribe, snapshot, () => false);
}

// ------------------------------------------------------------- persona --
export const REVIEW_PERSONA_KEY = "cashlens.discovery-review.persona";

/** The persona a reviewer chose, remembered in this browser only; null when none. */
export function readReviewPersona(): string | null {
  try {
    return window.localStorage.getItem(REVIEW_PERSONA_KEY);
  } catch {
    return null;
  }
}

export function writeReviewPersona(slug: string | null): void {
  try {
    if (slug) window.localStorage.setItem(REVIEW_PERSONA_KEY, slug);
    else window.localStorage.removeItem(REVIEW_PERSONA_KEY);
  } catch {
    // Without storage the choice lasts this page only.
  }
}

/** The chosen persona as state, written through to this browser on change. */
export function useReviewPersona(): [string | null, (slug: string | null) => void] {
  const [persona, setPersona] = useState<string | null>(() => (typeof window === "undefined" ? null : readReviewPersona()));
  return [
    persona,
    (slug) => {
      writeReviewPersona(slug);
      setPersona(slug);
    },
  ];
}

/**
 * "Review as": which of the reviewer's OWN assignments to render. Drawn only in reviewer mode.
 * A persona the account holds no assignment on is shown disabled; nothing here can reach
 * another person's assignment, because the list comes from the API for this user alone.
 */
export function ReviewPersonaSwitch({
  personas,
  held,
  current,
  onChange,
}: {
  personas: { slug: string; label: string }[];
  held: Set<string>;
  current: string | null;
  onChange: (slug: string) => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-xl border border-dashed border-warn bg-warn-soft/40 px-3 py-2">
      <span className="text-xs font-medium text-ink-soft">Review as</span>
      <div className="flex flex-wrap gap-1.5">
        {personas.map((p) => {
          const on = current === p.slug;
          const available = held.has(p.slug);
          return (
            <button
              key={p.slug}
              type="button"
              onClick={() => available && onChange(p.slug)}
              disabled={!available}
              title={available ? `Render the ${p.label} template` : `No ${p.label} assignment on this account`}
              className={`rounded-full border px-2.5 py-1 text-xs transition ${on ? "border-plum-500 bg-lavender-100 text-plum-800" : "border-line bg-surface text-ink-muted hover:border-line-strong"} disabled:cursor-not-allowed disabled:opacity-40`}
            >
              {p.label}
            </button>
          );
        })}
      </div>
      {current ? <Chip tone="warn">Previewing {personas.find((p) => p.slug === current)?.label ?? current}</Chip> : null}
    </div>
  );
}

// --------------------------------------------------------------- storage --
function readStore(): ReviewStore {
  try {
    return parseReviews(window.localStorage.getItem(REVIEW_STORAGE_KEY));
  } catch {
    return {};
  }
}

function writeReview(review: QuestionReview): void {
  try {
    const store = readStore();
    store[review.question_id] = review;
    window.localStorage.setItem(REVIEW_STORAGE_KEY, JSON.stringify(store));
  } catch {
    // Storage refused; the panel still shows what was typed for this page.
  }
}

function reviewTitle(question: Question): string {
  const c = question.transaction_case;
  if (c) return `${c.descriptor} (${c.direction} ${(Math.abs(c.amount_minor) / 100).toFixed(2)})`;
  return question.question_text.slice(0, 120);
}

// ------------------------------------------------------------------ export --
export function ReviewExport({ compact = false }: { compact?: boolean }) {
  const [note, setNote] = useState<string | null>(null);
  const reviewed = Object.values(readStore()).filter(isReviewed).length;

  function payload(): string {
    return JSON.stringify(buildExport(readStore(), new Date().toISOString()), null, 2);
  }
  function download() {
    const blob = new Blob([payload()], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `discovery-review-${new Date().toISOString().slice(0, 10)}.json`;
    a.click();
    URL.revokeObjectURL(url);
    setNote("Downloaded.");
  }
  async function copy() {
    try {
      await navigator.clipboard.writeText(payload());
      setNote("Copied to the clipboard.");
    } catch {
      setNote("Could not copy; use Download instead.");
    }
  }
  return (
    <div className={`flex flex-wrap items-center gap-2 ${compact ? "" : "rounded-xl border border-dashed border-warn bg-warn-soft/40 px-3 py-2"}`}>
      <Chip tone="warn">Reviewer mode · local only</Chip>
      <span className="text-xs text-ink-muted">{reviewed} reviewed</span>
      <button type="button" onClick={download} className={secondaryButtonClass}>
        Export review
      </button>
      <button type="button" onClick={copy} className={quietButtonClass}>
        Copy JSON
      </button>
      {note ? <span className="text-xs text-ink-muted">{note}</span> : null}
    </div>
  );
}

// ------------------------------------------------------------------- panel --
/**
 * "Review this question": notes about the question, beneath the question. Auto-saved to this
 * browser on every edit, keyed by the question's id, and never sent anywhere. Keyed by the
 * question in the parent so a new question gets a fresh panel.
 */
export function ReviewPanel({ question, section }: { question: Question; section: string }) {
  const [review, setReview] = useState<QuestionReview>(
    () => readStore()[question.id] ?? emptyReview(question.id, section, question.question_type, reviewTitle(question)),
  );
  const update = (patch: Partial<QuestionReview>) => {
    const next = { ...review, ...patch, title: reviewTitle(question), section, updated_at: new Date().toISOString() };
    setReview(next);
    writeReview(next);
  };
  const mark = reviewMark(review);

  return (
    <section className="rounded-2xl border border-dashed border-warn bg-warn-soft/30 p-4 sm:p-5" aria-label="Review this question">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-sm font-semibold text-ink">Review this question</h3>
          <Chip tone="warn">local only · not an answer</Chip>
          {mark ? <Chip tone="muted">{mark}</Chip> : null}
        </div>
        <ReviewExport compact />
      </div>

      <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Pills label="Verdict" options={[...REVIEW_VERDICTS]} value={review.verdict} onChange={(v) => update({ verdict: v as QuestionReview["verdict"] })} />
        <Pills label="Usefulness (1 low · 5 high)" options={["1", "2", "3", "4", "5"]} value={review.usefulness === null ? null : String(review.usefulness)} onChange={(v) => update({ usefulness: v === null ? null : Number(v) })} />
        <Pills label="Placement" options={[...REVIEW_PLACEMENTS]} value={review.placement} onChange={(v) => update({ placement: v as QuestionReview["placement"] })} />
        <Pills label="Training value" options={[...REVIEW_TRAINING_VALUES]} value={review.training_value} onChange={(v) => update({ training_value: v as QuestionReview["training_value"] })} />
      </div>

      <fieldset className="mt-4">
        <legend className="mb-1.5 text-xs font-medium text-ink-soft">Issues (any)</legend>
        <div className="flex flex-wrap gap-1.5">
          {REVIEW_ISSUES.map((issue) => {
            const on = review.issues.includes(issue);
            return (
              <label key={issue} className={`flex cursor-pointer items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs ${on ? "border-warn bg-warn-soft text-warn" : "border-line bg-surface text-ink-muted"}`}>
                <input
                  type="checkbox"
                  checked={on}
                  onChange={() => update({ issues: on ? review.issues.filter((i) => i !== issue) : [...review.issues, issue] })}
                  className="size-3 accent-plum-600"
                />
                {issue.replace(/_/g, " ")}
              </label>
            );
          })}
        </div>
      </fieldset>

      <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
        <label className="flex flex-col gap-1 text-sm text-ink">
          <span className="text-xs font-medium text-ink-soft">Comment (optional)</span>
          <textarea value={review.comment} onChange={(e) => update({ comment: e.target.value })} className={textareaClass} maxLength={2000} />
        </label>
        <label className="flex flex-col gap-1 text-sm text-ink">
          <span className="text-xs font-medium text-ink-soft">Suggested rewrite (optional)</span>
          <textarea value={review.suggested_rewrite} onChange={(e) => update({ suggested_rewrite: e.target.value })} className={textareaClass} maxLength={2000} />
        </label>
      </div>
      <p className="mt-2 text-xs text-ink-faint">Saved in this browser as you type. Export from here or from the Discovery landing page.</p>
    </section>
  );
}

/** A row of small pills; clicking the chosen one clears it. */
function Pills({ label, options, value, onChange }: { label: string; options: string[]; value: string | null; onChange: (value: string | null) => void }) {
  return (
    <fieldset>
      <legend className="mb-1.5 text-xs font-medium text-ink-soft">{label}</legend>
      <div className="flex flex-wrap gap-1.5">
        {options.map((option) => {
          const on = value === option;
          return (
            <button
              key={option}
              type="button"
              onClick={() => onChange(on ? null : option)}
              className={`rounded-full border px-2.5 py-1 text-xs transition ${on ? "border-plum-500 bg-lavender-100 text-plum-800" : "border-line bg-surface text-ink-muted hover:border-line-strong"}`}
            >
              {option.replace(/_/g, " ")}
            </button>
          );
        })}
      </div>
    </fieldset>
  );
}
