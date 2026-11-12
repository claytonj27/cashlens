"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { type AdminQuestion, type AdminResponse, type Engagement, fetchComparisons, fetchDataset } from "@/lib/discovery-admin-api";
import {
  IMPORTANT_ORDER,
  IMPORTANT_REASON_LABEL,
  RESPONSES_FILTERS,
  RESPONSE_STATUS_LABEL,
  type ResponsesFilter,
  analysisLinks,
  hrefFor,
  importantResponses,
  levelLabel,
  matchesAnswerSearch,
  matchesResponsesFilter,
  respondentQuestions,
  responseStatus,
  whenLabel,
  writtenComment,
} from "@/lib/discovery-admin-rules";
import { type DrawerTarget, FilterBar, Loading, SearchBox, StatusChip, Title, TypedAnswer } from "@/components/discovery-admin/shared";
import { useAdminData } from "@/components/discovery-admin/use-admin-data";
import { Card, Chip, Notice, ProgressBar, quietButtonClass, selectClass } from "@/components/ui";

/**
 * Respondent → questions → answers. Every question the person was asked, in the order they
 * were asked, with the answer in front and the analysis one click away. Read-only.
 */
export function ResponsesTab({ token, open }: { token: string | null; open: (t: DrawerTarget) => void }) {
  const params = useSearchParams();
  const router = useRouter();
  const data = useAdminData(fetchDataset, token);
  const comparisons = useAdminData(fetchComparisons, token);
  const wanted = params.get("respondent");
  const openId = params.get("open");
  const initialFilter = (RESPONSES_FILTERS.find((f) => f.key === params.get("filter"))?.key ?? "all") as ResponsesFilter;
  const [filter, setFilter] = useState<ResponsesFilter>(initialFilter);
  const [query, setQuery] = useState(params.get("q") ?? "");
  const [section, setSection] = useState("all");
  const [commentsOnly, setCommentsOnly] = useState(false);
  const [compareOpen, setCompareOpen] = useState<Record<string, boolean>>({});

  const stakeholders = useMemo(() => (data.data?.respondents ?? []).filter((r) => r.kind === "stakeholder"), [data.data]);
  const reviewers = useMemo(() => (data.data?.respondents ?? []).filter((r) => r.kind === "reviewer"), [data.data]);
  const selected: Engagement | null = useMemo(() => {
    const all = data.data?.respondents ?? [];
    return all.find((r) => r.assignment_id === wanted) ?? stakeholders[0] ?? null;
  }, [data.data, wanted, stakeholders]);

  const questions = useMemo(
    () => (data.data && selected ? respondentQuestions(data.data.questions, data.data.sections, selected.template_slug) : []),
    [data.data, selected],
  );
  const answers = useMemo(() => (data.data && selected ? data.data.answers[selected.assignment_id] ?? {} : {}), [data.data, selected]);
  const comparisonByQuestion = useMemo(() => {
    const map = new Map<string, { status: string; lead: AdminResponse | null; partner: AdminResponse | null; business_fact: boolean; underwriting: boolean }>();
    for (const row of comparisons.data?.rows ?? []) map.set(row.question.id, row);
    return map;
  }, [comparisons.data]);
  const otherFounder = useMemo(() => {
    if (!selected || !data.data) return null;
    const other = selected.template_slug === "discovery-lead" ? "discovery-partner" : selected.template_slug === "discovery-partner" ? "discovery-lead" : null;
    return other ? (stakeholders.find((r) => r.template_slug === other) ?? null) : null;
  }, [selected, data.data, stakeholders]);

  const shown = useMemo(
    () =>
      questions.filter((q) => {
        const r = answers[q.id];
        if (!matchesResponsesFilter(q, r, filter)) return false;
        if (section !== "all" && q.section_slug !== section) return false;
        if (commentsOnly && !writtenComment(q, r)) return false;
        return matchesAnswerSearch(q, r, query);
      }),
    [questions, answers, filter, section, commentsOnly, query],
  );
  const important = useMemo(
    () => importantResponses(questions, (id) => answers[id], (id) => comparisonByQuestion.get(id)?.status),
    [questions, answers, comparisonByQuestion],
  );

  const target = (q: AdminQuestion): DrawerTarget => {
    const mine = { label: selected?.label ?? "", response: answers[q.id] ?? null, assignment_id: selected?.assignment_id };
    if (otherFounder && q.owner === "founder" && data.data) {
      const theirs = { label: otherFounder.label, response: data.data.answers[otherFounder.assignment_id]?.[q.id] ?? null, assignment_id: otherFounder.assignment_id };
      return { question: q, answers: selected?.template_slug === "discovery-lead" ? [mine, theirs] : [theirs, mine] };
    }
    return { question: q, answers: [mine] };
  };

  // A deep link (?open=<question id>) opens that answer once the data is in hand.
  useEffect(() => {
    if (!openId || !data.data || !selected) return;
    const q = data.data.questions.find((x) => x.id === openId);
    if (q) open(target(q));
    router.replace(hrefFor("responses", { respondent: selected.assignment_id }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [openId, data.data, selected]);

  if (data.loading) return <Loading what="responses" />;
  if (!data.data) return data.refused ? <Notice tone="error">This account is not a Discovery admin.</Notice> : <Notice tone="error">{data.error}</Notice>;
  if (!selected) return <Notice tone="info">No respondent holds a real template yet.</Notice>;
  const sections = data.data.sections.filter((s) => s.positions[selected.template_slug] !== undefined).sort((a, b) => a.positions[selected.template_slug] - b.positions[selected.template_slug]);
  const cases = selected.template_slug === "discovery-engineer" ? null : selected.by_group.calibration;
  const scen = selected.by_group.scenarios;

  return (
    <div className="flex flex-col gap-4">
      <div className="grid gap-3 md:grid-cols-3">
        {stakeholders.map((r) => (
          <Link key={r.assignment_id} href={hrefFor("responses", { respondent: r.assignment_id })} className={`rounded-2xl border p-4 transition ${r.assignment_id === selected.assignment_id ? "border-plum-600 bg-lavender-50" : "border-line bg-surface hover:border-plum-400"}`}>
            <p className="text-lg font-bold text-ink">{r.label}</p>
            <dl className="mt-1 grid grid-cols-2 gap-x-3 gap-y-0.5 text-xs">
              <dt className="text-ink-muted">Core</dt><dd>{r.core_answered} / {r.core_total} · {r.core_percent}%</dd>
              <dt className="text-ink-muted">Total answered</dt><dd>{r.answered} / {r.total}</dd>
              <dt className="text-ink-muted">Remaining Core</dt><dd>{r.core_total - r.core_answered}</dd>
              <dt className="text-ink-muted">Skipped</dt><dd>{r.skips}</dd>
              <dt className="text-ink-muted">Last activity</dt><dd>{r.last_activity ? whenLabel(r.last_activity) : "Not started"}</dd>
            </dl>
          </Link>
        ))}
      </div>
      {reviewers.length ? (
        <p className="text-xs text-ink-muted">
          Reviewer accounts (holding more than one template):{" "}
          {reviewers.map((r) => (
            <Link key={r.assignment_id} href={hrefFor("responses", { respondent: r.assignment_id })} className="underline">{r.label} (reviewer)</Link>
          )).reduce<React.ReactNode[]>((acc, el, i) => (i ? [...acc, " · ", el] : [el]), [])}
        </p>
      ) : null}

      <Card>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="text-xl font-bold text-ink">{selected.label}{selected.kind === "reviewer" ? " (reviewer)" : ""} <span className="text-sm font-normal text-ink-muted">· {selected.name}</span></h2>
            <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-4 gap-y-0.5 text-sm">
              <dt className="text-ink-muted">Core</dt><dd className="font-semibold">{selected.core_answered} / {selected.core_total} answered</dd>
              <dt className="text-ink-muted">All</dt><dd>{selected.answered} / {selected.total} answered</dd>
              {cases ? (<><dt className="text-ink-muted">Calibration</dt><dd>{cases.answered} / {cases.available}</dd></>) : null}
              <dt className="text-ink-muted">{selected.template_slug === "discovery-engineer" ? "Technical scenarios" : "Borrower scenarios"}</dt><dd>{scen?.answered ?? 0} / {scen?.available ?? 0}</dd>
              <dt className="text-ink-muted">Last activity</dt><dd>{selected.last_activity ? whenLabel(selected.last_activity) : "Not started"}</dd>
            </dl>
          </div>
          <div className="min-w-[16rem] flex-1">
            <ProgressBar value={selected.core_answered} max={selected.core_total} label={`Core ${selected.core_percent}%`} />
            <ul className="mt-2 grid grid-cols-2 gap-x-3 gap-y-0.5 text-xs">
              {selected.topics.map((t) => (
                <li key={t.topic} className="flex justify-between"><span className="text-ink-muted">{t.topic}</span><span>{t.answered} / {t.available}</span></li>
              ))}
            </ul>
          </div>
        </div>
        {selected.answered === 0 ? (
          <Notice tone="info">{selected.label} has not answered any Discovery questions yet. The questions assigned to {selected.label} are listed below.</Notice>
        ) : null}
      </Card>

      <div className="grid gap-4 lg:grid-cols-[1fr_20rem]">
        <div className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <SearchBox value={query} onChange={setQuery} placeholder="Search question, id, or answer text" />
            <select className={`${selectClass} w-auto`} value={section} onChange={(e) => setSection(e.target.value)} aria-label="Section">
              <option value="all">All sections</option>
              {sections.map((s) => <option key={s.id} value={s.slug}>{s.title}</option>)}
            </select>
            <label className="flex items-center gap-1.5 text-xs text-ink-soft">
              <input type="checkbox" checked={commentsOnly} onChange={(e) => setCommentsOnly(e.target.checked)} /> Only answers with written comments
            </label>
            <span className="text-xs text-ink-muted">{shown.length} of {questions.length}</span>
          </div>
          <FilterBar value={filter} options={RESPONSES_FILTERS} onChange={setFilter} />
          <ol className="flex flex-col gap-2">
            {shown.map((q) => {
              const r = answers[q.id];
              const status = responseStatus(r);
              const cmp = comparisonByQuestion.get(q.id);
              const shared = q.owner === "founder" && otherFounder !== null;
              const theirs = shared && data.data ? data.data.answers[otherFounder!.assignment_id]?.[q.id] ?? null : null;
              const links = analysisLinks(q, cmp?.business_fact ?? false, cmp?.underwriting ?? false);
              return (
                <li key={q.id} className="rounded-xl border border-line bg-surface p-3">
                  <div className="flex flex-wrap items-center gap-1.5 text-[11px] text-ink-faint">
                    <Chip tone={q.required_core ? "accent" : "muted"}>{levelLabel(q)}</Chip>
                    <span>{q.section_title}</span>
                    <StatusChip status={status === "answered" ? "ANSWERED" : status === "prefer_not" ? "PREFER_NOT_TO_ANSWER" : status === "skipped" ? "SKIPPED" : "UNANSWERED"} />
                    {r?.created_at ? <span>answered {whenLabel(r.created_at)}</span> : null}
                    {r?.updated_at && r.updated_at !== r.created_at ? <span>· updated {whenLabel(r.updated_at)}</span> : null}
                    <span className="ml-auto font-mono">{q.question_key}</span>
                  </div>
                  <p className="mt-1 text-sm text-ink-soft">{q.meta?.title ? `${q.meta.title} — ` : ""}{q.question_text}</p>
                  <div className="mt-1.5 text-base font-semibold text-ink">
                    {r ? <TypedAnswer question={q} response={r} /> : <span className="font-normal text-ink-faint">No answer yet</span>}
                  </div>
                  {r?.note && q.question_type !== "short_text" && q.question_type !== "long_text" ? null : null}
                  <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
                    <button type="button" className={quietButtonClass} onClick={() => open(target(q))}>Details{r ? " & history" : ""}</button>
                    {shared ? (
                      <button type="button" className={quietButtonClass} onClick={() => setCompareOpen((s) => ({ ...s, [q.id]: !s[q.id] }))}>
                        {compareOpen[q.id] ? "Hide comparison" : "Compare founder answer"}
                      </button>
                    ) : null}
                    {links.map((l) => (
                      <Link key={l.tab} href={hrefFor(l.tab, { q: q.question_key })} className="text-plum-600 hover:underline">{l.label}</Link>
                    ))}
                  </div>
                  {shared && compareOpen[q.id] ? (
                    <div className="mt-2 grid gap-2 rounded-lg bg-lavender-50 p-2 text-sm md:grid-cols-2">
                      {(selected.template_slug === "discovery-lead" ? [[selected.label, r], [otherFounder!.label, theirs]] : [[otherFounder!.label, theirs], [selected.label, r]]).map(([label, resp]) => (
                        <div key={String(label)}>
                          <p className="text-xs font-semibold uppercase text-ink-muted">{String(label)}</p>
                          {resp ? <TypedAnswer question={q} response={resp as AdminResponse} /> : <span className="text-ink-faint">Awaiting {String(label)}</span>}
                        </div>
                      ))}
                      {cmp ? <p className="text-[11px] text-ink-muted md:col-span-2"><StatusChip status={cmp.status} /></p> : null}
                    </div>
                  ) : null}
                </li>
              );
            })}
            {shown.length === 0 ? <li className="rounded-xl border border-line p-4 text-sm text-ink-muted">Nothing matches.</li> : null}
          </ol>
        </div>
        <aside className="flex flex-col gap-3">
          <Card>
            <Title>Important responses</Title>
            <p className="text-[11px] text-ink-faint">From the authored metadata and the comparison statuses; nothing inferred.</p>
            {important.length === 0 ? (
              <p className="mt-2 text-sm text-ink-muted">Nothing yet — nothing has been answered.</p>
            ) : (
              IMPORTANT_ORDER.filter((k) => important.some((i) => i.reason === k)).map((k) => (
                <div key={k} className="mt-2">
                  <p className="text-xs font-semibold text-ink">{IMPORTANT_REASON_LABEL[k]} <span className="font-normal text-ink-muted">· {important.filter((i) => i.reason === k).length}</span></p>
                  <ul className="mt-1 flex flex-col gap-1">
                    {important.filter((i) => i.reason === k).slice(0, 8).map((i) => (
                      <li key={i.question.id} className="cursor-pointer rounded-md p-1 text-xs hover:bg-lavender-50" onClick={() => open(target(i.question))}>
                        <p className="text-ink-soft">{i.question.meta?.title ?? i.question.question_text}</p>
                        <p className="truncate font-semibold text-ink">{i.response.display}</p>
                      </li>
                    ))}
                  </ul>
                </div>
              ))
            )}
          </Card>
          <p className="text-[11px] text-ink-faint">{RESPONSE_STATUS_LABEL.prefer_not} counts as answered; skipped counts as unanswered.</p>
        </aside>
      </div>
    </div>
  );
}
