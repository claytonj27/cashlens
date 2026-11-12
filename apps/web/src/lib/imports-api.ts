import { api } from "@/lib/api";
import { getSupabase } from "@/lib/supabase";

export type AccountRef = { id: string; name: string; mask: string | null };
export type ImportSummary = {
  id: string;
  status: "awaiting_upload" | "staged" | "confirmed" | "cancelled" | "failed";
  format: "csv" | "pdf";
  file_label: string;
  account: AccountRef | null;
  proposed_account: { name: string; account_type: string; mask: string | null } | null;
  byte_size: number | null;
  parser_version: string | null;
  draft_revision: number;
  confirmed_revision: number | null;
  summary: ImportStats | null;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
  confirmed_at: string | null;
  expires_at: string;
  duplicate_of: string | null;
};
export type ImportStats = {
  parser_version: string;
  row_count: number;
  blocking_rows: number;
  credits_minor: number;
  debits_minor: number;
  net_minor: number;
  period_start: string | null;
  period_end: string | null;
  flag_counts: Record<string, number>;
  balance: { opening_minor: number | null; closing_minor: number; period_start: string; period_end: string; breaks: number; reconciles: boolean; source?: string } | null;
  statement?: { period_start?: string; period_end?: string; opening_minor?: number; closing_minor?: number; total_credits_minor?: number; total_debits_minor?: number; transaction_count?: number; account_last4?: string; page_count?: number } | null;
  reconciliation?: { status: "matched" | "mismatch" | "unavailable"; source: string | null; expected_closing_minor: number | null; computed_closing_minor: number | null; problems: string[] };
  overlap?: { rows_with_values: number; matched_rows: number; existing_in_period: number; undecided_rows: number; likely_duplicate_statement: boolean };
  mapping_flags: string[];
  existing_in_period?: number;
  decision_counts?: Record<string, number>;
  result?: { created: number; linked: number; replaced: number; excluded: number };
};
export type MappingColumns = { date: string | null; description: string | null; amount: string | null; debit: string | null; credit: string | null; balance: string | null; reference: string | null; currency: string | null };
export type Mapping = { columns: MappingColumns; date_format: string; sign_convention: "signed_inflow_positive" | "signed_outflow_positive" | "debit_credit"; flags: string[]; candidates: Record<string, string[]> };
export type ImportDetail = ImportSummary & { columns: string[]; sample: Record<string, string>[]; mapping: Mapping | null };
export type ImportRow = {
  row_index: number;
  page_number: number | null;
  source_locator: string;
  raw: Record<string, string>;
  extracted: Record<string, unknown>;
  reviewed: Record<string, unknown> | null;
  effective: { posted_date?: string | null; description?: string | null; amount_minor?: number | null; balance_minor?: number | null; external_ref?: string | null };
  flags: string[];
  decision: "keep" | "exclude" | "link_existing" | "replace_existing";
  decision_by: "system" | "owner";
  linked_transaction_id: string | null;
  conflict: { kind?: string; transaction_id: string; posted_date: string; amount_minor: number; description: string; external_id?: string | null; other_candidates?: number } | null;
  blocking: boolean;
  needs_decision: boolean;
};
export type DecisionsResult = { updated: number; skipped_without_target: number; revision: number };
export type RowsPage = { items: ImportRow[]; next_after: number | null; total: number; flagged_total: number };
export type CreateImportResponse = { import: ImportSummary; upload: { bucket: string; path: string; token: string; expires_in_seconds: number; max_bytes: number; content_type: string } };
export type ConfirmResult = { import: ImportSummary; created: number; linked: number; replaced: number; excluded: number; already_confirmed: boolean };
export type NewAccount = { name: string; account_type: "checking" | "savings"; mask: string | null };

export const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;
export type ImportLocation = { import_id: string; status: string; workspace_id: string; business: { id: string; name: string } };

/** Which workspace an import really belongs to, answered from the record and the caller's membership. */
export function fetchImportLocation(importId: string, token: string) {
  return api<ImportLocation>(`/api/private/imports/${encodeURIComponent(importId)}`, { token });
}

const root = (workspaceId: string) => `/api/workspaces/${workspaceId}/imports`;

export function listImports(workspaceId: string, token: string) {
  return api<ImportSummary[]>(root(workspaceId), { token });
}

export function createImport(workspaceId: string, token: string, body: { account_id?: string; new_account?: NewAccount; file_label: string; declared_bytes: number }) {
  return api<CreateImportResponse>(root(workspaceId), { method: "POST", token, body });
}

/** Browser to private Storage directly, authorized only by the one-path token the API issued. */
export async function uploadToStorage(target: CreateImportResponse["upload"], file: File): Promise<void> {
  const supabase = getSupabase();
  if (!supabase) throw new Error("Uploads are not configured in this deployment.");
  const contentType = file.name.toLowerCase().endsWith(".pdf") || file.type === "application/pdf" ? "application/pdf" : "text/csv";
  const { error } = await supabase.storage.from(target.bucket).uploadToSignedUrl(target.path, target.token, file, { contentType, upsert: false });
  if (error) throw new Error(error.message || "The upload failed.");
}

export function markUploaded(workspaceId: string, importId: string, token: string) {
  return api<ImportSummary>(`${root(workspaceId)}/${importId}/uploaded`, { method: "POST", token });
}

export function fetchImport(workspaceId: string, importId: string, token: string) {
  return api<ImportDetail>(`${root(workspaceId)}/${importId}`, { token });
}

export function fetchImportRows(workspaceId: string, importId: string, token: string, opts: { after?: number | null; only?: "all" | "flagged"; limit?: number } = {}) {
  const params = new URLSearchParams();
  if (opts.after !== null && opts.after !== undefined) params.set("after", String(opts.after));
  if (opts.only && opts.only !== "all") params.set("only", opts.only);
  params.set("limit", String(opts.limit ?? 100));
  return api<RowsPage>(`${root(workspaceId)}/${importId}/rows?${params.toString()}`, { token });
}

export function setMapping(workspaceId: string, importId: string, token: string, mapping: { columns: Partial<MappingColumns>; date_format: string; sign_convention: Mapping["sign_convention"] }) {
  return api<ImportDetail>(`${root(workspaceId)}/${importId}/mapping`, { method: "PUT", token, body: { mapping } });
}

export function patchRow(workspaceId: string, importId: string, rowIndex: number, token: string, patch: { posted_date?: string; amount_minor?: number; description?: string; decision?: ImportRow["decision"]; linked_transaction_id?: string }) {
  return api<ImportRow>(`${root(workspaceId)}/${importId}/rows/${rowIndex}`, { method: "PATCH", token, body: patch });
}

export function decideRows(workspaceId: string, importId: string, token: string, decision: "keep" | "link_existing" | "exclude", scope: "undecided" | "candidates" | "conflicts" = "undecided") {
  return api<DecisionsResult>(`${root(workspaceId)}/${importId}/decisions`, { method: "POST", token, body: { decision, scope } });
}

export function confirmImport(workspaceId: string, importId: string, token: string, revision: number) {
  return api<ConfirmResult>(`${root(workspaceId)}/${importId}/confirm`, { method: "POST", token, body: { revision } });
}

export function cancelImport(workspaceId: string, importId: string, token: string) {
  return api<ImportSummary>(`${root(workspaceId)}/${importId}/cancel`, { method: "POST", token });
}

export const flagLabel: Record<string, string> = {
  invalid_date: "date could not be read",
  invalid_amount: "amount could not be read",
  zero_amount: "zero amount",
  missing_description: "description missing",
  unsupported_currency: "not USD",
  amount_precision: "amount rounded to cents",
  invalid_balance: "balance could not be read",
  balance_break: "running balance does not follow from the previous row",
  repeated_in_file: "identical to another row in this file",
  already_imported: "already imported (same reference)",
  conflicting_revision: "same reference as an existing transaction with different values",
  duplicate_candidate: "looks like an existing transaction (same date, amount and description)",
  needs_decision: "your decision needed",
  debit_and_credit_both_present: "both debit and credit present",
};

export const reconciliationProblemLabel: Record<string, string> = {
  rows_do_not_bridge_statement_balances: "opening balance plus the rows does not reach the printed closing balance",
  credits_differ_from_statement_total: "credits differ from the statement's printed total",
  debits_differ_from_statement_total: "debits differ from the statement's printed total",
  row_count_differs_from_statement: "the statement says it has a different number of transactions",
  running_balance_differs_from_statement_closing: "the last running balance differs from the printed closing balance",
  running_balance_breaks: "the running balance does not follow from row to row",
};

export const mappingFlagLabel: Record<string, string> = {
  ambiguous_date_format: "Dates could be month/day or day/month. Choose the format below.",
  sign_unverified: "No running balance to verify which sign means money in. Check the totals below.",
  date_format_unrecognized: "The date format was not recognized. Choose it below.",
  date_column_unrecognized: "No date column was recognized. Choose it below.",
  description_column_unrecognized: "No description column was recognized. Choose it below.",
  amount_column_unrecognized: "No amount, debit or credit column was recognized. Choose them below.",
};

export const statusLabel: Record<ImportSummary["status"], string> = {
  awaiting_upload: "waiting for upload",
  staged: "ready for review",
  confirmed: "confirmed",
  cancelled: "cancelled",
  failed: "failed",
};
