/**
 * Backend health check used by the landing page.
 *
 * The request goes to the same-origin /api/health route, which next.config.ts
 * proxies to the Python API. The result is a discriminated union so the UI can
 * render exact states without inspecting exceptions.
 */

export const HEALTH_PATH = "/api/health";

export type HealthPayload = {
  status: "ok";
  service: string;
  version: string;
  environment: string;
  time: string;
};

export type HealthResult =
  | { state: "connected"; payload: HealthPayload; checkedAt: Date }
  | { state: "unavailable"; reason: string; checkedAt: Date };

function isHealthPayload(value: unknown): value is HealthPayload {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return (
    v.status === "ok" &&
    typeof v.service === "string" &&
    typeof v.version === "string" &&
    typeof v.environment === "string" &&
    typeof v.time === "string"
  );
}

function isAbortError(error: unknown): boolean {
  return typeof error === "object" && error !== null && (error as { name?: unknown }).name === "AbortError";
}

export async function checkBackendHealth(
  fetchImpl: typeof fetch = fetch,
  timeoutMs = 5000,
): Promise<HealthResult> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  const checkedAt = new Date();
  try {
    const response = await fetchImpl(HEALTH_PATH, {
      cache: "no-store",
      signal: controller.signal,
      headers: { accept: "application/json" },
    });
    if (!response.ok) {
      return {
        state: "unavailable",
        reason: `The API route answered with HTTP ${response.status}.`,
        checkedAt,
      };
    }
    let body: unknown;
    try {
      body = await response.json();
    } catch {
      return { state: "unavailable", reason: "The API route answered, but not with JSON.", checkedAt };
    }
    if (!isHealthPayload(body)) {
      return {
        state: "unavailable",
        reason: "The API answered with an unexpected health payload.",
        checkedAt,
      };
    }
    return { state: "connected", payload: body, checkedAt };
  } catch (error) {
    return {
      state: "unavailable",
      reason: isAbortError(error)
        ? `No response within ${timeoutMs / 1000} seconds.`
        : "The API route could not be reached.",
      checkedAt,
    };
  } finally {
    clearTimeout(timer);
  }
}
