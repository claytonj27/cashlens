"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";

import { Chip, Icon, selectClass, type IconName } from "@/components/ui";

export type Section = "overview" | "analysis" | "transactions" | "statements";

/** One business the reader may switch to, and where that business's current section lives. */
export type BusinessOption = {
  /** Stable key: a workspace id for a private business, a slug for a demo one. */
  key: string;
  name: string;
  href: string;
};

type Props = {
  section: Section;
  mode: "demo" | "private";
  businessName: string;
  /** Links for this business's own sections, with the workspace selection already carried. */
  sections: Record<Section, string>;
  options?: BusinessOption[];
  currentKey?: string;
  /** One short line beside the name — industry, counts, anything the reader can scan. */
  summary?: string;
  /** Actions that belong to the business as a whole, shown on the right of the heading. */
  actions?: React.ReactNode;
};

const LABELS: Record<Section, string> = {
  overview: "Overview",
  analysis: "Analysis",
  transactions: "Transactions",
  statements: "Statements",
};

const SECTION_ICON: Record<Section, IconName> = {
  overview: "chart",
  analysis: "layers",
  transactions: "search",
  statements: "doc",
};

/**
 * The workspace toolbar: which business, which kind of data, and the three sections.
 *
 * The business selector sits here rather than in the site header because only a workspace
 * page knows which businesses this caller may switch between. Switching keeps the reader in
 * the section they were looking at, every link carries the caller's own resolved selection,
 * and nothing here constructs a workspace id.
 */
export function WorkspaceNav({
  section,
  mode,
  businessName,
  sections,
  options,
  currentKey,
  summary,
  actions,
}: Props) {
  const router = useRouter();
  const order: Section[] = ["overview", "analysis", "transactions", "statements"];

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-3">
        <div className="flex min-w-0 items-center gap-3">
          <span className="inline-flex size-11 shrink-0 items-center justify-center rounded-2xl bg-lavender-100 text-lg font-bold text-plum-800">
            {businessName.trim().charAt(0).toUpperCase()}
          </span>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="t-page truncate">{businessName}</h1>
              {mode === "demo" ? (
                <Chip tone="accent">Demo · synthetic data</Chip>
              ) : (
                <Chip tone="muted" icon="bank">Your private business</Chip>
              )}
            </div>
            {summary ? <p className="mt-0.5 text-sm text-ink-muted">{summary}</p> : null}
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {options && options.length > 1 ? (
            <label className="flex items-center gap-2 text-sm text-ink-muted">
              <span className="sr-only sm:not-sr-only">Business</span>
              <select
                aria-label="Switch business"
                value={currentKey}
                onChange={(event) => {
                  const next = options.find((o) => o.key === event.target.value);
                  if (next) router.push(next.href);
                }}
                className={`${selectClass} w-auto min-w-[11rem] py-1.5 font-medium text-ink`}
              >
                {options.map((option) => (
                  <option key={option.key} value={option.key}>
                    {option.name}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          {actions}
        </div>
      </div>

      {/* Four sections do not fit across a phone. The strip scrolls sideways rather than
          wrapping to a second row or shrinking the labels to abbreviations. */}
      <nav aria-label="Workspace sections" className="thread-scroll overflow-x-auto border-b border-line">
        <ul className="-mb-px flex w-max min-w-full gap-1">
          {order.map((key) => {
            const active = key === section;
            return (
              <li key={key}>
                <Link
                  href={sections[key]}
                  aria-current={active ? "page" : undefined}
                  className={`inline-flex items-center gap-1.5 whitespace-nowrap border-b-2 px-2.5 py-2.5 text-sm font-medium transition focus:outline-none focus-visible:ring-2 focus-visible:ring-plum-600 sm:px-3 ${
                    active
                      ? "border-plum-600 text-plum-800"
                      : "border-transparent text-ink-muted hover:border-line-strong hover:text-ink"
                  }`}
                >
                  <Icon name={SECTION_ICON[key]} className="size-4" />
                  {LABELS[key]}
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>
    </div>
  );
}
