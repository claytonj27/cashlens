import Link from "next/link";

import { AnalyticsChoice } from "@/components/analytics";
import { BackendStatus } from "@/components/backend-status";
import { DemoEntry, ExploreDemoButton } from "@/components/landing-actions";
import { ScaffoldNotice } from "@/components/scaffold-notice";
import { Icon, secondaryLargeClass } from "@/components/ui";
import { accountsAvailable, demoAvailable } from "@/lib/features";

/**
 * The landing page: one claim, one explanation, two ways in, and a picture of the product.
 *
 * The preview is a schematic of the real overview — its actual layout, cards and labels —
 * with no figures in it. A screenshot would go stale and a made-up balance would be a made-up
 * balance; the shape of the thing is what a visitor needs before they open a demo.
 */
export default function HomePage() {
  return (
    <main className="flex-1">
      <section className="mx-auto w-full max-w-6xl px-4 pb-10 pt-12 sm:px-6 sm:pt-16 lg:px-8">
        <div className="grid items-center gap-10 lg:grid-cols-[minmax(0,5fr)_minmax(0,6fr)] lg:gap-14">
          <div className="max-w-xl">
            <p className="t-eyebrow mb-4">Independent concept · synthetic data</p>
            <h1 className="t-display">Know where your cash is heading.</h1>
            <p className="mt-5 text-lg leading-relaxed text-ink-soft">
              CashLens reads a small business&apos;s bank transactions, works out what changed
              and what is coming up, and explains it in plain words. Every figure is checked
              against the data before you see it.
            </p>
            {demoAvailable ? (
              <div className="mt-8 flex flex-wrap items-center gap-3">
                <ExploreDemoButton />
                {accountsAvailable ? (
                  <Link href="/signup" className={secondaryLargeClass}>
                    Analyze your statements
                  </Link>
                ) : null}
              </div>
            ) : (
              <div className="mt-8">
                <ScaffoldNotice />
              </div>
            )}
            <ul className="mt-8 grid gap-2 text-sm text-ink-muted sm:grid-cols-2">
              {[
                ["chart", "A 30-day projection from your own history"],
                ["spark", "An AI CFO that only explains computed figures"],
                ["review", "Categories suggested, never applied without you"],
                [
                  "doc",
                  accountsAvailable
                    ? "CSV and PDF statement imports, reviewed before they count"
                    : "Every figure traced to the transaction behind it",
                ],
              ].map(([icon, text]) => (
                <li key={text} className="flex items-start gap-2">
                  <Icon name={icon as "chart"} className="mt-0.5 size-4 shrink-0 text-plum-600" />
                  <span>{text}</span>
                </li>
              ))}
            </ul>
          </div>

          <ProductPreview />
        </div>
      </section>

      <section className="mx-auto w-full max-w-6xl px-4 py-12 sm:px-6 lg:px-8">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <p className="t-eyebrow mb-1.5">Three demo businesses</p>
            <h2 className="text-2xl font-bold tracking-tight text-ink">
              Open one and ask it something.
            </h2>
          </div>
          <p className="max-w-md text-sm text-ink-muted">
            Six months of generated bank history each. Your exploration lives in an anonymous
            session in this browser and never touches anyone&apos;s private data.
          </p>
        </div>
        <div className="mt-6 grid gap-4 md:grid-cols-3">
          <DemoEntry
            slug="juniper-kitchen"
            name="Juniper Kitchen"
            industry="Restaurant"
            icon="wallet"
            blurb="Card settlements every few days, weekly supplier deliveries, and a payout that sometimes lands late."
          />
          <DemoEntry
            slug="northline-services"
            name="Northline Services"
            industry="General contracting"
            icon="bank"
            blurb="Lumpy customer receipts, an equipment lease, and a deposit nobody has named yet."
          />
          <DemoEntry
            slug="willow-studio"
            name="Willow Studio"
            industry="Creative studio"
            icon="doc"
            blurb="Software subscriptions, contractor payments through Venmo, and quieter months."
          />
        </div>
      </section>

      <footer className="mx-auto flex w-full max-w-6xl flex-col gap-3 px-4 pb-12 sm:px-6 lg:px-8">
        <AnalyticsChoice />
        <details className="text-xs text-ink-faint">
          <summary className="cursor-pointer list-none hover:text-ink-muted">System status</summary>
          <div className="mt-3 max-w-lg">
            <BackendStatus />
          </div>
        </details>
      </footer>
    </main>
  );
}

/** The overview, as a shape. Labels are the real ones; every figure is deliberately blank. */
function ProductPreview() {
  const bar = (w: string) => (
    <span className={`inline-block h-2 rounded-full bg-lavender-200 ${w}`} aria-hidden />
  );
  return (
    <div className="rise" aria-label="A preview of the CashLens overview, without figures">
      <div className="rounded-[1.75rem] bg-plum-600 p-3 shadow-raised sm:p-4">
        <div className="mb-3 flex items-center gap-2 px-1.5 sm:mb-4">
          <Icon name="spark" className="size-4 text-white" />
          <p className="text-[13px] font-semibold text-white">
            Your cash, your projection, and an assistant that only explains checked figures
          </p>
        </div>
      <div className="overflow-hidden rounded-2xl border border-line bg-surface shadow-card">
        <div className="flex items-center justify-between border-b border-line px-4 py-2.5 text-xs">
          <span className="font-semibold text-plum-800">CashLens</span>
          <span className="rounded-full border border-line px-2 py-0.5 text-ink-muted">Juniper Kitchen ▾</span>
        </div>
        <div className="grid gap-3 bg-canvas p-3 sm:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
          <div className="flex flex-col gap-3">
            <div className="rounded-xl border border-line bg-surface p-3">
              <p className="text-[10px] font-medium text-ink-muted">Cash in the bank</p>
              <div className="mt-1.5 flex items-end gap-2">
                <span className="h-6 w-28 rounded bg-lavender-100" aria-hidden />
                <span className="rounded-full bg-lavender-100 px-1.5 py-0.5 text-[9px] text-plum-800">
                  latest reported · mixed dates
                </span>
              </div>
              <div className="mt-3 grid grid-cols-3 gap-2">
                {["Money in", "Money out", "Net cash"].map((label) => (
                  <div key={label}>
                    <p className="text-[9px] text-ink-muted">{label}</p>
                    <span className="mt-1 block h-3.5 w-16 rounded bg-lavender-100" aria-hidden />
                  </div>
                ))}
              </div>
            </div>
            <div className="rounded-xl border border-line bg-surface p-3">
              <p className="text-[10px] font-medium text-ink-muted">Next 30 days</p>
              <svg viewBox="0 0 240 64" className="mt-1 h-16 w-full" aria-hidden>
                <path d="M0 40 C40 38 60 30 90 34 S140 50 170 36 S220 14 240 20" fill="none" stroke="#7c8698" strokeWidth="1.75" strokeDasharray="4 3" />
                <path d="M0 40 C40 40 60 34 90 42 S140 58 170 44 S220 22 240 26" fill="none" stroke="#4422ff" strokeWidth="2.25" />
              </svg>
            </div>
            <div className="rounded-xl border border-line bg-surface p-3">
              <p className="text-[10px] font-medium text-ink-muted">Worth a look</p>
              <ul className="mt-2 space-y-2">
                {["Supplier payments changed", "A deposit needs your input", "Your brief is ready"].map((t) => (
                  <li key={t} className="flex items-center gap-2 text-[11px] text-ink">
                    <span className="size-4 rounded-md bg-lavender-100" aria-hidden />
                    {t}
                    <span className="ml-auto text-plum-700">→</span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
          <div className="flex flex-col rounded-xl border border-line bg-surface p-3">
            <div className="flex items-center gap-1.5 text-[11px] font-semibold text-ink">
              <Icon name="spark" className="size-3.5 text-plum-600" /> Your AI CFO
            </div>
            <div className="mt-2 rounded-lg bg-lavender-100 px-2.5 py-2 text-[11px] text-plum-800">
              What if receipts arrive three days late?
            </div>
            <div className="mt-2 rounded-lg bg-surface-soft px-2.5 py-2 text-[11px] text-ink-soft">
              I can compare that with your projection. Your other assumptions stay the same.
              <div className="mt-2 flex gap-1">{bar("w-16")}{bar("w-12")}</div>
            </div>
            <div className="mt-auto flex items-center gap-1.5 pt-3">
              <span className="flex-1 rounded-lg border border-line px-2 py-1.5 text-[10px] text-ink-faint">
                Ask about your business…
              </span>
              <span className="inline-flex size-6 items-center justify-center rounded-lg bg-plum-600 text-white">
                <Icon name="send" className="size-3" />
              </span>
            </div>
          </div>
        </div>
      </div>
      </div>
      <p className="mt-3 text-center text-xs text-ink-faint">
        The overview, as it looks once a demo is open. Figures appear from the data, not from us.
      </p>
    </div>
  );
}
