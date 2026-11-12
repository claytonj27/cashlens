"use client";

import Link from "next/link";
import { type ReactNode, useEffect, useState } from "react";

import { formatMinor } from "@/lib/api";
import { type AdminQuestion, type AdminResponse, type HistoryEntry, type RespondentCard, fetchAnswerHistory } from "@/lib/discovery-admin-api";
import { percentLabel, statusLabel, statusTone, whenLabel } from "@/lib/discovery-admin-rules";
import { Card, Chip, Notice, ProgressBar, inputClass, quietButtonClass } from "@/components/ui";

/** A card heading that may carry a count or a chip beside the words. */
export function Title({ children }: { children: ReactNode }) {
  return <h3 className="text-sm font-semibold text-ink">{children}</h3>;
}

/** A comparison status as a chip, coloured by how much it asks of the next call. */
export function StatusChip({ status }: { status: string }) {
  const tone = statusTone(status);
  const cls =
    tone === "good"
      ? "bg-emerald-50 text-emerald-800 border-emerald-200"
      : tone === "bad"
        ? "bg-rose-50 text-rose-800 border-rose-200"
        : tone === "warn"
          ? "bg-amber-50 text-amber-800 border-amber-200"
          : tone === "neutral"
            ? "bg-lavender-100 text-plum-700 border-lavender-200"
            : "bg-surface text-ink-muted border-line";
  return <span className={`inline-block whitespace-nowrap rounded-md border px-1.5 py-0.5 text-[11px] font-semibold ${cls}`}>{statusLabel(status)}</span>;
}

export function LevelChip({ level, core }: { level: string; core?: boolean }) {
  return <Chip tone={core ? "accent" : "muted"}>{core ? "Core" : level === "recommended" ? "Recommended" : level === "deep_dive" ? "Deep dive" : level}</Chip>;
}

/** One answer, rendered from its server-side display string, with its note and typed detail. */
export function AnswerCell({ response, empty = "—" }: { response: AdminResponse | null | undefined; empty?: string }) {
  if (!response) return <span className="text-ink-faint">{empty}</span>;
  if (response.status === "skipped") return <span className="italic text-ink-muted">skipped</span>;
  return (
    <div className="flex flex-col gap-0.5">
      <span className="whitespace-pre-wrap break-words">{response.display ?? String(response.answer ?? "")}</span>
      {response.note ? <span className="text-xs text-ink-muted">Note: {response.note}</span> : null}
    </div>
  );
}

export function ScenarioDetail({ response }: { response: AdminResponse | null }) {
  const s = response?.scenario as Record<string, unknown> | null | undefined;
  if (!s) return <AnswerCell response={response} />;
  const reasons = Array.isArray(s.reason_codes) ? (s.reason_codes as string[]) : [];
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-sm">
      <dt className="text-ink-muted">Decision</dt>
      <dd className="font-semibold capitalize">{String(s.decision ?? "").replace(/_/g, " ")}</dd>
      <dt className="text-ink-muted">Confidence</dt>
      <dd>{String(s.confidence ?? "—")} / 5</dd>
      <dt className="text-ink-muted">Max exposure</dt>
      <dd>{typeof s.maximum_exposure_minor === "number" ? formatMinor(s.maximum_exposure_minor) : "—"}</dd>
      <dt className="text-ink-muted">Reasons</dt>
      <dd>{reasons.length ? reasons.join(", ") : "—"}</dd>
      <dt className="text-ink-muted">More info</dt>
      <dd>{(s.additional_information_needed as string) || "—"}</dd>
      <dt className="text-ink-muted">Would change</dt>
      <dd>{(s.what_would_change_my_decision as string) || "—"}</dd>
      <dt className="text-ink-muted">Notes</dt>
      <dd>{(s.notes as string) || "—"}</dd>
    </dl>
  );
}

export function CalibrationDetail({ response }: { response: AdminResponse | null }) {
  const c = response?.calibration as Record<string, unknown> | null | undefined;
  if (!c) return <AnswerCell response={response} />;
  const reasons = Array.isArray(c.reason_codes) ? (c.reason_codes as string[]) : [];
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-sm">
      <dt className="text-ink-muted">Classification</dt>
      <dd className="font-semibold">{String(c.classification_category ?? "")}</dd>
      <dt className="text-ink-muted">Confidence</dt>
      <dd>{String(c.classification_confidence ?? "—")} / 5</dd>
      <dt className="text-ink-muted">Materiality</dt>
      <dd className="capitalize">{String(c.underwriting_materiality ?? "—")}</dd>
      <dt className="text-ink-muted">Signal</dt>
      <dd className="capitalize">{String(c.underwriting_signal ?? "—")}</dd>
      <dt className="text-ink-muted">Impact</dt>
      <dd>{(c.decision_impact as string) || "—"}</dd>
      <dt className="text-ink-muted">Handling</dt>
      <dd>{(c.handling_recommendation as string) || "—"}</dd>
      <dt className="text-ink-muted">Reasons</dt>
      <dd>{reasons.length ? reasons.join(", ") : "—"}</dd>
      <dt className="text-ink-muted">Needs context</dt>
      <dd>{c.needs_more_context ? `yes — ${(c.context_needed as string) || ""}` : "no"}</dd>
      <dt className="text-ink-muted">Reasoning</dt>
      <dd>{(c.reasoning as string) || "—"}</dd>
    </dl>
  );
}

export function TypedAnswer({ question, response }: { question: AdminQuestion; response: AdminResponse | null }) {
  if (question.question_type === "scenario_decision") return <ScenarioDetail response={response} />;
  if (question.question_type === "transaction_calibration") return <CalibrationDetail response={response} />;
  return <AnswerCell response={response} />;
}

export function RespondentCards({ cards, extra, href }: { cards: RespondentCard[]; extra?: (card: RespondentCard) => ReactNode; href?: (card: RespondentCard) => string }) {
  const stakeholders = cards.filter((c) => c.kind === "stakeholder");
  const reviewers = cards.filter((c) => c.kind === "reviewer");
  return (
    <div className="flex flex-col gap-3">
      <div className="grid gap-3 md:grid-cols-3">
        {stakeholders.map((c) => (
          <Card key={c.assignment_id} className={href ? "transition hover:border-plum-400" : undefined}>
            <Title>
              {href ? <Link href={href(c)} className="hover:underline">{c.label} →</Link> : c.label} <span className="text-xs font-normal text-ink-muted">· {c.name}</span>
            </Title>
            <div className="mt-2 flex items-center gap-2 text-xs text-ink-muted">
              <Chip tone={c.status === "not_started" ? "muted" : "accent"}>{c.status.replace(/_/g, " ")}</Chip>
              <span>last activity {whenLabel(c.last_activity)}</span>
            </div>
            <div className="mt-3">
              <ProgressBar value={c.core_answered} max={c.core_total} label={`Core ${c.core_answered}/${c.core_total} · ${c.core_percent}%`} />
            </div>
            <dl className="mt-3 grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
              <dt className="text-ink-muted">Total answered</dt>
              <dd>{c.answered} / {c.total}</dd>
              <dt className="text-ink-muted">Recommended</dt>
              <dd>{c.by_group.recommended?.answered ?? 0} / {c.by_group.recommended?.available ?? 0}</dd>
              <dt className="text-ink-muted">Deep dive</dt>
              <dd>{c.by_group.deep_dive?.answered ?? 0} / {c.by_group.deep_dive?.available ?? 0}</dd>
              <dt className="text-ink-muted">{c.template_slug === "discovery-engineer" ? "Technical scenarios" : "Calibration"}</dt>
              <dd>
                {c.template_slug === "discovery-engineer"
                  ? `${c.by_group.scenarios?.answered ?? 0} / ${c.by_group.scenarios?.available ?? 0}`
                  : `${c.by_group.calibration?.answered ?? 0} / ${c.by_group.calibration?.available ?? 0}`}
              </dd>
              {c.template_slug !== "discovery-engineer" ? (
                <>
                  <dt className="text-ink-muted">Borrower scenarios</dt>
                  <dd>{c.by_group.scenarios?.answered ?? 0} / {c.by_group.scenarios?.available ?? 0}</dd>
                </>
              ) : null}
              <dt className="text-ink-muted">Skips</dt>
              <dd>{c.skips}</dd>
              <dt className="text-ink-muted">Prefer not to answer</dt>
              <dd>{c.prefer_not_to_answer}</dd>
              <dt className="text-ink-muted">Sessions</dt>
              <dd>{c.sessions}</dd>
            </dl>
            {extra ? extra(c) : null}
          </Card>
        ))}
      </div>
      {reviewers.length ? (
        <p className="text-xs text-ink-muted">
          {reviewers.length} reviewer assignment{reviewers.length === 1 ? "" : "s"} (an account holding more than one template) listed under All responses, not compared here.
        </p>
      ) : null}
    </div>
  );
}

export function SearchBox({ value, onChange, placeholder = "Search question text, key, id or section" }: { value: string; onChange: (v: string) => void; placeholder?: string }) {
  return <input type="search" value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder} className={`${inputClass} max-w-md`} aria-label="Search" />;
}

export function FilterBar<T extends string>({ value, options, onChange }: { value: T; options: { key: T; label: string }[]; onChange: (v: T) => void }) {
  return (
    <div className="flex flex-wrap gap-1.5" role="tablist" aria-label="Filter">
      {options.map((o) => (
        <button
          key={o.key}
          type="button"
          role="tab"
          aria-selected={o.key === value}
          onClick={() => onChange(o.key)}
          className={`rounded-lg border px-2.5 py-1 text-xs font-semibold transition ${o.key === value ? "border-plum-600 bg-plum-600 text-white" : "border-line-strong bg-surface text-ink-soft hover:border-plum-400 hover:text-plum-700"}`}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

/** Why a question exists, from the authored metadata; nothing here is inferred. */
export function WhyBlock({ question }: { question: AdminQuestion }) {
  const m = question.meta;
  if (!m?.why && !m?.tells && !m?.tests && !m?.teaches) return null;
  return (
    <div className="flex flex-col gap-1 rounded-lg bg-lavender-50 p-3 text-xs text-ink-soft">
      {m.why ? <p><span className="font-semibold text-ink">Why we ask.</span> {m.why}</p> : null}
      {m.tells ? <p><span className="font-semibold text-ink">What it tells us.</span> {m.tells}</p> : null}
      {m.tests ? <p><span className="font-semibold text-ink">What this tests.</span> {m.tests}</p> : null}
      {m.teaches ? <p><span className="font-semibold text-ink">What different answers would teach us.</span> {m.teaches}</p> : null}
    </div>
  );
}

export type DrawerTarget = {
  question: AdminQuestion;
  answers: { label: string; response: AdminResponse | null; assignment_id?: string }[];
};

/** Earlier values of one answer, as the triggers recorded them; read when the drawer opens. */
function History({ assignmentId, questionId, token }: { assignmentId: string; questionId: string; token: string | null }) {
  const [state, setState] = useState<{ key: string; entries: HistoryEntry[] | null; error: string | null }>({ key: "", entries: null, error: null });
  const key = `${assignmentId}|${questionId}`;
  useEffect(() => {
    let live = true;
    fetchAnswerHistory(assignmentId, questionId, token)
      .then((h) => {
        if (live) setState({ key, entries: h.entries, error: null });
      })
      .catch((caught: unknown) => {
        if (live) setState({ key, entries: null, error: caught instanceof Error ? caught.message : "Could not load history." });
      });
    return () => {
      live = false;
    };
  }, [assignmentId, questionId, token, key]);
  if (state.key !== key) return <p className="mt-2 text-xs text-ink-faint">Loading history…</p>;
  if (state.error) return <p className="mt-2 text-xs text-ink-faint">History unavailable: {state.error}</p>;
  if (!state.entries || state.entries.length === 0) return <p className="mt-2 text-xs text-ink-faint">No earlier answers.</p>;
  return (
    <div className="mt-2">
      <p className="text-xs font-semibold text-ink">Previous answers</p>
      <ul className="mt-1 flex flex-col gap-0.5 text-xs">
        {state.entries.map((e, i) => (
          <li key={i} className="flex gap-2">
            <span className="w-28 shrink-0 text-ink-faint">{whenLabel(e.valid_to)}</span>
            <span className="text-ink-soft">{e.display ?? "(none)"}{e.change === "deleted" ? " · deleted" : ""}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** The detail panel: the question, its metadata, and each respondent's exact answer. Read-only. */
export function Drawer({ target, onClose, token = null }: { target: DrawerTarget | null; onClose: () => void; token?: string | null }) {
  if (!target) return null;
  const q = target.question;
  const brief = q.scenario_brief as Record<string, unknown> | null;
  const tc = q.transaction_case as Record<string, unknown> | null;
  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-black/30" onClick={onClose} role="presentation">
      <aside className="h-full w-full max-w-xl overflow-y-auto bg-surface p-5 shadow-xl" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="Answer detail">
        <div className="flex items-start justify-between gap-3">
          <div className="flex flex-wrap items-center gap-1.5">
            <LevelChip level={q.level} core={q.required_core} />
            <Chip tone="muted">{q.topic}</Chip>
            <Chip tone="muted">{q.question_type.replace(/_/g, " ")}</Chip>
          </div>
          <button type="button" onClick={onClose} className={quietButtonClass}>Close</button>
        </div>
        <h2 className="mt-3 text-lg font-bold leading-snug text-ink">{q.meta?.title ?? q.question_text}</h2>
        {q.meta?.title ? <p className="mt-1 text-sm text-ink-soft">{q.question_text}</p> : null}
        {q.helper_text ? <p className="mt-1 text-sm text-ink-muted">{q.helper_text}</p> : null}
        <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-xs text-ink-muted">
          <dt>Section</dt><dd>{q.section_title} <span className="text-ink-faint">({q.section_slug})</span></dd>
          <dt>Key</dt><dd className="font-mono">{q.question_key}</dd>
          <dt>ID</dt><dd className="font-mono">{q.id}</dd>
          <dt>Priority</dt><dd>{q.priority}{q.sensitivity ? ` · ${q.sensitivity} sensitivity` : ""}</dd>
          {q.meta?.system_use?.length ? (<><dt>System use</dt><dd>{q.meta.system_use.join(" · ")}</dd></>) : null}
          {q.meta?.semantic_class ? (<><dt>Semantic class</dt><dd>{q.meta.semantic_class}</dd></>) : null}
        </dl>
        <div className="mt-3"><WhyBlock question={q} /></div>
        {brief ? (
          <div className="mt-4">
            <Title>Borrower brief</Title>
            <pre className="mt-1 max-h-72 overflow-auto whitespace-pre-wrap rounded-lg bg-lavender-50 p-3 text-xs">{JSON.stringify(brief, null, 2)}</pre>
          </div>
        ) : null}
        {tc ? (
          <div className="mt-4">
            <Title>Transaction context</Title>
            <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-xs">
              <dt className="text-ink-muted">Direction</dt><dd>{String(tc.direction)}</dd>
              <dt className="text-ink-muted">Amount</dt><dd>{typeof tc.amount_minor === "number" ? formatMinor(tc.amount_minor) : "—"}</dd>
              <dt className="text-ink-muted">Descriptor</dt><dd className="font-mono break-all">{String(tc.descriptor ?? "")}</dd>
              <dt className="text-ink-muted">Business</dt><dd>{String(tc.business_vertical ?? "—")}{tc.business_description ? ` — ${String(tc.business_description)}` : ""}</dd>
              {tc.memo ? (<><dt className="text-ink-muted">Memo</dt><dd>{String(tc.memo)}</dd></>) : null}
              {tc.case_family ? (<><dt className="text-ink-muted">Family</dt><dd>{String(tc.case_family)} · {String(tc.variant ?? "")}{tc.changed_feature ? ` · changed ${String(tc.changed_feature)}` : ""}</dd></>) : null}
              {tc.reveal ? (<><dt className="text-ink-muted">Reveal</dt><dd>{JSON.stringify(tc.reveal)}</dd></>) : null}
            </dl>
            {tc.context && Object.keys(tc.context as object).length ? <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap rounded-lg bg-lavender-50 p-3 text-xs">{JSON.stringify(tc.context, null, 2)}</pre> : null}
          </div>
        ) : null}
        <div className="mt-4 flex flex-col gap-3">
          {target.answers.map((a) => (
            <Card key={a.label}>
              <Title>{a.label}</Title>
              <div className="mt-2 text-sm"><TypedAnswer question={q} response={a.response} /></div>
              {a.response ? (
                <p className="mt-2 text-xs text-ink-faint">
                  answered {whenLabel(a.response.created_at)} · updated {whenLabel(a.response.updated_at)} · v{a.response.question_version ?? "?"}
                </p>
              ) : (
                <p className="mt-2 text-xs text-ink-faint">no answer yet</p>
              )}
              {a.assignment_id ? <History assignmentId={a.assignment_id} questionId={q.id} token={token} /> : null}
            </Card>
          ))}
        </div>
        <Notice tone="info">Read-only. Answers change only when the respondent changes them.</Notice>
      </aside>
    </div>
  );
}

export function useDrawer() {
  const [target, setTarget] = useState<DrawerTarget | null>(null);
  return { target, open: setTarget, close: () => setTarget(null) };
}

export function Loading({ what }: { what: string }) {
  return <p className="text-ink-muted">Loading {what}…</p>;
}

export function Pct({ n, d }: { n: number; d: number }) {
  return <span>{percentLabel(n, d)}</span>;
}
