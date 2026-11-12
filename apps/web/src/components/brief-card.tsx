"use client";

import { useEffect, useState } from "react";

import { ApiError } from "@/lib/api";
import {
  cost,
  fetchBrief,
  generateBrief,
  producedBy,
  seconds,
  stamp,
  type BriefClaim,
  type BriefView,
  type RunSummary,
} from "@/lib/ai-api";
import { dayLabel } from "@/lib/analysis-api";
import { TransactionDetail } from "@/components/transaction-detail";
import { ModelChoice, useRoutes } from "@/components/model-choice";
import { Card, Notice, buttonClass } from "@/components/ui";

type Props = {
  workspaceId: string;
  token: string | null;
  /** The fingerprint the figures on screen were computed from. */
  fingerprint: string | null;
};

/**
 * The written brief above the figures.
 *
 * Three rules the wording follows, because they are the point of the milestone: nothing is
 * generated without someone asking for it, a brief written against older data says so rather
 * than being refreshed quietly, and an answer that could not be checked is not shown at all.
 * When anything fails, this card says one line and the figures below it are untouched.
 */
export function BriefCard({ workspaceId, token, fingerprint }: Props) {
  const [view, setView] = useState<BriefView | null>(null);
  const [off, setOff] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [evidence, setEvidence] = useState<string | null>(null);
  const { options, chosen, setChosen } = useRoutes(workspaceId, token);

  // Keyed on the fingerprint as well as the workspace: when the figures below are recomputed
  // the stored brief is asked about again, which is how it turns stale without anything here
  // deciding that for itself.
  useEffect(() => {
    let active = true;
    fetchBrief(workspaceId, token)
      .then((fresh) => {
        if (!active) return;
        setView(fresh);
        setOff(null);
      })
      .catch((caught: unknown) => {
        if (!active) return;
        // 503 is the deployment saying it has no AI configured. Nothing the owner did, so it
        // reads as one quiet line rather than a failure.
        if (caught instanceof ApiError && caught.status === 503) setOff(caught.message);
        else if (caught instanceof ApiError) setError(caught.describe());
        else setError("Could not check for a brief.");
      });
    return () => {
      active = false;
    };
  }, [workspaceId, token, fingerprint]);

  async function write() {
    setBusy(true);
    setError(null);
    try {
      setView(await generateBrief(workspaceId, token, null, chosen));
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.describe()
          : "Could not write a brief just now.",
      );
    } finally {
      setBusy(false);
    }
  }

  if (off) {
    return (
      <Card>
        <h3 className="t-section">Your cash, in words</h3>
        <p className="mt-2 text-sm text-ink-muted">{off}</p>
        <p className="mt-1 text-sm text-ink-muted">
          Everything below is calculated from your transactions and does not depend on it.
        </p>
      </Card>
    );
  }
  if (!view) return null;

  const brief = view.brief;
  const stale = view.stored_state === "stale";
  const failed = ["unavailable", "provider_error", "timeout", "invalid_output"].includes(
    view.status,
  );

  return (
    <Card>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="t-section">Your cash, in words</h3>
        {view.run ? (
          <span className="text-xs text-ink-muted">
            Written for your data as of {dayLabel(view.run.analysis_date)}
            {view.run.completed_at ? ` · generated ${stamp(view.run.completed_at)}` : ""}
          </span>
        ) : null}
      </div>

      {error ? (
        <div className="mt-3">
          <Notice tone="error">{error}</Notice>
        </div>
      ) : null}

      {failed ? (
        <div className="mt-3">
          <Notice tone="info">
            {view.message ?? "Explanation unavailable right now."} Your figures below are
            complete.
          </Notice>
        </div>
      ) : null}

      {view.status === "insufficient_data" ? (
        <div className="mt-3">
          <Notice tone="info">
            {view.message}
            {view.reasons.length ? (
              <ul className="mt-2 list-disc pl-5">
                {view.reasons.map((r) => (
                  <li key={r}>{r}</li>
                ))}
              </ul>
            ) : null}
          </Notice>
        </div>
      ) : null}

      {view.status === "superseded" ? (
        <div className="mt-3">
          <Notice tone="info">{view.message}</Notice>
        </div>
      ) : null}

      {stale ? (
        <div className="mt-3">
          <Notice tone="info">
            <span className="font-medium">Written before your data changed.</span>{" "}
            {view.changes_since.length
              ? `Since this was written: ${view.changes_since.join(", ")}.`
              : "Your figures below are already up to date."}
          </Notice>
        </div>
      ) : null}

      {brief ? (
        <div className="mt-4">
          <p className="text-base text-ink">{brief.headline}</p>
          {brief.claims.length ? (
            <ul className="mt-3 flex flex-col gap-3">
              {brief.claims.map((claim, index) => (
                <ClaimRow
                  key={`${claim.signal_id}-${index}`}
                  claim={claim}
                  onEvidence={setEvidence}
                />
              ))}
            </ul>
          ) : (
            <p className="mt-3 text-sm text-ink-muted">
              None of the statements could be checked against your figures, so none is shown.
            </p>
          )}

          {brief.claims_withheld > 0 ? (
            <p className="mt-3 text-sm text-amber-900">
              {brief.claims_withheld}{" "}
              {brief.claims_withheld === 1 ? "statement was" : "statements were"} withheld
              because {brief.claims_withheld === 1 ? "it" : "they"} could not be checked against
              your figures
              {brief.withheld_reasons.length ? ` (${brief.withheld_reasons.join("; ")})` : ""}.
            </p>
          ) : null}

          {brief.questions.length ? (
            <div className="mt-4 rounded-lg bg-lavender-100/60 p-3">
              <h4 className="text-sm font-medium text-ink">Questions for you</h4>
              <ul className="mt-1 list-disc pl-5 text-sm text-ink-muted">
                {brief.questions.map((q, index) => (
                  <li key={`${q.signal_id}-${index}`}>{q.text}</li>
                ))}
              </ul>
              <p className="mt-2 text-xs text-ink-muted">
                Your bank statements cannot say why something changed, so anything that needs a
                reason is asked rather than assumed.
              </p>
            </div>
          ) : null}

          {brief.caveats.length ? (
            <ul className="mt-3 list-disc pl-5 text-xs text-ink-muted">
              {brief.caveats.map((c) => (
                <li key={c}>{c}</li>
              ))}
            </ul>
          ) : null}
        </div>
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

      <div className="mt-4 flex flex-wrap items-center gap-3">
        <button type="button" onClick={write} className={buttonClass} disabled={busy}>
          {busy
            ? "Writing…"
            : brief
              ? stale
                ? "Update brief"
                : "Write it again"
              : "Generate financial brief"}
        </button>
        <ModelChoice
          options={options}
          chosen={chosen}
          onChoose={setChosen}
          disabled={busy}
        />
        {view.status === "none" && !brief ? (
          <span className="text-sm text-ink-muted">
            Nothing has been written yet. Your figures below are ready either way.
          </span>
        ) : null}
      </div>

      {view.run ? <RunTrace run={view.run} /> : null}
    </Card>
  );
}

function ClaimRow({
  claim,
  onEvidence,
}: {
  claim: BriefClaim;
  onEvidence: (id: string) => void;
}) {
  return (
    <li className="border-l-2 border-lavender-300 pl-3">
      <p className="text-sm text-ink">{claim.text}</p>
      <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-ink-muted">
        <span>{claim.signal_label}</span>
        {claim.evidence.map((id) => (
          <button
            key={id}
            type="button"
            onClick={() => onEvidence(id)}
            className="text-plum-600 underline underline-offset-2 hover:text-plum-500"
          >
            See the figures
          </button>
        ))}
        {claim.evidence_truncated ? (
          <span>
            showing {claim.evidence.length} of {claim.evidence_total} transactions behind this
          </span>
        ) : null}
      </div>
    </li>
  );
}

function RunTrace({ run }: { run: RunSummary }) {
  const [open, setOpen] = useState(false);
  const v = run.validation ?? {};
  const withheld = v.claims_withheld ?? 0;
  const layer = (name: string, ok: boolean | undefined) =>
    `${name} ${ok === false ? "failed" : "ok"}`;

  return (
    <div className="mt-4 border-t border-lavender-100 pt-3">
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => setOpen((value) => !value)}
          className="text-xs text-plum-600 underline underline-offset-2 hover:text-plum-500"
        >
          {open ? "Hide how this was generated" : "How this was generated"}
        </button>
        {run.mode === "live" ? (
          <span className="rounded-full bg-lavender-100 px-2 py-0.5 text-[11px] text-ink-muted">
            Live model call
          </span>
        ) : (
          <span className="rounded-full border border-amber-300 bg-amber-50 px-2 py-0.5 text-[11px] text-amber-900">
            Recorded or simulated response, not a live model call
          </span>
        )}
        <span className="rounded-full bg-lavender-100 px-2 py-0.5 text-[11px] text-ink-muted">
          {producedBy(run)}
        </span>
      </div>
      {open ? (
        <div className="mt-2 overflow-x-auto rounded-lg bg-lavender-100/60 p-3">
          <pre className="whitespace-pre-wrap font-mono text-[11px] leading-5 text-ink-muted">
            {`run ${run.id.slice(0, 8)}…  ${run.capability}  ${run.mode}  ${run.provider} / ${run.model_requested}${
              run.model_used && run.model_used !== run.model_requested
                ? ` → ${run.model_used}`
                : ""
            }
prompt ${run.prompt_version} · ${run.analysis_version} · ${run.rules_version} · data as of ${run.analysis_date} · fingerprint ${run.input_fingerprint.slice(0, 8)}…
status ${run.status}${run.error_code ? ` (${run.error_code})` : ""} · ${run.attempts} attempt${run.attempts === 1 ? "" : "s"} · ${seconds(run.latency_ms)} · ${run.prompt_tokens ?? "n/a"} → ${run.completion_tokens ?? "n/a"} tokens · ${cost(run.cost_micro)}${run.cost_source ? ` (${run.cost_source.replace("_", " ")})` : ""}
validation: ${layer("schema", v.schema?.ok)} · ${layer("evidence", v.evidence?.ok)} · numbers ${withheld ? `${withheld} withheld` : "ok"} · ${layer("support", v.support?.ok)}${
              v.coverage_gaps?.length ? ` · ${v.coverage_gaps.length} coverage gap(s)` : ""
            }${
              run.routing
                ? `
routing: upstream ${run.routing.upstream_provider ?? "not named by the provider"} · fallbacks ${run.routing.routing?.allow_fallbacks ? "allowed" : "refused"} · data collection ${run.routing.routing?.data_collection ?? "n/a"}${
                    run.routing.routing?.max_price
                      ? ` · ceiling $${run.routing.routing.max_price.prompt}/$${run.routing.routing.max_price.completion} per million`
                      : ""
                  }`
                : ""
            }`}
          </pre>
          <p className="mt-2 text-[11px] text-ink-muted">
            These checks confirm that every figure quoted matches your own data, that every
            reference points at a real transaction of this workspace, and that the wording does
            not claim more than the figures show. They do not confirm that the reading of your
            business is the right one.
          </p>
          {v.headline_replaced ? (
            <p className="mt-1 text-[11px] text-amber-900">
              The opening line quoted a figure that is not yours, so it was replaced with one
              written from your own numbers.
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
