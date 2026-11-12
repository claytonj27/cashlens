import type { ReactNode } from "react";
import Link from "next/link";

/**
 * The shared vocabulary every page is built from.
 *
 * One card, one title style, one figure style, one primary button. Structure is drawn in
 * neutral grey; purple is spent only on things that ask to be pressed or that carry meaning
 * (a chart line, a selected tab). Radii are 12px on controls and 16–20px on cards.
 */

// ------------------------------------------------------------------ icons --
// A small, consistent line-icon set. 1.5px strokes on a 24 grid, currentColor, so they take
// the text colour of wherever they sit. Nothing here is decorative for its own sake.
const ICONS = {
  spark:
    "M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8L12 3zM19 15l.9 2.1L22 18l-2.1.9L19 21l-.9-2.1L16 18l2.1-.9L19 15z",
  chart: "M4 19h16M7 15V9M12 15V5M17 15v-4",
  doc: "M7 3h7l5 5v13H7V3zM14 3v5h5M9 13h7M9 17h7",
  arrow: "M5 12h14M13 6l6 6-6 6",
  send: "M4 12l16-8-6 16-2-6-8-2z",
  check: "M5 12l4 4L19 6",
  alert: "M12 8v5M12 16.5h.01M10.3 3.9L2.6 17.3A2 2 0 004.3 20h15.4a2 2 0 001.7-2.7L13.7 3.9a2 2 0 00-3.4 0z",
  search: "M11 4a7 7 0 100 14 7 7 0 000-14zM20 20l-4-4",
  bank: "M3 10l9-6 9 6M5 10v9M9 10v9M15 10v9M19 10v9M3 19h18",
  wallet: "M3 7h15a3 3 0 013 3v7a3 3 0 01-3 3H6a3 3 0 01-3-3V7zM3 7a2 2 0 012-2h11v2M16 13h3",
  clock: "M12 3a9 9 0 100 18 9 9 0 000-18zM12 7v5l3 2",
  review: "M9 11l2 2 4-4M5 5h14v14H5z",
  chevron: "M9 6l6 6-6 6",
  close: "M6 6l12 12M18 6L6 18",
  menu: "M4 7h16M4 12h16M4 17h16",
  layers: "M12 3l9 5-9 5-9-5 9-5zM3 13.5l9 5 9-5M3 18l9 5 9-5",
  repeat: "M17 2l4 4-4 4M21 6H8a5 5 0 00-5 5M7 22l-4-4 4-4M3 18h13a5 5 0 005-5",
  swap: "M8 4L4 8l4 4M4 8h13M16 20l4-4-4-4M20 16H7",
} as const;

export type IconName = keyof typeof ICONS;

export function Icon({
  name,
  className = "size-4",
  title,
}: {
  name: IconName;
  className?: string;
  title?: string;
}) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden={title ? undefined : true}
      role={title ? "img" : undefined}
    >
      {title ? <title>{title}</title> : null}
      <path d={ICONS[name]} />
    </svg>
  );
}

// ------------------------------------------------------------------ cards --
export function Card({
  children,
  className = "",
  as: Tag = "section",
  padding = "normal",
}: {
  children: ReactNode;
  className?: string;
  as?: "section" | "div" | "article";
  padding?: "normal" | "tight" | "none";
}) {
  const pad = padding === "none" ? "" : padding === "tight" ? "p-4" : "p-5 sm:p-6";
  return (
    <Tag className={`rounded-2xl border border-line bg-surface shadow-card ${pad} ${className}`}>
      {children}
    </Tag>
  );
}

/** A card heading: a title, an optional line under it, an optional action on the right. */
export function CardTitle({
  title,
  hint,
  action,
  icon,
}: {
  title: string;
  hint?: ReactNode;
  action?: ReactNode;
  icon?: IconName;
}) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
      <div className="flex min-w-0 items-start gap-2.5">
        {icon ? (
          <span className="mt-0.5 inline-flex size-7 shrink-0 items-center justify-center rounded-lg bg-brand-tint text-plum-600">
            <Icon name={icon} className="size-4" />
          </span>
        ) : null}
        <div className="min-w-0">
          <h3 className="t-section">{title}</h3>
          {hint ? <p className="mt-0.5 text-sm text-ink-muted">{hint}</p> : null}
        </div>
      </div>
      {action ? <div className="shrink-0">{action}</div> : null}
    </div>
  );
}

/** A page's opening block: eyebrow, heading, one line, and actions on the right. */
export function PageHeader({
  eyebrow,
  title,
  lead,
  actions,
  children,
}: {
  eyebrow?: string;
  title: ReactNode;
  lead?: ReactNode;
  actions?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
      <div className="min-w-0">
        {eyebrow ? <p className="t-eyebrow mb-1.5">{eyebrow}</p> : null}
        <h1 className="t-page">{title}</h1>
        {lead ? <p className="mt-1.5 max-w-prose text-sm text-ink-muted sm:text-base">{lead}</p> : null}
        {children}
      </div>
      {actions ? <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div> : null}
    </div>
  );
}

// ---------------------------------------------------------------- figures --
/** One figure with its label and, where it matters, where and when it came from. */
export function Stat({
  label,
  value,
  note,
  tone = "default",
  size = "md",
}: {
  label: string;
  value: ReactNode;
  note?: ReactNode;
  tone?: "default" | "up" | "down";
  size?: "sm" | "md" | "lg";
}) {
  const colour = tone === "up" ? "text-up" : tone === "down" ? "text-down" : "text-ink";
  const scale =
    size === "lg" ? "text-3xl sm:text-[2rem]" : size === "sm" ? "text-lg" : "text-xl sm:text-2xl";
  return (
    <div className="min-w-0">
      <dt className="text-xs font-medium text-ink-muted">{label}</dt>
      <dd className={`t-figure mt-1 ${scale} ${colour}`}>{value}</dd>
      {note ? <p className="mt-1 text-xs text-ink-faint">{note}</p> : null}
    </div>
  );
}

/** A small rounded label. `accent` is the only purple one; the rest stay quiet. */
export function Chip({
  children,
  tone = "muted",
  icon,
}: {
  children: ReactNode;
  tone?: "muted" | "accent" | "warn" | "up" | "down";
  icon?: IconName;
}) {
  const tones = {
    muted: "border-line bg-surface text-ink-muted",
    accent: "border-transparent bg-lavender-100 text-plum-800",
    warn: "border-transparent bg-warn-soft text-warn",
    up: "border-transparent bg-up-soft text-up",
    down: "border-transparent bg-down-soft text-down",
  };
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium leading-4 ${tones[tone]}`}
    >
      {icon ? <Icon name={icon} className="size-3" /> : null}
      {children}
    </span>
  );
}

// ---------------------------------------------------------------- controls --
const focus =
  "focus:outline-none focus-visible:ring-2 focus-visible:ring-plum-600 focus-visible:ring-offset-2";
export const buttonClass = `inline-flex items-center justify-center gap-1.5 rounded-xl border border-plum-600 bg-plum-600 px-5 py-2 text-sm font-semibold text-white transition hover:border-plum-500 hover:bg-plum-500 active:border-plum-700 active:bg-plum-700 disabled:cursor-not-allowed disabled:opacity-50 ${focus}`;
/** The reference's own CTA proportions — 28px of side padding — for landing actions only. */
export const buttonLargeClass = `inline-flex items-center justify-center gap-2 rounded-xl border border-plum-600 bg-plum-600 px-7 py-2.5 text-[15px] font-semibold text-white transition hover:border-plum-500 hover:bg-plum-500 active:border-plum-700 active:bg-plum-700 disabled:cursor-not-allowed disabled:opacity-50 ${focus}`;
export const secondaryButtonClass = `inline-flex items-center justify-center gap-1.5 rounded-xl border border-line-strong bg-surface px-5 py-2 text-sm font-semibold text-ink transition hover:border-plum-400 hover:text-plum-700 disabled:cursor-not-allowed disabled:opacity-50 ${focus}`;
export const secondaryLargeClass = `inline-flex items-center justify-center gap-2 rounded-xl border border-line-strong bg-surface px-7 py-2.5 text-[15px] font-semibold text-ink transition hover:border-plum-400 hover:text-plum-700 ${focus}`;
export const quietButtonClass = `inline-flex items-center justify-center gap-1 rounded-lg px-2 py-1 text-xs font-semibold text-plum-700 transition hover:bg-lavender-100 ${focus}`;
export const iconButtonClass = `inline-flex size-9 shrink-0 items-center justify-center rounded-xl bg-plum-600 text-white transition hover:bg-plum-500 active:bg-plum-700 disabled:cursor-not-allowed disabled:opacity-50 ${focus}`;
export const inputClass = `w-full rounded-xl border border-line-strong bg-surface px-3 py-2 text-sm text-ink placeholder:text-ink-faint transition focus:border-plum-500 focus:outline-none focus-visible:ring-2 focus-visible:ring-plum-600 focus-visible:ring-offset-1 disabled:opacity-60`;
export const textareaClass = `${inputClass} min-h-[2.75rem] resize-none leading-relaxed`;
export const selectClass = `${inputClass} appearance-none bg-[url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='%236b6480' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><path d='M6 9l6 6 6-6'/></svg>")] bg-[length:16px] bg-[right_0.6rem_center] bg-no-repeat pr-9`;
export const linkClass = "text-plum-600 underline decoration-lavender-300 underline-offset-2 hover:decoration-plum-600";

export function ButtonLink({
  href,
  children,
  secondary = false,
  className = "",
}: {
  href: string;
  children: ReactNode;
  secondary?: boolean;
  className?: string;
}) {
  return (
    <Link href={href} className={`${secondary ? secondaryButtonClass : buttonClass} ${className}`}>
      {children}
    </Link>
  );
}

export function Notice({
  tone,
  children,
  compact = false,
}: {
  tone: "info" | "error" | "success";
  children: ReactNode;
  compact?: boolean;
}) {
  const tones = {
    info: "border-line bg-surface-soft text-ink-soft",
    error: "border-transparent bg-warn-soft text-warn",
    success: "border-transparent bg-up-soft text-up",
  };
  return (
    <p
      role={tone === "error" ? "alert" : "status"}
      className={`rounded-xl border ${compact ? "px-2.5 py-1.5 text-xs" : "px-3 py-2 text-sm"} ${tones[tone]}`}
    >
      {children}
    </p>
  );
}

export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <label className="flex flex-col gap-1 text-sm text-ink">
      <span className="text-xs font-medium text-ink-soft">{label}</span>
      {children}
      {hint ? <span className="text-xs text-ink-muted">{hint}</span> : null}
    </label>
  );
}

/** A labelled group of related inputs, so a form reads as a few decisions rather than a grid. */
export function FieldGroup({
  legend,
  hint,
  children,
}: {
  legend: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <fieldset className="rounded-xl border border-line bg-surface-soft p-4">
      <legend className="px-1 text-sm font-semibold text-ink">{legend}</legend>
      {hint ? <p className="mb-3 text-xs text-ink-muted">{hint}</p> : null}
      {children}
    </fieldset>
  );
}

/** Nothing here yet, said in a way that tells the reader what would put something here. */
export function Empty({
  title,
  children,
  icon = "doc",
}: {
  title: string;
  children?: ReactNode;
  icon?: IconName;
}) {
  return (
    <div className="rounded-xl border border-dashed border-line-strong bg-surface-soft px-4 py-8 text-center">
      <span className="mx-auto mb-2 inline-flex size-9 items-center justify-center rounded-full bg-brand-tint text-plum-600">
        <Icon name={icon} className="size-4" />
      </span>
      <p className="text-sm font-semibold text-ink">{title}</p>
      {children ? <div className="mx-auto mt-1 max-w-prose text-sm text-ink-muted">{children}</div> : null}
    </div>
  );
}

/** A disclosure for detail that should be available without being in the way. */
export function Disclosure({
  label,
  openLabel,
  children,
  className = "",
}: {
  label: string;
  openLabel?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <details className={`group ${className}`}>
      <summary className="inline-flex cursor-pointer list-none items-center gap-1 text-xs font-medium text-plum-700 hover:text-plum-600 [&::-webkit-details-marker]:hidden">
        <Icon name="chevron" className="size-3 transition group-open:rotate-90" />
        <span className="group-open:hidden">{label}</span>
        <span className="hidden group-open:inline">{openLabel ?? label}</span>
      </summary>
      <div className="mt-2 rounded-xl border border-line bg-surface-soft p-3 text-xs leading-relaxed text-ink-muted">
        {children}
      </div>
    </details>
  );
}

/** A thin bar of how far something has got, with the numbers left to the caller. */
export function ProgressBar({
  value,
  max,
  label,
  className = "",
}: {
  value: number;
  max: number;
  label: string;
  className?: string;
}) {
  const percent = max > 0 ? Math.min(100, Math.round((value / max) * 100)) : 0;
  return (
    <div
      className={`h-1.5 w-full overflow-hidden rounded-full bg-surface-soft ${className}`}
      role="progressbar"
      aria-valuenow={value}
      aria-valuemin={0}
      aria-valuemax={max}
      aria-label={label}
    >
      <div className="h-full rounded-full bg-plum-600 transition-[width] duration-500" style={{ width: `${percent}%` }} />
    </div>
  );
}

/** A row in a list of things worth acting on: icon, title, one line, and where to go. */
export function ActionRow({
  icon,
  title,
  body,
  href,
  cta,
  onClick,
}: {
  icon: IconName;
  title: string;
  body?: ReactNode;
  href?: string;
  cta: string;
  onClick?: () => void;
}) {
  const action = href ? (
    <Link href={href} className={`${quietButtonClass} whitespace-nowrap`}>
      {cta} <Icon name="arrow" className="size-3.5" />
    </Link>
  ) : (
    <button type="button" onClick={onClick} className={`${quietButtonClass} whitespace-nowrap`}>
      {cta} <Icon name="arrow" className="size-3.5" />
    </button>
  );
  return (
    <li className="flex items-center gap-3 py-3">
      <span className="inline-flex size-9 shrink-0 items-center justify-center rounded-xl bg-brand-tint text-plum-600">
        <Icon name={icon} className="size-4" />
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-sm font-semibold text-ink">{title}</p>
        {body ? <p className="text-xs text-ink-muted">{body}</p> : null}
      </div>
      {action}
    </li>
  );
}
