/**
 * The admin dashboard's pure rules: how a comparison status reads, how the filters and the
 * search box narrow a table, and how a timestamp is shown. No fetch, no React, so the unit test
 * can drive them with literals like the other rules files.
 */

import type { AdminQuestion, AdminResponse, ComparisonRow, ComparisonStatus } from "@/lib/discovery-admin-api";

export const STATUS_LABEL: Record<string, string> = {
  AGREE: "Agree",
  ROUGHLY_ALIGNED: "Roughly aligned",
  DIFFERENT: "Different",
  MATERIAL_DISAGREEMENT: "Material disagreement",
  NEITHER_ANSWERED: "Neither answered",
  NOT_COMPARABLE: "Not comparable",
  OWN_BANK: "Own bank",
  NEEDS_RECONCILIATION: "Needs reconciliation",
  ANSWERED: "Answered",
  UNANSWERED: "Unanswered",
  SKIPPED: "Skipped",
  PREFER_NOT_TO_ANSWER: "Prefer not to answer",
};

export function statusLabel(status: ComparisonStatus | string): string {
  if (status in STATUS_LABEL) return STATUS_LABEL[status];
  const only = /^ONLY_(.+)_ANSWERED$/.exec(status);
  if (only) return `Only ${only[1].charAt(0) + only[1].slice(1).toLowerCase()} answered`;
  return status.replace(/_/g, " ").toLowerCase();
}

export type StatusTone = "good" | "warn" | "bad" | "muted" | "neutral";

export function statusTone(status: ComparisonStatus | string): StatusTone {
  if (status === "AGREE" || status === "ANSWERED") return "good";
  if (status === "ROUGHLY_ALIGNED") return "neutral";
  if (status === "DIFFERENT") return "warn";
  if (status === "MATERIAL_DISAGREEMENT" || status === "NEEDS_RECONCILIATION") return "bad";
  return "muted";
}

export type ComparisonFilter =
  | "all"
  | "both"
  | "agree"
  | "disagree"
  | "material"
  | "awaiting"
  | "core"
  | "business"
  | "underwriting"
  | "risk"
  | "operations"
  | "role";

export const COMPARISON_FILTERS: { key: ComparisonFilter; label: string }[] = [
  { key: "all", label: "All" },
  { key: "both", label: "Both answered" },
  { key: "agree", label: "Agree" },
  { key: "disagree", label: "Disagree" },
  { key: "material", label: "Material disagreement" },
  { key: "awaiting", label: "Awaiting answer" },
  { key: "core", label: "Core only" },
  { key: "business", label: "Business" },
  { key: "underwriting", label: "Underwriting" },
  { key: "risk", label: "Risk" },
  { key: "operations", label: "Operations" },
  { key: "role", label: "Role" },
];

export function isAwaiting(status: string): boolean {
  return status === "NEITHER_ANSWERED" || status.startsWith("ONLY_");
}

export function matchesComparisonFilter(row: ComparisonRow, filter: ComparisonFilter): boolean {
  switch (filter) {
    case "all":
      return true;
    case "both":
      return !isAwaiting(row.status);
    case "agree":
      return row.status === "AGREE" || row.status === "ROUGHLY_ALIGNED";
    case "disagree":
      return row.status === "DIFFERENT" || row.status === "MATERIAL_DISAGREEMENT";
    case "material":
      return row.status === "MATERIAL_DISAGREEMENT";
    case "awaiting":
      return isAwaiting(row.status);
    case "core":
      return row.question.required_core;
    case "business":
      return row.question.topic === "Business";
    case "underwriting":
      return row.question.topic === "Underwriting";
    case "risk":
      return row.question.topic === "Risk";
    case "operations":
      return row.question.topic === "Operations";
    case "role":
      return row.question.topic === "Role";
  }
}

/** Case-insensitive match over the question's text, key, id and section. */
export function matchesSearch(question: AdminQuestion, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return [question.question_text, question.question_key, question.id, question.section_slug, question.section_title, question.meta?.title ?? ""]
    .join(" \n ")
    .toLowerCase()
    .includes(q);
}

export type DatasetFilters = {
  respondent: string; // assignment id or "all"
  level: "all" | "core" | "recommended" | "deep_dive";
  answered: "all" | "answered" | "unanswered";
  section: string; // slug or "all"
  type: string; // question_type or "all"
};

export const EMPTY_DATASET_FILTERS: DatasetFilters = { respondent: "all", level: "all", answered: "all", section: "all", type: "all" };

export function matchesDatasetFilters(
  question: AdminQuestion,
  filters: DatasetFilters,
  answeredBy: (assignmentId: string, questionId: string) => boolean,
  assignmentIds: string[],
): boolean {
  if (filters.level !== "all" && question.level !== filters.level) return false;
  if (filters.section !== "all" && question.section_slug !== filters.section) return false;
  if (filters.type !== "all" && question.question_type !== filters.type) return false;
  if (filters.answered === "all") return true;
  const ids = filters.respondent === "all" ? assignmentIds : [filters.respondent];
  const any = ids.some((id) => answeredBy(id, question.id));
  return filters.answered === "answered" ? any : !any;
}

export function whenLabel(stamp: string | null | undefined): string {
  if (!stamp) return "never";
  const date = new Date(stamp);
  if (Number.isNaN(date.getTime())) return stamp;
  return date.toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function percentLabel(numerator: number, denominator: number): string {
  if (!denominator) return "—";
  return `${Math.round((100 * numerator) / denominator)}%`;
}

export const OPEN_KIND_LABEL: Record<string, string> = {
  approve_vs_deny: "Approve vs deny",
  founder_material_disagreement: "Material disagreement",
  founder_disagreement: "Disagreement",
  unanswered_core: "Unanswered Core",
  calibration_disagreement: "Calibration disagreement",
  unresolved_disagreement_marker: "Authored as unresolved",
  engineer_unanswered_core: "Engineer: unanswered Core",
};

export const TABS = [
  { key: "overview", label: "Overview" },
  { key: "responses", label: "Responses" },
  { key: "comparison", label: "Founder comparison" },
  { key: "business", label: "Business" },
  { key: "underwriting", label: "Underwriting" },
  { key: "calibration", label: "Calibration" },
  { key: "scenarios", label: "Borrower scenarios" },
  { key: "engineering", label: "Engineering" },
  { key: "engagement", label: "Engagement" },
  { key: "open", label: "Open questions" },
] as const;

export type TabKey = (typeof TABS)[number]["key"];

export function tabFromParam(value: string | null): TabKey {
  if (value === "browse") return "responses";
  return (TABS.find((t) => t.key === value)?.key ?? "overview") as TabKey;
}

/** The dashboard URL for a tab, with optional parameters (respondent, filter, q, open). */
export function hrefFor(tab: TabKey, params: Record<string, string | undefined> = {}): string {
  const search = new URLSearchParams({ tab });
  for (const [k, v] of Object.entries(params)) if (v) search.set(k, v);
  return `/admin/discovery?${search.toString()}`;
}

// ------------------------------------------------------------------- responses view --
export function agoLabel(stamp: string | null | undefined, now: number = Date.now()): string {
  if (!stamp) return "never";
  const t = new Date(stamp).getTime();
  if (Number.isNaN(t)) return stamp;
  const s = Math.max(0, Math.round((now - t) / 1000));
  if (s < 60) return "just now";
  const m = Math.round(s / 60);
  if (m < 60) return `${m} minute${m === 1 ? "" : "s"} ago`;
  const h = Math.round(m / 60);
  if (h < 48) return `${h} hour${h === 1 ? "" : "s"} ago`;
  const d = Math.round(h / 24);
  return `${d} day${d === 1 ? "" : "s"} ago`;
}

export type ResponseStatus = "answered" | "unanswered" | "skipped" | "prefer_not";

export function responseStatus(response: AdminResponse | null | undefined): ResponseStatus {
  if (!response) return "unanswered";
  if (response.status === "skipped") return "skipped";
  if (/prefer not/i.test(response.display ?? "")) return "prefer_not";
  return "answered";
}

export const RESPONSE_STATUS_LABEL: Record<ResponseStatus, string> = {
  answered: "Answered",
  unanswered: "Unanswered",
  skipped: "Skipped",
  prefer_not: "Prefer not to answer",
};

export function levelLabel(question: AdminQuestion): string {
  if (question.group === "calibration") return "Calibration";
  if (question.group === "scenarios") return "Scenario";
  if (question.required_core) return "Core";
  return question.level === "recommended" ? "Recommended" : "Deep dive";
}

export type ResponsesFilter = "all" | "answered" | "unanswered" | "core" | "recommended" | "deep_dive" | "cases";

export const RESPONSES_FILTERS: { key: ResponsesFilter; label: string }[] = [
  { key: "all", label: "All" },
  { key: "answered", label: "Answered" },
  { key: "unanswered", label: "Unanswered" },
  { key: "core", label: "Core" },
  { key: "recommended", label: "Recommended" },
  { key: "deep_dive", label: "Deep dive" },
  { key: "cases", label: "Calibration / scenarios" },
];

export function matchesResponsesFilter(question: AdminQuestion, response: AdminResponse | null | undefined, filter: ResponsesFilter): boolean {
  const status = responseStatus(response);
  switch (filter) {
    case "all":
      return true;
    case "answered":
      return status === "answered" || status === "prefer_not";
    case "unanswered":
      return status === "unanswered" || status === "skipped";
    case "core":
      return question.required_core;
    case "recommended":
      return question.level === "recommended" && question.group !== "calibration" && question.group !== "scenarios";
    case "deep_dive":
      return question.level === "deep_dive" && question.group !== "calibration" && question.group !== "scenarios";
    case "cases":
      return question.group === "calibration" || question.group === "scenarios";
  }
}

/** The free text a respondent wrote, from whichever field their answer type keeps it in. */
export function writtenComment(question: AdminQuestion, response: AdminResponse | null | undefined): string | null {
  if (!response || response.status !== "answered") return null;
  const parts: string[] = [];
  if (response.note) parts.push(response.note);
  if ((question.question_type === "short_text" || question.question_type === "long_text") && typeof response.answer === "string") parts.push(response.answer);
  const c = response.calibration as Record<string, unknown> | null;
  if (c) for (const k of ["reasoning", "context_needed"]) if (typeof c[k] === "string" && c[k]) parts.push(c[k] as string);
  const s = response.scenario as Record<string, unknown> | null;
  if (s) for (const k of ["notes", "additional_information_needed", "what_would_change_my_decision"]) if (typeof s[k] === "string" && s[k]) parts.push(s[k] as string);
  return parts.length ? parts.join(" ") : null;
}

/** Search over the question and the answer as shown, including any written comment. */
export function matchesAnswerSearch(question: AdminQuestion, response: AdminResponse | null | undefined, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  if (matchesSearch(question, q)) return true;
  const hay = [response?.display ?? "", writtenComment(question, response) ?? ""].join(" \n ").toLowerCase();
  return hay.includes(q);
}

type SectionLike = { id: string; slug: string; positions?: Record<string, number> };

/** A respondent's questions in the order they were asked: section position, then order. */
export function respondentQuestions<Q extends AdminQuestion>(questions: Q[], sections: SectionLike[], templateSlug: string): Q[] {
  const position = new Map<string, number>();
  for (const s of sections) {
    const p = s.positions?.[templateSlug];
    if (p !== undefined) position.set(s.slug, p);
  }
  return questions
    .filter((q) => position.has(q.section_slug) && !q.retired)
    .sort((a, b) => position.get(a.section_slug)! - position.get(b.section_slug)! || a.order - b.order);
}

export type ImportantReason =
  | "core_answered"
  | "substantive"
  | "differs_from_other_founder"
  | "high_sensitivity"
  | "unresolved_disagreement"
  | "material_case"
  | "decision"
  | "ownership";

export const IMPORTANT_REASON_LABEL: Record<ImportantReason, string> = {
  differs_from_other_founder: "Differs from the other founder",
  decision: "Approve / deny / manual review decisions",
  material_case: "Calibration cases rated high or critical",
  unresolved_disagreement: "Authored as a known point of disagreement",
  high_sensitivity: "High-sensitivity answers",
  ownership: "Ownership and hand-off answers",
  substantive: "High-priority questions with substantive answers",
  core_answered: "Core questions answered",
};

export const IMPORTANT_ORDER: ImportantReason[] = ["differs_from_other_founder", "decision", "material_case", "unresolved_disagreement", "high_sensitivity", "ownership", "substantive", "core_answered"];

/**
 * What to look at first, from the metadata and the comparison statuses alone. A question is
 * listed under every reason that applies, in IMPORTANT_ORDER, so a decision that also differs
 * from the other founder is found under both headings.
 */
export function importantResponses(
  questions: AdminQuestion[],
  answerFor: (questionId: string) => AdminResponse | null | undefined,
  comparisonStatus: (questionId: string) => string | undefined,
): { reason: ImportantReason; question: AdminQuestion; response: AdminResponse }[] {
  const out: { reason: ImportantReason; question: AdminQuestion; response: AdminResponse }[] = [];
  for (const q of questions) {
    const r = answerFor(q.id);
    if (!r || r.status !== "answered") continue;
    const reasons: ImportantReason[] = [];
    const cmp = comparisonStatus(q.id);
    if (cmp === "DIFFERENT" || cmp === "MATERIAL_DISAGREEMENT") reasons.push("differs_from_other_founder");
    if (q.question_type === "scenario_decision") reasons.push("decision");
    const c = r.calibration as Record<string, unknown> | null;
    if (c && (c.underwriting_materiality === "high" || c.underwriting_materiality === "critical")) reasons.push("material_case");
    if (q.meta?.semantic_class === "UNRESOLVED_DISAGREEMENT") reasons.push("unresolved_disagreement");
    if (q.sensitivity === "high") reasons.push("high_sensitivity");
    if (/^en\.(own|team)\./.test(q.question_key)) reasons.push("ownership");
    const comment = writtenComment(q, r);
    if (q.priority >= 90 && comment && comment.length >= 80) reasons.push("substantive");
    if (q.required_core) reasons.push("core_answered");
    for (const reason of IMPORTANT_ORDER) if (reasons.includes(reason)) out.push({ reason, question: q, response: r });
  }
  return out.sort((a, b) => IMPORTANT_ORDER.indexOf(a.reason) - IMPORTANT_ORDER.indexOf(b.reason) || b.question.priority - a.question.priority);
}

/** Where an answer can be read in context, by tab. */
export function analysisLinks(question: AdminQuestion, businessFact: boolean, underwriting: boolean): { tab: TabKey; label: string }[] {
  const links: { tab: TabKey; label: string }[] = [];
  if (question.group === "calibration") links.push({ tab: "calibration", label: "View calibration case" });
  else if (question.group === "scenarios" && question.owner === "founder") links.push({ tab: "scenarios", label: "View borrower scenario" });
  else if (question.owner === "engineer") links.push({ tab: "engineering", label: "View Engineer analysis" });
  else if (question.owner === "founder") {
    links.push({ tab: "comparison", label: "View in Founder comparison" });
    if (businessFact) links.push({ tab: "business", label: "View in Business" });
    if (underwriting) links.push({ tab: "underwriting", label: "View in Underwriting" });
  }
  return links;
}
