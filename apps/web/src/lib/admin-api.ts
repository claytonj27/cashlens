import { api } from "@/lib/api";

/**
 * The operational dashboard's reads. Everything here is usage accounting: what was spent,
 * on what capability, by which provider, and how it ended. No route in this file can return
 * a question, an answer, a transaction or a business name, because the server never selects
 * those columns for it.
 */

export type WindowName = "today" | "7d" | "all";

export type Budget = {
  milestone_cap_micro: number;
  settled_micro: number;
  held_micro: number;
  total_micro: number;
  remaining_micro: number;
  rolling_24h_micro: number;
  rolling_24h_global_cap_micro: number;
  rolling_24h_identity_cap_micro: number;
  rolling_24h_truncated: boolean;
  active_reservations: number;
  uncertain_holds: number;
  external_probe_micro: number;
  external_probe_in_ledger: boolean;
  overall_total_micro: number;
};

export type CountRow = { key: string; label: string; count: number };

export type ProviderRow = {
  provider: string;
  model: string;
  mode: string;
  count: number;
  cost_micro: number;
  average_latency_ms: number | null;
  timed_count: number;
};

export type Usage = {
  window: WindowName;
  window_start: string | null;
  generated_at: string;
  budget: Budget;
  by_capability: CountRow[];
  by_status: CountRow[];
  by_provider: ProviderRow[];
  average_latency_ms: number | null;
  latency_sample: number;
  runs_in_window: number;
  runs_truncated: boolean;
  claims_withheld: number;
  claims_withheld_recorded: boolean;
  live_runs: number;
  non_live_runs: number;
  last_activity_at: string | null;
};

export type RunRow = {
  id: string;
  created_at: string;
  capability: string;
  mode: string;
  provider: string;
  model: string;
  status: string;
  latency_ms: number | null;
  cost_micro: number | null;
  cost_is_estimate: boolean;
  attempts: number | null;
  rounds: number | null;
  error_code: string | null;
};

export type Runs = {
  window: WindowName;
  items: RunRow[];
  next_before: string | null;
  truncated: boolean;
};

export function fetchAdminSession(token: string | null) {
  return api<{ is_admin: boolean }>("/api/admin/session", { token });
}

export function fetchUsage(window: WindowName, token: string | null) {
  return api<Usage>(`/api/admin/ai/usage?window=${window}`, { token });
}

export function fetchRuns(window: WindowName, token: string | null, limit = 25) {
  return api<Runs>(`/api/admin/ai/runs?window=${window}&limit=${limit}`, { token });
}

/** Micro-dollars as money. Six decimal places exist in the ledger, so small sums stay visible. */
export function dollars(micro: number | null, options: { precise?: boolean } = {}): string {
  if (micro === null || micro === undefined) return "Not recorded";
  const value = micro / 1_000_000;
  // Six places where a figure would otherwise round away to nothing, two where it would not.
  // Exact zero is plain zero: "$0.000000" reads as a measurement, and it is not one.
  const digits = value !== 0 && (options.precise || Math.abs(value) < 0.01) ? 6 : 2;
  return value.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

/** Milliseconds as seconds, or an honest absence. Never zero for "we did not record it". */
export function duration(ms: number | null): string {
  if (ms === null || ms === undefined) return "Not recorded";
  return `${(ms / 1000).toFixed(1)} s`;
}

export function stamp(iso: string | null): string {
  if (!iso) return "Not recorded";
  const when = new Date(iso);
  if (Number.isNaN(when.getTime())) return "Not recorded";
  return when.toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

/** How this run was produced, in the words the run trace itself uses. */
export function modeLabel(mode: string): string {
  return { live: "Live", replay: "Replayed", cached: "Cached", scripted: "Scripted" }[mode] ?? mode;
}
