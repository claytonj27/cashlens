import { api } from "@/lib/api";

export type Balance = { kind: string; provenance: string; amount_minor: number; as_of: string; source_type: string };
export type Account = { id: string; name: string; account_type: string; mask: string | null; source_type: string; balances: Balance[] };
export type TransactionRow = {
  id: string;
  posted_date: string;
  description: string;
  amount_minor: number;
  currency: string;
  status: string;
  source_type: string;
  revision: number;
  replaces_pending: boolean;
  account: { id: string; name: string; mask: string | null };
};
export type TransactionFilters = { account_id: string | null; date_from: string | null; date_to: string | null; status: "posted" | "pending" | "all"; q: string | null };
export type TransactionPage = { items: TransactionRow[]; next_cursor: string | null; total: number; limit: number; read_at: string; filters: TransactionFilters };
export type ProviderEvent = { kind: "provider_event"; operation: string; received_at: string; external_transaction_id: string; batch_start_cursor: string | null; batch_end_cursor: string | null; batch_applied_at: string | null };
export type ImportRowSource = {
  kind: "import_row";
  import_id: string;
  file_label: string;
  format: string;
  row_index: number;
  page_number: number | null;
  source_locator: string;
  imported_at: string | null;
  extracted: { posted_date?: string | null; amount_minor?: number | null; description?: string | null; external_ref?: string | null; balance_minor?: number | null };
  reviewed: boolean;
};
export type TransactionDetail = {
  transaction: TransactionRow;
  external_id: string | null;
  pending_transaction: { id: string; posted_date: string; amount_minor: number; status: string } | null;
  sources: (ProviderEvent | ImportRowSource)[];
};

export type FilterInput = { accountId: string; from: string; to: string; status: "posted" | "pending" | "all"; q: string };
export const defaultFilters: FilterInput = { accountId: "", from: "", to: "", status: "posted", q: "" };

export function fetchAccounts(workspaceId: string, token: string | null) {
  return api<Account[]>(`/api/workspaces/${workspaceId}/accounts`, { token });
}

export function fetchTransactions(workspaceId: string, filters: FilterInput, cursor: string | null, token: string | null) {
  const params = new URLSearchParams();
  if (filters.accountId) params.set("account_id", filters.accountId);
  if (filters.from) params.set("from", filters.from);
  if (filters.to) params.set("to", filters.to);
  if (filters.status !== "posted") params.set("status", filters.status);
  if (filters.q.trim()) params.set("q", filters.q.trim());
  if (cursor) params.set("cursor", cursor);
  params.set("limit", "50");
  return api<TransactionPage>(`/api/workspaces/${workspaceId}/transactions?${params.toString()}`, { token });
}

export function fetchTransactionDetail(workspaceId: string, transactionId: string, token: string | null) {
  return api<TransactionDetail>(`/api/workspaces/${workspaceId}/transactions/${transactionId}`, { token });
}

export function formatSigned(amountMinor: number): string {
  const abs = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(Math.abs(amountMinor) / 100);
  return amountMinor < 0 ? `−${abs}` : `+${abs}`;
}

export function formatDate(iso: string): string {
  return new Date(`${iso.slice(0, 10)}T00:00:00Z`).toLocaleDateString("en-US", { timeZone: "UTC", year: "numeric", month: "short", day: "numeric" });
}

export function formatTimestamp(iso: string): string {
  return new Date(iso).toLocaleString("en-US", { timeZone: "UTC", year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) + " UTC";
}

export const balanceKindLabel: Record<string, string> = {
  current: "Current balance",
  available: "Available balance",
  statement_closing: "Statement closing balance",
  statement_opening: "Statement opening balance",
};
export const provenanceLabel: Record<string, string> = { bank_observed: "bank-observed", owner_confirmed: "owner-confirmed", inferred: "inferred", projected: "projected" };
export const accountTypeLabel: Record<string, string> = { checking: "Checking", savings: "Savings" };
export const sourceTypeLabel: Record<string, string> = {
  synthetic_provider: "Synthetic demo data",
  csv_import: "CSV import",
  pdf_import: "PDF statement",
  bank_connector: "Bank connection",
};

/**
 * A stored value turned into words, with a readable fallback.
 *
 * The fallback matters: a value the interface has not been taught about would otherwise be
 * printed raw, and `statement_opening` in the middle of a sentence is a database column
 * leaking onto the screen rather than something an owner can read.
 */
export function readable(map: Record<string, string>, value: string): string {
  return map[value] ?? value.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}
