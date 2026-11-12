"use client";

import { Analytics as VercelAnalytics, type BeforeSendEvent } from "@vercel/analytics/next";
import Script from "next/script";
import { usePathname } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useSyncExternalStore } from "react";

import {
  ANALYTICS_CONFIGURED,
  routeLabel,
  GA_MEASUREMENT_ID,
  bootstrapTag,
  consentServerSnapshot,
  isExcludedPath,
  readConsent,
  subscribeConsent,
  trackPageView,
  writeConsent,
  type Consent,
} from "@/lib/analytics";

/**
 * The measurement tag, the one page view per navigation, and the choice that gates both.
 *
 * The script is not requested until someone has agreed, so a visitor who declines or ignores the
 * notice never loads Google's code at all rather than loading it in a denied state. The page
 * view is sent by hand from a sanitised route because the tag is configured not to send its own,
 * which is what keeps a navigation to exactly one.
 */
export function Analytics() {
  if (!ANALYTICS_CONFIGURED) return null;
  return (
    <Suspense fallback={null}>
      <AnalyticsInner />
    </Suspense>
  );
}

function AnalyticsInner() {
  const pathname = usePathname();
  // Subscribed rather than fetched into state on mount, so this holds no stale copy and a
  // choice made in another tab lands here too.
  const consent = useSyncExternalStore(subscribeConsent, readConsent, consentServerSnapshot);
  // Which route has already been reported. A re-render, a query change or a second effect pass
  // must not produce a second page view for the same navigation.
  const reported = useRef<string | null>(null);
  const bootstrapped = useRef(false);

  const decide = useCallback((choice: Consent) => writeConsent(choice), []);

  const granted = consent === "granted";

  // Consent defaults and configuration first, then one page view per navigation.
  //
  // Both go through `dataLayer`, which queues before Google's script arrives and is replayed in
  // order once it does, so doing this here rather than in the script's ready handler is what
  // guarantees the tag is configured before the first view is counted. It is also the only
  // place a view is sent: an earlier version also sent one when the script became ready, and
  // that produced two for the opening page.
  useEffect(() => {
    if (!granted) return;
    if (!bootstrapped.current) {
      bootstrapTag();
      bootstrapped.current = true;
    }
    if (!pathname || isExcludedPath(pathname)) return;
    if (reported.current === pathname) return;
    reported.current = pathname;
    trackPageView(pathname);
  }, [granted, pathname]);

  return (
    <>
      {granted ? (
        <>
          <Script
            id="ga-tag"
            strategy="afterInteractive"
            src={`https://www.googletagmanager.com/gtag/js?id=${GA_MEASUREMENT_ID}`}
          />
          {/* Vercel's own page-view counting, held to the same rules as the Google tag: the
              same consent, the same excluded screens, and the same sanitised route rather than
              the address bar. Without the rewrite below it would report the real URL, which on
              a protected preview carries the share token in its query string. */}
          <VercelAnalytics beforeSend={sanitiseVercelEvent} />
        </>
      ) : null}
      {/* Nothing is collected on an excluded screen, so there is nothing to ask about there. */}
      {consent === null && pathname && !isExcludedPath(pathname) ? (
        <ConsentNotice onDecide={decide} />
      ) : null}
    </>
  );
}

/**
 * What Vercel Web Analytics is allowed to record.
 *
 * Returning null drops the event entirely, which is what happens on every screen that never
 * reports. Otherwise the URL is rebuilt from the route template, so a demo slug, a workspace id,
 * an import id, a query string and a fragment are all left behind before anything is sent.
 */
function sanitiseVercelEvent(event: BeforeSendEvent): BeforeSendEvent | null {
  try {
    const url = new URL(event.url);
    if (isExcludedPath(url.pathname)) return null;
    return { ...event, url: `${url.origin}${routeLabel(url.pathname)}` };
  } catch {
    // An address this code cannot parse is not one worth reporting.
    return null;
  }
}

/** A choice before anything is collected, and plain about what is collected if it is. */
function ConsentNotice({ onDecide }: { onDecide: (choice: Consent) => void }) {
  return (
    <div
      role="dialog"
      aria-modal="false"
      aria-labelledby="analytics-choice-heading"
      className="fixed inset-x-0 bottom-0 z-50 border-t border-line bg-surface/97 px-4 py-4 shadow-raised backdrop-blur sm:px-6"
    >
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <p id="analytics-choice-heading" className="text-sm font-semibold text-ink">
            Measure which pages get opened?
          </p>
          <p className="mt-0.5 max-w-2xl text-xs text-ink-muted">
            Google Analytics, counting page views and a few interactions such as opening a demo
            or applying a scenario. Never your questions, answers, amounts, file names or any
            identifier of you or a business. Nothing is collected unless you agree, and you can
            change this at any time from the footer.
          </p>
        </div>
        <div className="flex shrink-0 gap-2">
          <button
            type="button"
            onClick={() => onDecide("denied")}
            className="rounded-xl border border-line-strong bg-surface px-4 py-2 text-sm font-semibold text-ink transition hover:border-plum-400 focus:outline-none focus-visible:ring-2 focus-visible:ring-plum-600"
          >
            No thanks
          </button>
          <button
            type="button"
            onClick={() => onDecide("granted")}
            className="rounded-xl border border-plum-600 bg-plum-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-plum-500 focus:outline-none focus-visible:ring-2 focus-visible:ring-plum-600 focus-visible:ring-offset-2"
          >
            Allow
          </button>
        </div>
      </div>
    </div>
  );
}

/** A way back to the choice, for anyone who wants to change it later. */
export function AnalyticsChoice() {
  const consent = useSyncExternalStore(subscribeConsent, readConsent, consentServerSnapshot);

  if (!ANALYTICS_CONFIGURED) return null;

  const change = (choice: Consent) => writeConsent(choice);

  return (
    <p className="text-xs text-ink-faint">
      Analytics:{" "}
      <span className="text-ink-muted">
        {consent === "granted" ? "on" : consent === "denied" ? "off" : "not chosen"}
      </span>{" "}
      <button
        type="button"
        onClick={() => change(consent === "granted" ? "denied" : "granted")}
        className="underline underline-offset-2 hover:text-plum-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-plum-600"
      >
        {consent === "granted" ? "Turn off" : "Turn on"}
      </button>
    </p>
  );
}
