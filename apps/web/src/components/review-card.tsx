"use client";

import { useEffect, useState } from "react";

import { ApiError } from "@/lib/api";
import {
  decideSuggestion,
  fetchSuggestions,
  generateSuggestions,
  type AllowedCategory,
  type Suggestion,
} from "@/lib/ai-api";
import { dayLabel, money } from "@/lib/analysis-api";
import { ModelChoice, useRoutes } from "@/components/model-choice";
import { TransactionDetail } from "@/components/transaction-detail";
import { Card, Notice, buttonClass, inputClass, secondaryButtonClass } from "@/components/ui";

type Props = {
  workspaceId: string;
  token: string | null;
  /** Changes when the figures are recomputed, so the list re-reads after a decision. */
  fingerprint: string | null;
  onDecided: () => void;
};

/**
 * Categories to review.
 *
 * The rules leave a description they cannot read alone rather than filing it somewhere
 * plausible. This card is where a person settles those, one at a time. Two things it is
 * careful about: a suggestion is shown as a proposal and changes no figure until someone
 * presses a button, and a category a person sets is described as saved by them — never as
 * anything the model learned, because nothing about the model changed.
 */
export function ReviewCard({ workspaceId, token, fingerprint, onDecided }: Props) {
  const [items, setItems] = useState<Suggestion[] | null>(null);
  const [allowed, setAllowed] = useState<AllowedCategory[]>([]);
  const [remaining, setRemaining] = useState(0);
  const [note, setNote] = useState<string>("");
  const [off, setOff] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const { options, chosen, setChosen } = useRoutes(workspaceId, token);
  const [evidence, setEvidence] = useState<string | null>(null);
  const [picked, setPicked] = useState<Record<string, string>>({});

  useEffect(() => {
    let active = true;
    fetchSuggestions(workspaceId, token)
      .then((list) => {
        if (!active) return;
        setItems(list.items.filter((i) => i.status === "open"));
        setAllowed(list.allowed_categories);
        setRemaining(list.candidates_remaining);
        setNote(list.note);
        setOff(false);
      })
      .catch((caught: unknown) => {
        if (!active) return;
        if (caught instanceof ApiError && caught.status === 503) setOff(true);
        else if (caught instanceof ApiError) setError(caught.describe());
        else setError("Could not load the review list.");
      });
    return () => {
      active = false;
    };
  }, [workspaceId, token, fingerprint]);

  async function ask() {
    setBusy("generate");
    setError(null);
    try {
      const result = await generateSuggestions(workspaceId, token, chosen);
      if (result.status === "nothing_to_review") setError(result.message);
      else if (result.status !== "succeeded")
        setError(result.message ?? "Suggestions are unavailable right now.");
      const list = await fetchSuggestions(workspaceId, token);
      setItems(list.items.filter((i) => i.status === "open"));
      setRemaining(list.candidates_remaining);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.describe() : "Could not ask just now.");
    } finally {
      setBusy(null);
    }
  }

  async function settle(
    suggestion: Suggestion,
    action: "accept" | "reject" | "correct",
    category?: string,
  ) {
    setBusy(suggestion.id);
    setError(null);
    try {
      await decideSuggestion(workspaceId, token, suggestion.id, action, category);
      setItems((current) => (current ?? []).filter((i) => i.id !== suggestion.id));
      onDecided();
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.describe()
          : "Could not save that decision.",
      );
    } finally {
      setBusy(null);
    }
  }

  if (off || !items) return null;
  if (items.length === 0 && remaining === 0) return null;

  return (
    <Card>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="t-section">Categories to review</h3>
        <span className="text-xs text-ink-muted">
          {remaining > 0
            ? `${remaining} transaction${remaining === 1 ? "" : "s"} the rules left alone`
            : "nothing waiting"}
        </span>
      </div>
      <p className="mt-1 text-sm text-ink-muted">{note}</p>

      {error ? (
        <div className="mt-3">
          <Notice tone="info">{error}</Notice>
        </div>
      ) : null}

      {items.length === 0 ? (
        <p className="mt-4 text-sm text-ink-muted">
          Nothing is waiting for you. Ask for suggestions and each one appears here with the
          transaction it is about.
        </p>
      ) : (
        <ul className="mt-4 flex flex-col gap-4">
          {items.map((s) => (
            <li key={s.id} className="rounded-xl border border-line-strong p-3">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <span className="text-sm font-medium text-ink">{s.description}</span>
                <span className={s.amount_minor < 0 ? "text-ink" : "text-emerald-800"}>
                  {money(s.amount_minor, { sign: true })}
                </span>
              </div>
              <p className="text-xs text-ink-muted">
                {dayLabel(s.posted_date)}
                {s.account_name ? ` · ${s.account_name}` : ""}
              </p>

              {s.outcome === "suggested" ? (
                <p className="mt-2 text-sm text-ink">
                  Suggested: <span className="font-medium">{s.category_label}</span>
                  {s.rationale ? <span className="text-ink-muted"> · {s.rationale}</span> : null}
                </p>
              ) : (
                <div className="mt-2 rounded bg-amber-50 px-2 py-1.5">
                  <p className="text-sm text-amber-900">
                    Not enough to tell from your statement.
                    {s.question ? ` ${s.question}` : ""}
                  </p>
                  {s.alternative_labels.length ? (
                    <p className="text-xs text-amber-900">
                      It could be: {s.alternative_labels.join(", ")}. You decide.
                    </p>
                  ) : null}
                </div>
              )}

              {s.evidence_transaction_ids.length ? (
                <div className="mt-1 flex flex-wrap gap-2 text-xs">
                  {s.evidence_transaction_ids.slice(0, 3).map((id) => (
                    <button
                      key={id}
                      type="button"
                      onClick={() => setEvidence(id)}
                      className="text-plum-600 underline underline-offset-2 hover:text-plum-500"
                    >
                      See the transaction
                    </button>
                  ))}
                </div>
              ) : null}

              <div className="mt-3 flex flex-wrap items-center gap-2">
                {s.outcome === "suggested" ? (
                  <button
                    type="button"
                    onClick={() => settle(s, "accept")}
                    className={buttonClass}
                    disabled={busy !== null}
                  >
                    {busy === s.id ? "Saving…" : `Use ${s.category_label}`}
                  </button>
                ) : null}
                <select
                  aria-label={`Choose a category for ${s.description}`}
                  className={`${inputClass} max-w-[15rem]`}
                  value={picked[s.id] ?? ""}
                  onChange={(e) => setPicked((p) => ({ ...p, [s.id]: e.target.value }))}
                >
                  <option value="">Choose another category…</option>
                  {allowed
                    .filter(
                      (c) =>
                        c.direction === "either" ||
                        (c.direction === "inflow") === s.amount_minor > 0,
                    )
                    .map((c) => (
                      <option key={c.key} value={c.key}>
                        {c.label}
                      </option>
                    ))}
                </select>
                {picked[s.id] ? (
                  <button
                    type="button"
                    onClick={() => settle(s, "correct", picked[s.id])}
                    className={secondaryButtonClass}
                    disabled={busy !== null}
                  >
                    Save this instead
                  </button>
                ) : null}
                <button
                  type="button"
                  onClick={() => settle(s, "reject")}
                  className="text-xs text-plum-600 underline underline-offset-2"
                  disabled={busy !== null}
                >
                  Leave it alone
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}

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

      <div className="mt-4 flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={ask}
          className={secondaryButtonClass}
          disabled={busy !== null || remaining === 0}
        >
          {busy === "generate" ? "Asking…" : "Suggest categories for these"}
        </button>
        <ModelChoice
          options={options}
          chosen={chosen}
          onChoose={setChosen}
          disabled={busy !== null}
        />
        <span className="text-xs text-ink-muted">
          Suggestions are only ever proposals. A category you set is saved for this workspace,
          and your bank transactions are never changed.
        </span>
      </div>
    </Card>
  );
}
