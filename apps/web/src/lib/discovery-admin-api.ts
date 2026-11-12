import { api } from "@/lib/api";

/**
 * The admin dashboard's reads. Every route here is GET, requires the admin profile role on the
 * server, and returns other people's answers; nothing in this file can write one. The shapes
 * mirror `app.discovery.analysis` and are rendered, filtered and searched client-side.
 */

export type ComparisonStatus =
  | "AGREE"
  | "ROUGHLY_ALIGNED"
  | "DIFFERENT"
  | "MATERIAL_DISAGREEMENT"
  | "NEITHER_ANSWERED"
  | "NOT_COMPARABLE"
  | "OWN_BANK"
  | `ONLY_${string}_ANSWERED`;

export type Meta = {
  system_use: string[] | null;
  semantic_class: string | null;
  ask_stage: string | null;
  why: string | null;
  tells: string | null;
  tests: string | null;
  teaches: string | null;
  title: string | null;
};

export type AdminQuestion = {
  id: string;
  question_key: string;
  question_text: string;
  helper_text: string | null;
  question_type: string;
  options: { value: string; label: string }[];
  required_core: boolean;
  order: number;
  priority: number;
  sensitivity: string | null;
  section_slug: string;
  section_title: string;
  level: "core" | "recommended" | "deep_dive";
  group: "core" | "recommended" | "deep_dive" | "calibration" | "scenarios";
  topic: string;
  owner: string;
  retired: boolean;
  meta: Meta;
  scenario_brief: Record<string, unknown> | null;
  transaction_case: Record<string, unknown> | null;
};

export type AdminResponse = {
  status: "answered" | "skipped";
  answer: unknown;
  display: string | null;
  note: string | null;
  scenario: Record<string, unknown> | null;
  calibration: Record<string, unknown> | null;
  question_version: number | null;
  created_at: string | null;
  updated_at: string | null;
};

export type GroupCounts = { available: number; answered: number; skipped: number };

export type RespondentCard = {
  user_id: string;
  assignment_id: string;
  template_slug: string;
  label: string;
  name: string;
  kind: "stakeholder" | "reviewer";
  status: string;
  last_activity: string | null;
  sessions: number;
  core_total: number;
  core_answered: number;
  core_percent: number;
  total: number;
  answered: number;
  skips: number;
  prefer_not_to_answer: number;
  by_group: Record<string, GroupCounts>;
};

export type ComparisonRow = {
  question: AdminQuestion;
  status: ComparisonStatus;
  detail: string;
  extra: Record<string, unknown>;
  lead: AdminResponse | null;
  partner: AdminResponse | null;
  business_fact: boolean;
  underwriting: boolean;
  needs_reconciliation?: boolean;
};

export type FounderSummary = {
  total: number;
  both: number;
  only_lead: number;
  only_partner: number;
  neither: number;
  agree: number;
  roughly: number;
  different: number;
  material: number;
  not_comparable: number;
};

export type Comparisons = { labels: [string, string]; assignments: Record<string, string | null>; summary: FounderSummary; rows: ComparisonRow[]; tolerance: Record<string, number> };

export type CalibrationRow = ComparisonRow & {
  case_key: string;
  title: string | null;
  case_family: string | null;
  variant: string | null;
  changed_feature: string | null;
  baseline_question_id: string | null;
  reveal_of_question_id: string | null;
  is_variant: boolean;
  is_reveal: boolean;
  amount_minor: number | null;
  direction: string | null;
  business_vertical: string | null;
  high_dollar: boolean;
};

export type Changed = { classification: boolean; confidence: boolean; materiality: boolean; signal: boolean } | null;

export type Calibration = {
  labels: [string, string];
  assignments: Record<string, string | null>;
  summary: { cases: number; both: number; agreements: number; material: number; classification_disagreements: number; awaiting_both: number; awaiting_one: number };
  rows: CalibrationRow[];
  controlled_pairs: { case_family: string; changed_feature: string | null; baseline: CalibrationRow; variant: CalibrationRow; lead_changed: Changed; partner_changed: Changed }[];
  reveals: { case_key: string; title: string | null; reveal: string | null; before: CalibrationRow; after: CalibrationRow; lead_changed: Changed; partner_changed: Changed }[];
};

export type ScenarioRow = ComparisonRow & { scenario_key: string; title: string | null; approve_vs_deny: boolean };
export type Scenarios = {
  labels: [string, string];
  assignments: Record<string, string | null>;
  summary: { scenarios: number; both: number; decision_agreements: number; decision_disagreements: number; approve_vs_deny: number; awaiting_both: number };
  rows: ScenarioRow[];
};

export type EngineeringItem = { question: AdminQuestion; category: string; buckets: string[]; response: AdminResponse | null };
export type Engineering = {
  respondent: string | null;
  assignment_id: string | null;
  categories: { category: string; items: EngineeringItem[] }[];
  buckets: { bucket: string; rule: string; answered: number; total: number; items: EngineeringItem[] }[];
  stated_constraints: EngineeringItem[];
  answered: number;
  total: number;
};

export type Engagement = RespondentCard & {
  total_percent: number;
  explanation_rate: number;
  average_text_length: number | null;
  topics: { topic: string; available: number; answered: number; percent: number }[];
};

export type OpenItem = {
  kind: string;
  rank: number;
  question: AdminQuestion;
  why: string | null;
  detail: string;
  lead?: AdminResponse | null;
  partner?: AdminResponse | null;
  engineer?: AdminResponse | null;
};

export type Signal = { kind: "aligned" | "split"; question_id: string; question_key: string; text: string };

export type Overview = {
  respondents: RespondentCard[];
  founder: FounderSummary;
  calibration: Calibration["summary"];
  scenarios: Scenarios["summary"];
  answers_received: Record<string, number>;
  engineer_core: { answered: number; total: number };
  open_high_priority: number;
  recent_activity: RecentResponse[];
  top_disagreements: OpenItem[];
  top_unanswered: OpenItem[];
  facts_collected: number;
  facts_needing_reconciliation: number;
  policy_signals: Signal[];
  engineer_constraints: { question: AdminQuestion; response: AdminResponse | null }[];
  unknowns: OpenItem[];
  open_questions: number;
};

export type Business = { groups: { group: string; items: ComparisonRow[] }[]; facts_collected: number; needs_reconciliation: number };
export type Underwriting = { groups: { group: string; items: ComparisonRow[] }[]; signals: Signal[] };

export type Dataset = {
  respondents: Engagement[];
  sections: { id: string; slug: string; title: string; is_core: boolean; group: string; level: string; owner: string; templates: string[]; positions: Record<string, number> }[];
  questions: AdminQuestion[];
  answers: Record<string, Record<string, AdminResponse>>;
};

export type HistoryEntry = {
  change: string | null;
  valid_from: string | null;
  valid_to: string | null;
  display: string | null;
  status: string | null;
  answer: unknown;
  scenario: Record<string, unknown> | null;
  calibration: Record<string, unknown> | null;
};

export type AnswerHistory = { assignment_id: string; question_id: string; respondent: string; current: AdminResponse | null; entries: HistoryEntry[] };

export type RecentResponse = {
  respondent: string;
  kind: "stakeholder" | "reviewer";
  assignment_id: string;
  question_id: string;
  question_key: string;
  question_text: string;
  level: string;
  group: string;
  status: string;
  display: string | null;
  at: string | null;
};

const BASE = "/api/admin/discovery";

export const fetchDiscoveryAdminSession = (token: string | null) => api<{ is_discovery_admin: boolean }>(`${BASE}/session`, { token });
export const fetchOverview = (token: string | null) => api<Overview>(`${BASE}/overview`, { token });
export const fetchDataset = (token: string | null) => api<Dataset>(`${BASE}/dataset`, { token });
export const fetchComparisons = (token: string | null) => api<Comparisons>(`${BASE}/comparisons`, { token });
export const fetchBusiness = (token: string | null) => api<Business>(`${BASE}/business`, { token });
export const fetchUnderwriting = (token: string | null) => api<Underwriting>(`${BASE}/underwriting`, { token });
export const fetchCalibration = (token: string | null) => api<Calibration>(`${BASE}/calibration`, { token });
export const fetchScenarios = (token: string | null) => api<Scenarios>(`${BASE}/scenarios`, { token });
export const fetchEngineering = (token: string | null) => api<Engineering>(`${BASE}/engineering`, { token });
export const fetchEngagement = (token: string | null) => api<{ respondents: Engagement[] }>(`${BASE}/engagement`, { token });
export const fetchOpenQuestions = (token: string | null) => api<{ count: number; items: OpenItem[] }>(`${BASE}/open-questions`, { token });
export const fetchAnswerHistory = (assignmentId: string, questionId: string, token: string | null) =>
  api<AnswerHistory>(`${BASE}/history/${encodeURIComponent(assignmentId)}/${encodeURIComponent(questionId)}`, { token });

export type ExportName = "founder-comparison" | "calibration" | "scenarios" | "progress" | "dataset";

/** Fetch an export with the session token and hand it to the browser as a file. */
export async function downloadExport(name: ExportName, token: string | null): Promise<void> {
  const path = name === "dataset" ? `${BASE}/dataset` : `${BASE}/export/${name}`;
  const response = await fetch(path, {
    headers: { accept: name === "dataset" ? "application/json" : "text/csv", "x-cashlens-client": "web", ...(token ? { authorization: `Bearer ${token}` } : {}) },
    cache: "no-store",
    credentials: "same-origin",
  });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name === "dataset" ? "discovery-dataset.json" : `discovery-${name}.csv`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
