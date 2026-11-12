/** Same-origin calls to the CashLens API through the server-side proxy. */

export class ApiError extends Error {
  status: number;
  /** Safe correlation id from the API (X-Request-Id), for support and logs. */
  reference: string | null;
  retryable: boolean;
  /** Structured detail, when the API answered with an object. */
  detail: Record<string, unknown> | null;
  constructor(status: number, message: string, options: { reference?: string | null; retryable?: boolean; detail?: Record<string, unknown> | null } = {}) {
    super(message);
    this.status = status;
    this.reference = options.reference ?? null;
    this.retryable = options.retryable ?? false;
    this.detail = options.detail ?? null;
  }
  /** Message plus the reference, for display. */
  describe(): string {
    return this.reference ? `${this.message} (reference ${this.reference})` : this.message;
  }
}

type Init = { method?: "GET" | "POST" | "PUT" | "PATCH"; body?: unknown; token?: string | null };

export async function api<T>(path: string, init: Init = {}): Promise<T> {
  const headers: Record<string, string> = { accept: "application/json", "x-cashlens-client": "web" };
  if (init.body !== undefined) headers["content-type"] = "application/json";
  if (init.token) headers.authorization = `Bearer ${init.token}`;
  const response = await fetch(path, {
    method: init.method ?? "GET",
    headers,
    body: init.body === undefined ? undefined : JSON.stringify(init.body),
    cache: "no-store",
    credentials: "same-origin",
  });
  const text = await response.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = null;
  }
  if (!response.ok) {
    const body = typeof data === "object" && data !== null ? (data as Record<string, unknown>) : null;
    const rawDetail = body?.detail;
    const structured = typeof rawDetail === "object" && rawDetail !== null ? (rawDetail as Record<string, unknown>) : null;
    const message = typeof rawDetail === "string" ? rawDetail : structured && typeof structured.message === "string" ? structured.message : response.status >= 500 ? "The service did not respond correctly." : `HTTP ${response.status}`;
    const reference = (typeof body?.reference === "string" ? body.reference : null) ?? response.headers.get("x-request-id");
    throw new ApiError(response.status, message, { reference, retryable: body?.retryable === true || response.status === 502 || response.status === 503 || response.status === 504, detail: structured });
  }
  return data as T;
}

export function formatMinor(amountMinor: number | null | undefined): string {
  if (amountMinor === null || amountMinor === undefined) return "n/a";
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(amountMinor / 100);
}
