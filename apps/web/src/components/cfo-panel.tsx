"use client";

import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from "react";

import { ApiError } from "@/lib/api";
import { track } from "@/lib/analytics";
import {
  askCfo,
  cost,
  fetchAskSuggestions,
  fetchConversation,
  producedBy,
  progressLabel,
  seconds,
  type AskSuggestion,
  type AskView,
  type ClarifyOption,
  type ToolTrace,
  type Turn,
} from "@/lib/ai-api";
import type { ScenarioInput } from "@/lib/analysis-api";
import { ModelChoice, useRoutes } from "@/components/model-choice";
import { Chip, Icon, Notice, iconButtonClass, quietButtonClass, textareaClass } from "@/components/ui";

type Props = {
  workspaceId: string;
  token: string | null;
  fingerprint: string | null;
  onScenario: (input: ScenarioInput) => void;
  onProposal: () => void;
  onScenarioApplied?: () => void;
};

type Entry = { id: string; question: string; view: AskView | null };

function fromTurn(turn: Turn): Entry {
  return {
    id: turn.id,
    question: turn.question,
    view: {
      status: turn.status,
      conversation_id: "",
      turn_id: turn.id,
      answer: turn.answer,
      clarify: turn.clarify,
      scenario_applied: turn.scenario_applied,
      scenario_paths: null,
      proposal_id: turn.proposal_id,
      tool_calls: turn.tool_calls ?? [],
      message: null,
      run: null,
      fingerprint: turn.input_fingerprint ?? "",
    },
  };
}

/**
 * "Your AI CFO".
 *
 * The owner asks about their own figures. Everything shown here was checked against the data
 * the tools fetched during that turn; what could not be checked is said to have been withheld.
 * A turn costs money, so it happens only on send, and send is locked while one is in flight.
 *
 * The panel is a column: header, a thread that scrolls within the panel, and a composer that
 * stays put at the bottom so the input is always reachable however long the conversation.
 */
export function CfoPanel({
  workspaceId,
  token,
  fingerprint,
  onScenario,
  onProposal,
  onScenarioApplied,
}: Props) {
  const [entries, setEntries] = useState<Entry[]>([]);
  const [suggestions, setSuggestions] = useState<AskSuggestion[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState<ToolTrace[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [lastFailed, setLastFailed] = useState<{ text: string; optionId?: string } | null>(null);
  const [stale, setStale] = useState(false);
  const [turnsLeft, setTurnsLeft] = useState(12);
  const [open, setOpen] = useState<string | null>(null);
  const { options, chosen, setChosen } = useRoutes(workspaceId, token);
  // A ref as well as the busy flag: two clicks in the same tick both see the old state.
  const inFlight = useRef(false);
  const thread = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = thread.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [entries, busy]);

  useEffect(() => {
    let live = true;
    Promise.all([
      fetchAskSuggestions(workspaceId, token).catch(() => null),
      fetchConversation(workspaceId, token).catch(() => null),
    ])
      .then(([offered, conversation]) => {
        if (!live) return;
        if (offered) setSuggestions(offered.items);
        if (conversation) {
          setConversationId(conversation.conversation_id);
          setEntries(conversation.turns.map(fromTurn));
          setStale(conversation.assumptions_stale);
          setTurnsLeft(conversation.turns_left);
        }
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [workspaceId, token, fingerprint]);

  const send = useCallback(
    async (text: string, optionId?: string) => {
      const question = text.trim();
      if (!question || inFlight.current) return;
      inFlight.current = true;
      setBusy(true);
      setError(null);
      setLastFailed(null);
      setProgress([]);
      const pending: Entry = { id: `pending-${Date.now()}`, question, view: null };
      setEntries((current) => [...current, pending]);
      // The question itself is never sent anywhere but this application's own API.
      track("ai_question_submitted", { capability: "ask", provider_route: chosen ?? "direct" });
      try {
        const view = await askCfo(
          workspaceId,
          token,
          { text: question, conversation_id: conversationId, option_id: optionId ?? null },
          chosen,
        );
        setConversationId(view.conversation_id);
        setProgress(view.tool_calls);
        setEntries((current) =>
          current.map((entry) => (entry.id === pending.id ? { ...entry, view } : entry)),
        );
        setTurnsLeft((left) => Math.max(0, left - 1));
        setDraft("");
        // A partial answer is a shorter success, not a failure: it was produced, checked and
        // shown with its withheld claims counted. Only a turn that produced nothing is a
        // failure.
        const produced = ["answered", "partial", "clarifying"].includes(view.status);
        track(produced ? "ai_answer_received" : "ai_request_failed", {
          capability: "ask",
          provider_route: chosen ?? "direct",
          outcome: view.status,
        });
        if (view.message && view.status !== "answered") setError(view.message);
        if (view.scenario_applied) {
          setStale(false);
          onScenario(view.scenario_applied as ScenarioInput);
          onScenarioApplied?.();
        }
        if (view.proposal_id) onProposal();
      } catch (caught) {
        // The question stays in the box so nothing the owner typed is lost to a failure.
        setEntries((current) => current.filter((entry) => entry.id !== pending.id));
        setDraft(question);
        setLastFailed({ text: question, optionId });
        track("ai_request_failed", {
          capability: "ask",
          provider_route: chosen ?? "direct",
          outcome: caught instanceof ApiError ? `http_${caught.status}` : "request_error",
        });
        setError(
          caught instanceof ApiError
            ? caught.describe()
            : "Could not ask that just now. Your figures are unaffected.",
        );
      } finally {
        inFlight.current = false;
        setBusy(false);
      }
    },
    [workspaceId, token, conversationId, chosen, onScenario, onProposal, onScenarioApplied],
  );

  function onKey(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void send(draft);
    }
  }

  const empty = entries.length === 0;

  return (
    <section
      aria-labelledby="cfo-heading"
      className="flex max-h-[calc(100vh-6rem)] min-h-[34rem] flex-col overflow-hidden rounded-2xl border border-line bg-surface shadow-card"
    >
      <div className="border-b border-line px-5 py-3.5">
        <div className="flex items-center gap-2.5">
          <span className="inline-flex size-8 shrink-0 items-center justify-center rounded-xl bg-plum-600 text-white">
            <Icon name="spark" className="size-4" />
          </span>
          <div className="min-w-0">
            <h3 id="cfo-heading" className="t-section whitespace-nowrap">Your AI CFO</h3>
            <p className="truncate text-[11px] text-ink-muted">
              Every answer is checked against your figures first
            </p>
          </div>
        </div>
        {options.length > 1 ? (
          <div className="mt-2.5">
            <ModelChoice options={options} chosen={chosen} onChoose={setChosen} disabled={busy} />
          </div>
        ) : null}
      </div>

      <div ref={thread} className="thread-scroll flex-1 overflow-y-auto px-5 py-4">
        {stale ? (
          <div className="mb-3">
            <Notice tone="info" compact>
              Your figures changed since this conversation&apos;s assumptions were agreed. Confirm
              them again before relying on an earlier what-if.
            </Notice>
          </div>
        ) : null}

        {empty ? (
          <div className="flex h-full flex-col justify-end">
            <p className="text-sm leading-relaxed text-ink-soft">
              Ask what changed, which transactions are behind it, or what happens if a payment
              lands late. Or start with one of these:
            </p>
            <ul className="mt-3 flex flex-col gap-2">
              {suggestions.map((s) => (
                <li key={s.text}>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void send(s.text)}
                    className="group flex w-full items-center gap-2.5 rounded-xl border border-line bg-surface-soft px-3 py-2.5 text-left text-sm text-ink transition hover:border-lavender-300 hover:bg-lavender-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-plum-600 disabled:opacity-60"
                  >
                    <Icon name="spark" className="size-3.5 shrink-0 text-plum-600" />
                    <span className="flex-1">{s.text}</span>
                    <Icon
                      name="arrow"
                      className="size-3.5 text-ink-faint transition group-hover:translate-x-0.5 group-hover:text-plum-600"
                    />
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ) : (
          <ol className="flex flex-col gap-5">
            {entries.map((entry) => (
              <Exchange
                key={entry.id}
                entry={entry}
                busy={busy}
                progress={progress}
                open={open === entry.id}
                onToggle={() => setOpen(open === entry.id ? null : entry.id)}
                onChoose={(option) => void send(option.label, option.id)}
              />
            ))}
          </ol>
        )}
      </div>

      <div className="border-t border-line bg-surface-soft px-4 py-3">
        {error ? (
          <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
            <Notice tone="error" compact>
              {error}
            </Notice>
            {lastFailed ? (
              <button
                type="button"
                className={quietButtonClass}
                onClick={() => void send(lastFailed.text, lastFailed.optionId)}
              >
                Try again
              </button>
            ) : null}
          </div>
        ) : null}
        <form
          className="flex items-end gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            void send(draft);
          }}
        >
          <label className="sr-only" htmlFor="cfo-input">
            Ask about your business
          </label>
          <textarea
            id="cfo-input"
            rows={1}
            value={draft}
            maxLength={500}
            disabled={busy || turnsLeft === 0}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={onKey}
            placeholder="Ask about your business…"
            className={`${textareaClass} field-sizing-content max-h-32`}
          />
          <button
            type="submit"
            className={iconButtonClass}
            aria-label={busy ? "Working" : "Send"}
            disabled={busy || !draft.trim() || turnsLeft === 0}
          >
            {busy ? (
              <span
                className="size-4 animate-spin rounded-full border-2 border-white/40 border-t-white"
                aria-hidden
              />
            ) : (
              <Icon name="send" className="size-4" />
            )}
          </button>
        </form>
        <div className="mt-1.5 flex flex-wrap items-center justify-between gap-x-3 text-[11px] text-ink-faint">
          <span>Enter to send · Shift+Enter for a new line</span>
          {turnsLeft === 0 ? (
            <span className="text-warn">Conversation limit reached. Reload to start a new one</span>
          ) : (
            <span>
              {turnsLeft} question{turnsLeft === 1 ? "" : "s"} left in this conversation
            </span>
          )}
        </div>
        <p className="mt-2 lg:hidden">
          <a href="#cash-overview" className="text-xs text-plum-700 underline underline-offset-2">
            ← Back to your figures
          </a>
        </p>
      </div>
    </section>
  );
}

function Exchange({
  entry,
  busy,
  progress,
  open,
  onToggle,
  onChoose,
}: {
  entry: Entry;
  busy: boolean;
  progress: ToolTrace[];
  open: boolean;
  onToggle: () => void;
  onChoose: (option: ClarifyOption) => void;
}) {
  const view = entry.view;
  return (
    <li className="flex flex-col gap-2.5">
      <p className="ml-8 self-end rounded-2xl rounded-br-md bg-plum-600 px-3.5 py-2 text-sm leading-relaxed text-white">
        {entry.question}
      </p>

      {!view ? (
        <div className="flex items-center gap-2 text-sm text-ink-muted" aria-live="polite">
          <span
            className="size-3.5 animate-spin rounded-full border-2 border-lavender-300 border-t-plum-600"
            aria-hidden
          />
          {busy ? progressLabel(progress) : "…"}
        </div>
      ) : null}

      {view?.answer ? (
        <div className="mr-6 rounded-2xl rounded-bl-md border border-line bg-surface-soft px-3.5 py-3">
          <p className="text-sm leading-relaxed text-ink">{view.answer.answer}</p>
          {view.answer.claims.length ? (
            <ul className="mt-2.5 flex flex-col gap-1.5 border-l-2 border-lavender-300 pl-3">
              {view.answer.claims.map((claim, index) => (
                <li key={`${claim.signal_id}-${index}`} className="text-[13px] leading-relaxed text-ink-soft">
                  {claim.text}
                </li>
              ))}
            </ul>
          ) : null}
          {view.answer.claims_withheld > 0 ? (
            <p className="mt-2 text-xs text-warn">
              {view.answer.claims_withheld} statement{view.answer.claims_withheld === 1 ? " was" : "s were"}{" "}
              withheld because {view.answer.claims_withheld === 1 ? "it" : "they"} could not be checked
              against your figures.
            </p>
          ) : null}
          {view.answer.sources.length ? (
            <div className="mt-3 flex flex-wrap items-center gap-1">
              <span className="mr-1 text-[11px] text-ink-faint">Sources</span>
              {view.answer.sources.map((source) => (
                <Chip key={source}>{sourceLabel(source)}</Chip>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}

      {view?.clarify ? (
        <div className="mr-6 rounded-2xl rounded-bl-md border border-lavender-200 bg-lavender-100/70 px-3.5 py-3">
          <p className="text-sm leading-relaxed text-ink">{view.clarify.question}</p>
          <div className="mt-2.5 flex flex-wrap gap-2">
            {view.clarify.options.map((option) => (
              <button
                key={option.id}
                type="button"
                disabled={busy}
                onClick={() => onChoose(option)}
                className="rounded-xl border border-lavender-300 bg-surface px-3 py-1.5 text-sm font-medium text-plum-800 transition hover:bg-lavender-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-plum-600 disabled:opacity-60"
              >
                {option.label}
              </button>
            ))}
          </div>
        </div>
      ) : null}

      {view?.scenario_applied ? (
        <p className="mr-6 flex items-center gap-1.5 text-xs text-ink-muted">
          <Icon name="chart" className="size-3.5 text-plum-600" />
          The projection now shows this what-if, with its assumptions listed beside the chart.
        </p>
      ) : null}
      {view?.proposal_id ? (
        <p className="mr-6 flex items-center gap-1.5 text-xs text-ink-muted">
          <Icon name="review" className="size-3.5 text-plum-600" />
          Added to your review list. Nothing changes until you accept it.
        </p>
      ) : null}
      {view && view.status === "partial" ? (
        <p className="mr-6 text-xs text-warn">This answer is shorter than it should be.</p>
      ) : null}

      {view?.tool_calls?.length || view?.run ? (
        <div className="mr-6">
          <button type="button" onClick={onToggle} className="text-[11px] text-ink-faint hover:text-plum-700">
            {open ? "Hide how this was answered" : "How this was answered"}
          </button>
          {open ? (
            <pre className="mt-1 overflow-x-auto whitespace-pre-wrap rounded-xl border border-line bg-surface-soft p-2.5 font-mono text-[11px] leading-5 text-ink-muted">
              {[
                ...(view.tool_calls ?? []).map(
                  (call) =>
                    `${call.ok ? "✓" : "✗"} ${call.tool}(${JSON.stringify(call.arguments)}) · ${call.summary}`,
                ),
                view.run
                  ? `run ${view.run.id.slice(0, 8)}… · ${producedBy(view.run)} · ${view.run.attempts} round${view.run.attempts === 1 ? "" : "s"} · ${seconds(view.run.latency_ms)} · ${cost(view.run.cost_micro)}`
                  : "",
                view.run?.validation
                  ? `validation: schema ${view.run.validation.schema?.ok ? "ok" : "failed"} · evidence ${view.run.validation.evidence?.ok ? "ok" : "failed"} · numbers ${view.run.validation.claims_withheld ? `${view.run.validation.claims_withheld} withheld` : "ok"} · support ${view.run.validation.support?.ok ? "ok" : "failed"}`
                  : "",
              ]
                .filter(Boolean)
                .join("\n")}
            </pre>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}

function sourceLabel(source: string): string {
  const [kind] = source.split(":");
  return (
    {
      bal: "Balance",
      month: "Monthly totals",
      chg: "What changed",
      rec: "Recurring payments",
      big: "A large one-off",
      fin: "Loans and owner money",
      fc: "The projection",
      sc: "Your assumptions",
      asm: "An assumption",
      tx: "A transaction",
      cov: "Data coverage",
      cls: "Waiting for review",
    }[kind] ?? "A figure"
  );
}
