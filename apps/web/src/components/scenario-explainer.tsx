"use client";

import { useState } from "react";

import { ApiError } from "@/lib/api";
import {
  cost,
  explainScenario,
  producedBy,
  seconds,
  type ScenarioExplanation,
} from "@/lib/ai-api";
import type { ScenarioInput } from "@/lib/analysis-api";
import { ModelChoice, useRoutes } from "@/components/model-choice";
import { Notice, secondaryButtonClass } from "@/components/ui";

type Props = {
  workspaceId: string;
  token: string | null;
  scenario: ScenarioInput;
  /** The data these figures came from. With the assumptions, it decides staleness. */
  fingerprint: string | null;
};

/**
 * "Explain this scenario".
 *
 * Deliberately a button and never automatic: an explanation costs money, and typing in the
 * form above would otherwise buy one on every keystroke. The chart and the figures beside it
 * are computed in Python and do not depend on this working — if the explanation fails, the
 * card says one line and the projection is untouched.
 *
 * Pressing it again with the same data and the same assumptions returns the explanation that
 * already exists rather than buying a second one.
 */
export function ScenarioExplainer({ workspaceId, token, scenario, fingerprint }: Props) {
  const [result, setResult] = useState<ScenarioExplanation | null>(null);
  const [shownFor, setShownFor] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const { options, chosen, setChosen } = useRoutes(workspaceId, token);

  // What the explanation on screen was written for. If the figures, the assumptions or the
  // model have moved since, it is stale and says so rather than being quietly re-fetched.
  // The model belongs in this key for the same reason the server keeps it in the reuse key:
  // one model's answer is not another's, and switching should not leave the first on screen
  // looking like the second's.
  const key = JSON.stringify({ fingerprint, scenario, chosen });
  const stale = result !== null && shownFor !== null && shownFor !== key;

  async function ask() {
    setBusy(true);
    setError(null);
    try {
      const fresh = await explainScenario(
        workspaceId,
        token,
        { ...scenario } as Record<string, unknown>,
        chosen,
      );
      setResult(fresh);
      setShownFor(key);
      // `partial` is shown, not hidden: it carries a real explanation that did not cover
      // everything, and the message below says which part is missing.
      if (
        fresh.status !== "succeeded" &&
        fresh.status !== "current" &&
        fresh.status !== "partial"
      ) {
        setError(fresh.message ?? "The explanation is unavailable right now.");
      }
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 503) {
        setError("Explanations are off in this environment. The projection is unaffected.");
      } else {
        setError(
          caught instanceof ApiError ? caught.describe() : "Could not explain that just now.",
        );
      }
    } finally {
      setBusy(false);
    }
  }

  const brief = result?.explanation ?? null;

  return (
    <div className="mt-5 border-t border-lavender-100 pt-4">
      <div className="flex flex-wrap items-center gap-3">
        <button type="button" onClick={ask} className={secondaryButtonClass} disabled={busy}>
          {busy ? "Working it out…" : brief ? "Explain again" : "Explain this scenario"}
        </button>
        <span className="text-xs text-ink-muted">
          The chart above is calculated. This asks for it in words, and only when you press it.
        </span>
        <ModelChoice
          options={options}
          chosen={chosen}
          onChoose={setChosen}
          disabled={busy}
        />
      </div>

      {error ? (
        <div className="mt-3">
          <Notice tone="info">{error}</Notice>
        </div>
      ) : null}

      {stale ? (
        <div className="mt-3">
          <Notice tone="info">
            <span className="font-medium">Written for your earlier figures.</span> Your data or
            your assumptions have changed since. Ask again for an explanation of what is on the
            chart now.
          </Notice>
        </div>
      ) : null}

      {result?.status === "partial" && result.message ? (
        <div className="mt-3">
          <Notice tone="info">
            <span className="font-medium">This answer is incomplete.</span>{" "}
            {result.message}
          </Notice>
        </div>
      ) : null}

      {brief ? (
        <div className="mt-3">
          <p className="text-base text-ink">{brief.headline}</p>
          {brief.claims.length ? (
            <ul className="mt-2 flex flex-col gap-2">
              {brief.claims.map((claim, index) => (
                <li
                  key={`${claim.signal_id}-${index}`}
                  className="border-l-2 border-lavender-300 pl-3 text-sm text-ink"
                >
                  {claim.text}
                  <span className="block text-xs text-ink-muted">{claim.signal_label}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-2 text-sm text-ink-muted">
              None of the statements could be checked against your figures, so none is shown.
            </p>
          )}

          {brief.claims_withheld > 0 ? (
            <p className="mt-2 text-sm text-amber-900">
              {brief.claims_withheld}{" "}
              {brief.claims_withheld === 1 ? "statement was" : "statements were"} withheld
              because {brief.claims_withheld === 1 ? "it" : "they"} could not be checked against
              your figures.
            </p>
          ) : null}

          {brief.questions.length ? (
            <div className="mt-3 rounded-lg bg-lavender-100/60 p-3">
              <h5 className="text-sm font-medium text-ink">What would sharpen this</h5>
              <ul className="mt-1 list-disc pl-5 text-sm text-ink-muted">
                {brief.questions.map((q, index) => (
                  <li key={`${q.signal_id}-${index}`}>{q.text}</li>
                ))}
              </ul>
            </div>
          ) : null}

          {brief.caveats.length ? (
            <ul className="mt-2 list-disc pl-5 text-xs text-ink-muted">
              {brief.caveats.map((c) => (
                <li key={c}>{c}</li>
              ))}
            </ul>
          ) : null}

          {result?.run ? (
            <div className="mt-3">
              <div className="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  onClick={() => setOpen((v) => !v)}
                  className="text-xs text-plum-600 underline underline-offset-2"
                >
                  {open ? "Hide how this was generated" : "How this was generated"}
                </button>
                {result.run.mode === "live" ? (
                  <span className="rounded-full bg-lavender-100 px-2 py-0.5 text-[11px] text-ink-muted">
                    Live model call
                  </span>
                ) : (
                  <span className="rounded-full border border-amber-300 bg-amber-50 px-2 py-0.5 text-[11px] text-amber-900">
                    Recorded or simulated response, not a live model call
                  </span>
                )}
                <span className="rounded-full bg-lavender-100 px-2 py-0.5 text-[11px] text-ink-muted">
                  {producedBy(result.run)}
                </span>
                {result.reused ? (
                  <span className="text-[11px] text-ink-muted">
                    reused from this same model, nothing was spent
                  </span>
                ) : null}
              </div>
              {open ? (
                <pre className="mt-2 overflow-x-auto whitespace-pre-wrap rounded-lg bg-lavender-100/60 p-3 font-mono text-[11px] leading-5 text-ink-muted">
                  {`run ${result.run.id.slice(0, 8)}…  ${result.run.capability}  ${result.run.mode}  ${result.run.provider} / ${result.run.model_requested}
prompt ${result.run.prompt_version} · ${result.run.analysis_version} · ${result.run.rules_version} · data+assumptions ${(result.fingerprint ?? "").slice(0, 8)}…
status ${result.run.status} · ${result.run.attempts} attempt${result.run.attempts === 1 ? "" : "s"} · ${seconds(result.run.latency_ms)} · ${result.run.prompt_tokens ?? "n/a"} → ${result.run.completion_tokens ?? "n/a"} tokens · ${cost(result.run.cost_micro)}
validation: schema ${result.run.validation?.schema?.ok ? "ok" : "failed"} · evidence ${result.run.validation?.evidence?.ok ? "ok" : "failed"} · numbers ${result.run.validation?.claims_withheld ? `${result.run.validation.claims_withheld} withheld` : "ok"} · support ${result.run.validation?.support?.ok ? "ok" : "failed"}`}
                </pre>
              ) : null}
              <p className="mt-2 text-[11px] text-ink-muted">
                Every figure quoted was computed before the model was asked, and checked against
                your own projection afterwards. The projection itself is an estimate, not a
                statement of what will happen.
              </p>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
