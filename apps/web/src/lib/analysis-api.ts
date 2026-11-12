import { api } from "@/lib/api";

/** The shapes the API returns. Kept in step with services/api/app/analysis/routes.py. */
export type BalanceRow = {
  account_id: string;
  account_name: string;
  mask: string | null;
  kind: string;
  provenance: string;
  amount_minor: number;
  as_of: string;
  is_live: boolean;
};

export type Balances = {
  rows: BalanceRow[];
  live_total_minor: number | null;
  statement_total_minor: number | null;
  as_of_dates: string[];
  mixed_dates: boolean;
  label: string;
};

export type MonthRow = {
  month: string;
  money_in_minor: number;
  money_out_minor: number;
  net_minor: number;
  posted_count: number;
  transfers_netted_minor: number;
  complete: boolean;
};

export type CategoryRow = {
  key: string;
  label: string;
  group: string;
  inflow_minor: number;
  outflow_minor: number;
  count: number;
  netted_pair: boolean;
  transaction_ids: string[];
};

export type ChangeRow = {
  key: string;
  label: string;
  group: string;
  current_minor: number;
  previous_minor: number;
  change_minor: number;
};

export type RecurringRow = {
  label: string;
  category: string;
  typical_amount_minor: number;
  cadence_days: number;
  occurrences: number;
  last_seen: string;
  next_expected: string;
  direction: string;
  transaction_ids: string[];
};

export type ForecastPoint = {
  date: string;
  balance_minor: number;
  events: { date: string; label: string; amount_minor: number; kind: string }[];
};

export type ForecastPath = {
  points: ForecastPoint[];
  ending_minor: number;
  lowest_minor: number;
  lowest_date: string;
};

export type Assumption = {
  key: string;
  label: string;
  detail: string;
  estimated: boolean;
};

export type Forecast = {
  version: string;
  available: boolean;
  as_of: string;
  horizon_days: number;
  anchor: {
    amount_minor: number;
    as_of: string;
    kind: string;
    provenance: string;
    is_live: boolean;
  } | null;
  daily_rate_minor: number | null;
  reasons: string[];
  baseline: ForecastPath | null;
  scenario: ForecastPath | null;
  difference_minor: number | null;
  lowest_difference_minor: number | null;
  assumptions: Assumption[];
};

export type CashAnalysis = {
  version: string;
  metrics_version: string;
  category_rules_version: string;
  workspace_id: string;
  business_name: string;
  data_mode: string;
  as_of: string;
  data_through: string | null;
  currency: string;
  balances: Balances;
  months: MonthRow[];
  categories: CategoryRow[];
  comparison: {
    month: string;
    previous_month: string;
    month_totals: MonthRow | null;
    previous_totals: MonthRow | null;
    changes: ChangeRow[];
  } | null;
  recurring: RecurringRow[];
  coverage: {
    months_available: number;
    complete_months: number;
    posted_count: number;
    first_posted: string | null;
    last_posted: string | null;
    stale_days: number | null;
    sufficient_for_forecast: boolean;
    reasons: string[];
  };
  forecast: Forecast;
  pending_excluded: number;
  /** Hash of every input behind these figures. A stored explanation is stale if it differs. */
  input_fingerprint: string;
  overrides_applied: number;
  classification: {
    uncategorized_count: number;
    generic_instrument_count: number;
    examples: string[];
  };
  read_at: string;
};

export type ScenarioInput = {
  as_of?: string | null;
  receipts_change_percent?: number;
  expected_payment_minor?: number | null;
  expected_payment_date?: string | null;
  delay_days?: number;
  one_time_expense_minor?: number | null;
  one_time_expense_date?: string | null;
  one_time_expense_label?: string | null;
};

export type CategoryRules = {
  version: string;
  note: string;
  counterparty_note: string;
  instrument_words: string[];
  reason_codes: { key: string; meaning: string }[];
  rules: {
    key: string;
    label: string;
    group: string;
    direction: string;
    keywords: string[];
  }[];
};

const root = (workspaceId: string) =>
  `/api/workspaces/${encodeURIComponent(workspaceId)}/analysis`;

export function fetchAnalysis(
  workspaceId: string,
  token: string | null,
  asOf?: string | null,
) {
  const query = asOf ? `?as_of=${encodeURIComponent(asOf)}` : "";
  return api<CashAnalysis>(`${root(workspaceId)}${query}`, { token });
}

export function runScenario(
  workspaceId: string,
  token: string | null,
  body: ScenarioInput,
) {
  return api<CashAnalysis>(`${root(workspaceId)}/scenario`, {
    method: "POST",
    token,
    body,
  });
}

export function fetchCategoryRules(workspaceId: string, token: string | null) {
  return api<CategoryRules>(`${root(workspaceId)}/rules`, { token });
}

/** Money is stored in integer minor units; never do arithmetic on the formatted string. */
export function money(
  minor: number,
  options: { sign?: boolean; compact?: boolean } = {},
): string {
  const value = Math.abs(minor) / 100;
  // `compact` is for axis ticks and other places where the exact cent is noise and the
  // room is a few characters wide: $12.4k rather than $12,431. Everywhere a figure is
  // quoted as a figure, the full form is used.
  const text = value.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    ...(options.compact && value >= 10000
      ? { notation: "compact" as const, maximumFractionDigits: 1 }
      : { maximumFractionDigits: value >= 1000 ? 0 : 2 }),
  });
  if (options.sign) return `${minor < 0 ? "−" : "+"}${text}`;
  return minor < 0 ? `−${text}` : text;
}

export function monthLabel(month: string): string {
  const [year, mon] = month.split("-");
  const name = new Date(Number(year), Number(mon) - 1, 1).toLocaleDateString(
    "en-US",
    { month: "short" },
  );
  return `${name} ${year}`;
}

export function dayLabel(day: string): string {
  const [y, m, d] = day.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
  });
}
