import { api } from "@/lib/api";
import { destinationFor, showsDiscoveryLink, type MeRoles } from "@/lib/discovery-rules";

/**
 * The stakeholder's own reads and writes on the Discovery portal.
 *
 * Every route here is scoped by the server to the caller's own assignment: the assignment id in
 * a path is looked up together with the session's user id, and anybody else's id answers 404.
 * Nothing in this file sends a user id, and nothing decides visibility: the section payload
 * already holds only the questions the person may see.
 */

export type Progress = {
  total: number;
  answered: number;
  core_total: number;
  core_answered: number;
  core_complete: boolean;
  complete: boolean;
  percent: number;
};

export type SectionSummary = {
  id: string;
  slug: string;
  title: string;
  description: string | null;
  is_core: boolean;
  estimated_minutes: number | null;
  order: number;
  progress: Progress;
};

export type Assignment = {
  assignment_id: string;
  template_id: string;
  template_slug: string;
  title: string;
  subtitle: string | null;
  estimated_minutes: number | null;
  status: string;
  current_section_slug: string | null;
  resume_section_slug: string | null;
  sections: SectionSummary[];
  progress: Progress;
};

/** One of the caller's own assignments, by name. The API lists only the caller's. */
export type AssignmentSummary = {
  assignment_id: string;
  template_slug: string;
  title: string;
};

export type DiscoveryMe = {
  role: string;
  first_name: string | null;
  /** The newest assignment, fully read; what the portal opens by default. */
  assignment: Assignment | null;
  /** Every assignment the caller holds, newest first, the one above included. */
  assignments: AssignmentSummary[];
};

export type Option = { value: string; label: string };

export type ScenarioAnswer = {
  decision: "approve" | "deny" | "manual_review";
  confidence: number;
  reason_codes: string[];
  maximum_exposure_minor: number | null;
  additional_information_needed: string | null;
  what_would_change_my_decision: string | null;
  notes: string | null;
};

/** A stored transaction calibration answer. `direction` is the case's, copied by the server. */
export type CalibrationAnswer = {
  direction: "inflow" | "outflow";
  classification_category: string;
  classification_confidence: number;
  underwriting_materiality: string;
  underwriting_signal: string;
  decision_impact: string | null;
  reason_codes: string[];
  needs_more_context: boolean;
  context_needed: string | null;
  reasoning: string | null;
  handling_recommendation: string | null;
};

/**
 * The synthetic transaction a calibration question asks about. No family or variant metadata
 * is served, so nothing here lets two cases be recognised as versions of each other.
 */
export type TransactionCase = {
  direction: "inflow" | "outflow";
  amount_minor: number;
  descriptor: string;
  categories: string[];
  business_vertical: string | null;
  business_description: string | null;
  account_type: string | null;
  merchant: string | null;
  counterparty: string | null;
  payment_rail: string | null;
  posted_date: string | null;
  day_of_week: number | null;
  time_of_day: string | null;
  memo: string | null;
  frequency_30d: number | null;
  frequency_90d: number | null;
  average_amount_minor: number | null;
  amount_min_minor: number | null;
  amount_max_minor: number | null;
  recurrence_pattern: string | null;
  related_transactions: Record<string, unknown>[];
  context: Record<string, unknown>;
};

export type Response = {
  question_id: string;
  status: "answered" | "skipped";
  answer: unknown;
  scenario: ScenarioAnswer | null;
  calibration: CalibrationAnswer | null;
  note: string | null;
  question_version: number;
  updated_at: string | null;
};

export type QuestionType =
  | "short_text"
  | "long_text"
  | "boolean"
  | "single_select"
  | "multi_select"
  | "number"
  | "currency"
  | "percentage"
  | "scale"
  | "ranking"
  | "scenario_decision"
  | "transaction_calibration";

export type Question = {
  id: string;
  question_key: string;
  question_version: number;
  question_text: string;
  helper_text: string | null;
  question_type: QuestionType;
  options: Option[];
  scenario_brief: Record<string, unknown> | null;
  transaction_case: TransactionCase | null;
  required_core: boolean;
  sensitivity: "low" | "medium" | "high";
  order: number;
  response: Response | null;
};

export type Section = {
  id: string;
  slug: string;
  title: string;
  description: string | null;
  is_core: boolean;
  estimated_minutes: number | null;
  order: number;
  position: number;
  section_count: number;
  next_section_slug: string | null;
  previous_section_slug: string | null;
  questions: Question[];
  progress: Progress;
};

export type SaveRequest =
  | { answer: unknown }
  | { scenario: ScenarioAnswer }
  | { calibration: Omit<CalibrationAnswer, "direction"> };

export type SaveResult = {
  response: Response;
  section_progress: Progress;
  assignment_progress: Progress;
  status: string;
};

export type HistoryEntry = {
  change: "updated" | "deleted";
  valid_from: string;
  valid_to: string;
  status: string | null;
  answer: unknown;
  scenario: ScenarioAnswer | null;
  calibration: CalibrationAnswer | null;
  question_version: number | null;
};

export function fetchDiscoveryMe(token: string | null) {
  return api<DiscoveryMe>("/api/discovery/me", { token });
}

export function fetchAssignment(assignmentId: string, token: string | null) {
  return api<Assignment>(`/api/discovery/assignments/${encodeURIComponent(assignmentId)}`, { token });
}

export function fetchSection(assignmentId: string, slug: string, token: string | null) {
  return api<Section>(
    `/api/discovery/assignments/${encodeURIComponent(assignmentId)}/sections/${encodeURIComponent(slug)}`,
    { token },
  );
}

export function saveAnswer(assignmentId: string, questionId: string, body: SaveRequest, token: string | null) {
  return api<SaveResult>(
    `/api/discovery/assignments/${encodeURIComponent(assignmentId)}/responses/${encodeURIComponent(questionId)}`,
    { method: "PUT", body, token },
  );
}

export function skipQuestion(assignmentId: string, questionId: string, token: string | null) {
  return api<SaveResult>(
    `/api/discovery/assignments/${encodeURIComponent(assignmentId)}/responses/${encodeURIComponent(questionId)}/skip`,
    { method: "POST", token },
  );
}

export function fetchHistory(assignmentId: string, questionId: string, token: string | null) {
  return api<{ question_id: string; entries: HistoryEntry[] }>(
    `/api/discovery/assignments/${encodeURIComponent(assignmentId)}/responses/${encodeURIComponent(questionId)}/history`,
    { token },
  );
}

/** Opens or touches a visit. Fire and forget: a lost timing is not a lost answer. */
export function touchSession(assignmentId: string, token: string | null): void {
  api(`/api/discovery/assignments/${encodeURIComponent(assignmentId)}/sessions`, { method: "POST", token }).catch(
    () => undefined,
  );
}

/**
 * Where to send someone who has just signed in. Reads the same `/api/me` the workspace reads;
 * any failure to read it falls through to the workspace, which is where sign-in always went.
 */
export async function destinationAfterLogin(token: string | null): Promise<"/discovery" | "/workspace"> {
  if (!token) return "/workspace";
  try {
    const me = await api<MeRoles>("/api/me", { token });
    return destinationFor(me);
  } catch {
    return "/workspace";
  }
}

/** Whether the header should draw the Discovery link for this session. */
export async function fetchDiscoveryLink(token: string | null): Promise<boolean> {
  if (!token) return false;
  const me = await api<MeRoles>("/api/me", { token });
  return showsDiscoveryLink(me.role);
}

export function minutesLabel(minutes: number | null): string | null {
  if (minutes === null || minutes <= 0) return null;
  return minutes === 1 ? "about a minute" : `about ${minutes} minutes`;
}
