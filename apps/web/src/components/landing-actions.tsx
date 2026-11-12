"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { api, ApiError } from "@/lib/api";
import { track } from "@/lib/analytics";
import { Icon, buttonClass, buttonLargeClass, Notice, type IconName } from "@/components/ui";

/** Starts an anonymous demo session and opens the demo list. */
export function ExploreDemoButton() {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function explore() {
    setBusy(true);
    setError(null);
    try {
      await api("/api/demo/session", { method: "POST" });
      router.push("/demo");
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? `The demo is unavailable right now (${caught.message}).`
          : "The demo is unavailable right now.",
      );
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-2">
      <button type="button" onClick={explore} disabled={busy} className={buttonLargeClass}>
        {busy ? "Starting demo…" : "Explore live demo"}
        <Icon name="arrow" className="size-4" />
      </button>
      {error ? <Notice tone="error" compact>{error}</Notice> : null}
    </div>
  );
}

/** One demo business, opened straight into its overview. */
export function DemoEntry({
  slug,
  name,
  industry,
  blurb,
  icon,
}: {
  slug: string;
  name: string;
  industry: string;
  blurb: string;
  icon: IconName;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function open() {
    setBusy(true);
    setError(null);
    try {
      await api("/api/demo/session", { method: "POST" });
      await api(`/api/demo/workspaces/${slug}`, { method: "POST" });
      track("demo_opened", { section: "demo-list", demo_business: slug });
      router.push(`/demo/${slug}`);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Could not open this business.");
      setBusy(false);
    }
  }

  return (
    <article className="group flex flex-col rounded-2xl border border-line bg-surface p-5 shadow-card transition hover:-translate-y-0.5 hover:shadow-raised">
      <div className="flex items-center gap-3">
        <span className="inline-flex size-10 items-center justify-center rounded-xl bg-lavender-100 text-plum-700">
          <Icon name={icon} className="size-5" />
        </span>
        <div className="min-w-0">
          <h3 className="text-base font-semibold text-ink">{name}</h3>
          <p className="text-xs text-ink-muted">{industry}</p>
        </div>
      </div>
      <p className="mt-3 flex-1 text-sm leading-relaxed text-ink-soft">{blurb}</p>
      <div className="mt-4">
        <button type="button" onClick={open} disabled={busy} className={`${buttonClass} w-full sm:w-auto`}>
          {busy ? "Opening…" : "Open demo"}
          <Icon name="arrow" className="size-4 transition group-hover:translate-x-0.5" />
        </button>
      </div>
      {error ? (
        <div className="mt-2">
          <Notice tone="error" compact>{error}</Notice>
        </div>
      ) : null}
    </article>
  );
}
