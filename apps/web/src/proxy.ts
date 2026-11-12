import { NextResponse, type NextRequest } from "next/server";

/**
 * Forwards every /api/* request to the CashLens API from the Next.js server.
 *
 * The browser only ever talks to this origin. The API origin and, on Vercel,
 * the deployment-protection bypass secret are read from server-side
 * environment variables at request time, so they never reach client code and
 * no rebuild is needed to change them.
 *
 * - Local development: defaults to the uvicorn address below.
 * - Vercel: API_ORIGIN must be set for the environment or branch (origin
 *   only, no /api suffix). A missing value answers 503 with a clear message
 *   instead of silently proxying to localhost or to another environment.
 * - Only the configured origin is ever used: the destination is built from
 *   the matched path and query, so a request cannot pick a different host.
 * - Incoming protection-bypass headers from clients are always dropped; the
 *   server sets its own from API_PROTECTION_BYPASS when configured. The
 *   secret is added only to requests to API_ORIGIN and never to redirects,
 *   which are passed back to the client unfollowed.
 */
const DEFAULT_LOCAL_API_ORIGIN = "http://127.0.0.1:8000";
const BYPASS_HEADER = "x-vercel-protection-bypass";
const BYPASS_COOKIE_HEADER = "x-vercel-set-bypass-cookie";

function resolveApiOrigin(): string | null {
  const configured = process.env.API_ORIGIN?.trim();
  if (configured) {
    try {
      return new URL(configured).origin;
    } catch {
      return null;
    }
  }
  return process.env.VERCEL === "1" ? null : DEFAULT_LOCAL_API_ORIGIN;
}

export function proxy(request: NextRequest) {
  const apiOrigin = resolveApiOrigin();
  if (!apiOrigin) {
    const environment = process.env.VERCEL_ENV ?? "unknown";
    return NextResponse.json(
      {
        error: `API_ORIGIN is not configured for the ${environment} environment of this deployment. Set it for this environment or Git branch in the Vercel project settings.`,
      },
      { status: 503 },
    );
  }
  const target = new URL(request.nextUrl.pathname + request.nextUrl.search, apiOrigin);
  const headers = new Headers(request.headers);
  headers.delete(BYPASS_HEADER);
  headers.delete(BYPASS_COOKIE_HEADER);
  const bypass = process.env.API_PROTECTION_BYPASS?.trim();
  if (bypass) {
    headers.set(BYPASS_HEADER, bypass);
  }
  return NextResponse.rewrite(target, { request: { headers } });
}

export const config = {
  matcher: "/api/:path*",
};
