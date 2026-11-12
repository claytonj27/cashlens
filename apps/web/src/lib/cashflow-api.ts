/** Cash-flow classification over one staged import: run it, read it, correct it, export it. */

import { ApiError, api } from "@/lib/api";

export type Direction = "inflow" | "outflow";
export type Confidence = "high" | "medium" | "low";

export const INFLOW_CATEGORIES = ["Organic revenue", "Internal transfers", "Loans income", "Personal income", "Overdraft/Reversals", "Unclassified"] as const;
export const OUTFLOW_CATEGORIES = ["Operating expense", "Transfer out", "Loan payments", "Personal expense", "Payroll", "Unclassified"] as const;

/** The buckets a row may be moved to, which is only ever the ones for its own direction. */
export function categoriesFor(direction: Direction): readonly string[] {
  return direction === "inflow" ? INFLOW_CATEGORIES : OUTFLOW_CATEGORIES;
}

/** Every bucket, for a filter that spans both directions. */
export const ALL_CATEGORIES: readonly string[] = [...INFLOW_CATEGORIES, ...OUTFLOW_CATEGORIES.filter((c) => c !== "Unclassified")];

export type Progress = {
  total: number;
  classified: number;
  remaining: number;
  done: boolean;
  batches: number;
  cost_micro: number;
  /** Why a call stopped short: a spend cap, an unpriced model, or a run already in flight. */
  stopped: string | null;
};

export type ClassifiedRow = {
  row_index: number;
  transaction_id: string;
  date: string | null;
  name: string | null;
  merchant: string | null;
  amount_minor: number;
  direction: Direction | null;
  category: string | null;
  confidence: Confidence | null;
  reason: string | null;
  source: "ai" | "manual" | null;
};

export type CashflowPage = { items: ClassifiedRow[]; next_after: number | null; total: number; matched: number };
export type Bucket = { category: string; direction: Direction; count: number; total_minor: number };
export type CashflowSummary = {
  total_rows: number;
  classified: number;
  unclassified: number;
  inflow_count: number;
  inflow_minor: number;
  outflow_count: number;
  outflow_minor: number;
  net_minor: number;
  manually_reviewed: number;
  by_confidence: Record<Confidence, number>;
  buckets: Bucket[];
};

export type Filters = { direction?: Direction | ""; category?: string; confidence?: Confidence | ""; search?: string };

const root = (workspaceId: string, importId: string) => `/api/workspaces/${workspaceId}/imports/${importId}/cashflow`;

/** One bounded pass. Call again while `done` is false; only unclassified rows are picked up. */
export function runClassification(workspaceId: string, importId: string, token: string) {
  return api<Progress>(root(workspaceId, importId), { method: "POST", token });
}

export function fetchCashflowRows(workspaceId: string, importId: string, token: string, opts: Filters & { after?: number | null; limit?: number } = {}) {
  const params = new URLSearchParams();
  if (opts.after !== null && opts.after !== undefined) params.set("after", String(opts.after));
  params.set("limit", String(opts.limit ?? 50));
  if (opts.direction) params.set("direction", opts.direction);
  if (opts.category) params.set("category", opts.category);
  if (opts.confidence) params.set("confidence", opts.confidence);
  if (opts.search?.trim()) params.set("search", opts.search.trim());
  return api<CashflowPage>(`${root(workspaceId, importId)}?${params.toString()}`, { token });
}

export function fetchCashflowSummary(workspaceId: string, importId: string, token: string) {
  return api<CashflowSummary>(`${root(workspaceId, importId)}/summary`, { token });
}

/** The owner's correction. The API refuses a category that does not belong to the row's direction. */
export function correctRow(workspaceId: string, importId: string, rowIndex: number, token: string, category: string) {
  return api<ClassifiedRow>(`${root(workspaceId, importId)}/${rowIndex}`, { method: "PATCH", token, body: { category } });
}

/**
 * Download the reviewed file.
 *
 * Not a plain link: the API needs the session bearer token, which an anchor cannot send, and the
 * response is CSV rather than the JSON `api()` parses. So the body is fetched, turned into a blob
 * and handed to a temporary anchor. The filename comes from the API's own Content-Disposition
 * where the browser exposes it, and falls back to the same shape when it does not.
 */
export async function downloadClassifiedCsv(workspaceId: string, importId: string, token: string, fallbackLabel: string): Promise<void> {
  const response = await fetch(`${root(workspaceId, importId)}/export`, {
    headers: { accept: "text/csv", "x-cashlens-client": "web", authorization: `Bearer ${token}` },
    cache: "no-store",
    credentials: "same-origin",
  });
  if (!response.ok) {
    throw new ApiError(response.status, response.status >= 500 ? "The export did not complete." : "The export was refused.", { reference: response.headers.get("x-request-id") });
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filenameFrom(response.headers.get("content-disposition")) ?? exportName(fallbackLabel);
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  // Revoked on the next turn of the event loop: revoking before the click is handled cancels it.
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

function filenameFrom(header: string | null): string | null {
  const match = header?.match(/filename="([^"]+)"/);
  return match ? match[1] : null;
}

/** `statement.csv` becomes `statement_classified.csv`, matching what the API sends. */
export function exportName(label: string): string {
  const stem = label.replace(/\.(csv|pdf)$/i, "");
  const safe = stem.replace(/[^A-Za-z0-9._-]+/g, "_").replace(/^[._-]+|[._-]+$/g, "").slice(0, 80) || "import";
  return `${safe}_classified.csv`;
}

export const confidenceLabel: Record<Confidence, string> = {
  high: "High",
  medium: "Medium",
  low: "Low",
};

/** Why a run stopped short, said plainly. The owner can act on every one of these. */
export const stoppedLabel: Record<string, string> = {
  in_flight: "Another classification run is already going. Wait for it to finish, then continue.",
  identity_cap: "This account has reached its daily model spend limit. Classification will continue tomorrow, or sooner if the limit is raised.",
  global_cap: "This deployment has reached its daily model spend limit for everyone. Rows already classified are kept.",
  total_cap: "The approved model budget for this milestone is spent. Rows already classified are kept.",
  unpriced_model: "This deployment has no recorded price for its model, so no paid call can be bounded. Nothing was spent.",
};
