import type { NextConfig } from "next";

// Requests to /api/* are forwarded to the CashLens API by src/proxy.ts, which
// reads API_ORIGIN (and, on Vercel, API_PROTECTION_BYPASS) at request time.
//
// Response headers for every page. HSTS comes from the platform; these cover
// what it does not: no framing by other sites, no MIME sniffing, a referrer
// policy that keeps paths off third-party logs, and no device permissions.
// A full Content-Security-Policy is not set here: the app relies on inline
// bootstrapping scripts and two analytics loaders, and a policy strict enough
// to be worth having needs per-request nonces, which is its own change.
const securityHeaders = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Content-Security-Policy", value: "frame-ancestors 'none'" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(), payment=()" },
];

const nextConfig: NextConfig = {
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
};

export default nextConfig;
