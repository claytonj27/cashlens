"use client";

import { useMemo, useState } from "react";

import { formatMinor } from "@/lib/api";
import {
  type CalibrationRow,
  type Changed,
  type ComparisonRow,
  type EngineeringItem,
  fetchBusiness,
  fetchCalibration,
  fetchComparisons,
  fetchEngagement,
  fetchEngineering,
  fetchOpenQuestions,
  fetchOverview,
  fetchScenarios,
  fetchUnderwriting,
} from "@/lib/discovery-admin-api";
import {
  OPEN_KIND_LABEL,
  agoLabel,
  hrefFor,
  isAwaiting,
  matchesSearch,
  whenLabel,
} from "@/lib/discovery-admin-rules";
import { ComparisonTable } from "@/components/discovery-admin/comparison-table";
import {
  AnswerCell,
  CalibrationDetail,
  type DrawerTarget,
  FilterBar,
  LevelChip,
  Loading,
  RespondentCards,
  ScenarioDetail,
  SearchBox,
  StatusChip,
  Title,
  TypedAnswer,
} from "@/components/discovery-admin/shared";
import { useAdminData } from "@/components/discovery-admin/use-admin-data";
import Link from "next/link";
import { useSearchParams } from "next/navigation";

import { Card, Chip, Notice, ProgressBar } from "@/components/ui";

type TabProps = { token: string | null; open: (t: DrawerTarget) => void };

function Problem({ error, refused }: { error: string | null; refused: boolean }) {
  if (refused) return <Notice tone="error">This account is not a Discovery admin.</Notice>;
  if (error) return <Notice tone="error">{error}</Notice>;
  return null;
}

const founderAnswers = (labels: [string, string], r: ComparisonRow, ids: Record<string, string | null> = {}): DrawerTarget => ({
  question: r.question,
  answers: [
    { label: labels[0], response: r.lead, assignment_id: ids[labels[0]] ?? undefined },
    { label: labels[1], response: r.partner, assignment_id: ids[labels[1]] ?? undefined },
  ],
});

/** The initial search and filter a tab starts with, from ?q= and ?filter= on the URL. */
function useInitial() {
  const params = useSearchParams();
  return { q: params.get("q") ?? "", filter: params.get("filter") };
}

function Stat({ href, label, value }: { href: string; label: string; value: number | string }) {
  return (
    <Link href={href} className="rounded-xl border border-line bg-surface p-3 transition hover:border-plum-400">
      <p className="text-2xl font-bold text-ink">{value}</p>
      <p className="text-xs text-ink-muted">{label}</p>
    </Link>
  );
}

// ------------------------------------------------------------------------------ overview --
export function OverviewTab({ token, open }: TabProps) {
  const { data, error, refused, loading } = useAdminData(fetchOverview, token);
  if (loading) return <Loading what="overview" />;
  if (!data) return <Problem error={error} refused={refused} />;
  const labels: [string, string] = ["Lead", "Partner"];
  return (
    <div className="flex flex-col gap-5">
      <RespondentCards cards={data.respondents} href={(c) => hrefFor("responses", { respondent: c.assignment_id })} />
      <Card>
        <Title>Analysis at a glance <span className="text-xs font-normal text-ink-muted">· every number opens its view</span></Title>
        <div className="mt-2 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          {data.respondents.filter((c) => c.kind === "stakeholder").map((c) => (
            <Stat key={c.assignment_id} href={hrefFor("responses", { respondent: c.assignment_id, filter: "answered" })} label={`${c.label} answers received`} value={c.answered} />
          ))}
          <Stat href={hrefFor("comparison", { filter: "both" })} label="Founder questions both answered" value={`${data.founder.both} / ${data.founder.total}`} />
          <Stat href={hrefFor("comparison", { filter: "disagree" })} label="Disagreements" value={data.founder.different + data.founder.material} />
          <Stat href={hrefFor("business")} label="Business facts captured" value={data.facts_collected} />
          <Stat href={hrefFor("calibration", { filter: "both" })} label="Calibration cases both answered" value={`${data.calibration.both} / ${data.calibration.cases}`} />
          <Stat href={hrefFor("scenarios", { filter: "both" })} label="Borrower scenarios both answered" value={`${data.scenarios.both} / ${data.scenarios.scenarios}`} />
          {data.respondents.filter((c) => c.template_slug === "discovery-engineer" && c.kind === "stakeholder").slice(0, 1).map((c) => (
            <Stat key={c.assignment_id} href={hrefFor("responses", { respondent: c.assignment_id, filter: "core" })} label="Engineer Core answered" value={`${data.engineer_core.answered} / ${data.engineer_core.total}`} />
          ))}
          <Stat href={hrefFor("open")} label="Open high-priority questions" value={data.open_high_priority} />
        </div>
      </Card>
      <Card>
        <Title>Recent responses</Title>
        {data.recent_activity.length === 0 ? (
          <p className="mt-2 text-sm text-ink-muted">No answers yet.</p>
        ) : (
          <ul className="mt-2 flex flex-col divide-y divide-line">
            {data.recent_activity.slice(0, 10).map((a) => (
              <li key={a.assignment_id + a.question_id}>
                <Link href={hrefFor("responses", { respondent: a.assignment_id, open: a.question_id })} className="flex flex-col gap-0.5 py-2 hover:bg-lavender-50">
                  <span className="text-xs text-ink-muted"><span className="font-semibold text-ink">{a.respondent}{a.kind === "reviewer" ? " (reviewer)" : ""}</span> · {agoLabel(a.at)} · {whenLabel(a.at)}</span>
                  <span className="text-sm text-ink-soft">&ldquo;{a.question_text}&rdquo;</span>
                  <span className="text-sm font-semibold text-ink">{a.status === "skipped" ? "Skipped" : `Answered: ${a.display ?? ""}`}</span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </Card>
      <div className="grid gap-3 md:grid-cols-3">
        <Card>
          <Title>Shared founder questions</Title>
          <dl className="mt-2 grid grid-cols-[1fr_auto] gap-y-1 text-sm">
            <dt>Both answered</dt><dd className="font-semibold">{data.founder.both} / {data.founder.total}</dd>
            <dt>Only Lead</dt><dd>{data.founder.only_lead}</dd>
            <dt>Only Partner</dt><dd>{data.founder.only_partner}</dd>
            <dt>Neither</dt><dd>{data.founder.neither}</dd>
            <dt className="pt-1 text-ink-muted">Agree · roughly</dt><dd className="pt-1">{data.founder.agree} · {data.founder.roughly}</dd>
            <dt className="text-ink-muted">Different · material</dt><dd>{data.founder.different} · {data.founder.material}</dd>
          </dl>
        </Card>
        <Card>
          <Title>Calibration</Title>
          <dl className="mt-2 grid grid-cols-[1fr_auto] gap-y-1 text-sm">
            <dt>Cases both answered</dt><dd className="font-semibold">{data.calibration.both} / {data.calibration.cases}</dd>
            <dt>Agreements</dt><dd>{data.calibration.agreements}</dd>
            <dt>Material disagreements</dt><dd>{data.calibration.material}</dd>
            <dt>Classification disagreements</dt><dd>{data.calibration.classification_disagreements}</dd>
            <dt>Awaiting both</dt><dd>{data.calibration.awaiting_both}</dd>
          </dl>
        </Card>
        <Card>
          <Title>Borrower scenarios</Title>
          <dl className="mt-2 grid grid-cols-[1fr_auto] gap-y-1 text-sm">
            <dt>Both answered</dt><dd className="font-semibold">{data.scenarios.both} / {data.scenarios.scenarios}</dd>
            <dt>Decision agreements</dt><dd>{data.scenarios.decision_agreements}</dd>
            <dt>Decision disagreements</dt><dd>{data.scenarios.decision_disagreements}</dd>
            <dt>Approve vs deny</dt><dd className="font-semibold text-rose-700">{data.scenarios.approve_vs_deny}</dd>
          </dl>
        </Card>
      </div>
      <div className="grid gap-3 lg:grid-cols-2">
        <Card>
          <Title>Top founder disagreements</Title>
          <OpenList items={data.top_disagreements} open={open} labels={labels} empty="No disagreements yet." />
        </Card>
        <Card>
          <Title>Top unanswered high-priority questions</Title>
          <OpenList items={data.top_unanswered} open={open} labels={labels} empty="Every high-priority Core question is answered." />
        </Card>
        <Card>
          <Title>Business facts collected</Title>
          <p className="mt-2 text-3xl font-bold text-ink">{data.facts_collected}</p>
          <p className="text-sm text-ink-muted">{data.facts_needing_reconciliation} need reconciliation between the founders. See the Business tab.</p>
        </Card>
        <Card>
          <Title>Observed response patterns (underwriting)</Title>
          <p className="text-xs text-ink-muted">Patterns in what was answered. Not the customer policy; nothing here is a rule.</p>
          <ul className="mt-2 flex flex-col gap-1.5 text-sm">
            {data.policy_signals.map((s) => (
              <li key={s.question_id + s.text} className="flex gap-2">
                <Chip tone={s.kind === "aligned" ? "accent" : "muted"}>{s.kind}</Chip>
                <span>{s.text}</span>
              </li>
            ))}
            {data.policy_signals.length === 0 ? <li className="text-ink-muted">Nothing yet.</li> : null}
          </ul>
        </Card>
        <Card>
          <Title>Engineer&apos;s stated constraints</Title>
          <ul className="mt-2 flex flex-col gap-2 text-sm">
            {data.engineer_constraints.map((c) => (
              <li key={c.question.id} className="cursor-pointer rounded-lg p-2 hover:bg-lavender-50" onClick={() => open({ question: c.question, answers: [{ label: "Engineer", response: c.response }] })}>
                <p className="text-xs text-ink-muted">{c.question.question_text}</p>
                <AnswerCell response={c.response} />
              </li>
            ))}
            {data.engineer_constraints.length === 0 ? <li className="text-ink-muted">No risk-signal answers from Engineer yet.</li> : null}
          </ul>
        </Card>
        <Card>
          <Title>Major unknowns</Title>
          <OpenList items={data.unknowns} open={open} labels={labels} empty="Nothing outstanding." />
        </Card>
      </div>
    </div>
  );
}

function OpenList({ items, open, labels, empty }: { items: { kind: string; question: ComparisonRow["question"]; detail: string; lead?: ComparisonRow["lead"]; partner?: ComparisonRow["partner"]; engineer?: ComparisonRow["lead"] }[]; open: (t: DrawerTarget) => void; labels: [string, string]; empty: string }) {
  if (items.length === 0) return <p className="mt-2 text-sm text-ink-muted">{empty}</p>;
  return (
    <ul className="mt-2 flex flex-col gap-1.5 text-sm">
      {items.map((it) => (
        <li
          key={it.kind + it.question.id}
          className="cursor-pointer rounded-lg p-2 hover:bg-lavender-50"
          onClick={() =>
            open({
              question: it.question,
              answers: "engineer" in it ? [{ label: "Engineer", response: it.engineer ?? null }] : [{ label: labels[0], response: it.lead ?? null }, { label: labels[1], response: it.partner ?? null }],
            })
          }
        >
          <div className="flex flex-wrap items-center gap-1.5">
            <Chip tone="muted">{OPEN_KIND_LABEL[it.kind] ?? it.kind}</Chip>
            <LevelChip level={it.question.level} core={it.question.required_core} />
            <span className="text-xs text-ink-faint">{it.detail}</span>
          </div>
          <p className="mt-1">{it.question.question_text}</p>
        </li>
      ))}
    </ul>
  );
}

// ------------------------------------------------------------------------------ business --
export function BusinessTab({ token, open }: TabProps) {
  const { data, error, refused, loading } = useAdminData(fetchBusiness, token);
  const initial = useInitial();
  const [query, setQuery] = useState(initial.q);
  if (loading) return <Loading what="business facts" />;
  if (!data) return <Problem error={error} refused={refused} />;
  const labels: [string, string] = ["Lead", "Partner"];
  const groups = data.groups.map((g) => ({ ...g, items: g.items.filter((r) => matchesSearch(r.question, query)) })).filter((g) => g.items.length);
  return (
    <div className="flex flex-col gap-4">
      <SearchBox value={query} onChange={setQuery} />
      <p className="text-sm text-ink-muted">
        {data.facts_collected} facts collected · {data.needs_reconciliation} need reconciliation. Where the founders answered the same fact differently, both answers stand side by side; nothing here decides who is right.
      </p>
      {groups.map((g) => (
        <Card key={g.group}>
          <Title>{g.group}</Title>
          <div className="mt-2 flex flex-col divide-y divide-line">
            {g.items.map((r) => (
              <div key={r.question.id} className="grid cursor-pointer gap-2 py-2 hover:bg-lavender-50 md:grid-cols-[1.4fr_1fr_1fr_auto]" onClick={() => open(founderAnswers(labels, r))}>
                <div>
                  <p className="text-sm">{r.question.question_text}</p>
                  <span className="text-[11px] text-ink-faint"><LevelChip level={r.question.level} core={r.question.required_core} /> {r.question.question_key}</span>
                </div>
                <div className="text-sm"><span className="text-xs text-ink-muted">Lead: </span><AnswerCell response={r.lead} empty="not asked / no answer" /></div>
                <div className="text-sm"><span className="text-xs text-ink-muted">Partner: </span><AnswerCell response={r.partner} empty="not asked / no answer" /></div>
                <div>{r.needs_reconciliation ? <StatusChip status="NEEDS_RECONCILIATION" /> : <StatusChip status={r.status} />}</div>
              </div>
            ))}
          </div>
        </Card>
      ))}
      {groups.length === 0 ? <Notice tone="info">{query ? "Nothing matches." : "No business facts answered yet."}</Notice> : null}
    </div>
  );
}

// ---------------------------------------------------------------------------- comparison --
export function ComparisonTab({ token, open }: TabProps) {
  const { data, error, refused, loading } = useAdminData(fetchComparisons, token);
  const initial = useInitial();
  if (loading) return <Loading what="founder comparison" />;
  if (!data) return <Problem error={error} refused={refused} />;
  const s = data.summary;
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap gap-2 text-xs text-ink-muted">
        <Chip tone="accent">{s.both} / {s.total} both answered</Chip>
        <Chip tone="muted">agree {s.agree}</Chip>
        <Chip tone="muted">roughly {s.roughly}</Chip>
        <Chip tone="muted">different {s.different}</Chip>
        <Chip tone="muted">material {s.material}</Chip>
        <Chip tone="muted">only Lead {s.only_lead}</Chip>
        <Chip tone="muted">only Partner {s.only_partner}</Chip>
        <Chip tone="muted">neither {s.neither}</Chip>
        <Chip tone="muted">free text {s.not_comparable}</Chip>
      </div>
      <p className="text-xs text-ink-muted">
        Deterministic rules: same option agrees; adjacent bands or values within {Math.round(data.tolerance.relative_rough * 100)}% are roughly aligned; beyond {Math.round(data.tolerance.relative_different * 100)}% apart, approve against deny, or opposite booleans are material. Free text is shown side by side and never judged.
      </p>
      <ComparisonTable rows={data.rows} labels={data.labels} open={open} showFlags={false} assignments={data.assignments} initialQuery={initial.q} initialFilter={initial.filter} />
    </div>
  );
}

// -------------------------------------------------------------------------- underwriting --
export function UnderwritingTab({ token, open }: TabProps) {
  const { data, error, refused, loading } = useAdminData(fetchUnderwriting, token);
  const initial = useInitial();
  if (loading) return <Loading what="underwriting answers" />;
  if (!data) return <Problem error={error} refused={refused} />;
  const labels: [string, string] = ["Lead", "Partner"];
  return (
    <div className="flex flex-col gap-4">
      <Card>
        <Title>Potential policy signals — observed response patterns</Title>
        <p className="text-xs text-ink-muted">What the two founders said, side by side. Not the customer policy. Nothing becomes an underwriting rule from this page.</p>
        <ul className="mt-2 flex flex-col gap-1.5 text-sm">
          {data.signals.map((s) => (
            <li key={s.question_id + s.text} className="flex gap-2"><Chip tone={s.kind === "aligned" ? "accent" : "muted"}>{s.kind}</Chip><span>{s.text}</span></li>
          ))}
          {data.signals.length === 0 ? <li className="text-ink-muted">Nothing yet.</li> : null}
        </ul>
      </Card>
      {data.groups.map((g) => (
        <Card key={g.group}>
          <Title>{g.group} <span className="text-xs font-normal text-ink-muted">· {g.items.length}</span></Title>
          <div className="mt-2">
            <ComparisonTable rows={g.items} labels={labels} open={open} initialQuery={initial.q} filters={[{ key: "all", label: "All" }, { key: "agree", label: "Agree" }, { key: "disagree", label: "Disagree" }, { key: "awaiting", label: "Awaiting" }, { key: "core", label: "Core only" }]} showFlags={false} />
          </div>
        </Card>
      ))}
    </div>
  );
}

// --------------------------------------------------------------------------- calibration --
type CalFilter = "all" | "both" | "one" | "class" | "materiality" | "signal" | "high" | "core" | "recommended" | "deep_dive" | "variant" | "reveal";
const CAL_FILTERS: { key: CalFilter; label: string }[] = [
  { key: "all", label: "All" }, { key: "both", label: "Both answered" }, { key: "one", label: "Only one answered" },
  { key: "class", label: "Classification disagreement" }, { key: "materiality", label: "Materiality disagreement" }, { key: "signal", label: "Risk-signal disagreement" },
  { key: "high", label: "High-dollar" }, { key: "core", label: "Core" }, { key: "recommended", label: "Recommended" }, { key: "deep_dive", label: "Deep dive" },
  { key: "variant", label: "Controlled variant" }, { key: "reveal", label: "Progressive reveal" },
];

function calMatches(r: CalibrationRow, f: CalFilter): boolean {
  const both = !isAwaiting(r.status);
  switch (f) {
    case "all": return true;
    case "both": return both;
    case "one": return r.status.startsWith("ONLY_");
    case "class": return both && r.extra.classification_match === false;
    case "materiality": return both && r.extra.materiality_match === false;
    case "signal": return both && r.extra.signal_match === false;
    case "high": return r.high_dollar;
    case "core": case "recommended": case "deep_dive": return r.question.level === f;
    case "variant": return r.is_variant;
    case "reveal": return r.is_reveal;
  }
}

function ChangedCells({ changed, label }: { changed: Changed; label: string }) {
  if (!changed) return <p className="text-xs text-ink-muted">{label}: not both answered</p>;
  const yes = (v: boolean) => (v ? <span className="font-semibold text-rose-700">changed</span> : <span className="text-ink-muted">same</span>);
  return (
    <p className="text-xs">
      <span className="font-semibold">{label} changed:</span> classification {yes(changed.classification)} · confidence {yes(changed.confidence)} · materiality {yes(changed.materiality)} · signal {yes(changed.signal)}
    </p>
  );
}

function CalMini({ r }: { r: CalibrationRow }) {
  const c = (x: CalibrationRow["lead"]) => (x?.calibration ? `${x.calibration.classification_category} · ${x.calibration.classification_confidence}/5 · ${x.calibration.underwriting_materiality} · ${x.calibration.underwriting_signal}` : "—");
  return (
    <div className="text-xs">
      <p><span className="text-ink-muted">Lead:</span> {c(r.lead)}</p>
      <p><span className="text-ink-muted">Partner:</span> {c(r.partner)}</p>
    </div>
  );
}

export function CalibrationTab({ token, open }: TabProps) {
  const { data, error, refused, loading } = useAdminData(fetchCalibration, token);
  const initial = useInitial();
  const [filter, setFilter] = useState<CalFilter>((CAL_FILTERS.find((f) => f.key === initial.filter)?.key ?? "all") as CalFilter);
  const [query, setQuery] = useState(initial.q);
  const rows = useMemo(() => (data ? data.rows.filter((r) => calMatches(r, filter) && matchesSearch(r.question, query)) : []), [data, filter, query]);
  if (loading) return <Loading what="calibration cases" />;
  if (!data) return <Problem error={error} refused={refused} />;
  const labels = data.labels;
  const target = (r: CalibrationRow): DrawerTarget => founderAnswers(labels, r, data.assignments);
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap gap-2 text-xs">
        <Chip tone="accent">{data.summary.both} / {data.summary.cases} both answered</Chip>
        <Chip tone="muted">agreements {data.summary.agreements}</Chip>
        <Chip tone="muted">material {data.summary.material}</Chip>
        <Chip tone="muted">classification splits {data.summary.classification_disagreements}</Chip>
        <Chip tone="muted">awaiting both {data.summary.awaiting_both}</Chip>
      </div>
      <div className="flex flex-wrap items-center gap-3"><SearchBox value={query} onChange={setQuery} /><span className="text-xs text-ink-muted">{rows.length} of {data.rows.length}</span></div>
      <FilterBar value={filter} options={CAL_FILTERS} onChange={setFilter} />
      <div className="overflow-x-auto rounded-xl border border-line">
        <table className="w-full min-w-[64rem] text-sm">
          <thead className="bg-lavender-50 text-left text-xs uppercase tracking-wide text-ink-muted">
            <tr><th className="px-3 py-2">Case</th><th className="px-3 py-2">Business · amount</th><th className="px-3 py-2">{labels[0]}</th><th className="px-3 py-2">{labels[1]}</th><th className="px-3 py-2">Comparison</th></tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.question.id} className="cursor-pointer border-t border-line align-top hover:bg-lavender-50" onClick={() => open(target(r))}>
                <td className="px-3 py-2">
                  <p className="font-semibold">{r.case_key.toUpperCase()} {r.title ? <span className="font-normal">· {r.title}</span> : null}</p>
                  <p className="flex flex-wrap gap-1 text-[11px] text-ink-faint"><LevelChip level={r.question.level} /> {r.is_variant ? <Chip tone="muted">variant · {r.changed_feature}</Chip> : null}{r.is_reveal ? <Chip tone="muted">reveal</Chip> : null}</p>
                </td>
                <td className="px-3 py-2 text-xs">{r.business_vertical ?? "—"}<br />{r.direction} {r.amount_minor !== null ? formatMinor(r.amount_minor) : ""}</td>
                <td className="px-3 py-2"><CalibrationDetail response={r.lead} /></td>
                <td className="px-3 py-2"><CalibrationDetail response={r.partner} /></td>
                <td className="px-3 py-2">
                  <StatusChip status={r.status} />
                  {!isAwaiting(r.status) ? (
                    <ul className="mt-1 text-[11px] text-ink-muted">
                      <li>classification {r.extra.classification_match ? "match" : "differs"}</li>
                      <li>confidence Δ {String(r.extra.confidence_delta)}</li>
                      <li>materiality {r.extra.materiality_match ? "match" : `${String(r.extra.materiality_steps)} apart`}</li>
                      <li>signal {r.extra.signal_match ? "match" : "differs"}</li>
                    </ul>
                  ) : null}
                </td>
              </tr>
            ))}
            {rows.length === 0 ? <tr><td colSpan={5} className="px-3 py-6 text-center text-ink-muted">Nothing matches.</td></tr> : null}
          </tbody>
        </table>
      </div>
      <Card>
        <Title>Controlled variant analysis</Title>
        <p className="text-xs text-ink-muted">Baseline against variant, one feature changed. Whether each founder&apos;s assessment moved is read from their two answers; it is an observation, not a conclusion.</p>
        <div className="mt-2 flex flex-col divide-y divide-line">
          {data.controlled_pairs.map((p) => (
            <div key={p.variant.question.id} className="grid gap-3 py-3 md:grid-cols-[1fr_1fr_1.2fr]">
              <div className="cursor-pointer" onClick={() => open(target(p.baseline))}>
                <p className="text-xs font-semibold uppercase text-ink-muted">Baseline · {p.baseline.case_key}</p>
                <CalMini r={p.baseline} />
              </div>
              <div className="cursor-pointer" onClick={() => open(target(p.variant))}>
                <p className="text-xs font-semibold uppercase text-ink-muted">Variant · {p.variant.case_key}</p>
                <CalMini r={p.variant} />
              </div>
              <div>
                <p className="text-xs"><span className="font-semibold">Changed feature:</span> {p.changed_feature ?? "—"} <span className="text-ink-faint">({p.case_family})</span></p>
                <ChangedCells changed={p.lead_changed} label={labels[0]} />
                <ChangedCells changed={p.partner_changed} label={labels[1]} />
              </div>
            </div>
          ))}
          {data.controlled_pairs.length === 0 ? <p className="py-2 text-sm text-ink-muted">No controlled pairs are served in the runtime yet.</p> : null}
        </div>
      </Card>
      <Card>
        <Title>Progressive reveal analysis</Title>
        <p className="text-xs text-ink-muted">Before and after the added context. Changed fields are highlighted; observed respondent change only.</p>
        <div className="mt-2 flex flex-col divide-y divide-line">
          {data.reveals.map((rv) => (
            <div key={rv.after.question.id} className="grid gap-3 py-3 md:grid-cols-[1fr_1fr_1.2fr]">
              <div className="cursor-pointer" onClick={() => open(target(rv.before))}><p className="text-xs font-semibold uppercase text-ink-muted">Before context · {rv.case_key}</p><CalMini r={rv.before} /></div>
              <div className="cursor-pointer" onClick={() => open(target(rv.after))}><p className="text-xs font-semibold uppercase text-ink-muted">After reveal</p><CalMini r={rv.after} /></div>
              <div>
                <p className="text-xs"><span className="font-semibold">Revealed:</span> {rv.reveal ?? "—"}</p>
                <ChangedCells changed={rv.lead_changed} label={labels[0]} />
                <ChangedCells changed={rv.partner_changed} label={labels[1]} />
              </div>
            </div>
          ))}
          {data.reveals.length === 0 ? <p className="py-2 text-sm text-ink-muted">No reveal is served yet.</p> : null}
        </div>
      </Card>
    </div>
  );
}

// ----------------------------------------------------------------------------- scenarios --
type ScenFilter = "all" | "approve_deny" | "disagree" | "agree" | "awaiting" | "both";
export function ScenariosTab({ token, open }: TabProps) {
  const { data, error, refused, loading } = useAdminData(fetchScenarios, token);
  const initial = useInitial();
  const [filter, setFilter] = useState<ScenFilter>((["all", "approve_deny", "disagree", "agree", "awaiting", "both"].includes(initial.filter ?? "") ? initial.filter : "all") as ScenFilter);
  const [query, setQuery] = useState(initial.q);
  if (loading) return <Loading what="borrower scenarios" />;
  if (!data) return <Problem error={error} refused={refused} />;
  const labels = data.labels;
  const rows = data.rows.filter((r) =>
    matchesSearch(r.question, query) &&
    (filter === "all" ? true : filter === "approve_deny" ? r.approve_vs_deny : filter === "disagree" ? !isAwaiting(r.status) && r.extra.decision_match === false : filter === "agree" ? r.extra.decision_match === true : filter === "both" ? !isAwaiting(r.status) : isAwaiting(r.status)),
  );
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap gap-2 text-xs">
        <Chip tone="accent">{data.summary.both} / {data.summary.scenarios} both answered</Chip>
        <Chip tone="muted">decision agreements {data.summary.decision_agreements}</Chip>
        <Chip tone="muted">decision disagreements {data.summary.decision_disagreements}</Chip>
        <Chip tone="muted">approve vs deny {data.summary.approve_vs_deny}</Chip>
      </div>
      <div className="flex flex-wrap items-center gap-3"><SearchBox value={query} onChange={setQuery} /><span className="text-xs text-ink-muted">{rows.length} of {data.rows.length}</span></div>
      <FilterBar value={filter} options={[{ key: "all", label: "All" }, { key: "both", label: "Both answered" }, { key: "approve_deny", label: "APPROVE vs DENY" }, { key: "disagree", label: "Decision disagreement" }, { key: "agree", label: "Decision agreement" }, { key: "awaiting", label: "Awaiting" }]} onChange={setFilter} />
      <div className="flex flex-col gap-3">
        {rows.map((r) => (
          <Card key={r.question.id}>
            <div className="flex cursor-pointer flex-wrap items-center justify-between gap-2" onClick={() => open(founderAnswers(labels, r, data.assignments))}>
              <Title>{r.scenario_key.toUpperCase()} {r.title ? `· ${r.title}` : ""}</Title>
              <div className="flex items-center gap-2"><LevelChip level={r.question.level} core={r.question.required_core} />{r.approve_vs_deny ? <StatusChip status="MATERIAL_DISAGREEMENT" /> : <StatusChip status={r.status} />}</div>
            </div>
            <div className="mt-2 grid gap-3 md:grid-cols-2">
              <div><p className="text-xs font-semibold uppercase text-ink-muted">{labels[0]}</p><ScenarioDetail response={r.lead} /></div>
              <div><p className="text-xs font-semibold uppercase text-ink-muted">{labels[1]}</p><ScenarioDetail response={r.partner} /></div>
            </div>
            {!isAwaiting(r.status) ? (
              <p className="mt-2 text-xs text-ink-muted">
                decision {r.extra.decision_match ? "agree" : "disagree"} · exposure Δ {typeof r.extra.exposure_delta_minor === "number" ? formatMinor(r.extra.exposure_delta_minor) : "n/a"} · reason overlap {String(r.extra.reason_overlap)} · both asked for more info: {r.extra.info_overlap ? "yes" : "no"}
              </p>
            ) : null}
          </Card>
        ))}
        {rows.length === 0 ? <Notice tone="info">Nothing matches.</Notice> : null}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------- engineering --
export function EngineeringTab({ token, open }: TabProps) {
  const { data, error, refused, loading } = useAdminData(fetchEngineering, token);
  const initial = useInitial();
  const [query, setQuery] = useState(initial.q);
  if (loading) return <Loading what="Engineer's answers" />;
  if (!data) return <Problem error={error} refused={refused} />;
  const item = (it: EngineeringItem) => (
    <li key={it.question.id} className="cursor-pointer rounded-lg p-2 hover:bg-lavender-50" onClick={() => open({ question: it.question, answers: [{ label: "Engineer", response: it.response, assignment_id: data.assignment_id ?? undefined }] })}>
      <p className="flex flex-wrap items-center gap-1.5 text-xs text-ink-muted"><LevelChip level={it.question.level} core={it.question.required_core} /> {it.question.question_key}</p>
      <p className="text-sm">{it.question.question_text}</p>
      <div className="mt-1 text-sm"><TypedAnswer question={it.question} response={it.response} /></div>
    </li>
  );
  const q = query.trim();
  const filt = (items: EngineeringItem[]) => items.filter((it) => matchesSearch(it.question, q));
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3"><SearchBox value={query} onChange={setQuery} /><span className="text-xs text-ink-muted">{data.answered} of {data.total} answered</span></div>
      <div className="grid gap-3 lg:grid-cols-3">
        {data.buckets.map((b) => (
          <Card key={b.bucket}>
            <Title>{b.bucket} <span className="text-xs font-normal text-ink-muted">· {b.answered}/{b.total}</span></Title>
            <p className="text-[11px] text-ink-faint">filed by: {b.rule}</p>
            <ul className="mt-1 flex flex-col">{filt(b.items.filter((it) => it.response)).map(item)}</ul>
            {b.items.filter((it) => it.response).length === 0 ? <p className="mt-1 text-xs text-ink-muted">Nothing answered here yet.</p> : null}
          </Card>
        ))}
      </div>
      {data.categories.map((c) => (
        <Card key={c.category}>
          <Title>{c.category} <span className="text-xs font-normal text-ink-muted">· {c.items.filter((it) => it.response?.status === "answered").length}/{c.items.length}</span></Title>
          <ul className="mt-1 flex flex-col divide-y divide-line">{filt(c.items).map(item)}</ul>
        </Card>
      ))}
    </div>
  );
}

// ----------------------------------------------------------------------------- engagement --
export function EngagementTab({ token }: TabProps) {
  const { data, error, refused, loading } = useAdminData(fetchEngagement, token);
  if (loading) return <Loading what="engagement" />;
  if (!data) return <Problem error={error} refused={refused} />;
  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-ink-muted">Stakeholder engagement: what each person has answered, by group and by topic. Measured, never scored.</p>
      <div className="grid gap-3 lg:grid-cols-3">
        {data.respondents.filter((r) => r.kind === "stakeholder").map((r) => (
          <Card key={r.assignment_id}>
            <Title>{r.label}</Title>
            <div className="mt-2 flex flex-col gap-2">
              <ProgressBar value={r.core_answered} max={r.core_total} label={`Core ${r.core_percent}%`} />
              <ProgressBar value={r.answered} max={r.total} label={`Everything ${r.total_percent}%`} />
            </div>
            <dl className="mt-3 grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
              <dt className="text-ink-muted">Sessions</dt><dd>{r.sessions}</dd>
              <dt className="text-ink-muted">Last activity</dt><dd>{whenLabel(r.last_activity)}</dd>
              {(["recommended", "deep_dive", "calibration", "scenarios"] as const).map((g) => (
                <div key={g} className="contents">
                  <dt className="capitalize text-ink-muted">{g.replace("_", " ")}</dt>
                  <dd>{r.by_group[g]?.answered ?? 0} / {r.by_group[g]?.available ?? 0}</dd>
                </div>
              ))}
              <dt className="text-ink-muted">Optional explanation rate</dt><dd>{r.explanation_rate}%</dd>
              <dt className="text-ink-muted">Skips</dt><dd>{r.skips}</dd>
              <dt className="text-ink-muted">Prefer not to answer</dt><dd>{r.prefer_not_to_answer}</dd>
              <dt className="text-ink-muted">Average text answer</dt><dd>{r.average_text_length !== null ? `${r.average_text_length} chars` : "—"}</dd>
            </dl>
            <p className="mt-3 text-xs font-semibold uppercase text-ink-muted">Topic engagement</p>
            <ul className="mt-1 flex flex-col gap-1">
              {r.topics.map((t) => (
                <li key={t.topic} className="text-xs"><ProgressBar value={t.answered} max={t.available} label={`${t.topic} ${t.percent}% (${t.answered}/${t.available})`} /></li>
              ))}
            </ul>
          </Card>
        ))}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------------- open questions --
export function OpenQuestionsTab({ token, open }: TabProps) {
  const { data, error, refused, loading } = useAdminData(fetchOpenQuestions, token);
  const [kind, setKind] = useState<string>("all");
  if (loading) return <Loading what="open questions" />;
  if (!data) return <Problem error={error} refused={refused} />;
  const kinds = Array.from(new Set(data.items.map((i) => i.kind)));
  const items = data.items.filter((i) => kind === "all" || i.kind === kind);
  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-ink-muted">{data.count} items. The preparation list for the next call: what to reconcile, what to ask, and why it matters, from the authored metadata.</p>
      <FilterBar value={kind} options={[{ key: "all", label: "All" }, ...kinds.map((k) => ({ key: k, label: OPEN_KIND_LABEL[k] ?? k }))]} onChange={setKind} />
      <ol className="flex flex-col gap-2">
        {items.map((it, i) => (
          <li key={it.kind + it.question.id} className="cursor-pointer rounded-xl border border-line bg-surface p-3 hover:bg-lavender-50" onClick={() => open({ question: it.question, answers: "engineer" in it ? [{ label: "Engineer", response: it.engineer ?? null }] : [{ label: "Lead", response: it.lead ?? null }, { label: "Partner", response: it.partner ?? null }] })}>
            <div className="flex flex-wrap items-center gap-1.5 text-xs">
              <span className="font-mono text-ink-faint">{i + 1}.</span>
              <Chip tone={it.rank === 0 ? "accent" : "muted"}>{OPEN_KIND_LABEL[it.kind] ?? it.kind}</Chip>
              <LevelChip level={it.question.level} core={it.question.required_core} />
              <span className="text-ink-faint">{it.question.topic} · {it.detail}</span>
            </div>
            <p className="mt-1 text-sm font-semibold">{it.question.meta?.title ?? it.question.question_text}</p>
            {"lead" in it || "partner" in it ? (
              <div className="mt-1 grid gap-2 text-xs md:grid-cols-2">
                <div><span className="text-ink-muted">Lead: </span><AnswerCell response={it.lead ?? null} /></div>
                <div><span className="text-ink-muted">Partner: </span><AnswerCell response={it.partner ?? null} /></div>
              </div>
            ) : null}
            {it.why ? <p className="mt-1 text-xs text-ink-soft"><span className="font-semibold text-ink">Why this matters.</span> {it.why}</p> : null}
          </li>
        ))}
        {items.length === 0 ? <Notice tone="info">Nothing open.</Notice> : null}
      </ol>
    </div>
  );
}
