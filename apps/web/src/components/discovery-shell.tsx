"use client";

import Link from "next/link";
import { useEffect, useState, type ReactNode } from "react";

import { ApiError } from "@/lib/api";
import { fetchDiscoveryMe, type DiscoveryMe } from "@/lib/discovery-api";
import { featuresConfigured } from "@/lib/features";
import { useSession } from "@/lib/use-session";
import { ScaffoldNotice } from "@/components/scaffold-notice";
import { buttonClass, Empty, Notice, secondaryButtonClass } from "@/components/ui";

export type Loaded = { me: DiscoveryMe; token: string; reload: () => void };

/**
 * The frame every Discovery page sits in: the session, the caller's own portal view, and the
 * three ways there is nothing to show. It is a soft gate in one direction only: a person with
 * nothing to answer is pointed at CashLens, and a person with something to answer is never kept
 * from CashLens.
 */
export function DiscoveryShell({ children }: { children: (loaded: Loaded) => ReactNode }) {
  const { loading, session } = useSession();
  const token = session?.access_token ?? null;
  const [me, setMe] = useState<DiscoveryMe | null>(null);
  const [absent, setAbsent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    if (!featuresConfigured || !token) return;
    let active = true;
    fetchDiscoveryMe(token)
      .then((loaded) => {
        if (active) setMe(loaded);
      })
      .catch((caught: unknown) => {
        if (!active) return;
        // The portal answers 404 wherever a deployment has not switched it on.
        if (caught instanceof ApiError && caught.status === 404) {
          setAbsent(true);
          return;
        }
        setError(caught instanceof ApiError ? caught.describe() : "Could not load Discovery.");
      });
    return () => {
      active = false;
    };
  }, [token, reloadKey]);

  const shell = (inner: ReactNode) => (
    <main className="mx-auto flex w-full max-w-3xl flex-1 flex-col gap-6 px-4 py-8 sm:px-6 lg:px-8">
      {inner}
    </main>
  );
  const openCashLens = (
    <Link href="/workspace" className={secondaryButtonClass}>
      Open CashLens
    </Link>
  );

  if (!featuresConfigured) return shell(<ScaffoldNotice />);
  if (loading) return shell(<p className="text-ink-muted">Checking your session…</p>);
  if (!session || !token)
    return shell(
      <>
        <Notice tone="info">Log in to open Discovery.</Notice>
        <div>
          <Link href="/login" className={buttonClass}>
            Log in
          </Link>
        </div>
      </>,
    );
  if (absent)
    return shell(
      <>
        <Empty title="Discovery is not available here" icon="review">
          This deployment does not have the portal switched on. Your CashLens workspace is unaffected.
        </Empty>
        <div>{openCashLens}</div>
      </>,
    );
  if (error)
    return shell(
      <>
        <Notice tone="error">{error}</Notice>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => {
              setError(null);
              setReloadKey((v) => v + 1);
            }}
            className={buttonClass}
          >
            Try again
          </button>
          {openCashLens}
        </div>
      </>,
    );
  if (!me) return shell(<p className="text-ink-muted">Loading…</p>);
  if (!me.assignment)
    return shell(
      <>
        <Empty title="Nothing to answer yet" icon="review">
          This account has no Discovery questions assigned. Everything in CashLens works as usual.
        </Empty>
        <div>{openCashLens}</div>
      </>,
    );
  return shell(children({ me, token, reload: () => setReloadKey((v) => v + 1) }));
}
