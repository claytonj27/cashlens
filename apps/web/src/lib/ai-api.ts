import { api } from "@/lib/api";

/** The shapes the API returns. Kept in step with services/api/app/ai/routes.py. */

export type RunSummary = {
  id: string;
  capability: string;
  /** live means a model answered just now. replay means a recorded or scripted answer. */
  mode: "live" | "cached" | "replay";
  provider: string;
  model_requested: string;
  model_used: string | null;
  prompt_version: string;
  analysis_version: string;
  rules_version: string;
  analysis_date: string;
  input_fingerprint: string;
  status: string;
  attempts: number;
  latency_ms: number | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  cost_micro: number | null;
  cost_source: string | null;
  error_code: string | null;
  validation: {
    schema?: { ok: boolean; messages: string[] };
    evidence?: { ok: boolean; messages: string[] };
    numbers?: { ok: boolean; messages: string[] };
    support?: { ok: boolean; messages: string[] };
    claims_withheld?: number;
    coverage_gaps?: string[];
    headline_replaced?: boolean;
  } | null;
  /** Null for a direct provider. Present only where a router chose the upstream. */
  routing: {
    routing?: {
      allow_fallbacks?: boolean;
      require_parameters?: boolean;
      data_collection?: string;
      max_price?: { prompt: number; completion: number };
    };
    upstream_provider?: string | null;
    upstream_provider_note?: string;
  } | null;
  created_at: string;
  completed_at: string | null;
};

export type BriefFigure = {
  ref: string;
  amount_minor: number;
  rendered: string;
};

export type BriefClaim = {
  signal_id: string;
  signal_label: string;
  text: string;
  figures: BriefFigure[];
  evidence: string[];
  evidence_total: number;
  evidence_truncated: boolean;
};

export type Brief = {
  headline: string;
  claims: BriefClaim[];
  questions: { signal_id: string; text: string }[];
  caveats: string[];
  claims_withheld: number;
  withheld_reasons: string[];
};

export type BriefStatus =
  | "none"
  | "current"
  | "stale"
  | "unavailable"
  | "insufficient_data"
  | "succeeded"
  | "superseded"
  | "invalid_output"
  | "provider_error"
  | "timeout";

export type BriefView = {
  status: BriefStatus;
  /** Whether the brief carried below still matches the figures on screen. */
  stored_state: "none" | "current" | "stale";
  brief: Brief | null;
  run: RunSummary | null;
  changes_since: string[];
  reasons: string[];
  message: string | null;
  retryable: boolean;
  as_of: string;
  input_fingerprint: string;
};

export type RouteOption = {
  key: string;
  label: string;
  model: string | null;
  default: boolean;
  note: string | null;
};

export type RouteChoices = {
  options: RouteOption[];
  note: string;
};

const root = (workspaceId: string) =>
  `/api/workspaces/${encodeURIComponent(workspaceId)}/ai`;

/**
 * Which provider and model this workspace may be asked with.
 *
 * The server decides, from the business's own data mode. A private workspace is only ever
 * offered the direct provider, so this list is never a menu of things that would be refused.
 */
export function fetchRoutes(workspaceId: string, token: string | null) {
  return api<RouteChoices>(`${root(workspaceId)}/routes`, { token });
}

/** A chosen route as a query string. Absent means the deployment's own provider. */
function routeQuery(route: string | null | undefined, existing = ""): string {
  if (!route || route === "direct") return existing;
  return existing
    ? `${existing}&route=${encodeURIComponent(route)}`
    : `?route=${encodeURIComponent(route)}`;
}

export function fetchBrief(
  workspaceId: string,
  token: string | null,
  asOf?: string | null,
) {
  const query = asOf ? `?as_of=${encodeURIComponent(asOf)}` : "";
  return api<BriefView>(`${root(workspaceId)}/brief${query}`, { token });
}

export function generateBrief(
  workspaceId: string,
  token: string | null,
  asOf?: string | null,
  route?: string | null,
) {
  const asOfQuery = asOf ? `?as_of=${encodeURIComponent(asOf)}` : "";
  const query = routeQuery(route, asOfQuery);
  return api<BriefView>(`${root(workspaceId)}/brief${query}`, {
    method: "POST",
    token,
  });
}

export function fetchRun(
  workspaceId: string,
  token: string | null,
  runId: string,
) {
  return api<RunSummary>(
    `${root(workspaceId)}/runs/${encodeURIComponent(runId)}`,
    { token },
  );
}

/** Micro-dollars ($1 = 1,000,000) shown to four decimal places, because calls are cheap. */
export function cost(micro: number | null): string {
  if (micro === null) return "cost unknown";
  return `$${(micro / 1_000_000).toFixed(4)}`;
}

export function seconds(ms: number | null): string {
  return ms === null ? "time unknown" : `${(ms / 1000).toFixed(1)} s`;
}

export function stamp(iso: string | null): string {
  if (!iso) return "";
  return new Date(iso).toLocaleString("en-US", {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** Reviewable suggestions. A suggestion changes nothing until a person decides. */

export type Suggestion = {
  id: string;
  run_id: string;
  status: "open" | "accepted" | "rejected" | "superseded";
  outcome: "suggested" | "unresolved";
  category: string | null;
  category_label: string | null;
  alternatives: string[];
  alternative_labels: string[];
  rationale: string | null;
  question: string | null;
  evidence: string[];
  evidence_transaction_ids: string[];
  transaction_id: string;
  transaction_revision: number;
  description: string;
  posted_date: string;
  amount_minor: number;
  account_name: string | null;
  current_category: string | null;
  created_at: string;
};

export type AllowedCategory = {
  key: string;
  label: string;
  group: string;
  direction: string;
};

export type SuggestionList = {
  items: Suggestion[];
  candidates_remaining: number;
  allowed_categories: AllowedCategory[];
  note: string;
};

export type GenerateSuggestions = {
  status: string;
  run_id: string | null;
  created: number;
  dropped: number;
  message: string | null;
  run: RunSummary | null;
};

export type DecisionResult = {
  status: string;
  suggestion_id: string;
  applied: boolean;
  category: string | null;
  previous_category: string | null;
  message: string | null;
};

export function fetchSuggestions(workspaceId: string, token: string | null) {
  return api<SuggestionList>(`${root(workspaceId)}/suggestions`, { token });
}

export function generateSuggestions(
  workspaceId: string,
  token: string | null,
  route?: string | null,
) {
  return api<GenerateSuggestions>(
    `${root(workspaceId)}/suggestions${routeQuery(route)}`,
    { method: "POST", token },
  );
}

export function decideSuggestion(
  workspaceId: string,
  token: string | null,
  suggestionId: string,
  action: "accept" | "reject" | "correct",
  category?: string,
) {
  return api<DecisionResult>(
    `${root(workspaceId)}/suggestions/${encodeURIComponent(suggestionId)}/decision`,
    { method: "POST", token, body: { action, category: category ?? null } },
  );
}

/** An explanation of a scenario the owner already ran. The projection stands without it. */

export type ScenarioExplanation = {
  status:
    | "succeeded"
    /** Checked and shown, but it did not cover everything the scenario turns on. */
    | "partial"
    | "current"
    | "insufficient_data"
    | "invalid_output"
    | "provider_error"
    | "timeout";
  explanation: Brief | null;
  run: RunSummary | null;
  message: string | null;
  fingerprint: string | null;
  /** True when this data and these assumptions were already explained, so nothing was spent. */
  reused: boolean;
};

export function explainScenario(
  workspaceId: string,
  token: string | null,
  input: Record<string, unknown>,
  route?: string | null,
) {
  return api<ScenarioExplanation>(
    `${root(workspaceId)}/scenario/explain${routeQuery(route)}`,
    { method: "POST", token, body: input },
  );
}

/** What produced a result, for the line shown beside it. Never a guess: both come from the run. */
export function producedBy(run: RunSummary | null): string {
  if (!run) return "";
  const model = run.model_used ?? run.model_requested;
  if (run.provider !== "openrouter") return `${run.provider} · ${model}`;
  const upstream = run.routing?.upstream_provider;
  // OpenRouter does not promise to name the upstream, so an unnamed one is said to be
  // unnamed rather than filled in from the model id.
  return `OpenRouter · ${model} · served by ${upstream ?? "an upstream it did not name"}`;
}

/** The AI CFO panel. The owner asks; the server fetches; the model explains what came back. */

export type ToolTrace = {
  tool: string;
  arguments: Record<string, unknown>;
  ok: boolean;
  summary: string;
};

export type ClarifyOption = {
  id: string;
  label: string;
  scenario: Record<string, unknown> | null;
};

export type Clarify = {
  question: string;
  options: ClarifyOption[];
};

export type AskAnswer = {
  answer: string;
  claims: BriefClaim[];
  questions: { signal_id: string; text: string }[];
  sources: string[];
  claims_withheld: number;
  withheld_reasons: string[];
};

export type AskStatus =
  | "answered"
  /** Shown, but shorter than it should be. Never reported as a complete answer. */
  | "partial"
  /** Asked back rather than computing something adjacent to the question. */
  | "clarifying"
  | "invalid_output"
  | "provider_error"
  | "timeout"
  /** Ran out of rounds, tool calls or the sixty seconds. */
  | "deadline";

export type AskView = {
  status: AskStatus;
  conversation_id: string;
  turn_id: string | null;
  answer: AskAnswer | null;
  clarify: Clarify | null;
  scenario_applied: Record<string, unknown> | null;
  scenario_paths: Record<string, unknown> | null;
  proposal_id: string | null;
  tool_calls: ToolTrace[];
  message: string | null;
  run: RunSummary | null;
  fingerprint: string;
};

export type AskSuggestion = {
  text: string;
  scenario: Record<string, unknown> | null;
};

export type AskSuggestions = {
  items: AskSuggestion[];
  conversation_id: string | null;
  note: string;
};

export type Turn = {
  id: string;
  sequence: number;
  question: string;
  answer: AskAnswer | null;
  clarify: Clarify | null;
  scenario_applied: Record<string, unknown> | null;
  proposal_id: string | null;
  tool_calls: ToolTrace[];
  run_id: string | null;
  input_fingerprint: string | null;
  status: AskStatus;
  created_at: string;
};

export type Conversation = {
  conversation_id: string | null;
  turns: Turn[];
  agreed_assumptions: Record<string, unknown> | null;
  /** True when the figures have moved since those assumptions were agreed. */
  assumptions_stale: boolean;
  turns_left: number;
  fingerprint: string;
};

export function fetchAskSuggestions(workspaceId: string, token: string | null) {
  return api<AskSuggestions>(`${root(workspaceId)}/ask/suggestions`, { token });
}

export function fetchConversation(workspaceId: string, token: string | null) {
  return api<Conversation>(`${root(workspaceId)}/ask/conversation`, { token });
}

/**
 * Ask one question.
 *
 * The body carries the owner's text and, at most, the id of a choice the server itself
 * offered. It never carries history or a scenario: the server reads the turns it wrote, so a
 * client cannot supply its own evidence.
 */
export function askCfo(
  workspaceId: string,
  token: string | null,
  body: { text: string; conversation_id?: string | null; option_id?: string | null },
  route?: string | null,
) {
  return api<AskView>(`${root(workspaceId)}/ask${routeQuery(route)}`, {
    method: "POST",
    token,
    body,
  });
}

/** What a turn is doing right now, from what has actually happened, never invented. */
export function progressLabel(calls: ToolTrace[]): string {
  const last = calls[calls.length - 1];
  if (!last) return "Thinking…";
  return (
    {
      facts: "Reading your figures…",
      transactions: "Checking transactions…",
      scenario: "Calculating the scenario…",
      propose_category: "Preparing a suggestion…",
    }[last.tool] ?? "Working…"
  );
}
