"use client";

import { useEffect, useState } from "react";

import { api, ApiError } from "@/lib/api";
import { demoAvailable } from "@/lib/features";
import { DemoEntry } from "@/components/landing-actions";
import { ScaffoldNotice } from "@/components/scaffold-notice";
import { Notice, PageHeader } from "@/components/ui";
import { DEMO_CATALOGUE } from "@/lib/demo-catalogue";

type Business = { slug: string; name: string; industry: string };

export default function DemoPage() {
  const [businesses, setBusinesses] = useState<Business[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!demoAvailable) return;
    let active = true;
    api<{ businesses: Business[] }>("/api/demo/session", { method: "POST" })
      .then((data) => {
        if (active) setBusinesses(data.businesses);
      })
      .catch((caught: unknown) => {
        if (active)
          setError(caught instanceof ApiError ? caught.message : "The demo is unavailable right now.");
      });
    return () => {
      active = false;
    };
  }, []);

  if (!demoAvailable) {
    return (
      <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-12 sm:px-6 lg:px-8">
        <ScaffoldNotice />
      </main>
    );
  }

  return (
    <main className="mx-auto flex w-full max-w-6xl flex-1 flex-col gap-8 px-4 py-10 sm:px-6 lg:px-8">
      <PageHeader
        eyebrow="Three demo businesses"
        title="Open one and ask it something."
        lead="Six months of generated bank history each. Your exploration lives in an anonymous session in this browser and never touches anyone's private data."
      />
      {error ? <Notice tone="error">{error}</Notice> : null}
      {businesses === null && !error ? <p className="text-ink-muted">Loading demo businesses…</p> : null}
      {/* Only the businesses this page has a description for. A development deployment's
          database can hold synthetic workspaces left behind by a check, and an offer with no
          description of what is in it is not an offer — it is a loose end on the page that
          says "three demo businesses". Filtering here rather than in the API keeps the
          server's answer honest about what exists. */}
      <div className="grid gap-4 md:grid-cols-3">
        {(businesses ?? [])
          .filter((b) => DEMO_CATALOGUE[b.slug])
          .map((b) => (
            <DemoEntry
              key={b.slug}
              slug={b.slug}
              name={b.name}
              industry={b.industry.replace(/^./, (c) => c.toUpperCase())}
              icon={DEMO_CATALOGUE[b.slug].icon}
              blurb={DEMO_CATALOGUE[b.slug].blurb}
            />
          ))}
      </div>
    </main>
  );
}
