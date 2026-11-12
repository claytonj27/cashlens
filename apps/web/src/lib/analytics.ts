/**
 * Traffic analytics: what parts of this application people open, and nothing else.
 *
 * Three rules this file exists to keep, because a measurement tag is exactly the kind of thing
 * that quietly starts carrying more than it should.
 *
 *  1. Nothing is collected before someone agrees. Consent Mode starts denied, the tag is not
 *     loaded until a choice is stored, and the choice can be changed afterwards.
 *  2. No URL, title or referrer leaves this file as the browser saw it. Every path is reduced
 *     to a route template first, so a workspace id, an import id, a slug that is not one of the
 *     three shipped demos, a query string or a fragment cannot travel inside a page view. That
 *     matters more than usual here: a preview share token rides in the query string.
 *  3. Event parameters come from a fixed list. Anything not on it is dropped rather than sent,
 *     so a future call site cannot add an amount or a question by passing an extra field.
 *
 * Advertising features, Google Signals and User-ID are never enabled, and nothing here sends an
 * identifier of any person, account or business.
 */

export const GA_MEASUREMENT_ID = process.env.NEXT_PUBLIC_GA_MEASUREMENT_ID ?? "";

/**
 * Whether this deployment collects at all. Off unless explicitly switched on, so a local run
 * and an automated check never load the tag; the branch preview sets it, and a verification run
 * can set it deliberately.
 */
export const ANALYTICS_CONFIGURED =
  process.env.NEXT_PUBLIC_ANALYTICS_ENABLED === "true" && GA_MEASUREMENT_ID.startsWith("G-");

export const CONSENT_KEY = "cashlens.analytics-consent";
export type Consent = "granted" | "denied";

/** The three demo businesses this application ships. Any other slug is not reported. */
const OFFERED_DEMOS = new Set(["juniper-kitchen", "northline-services", "willow-studio"]);

/**
 * Screens that never report. Authentication carries credentials in flight, import and review
 * carry a person's own bank statement, the operations dashboard is nobody's business but the
 * operator's, and the Discovery portal is a private questionnaire whose section names, question
 * text and answers stay between the person and the server.
 */
const EXCLUDED = [
  "/admin",
  "/login",
  "/signup",
  "/forgot-password",
  "/auth",
  "/workspace/import",
  "/workspace/imports",
  "/discovery",
];

export function isExcludedPath(pathname: string): boolean {
  return EXCLUDED.some((prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`));
}

/**
 * A real path reduced to the route it is an instance of.
 *
 * `/demo/juniper-kitchen/analysis` becomes `/demo/[slug]/analysis`, and an id or an unknown slug
 * becomes a placeholder rather than travelling. The query string and fragment are dropped
 * entirely; nothing in this application puts anything in them that analytics should see.
 */
export function routeLabel(pathname: string): string {
  const clean = pathname.split("?")[0].split("#")[0].replace(/\/+$/, "") || "/";
  const parts = clean.split("/").filter(Boolean);
  if (parts.length === 0) return "/";
  if (parts[0] === "demo") {
    if (parts.length === 1) return "/demo";
    const rest = parts.slice(2).join("/");
    return rest ? `/demo/[slug]/${rest}` : "/demo/[slug]";
  }
  if (parts[0] === "workspace") {
    const rest = parts.slice(1);
    if (rest.length === 0) return "/workspace";
    if (rest[0] === "imports") return "/workspace/imports/[id]";
    return `/workspace/${rest[0]}`;
  }
  // Excluded above, so never reported; reduced anyway, so a section slug cannot travel even if
  // the exclusion list were edited.
  if (parts[0] === "discovery") return parts.length === 1 ? "/discovery" : "/discovery/[section]";
  return `/${parts[0]}`;
}

/** The demo business in a path, but only when it is one this application actually ships. */
export function demoBusiness(pathname: string): string | undefined {
  const parts = pathname.split("/").filter(Boolean);
  if (parts[0] !== "demo" || parts.length < 2) return undefined;
  return OFFERED_DEMOS.has(parts[1]) ? parts[1] : undefined;
}

/** The section of the product a route belongs to, as a coarse label. */
export function sectionLabel(pathname: string): string {
  const route = routeLabel(pathname);
  if (route === "/") return "landing";
  if (route === "/demo") return "demo-list";
  if (route.startsWith("/demo/[slug]")) return `demo-${route.split("/")[3] ?? "overview"}`;
  if (route.startsWith("/workspace")) return `workspace-${route.split("/")[2] ?? "overview"}`;
  if (route.startsWith("/discovery")) return "discovery";
  return route.replace(/^\//, "");
}

// ---------------------------------------------------------------- the tag --
type Gtag = (...args: unknown[]) => void;

declare global {
  interface Window {
    dataLayer?: unknown[];
    gtag?: Gtag;
  }
}

function gtag(...args: unknown[]): void {
  if (typeof window === "undefined") return;
  window.dataLayer = window.dataLayer || [];
  window.dataLayer.push(args);
}

export function readConsent(): Consent | null {
  if (typeof window === "undefined") return null;
  try {
    const stored = window.localStorage.getItem(CONSENT_KEY);
    return stored === "granted" || stored === "denied" ? stored : null;
  } catch {
    // A browser that refuses storage is treated as not having agreed.
    return null;
  }
}

/** Broadcast within this tab; `storage` only fires in the others. */
const CONSENT_EVENT = "cashlens:analytics-consent";

export function writeConsent(choice: Consent): void {
  try {
    window.localStorage.setItem(CONSENT_KEY, choice);
  } catch {
    // Nothing to do. Without storage the choice lasts this page only, which is the safe way
    // round: the next load starts denied again.
  }
  gtag("consent", "update", { analytics_storage: choice });
  window.dispatchEvent(new Event(CONSENT_EVENT));
}

/**
 * The stored choice as an external store, so the interface can read it without an effect that
 * sets state on mount. Rendering it this way is also what keeps the server and the first client
 * render in step: the server has no storage, so it sees no choice.
 */
export function subscribeConsent(onChange: () => void): () => void {
  window.addEventListener("storage", onChange);
  window.addEventListener(CONSENT_EVENT, onChange);
  return () => {
    window.removeEventListener("storage", onChange);
    window.removeEventListener(CONSENT_EVENT, onChange);
  };
}

export const consentServerSnapshot = (): Consent | null => null;

/**
 * The parameters an event may carry. A fixed list, checked at the point of sending, so no call
 * site can introduce a new field without changing this file.
 */
export type EventParams = {
  section?: string;
  capability?: string;
  provider_route?: string;
  demo_business?: string;
  outcome?: string;
};

const ALLOWED_PARAMS = ["section", "capability", "provider_route", "demo_business", "outcome"] as const;

export type EventName =
  | "demo_opened"
  | "analysis_viewed"
  | "scenario_applied"
  | "evidence_opened"
  | "ai_question_submitted"
  | "ai_answer_received"
  | "ai_request_failed";

/** A short, lowercase, bounded value. Anything longer or odder is not worth sending. */
function safeValue(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim().toLowerCase().slice(0, 40);
  return /^[a-z0-9._-]+$/.test(trimmed) ? trimmed : undefined;
}

/**
 * Send one event, if collection is on and consent was given.
 *
 * Analytics never interrupts the application: every path through here is guarded, and a failure
 * to measure something is not a failure to do it.
 */
export function track(name: EventName, params: EventParams = {}): void {
  try {
    if (!ANALYTICS_CONFIGURED || typeof window === "undefined") return;
    if (readConsent() !== "granted") return;
    if (isExcludedPath(window.location.pathname)) return;
    const payload: Record<string, string> = {};
    for (const key of ALLOWED_PARAMS) {
      const value = safeValue(params[key]);
      if (value) payload[key] = value;
    }
    gtag("event", name, payload);
  } catch {
    // Deliberately silent.
  }
}

/** The one page view for a navigation, built only from sanitised values. */
export function trackPageView(pathname: string): void {
  try {
    if (!ANALYTICS_CONFIGURED || typeof window === "undefined") return;
    if (readConsent() !== "granted") return;
    if (isExcludedPath(pathname)) return;
    const route = routeLabel(pathname);
    gtag("event", "page_view", {
      page_location: `${window.location.origin}${route}`,
      page_path: route,
      page_title: `CashLens ${sectionLabel(pathname)}`,
      // Never the real referrer: it can be another protected URL, share token and all.
      page_referrer: window.location.origin,
    });
  } catch {
    // Deliberately silent.
  }
}

/** The consent defaults and tag configuration, run once before anything is sent. */
export function bootstrapTag(): void {
  gtag("consent", "default", {
    analytics_storage: "denied",
    ad_storage: "denied",
    ad_user_data: "denied",
    ad_personalization: "denied",
  });
  gtag("js", new Date());
  gtag("config", GA_MEASUREMENT_ID, {
    // Every page view is sent by hand from a sanitised route, so the tag must not send its own.
    send_page_view: false,
    // Automatic collection reads real URLs and form contents; both are refused here.
    allow_google_signals: false,
    allow_ad_personalization_signals: false,
    anonymize_ip: true,
    page_location: `${window.location.origin}/`,
    page_referrer: window.location.origin,
  });
}
