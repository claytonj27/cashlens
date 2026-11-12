"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { accountsAvailable, demoAvailable } from "@/lib/features";
import { getSupabase } from "@/lib/supabase";
import { useSession } from "@/lib/use-session";
import { fetchAdminSession } from "@/lib/admin-api";
import { fetchDiscoveryAdminSession } from "@/lib/discovery-admin-api";
import { fetchDiscoveryLink } from "@/lib/discovery-api";
import { readWorkspaceSelection, withWorkspace } from "@/lib/workspace-selection";

/**
 * A compact, sticky header: the brand, a discreet honesty line, and account controls.
 *
 * Business selection lives one row down, in the workspace toolbar, because only a workspace
 * page knows which businesses this caller may switch between. The header itself is the same
 * on every page.
 */
export function SiteHeader() {
  const { loading, session } = useSession();
  const router = useRouter();
  const token = session?.access_token ?? null;
  // Whether to draw the operations link. The server answers this, and answers it again on every
  // request the dashboard makes: this only decides whether a link is worth showing.
  //
  // The answer is stored with the token it was given for, and read back only when that token is
  // still the current one, so a sign-out or a switch hides the link on the same render rather
  // than needing an effect to clear it.
  const [answer, setAnswer] = useState<{ token: string; value: boolean } | null>(null);
  const isAdmin = Boolean(token && answer?.token === token && answer.value);
  // The Discovery link, decided the same way: the server says whether this account has a
  // portal to open, and the answer is tied to the token it was given for. The word is neutral
  // on purpose; the portal names its customer only once it is open.
  const [portal, setPortal] = useState<{ token: string; value: boolean } | null>(null);
  const hasDiscovery = Boolean(token && portal?.token === token && portal.value);
  // The Discovery admin link, decided by the server's own session question. The link is a
  // convenience only: every analysis route checks the admin role again on each request.
  const [discoveryAdmin, setDiscoveryAdmin] = useState<{ token: string; value: boolean } | null>(null);
  const isDiscoveryAdmin = Boolean(token && discoveryAdmin?.token === token && discoveryAdmin.value);

  useEffect(() => {
    if (!token) return;
    let live = true;
    fetchAdminSession(token)
      .then((result) => {
        if (live) setAnswer({ token, value: result.is_admin });
      })
      .catch(() => {
        if (live) setAnswer({ token, value: false });
      });
    fetchDiscoveryLink(token)
      .then((value) => {
        if (live) setPortal({ token, value });
      })
      .catch(() => {
        if (live) setPortal({ token, value: false });
      });
    fetchDiscoveryAdminSession(token)
      .then((result) => {
        if (live) setDiscoveryAdmin({ token, value: result.is_discovery_admin });
      })
      .catch(() => {
        if (live) setDiscoveryAdmin({ token, value: false });
      });
    return () => {
      live = false;
    };
  }, [token]);

  async function signOut() {
    await getSupabase()?.auth.signOut();
    router.push("/");
  }

  return (
    <header className="sticky top-0 z-30 border-b border-line bg-surface/90 backdrop-blur">
      <div className="mx-auto flex h-14 w-full max-w-6xl items-center justify-between gap-4 px-4 sm:px-6 lg:px-8">
        <div className="flex min-w-0 items-center gap-3">
          <Link href="/" className="flex shrink-0 items-center gap-2 font-bold tracking-tight text-ink">
            <span className="inline-flex size-7 items-center justify-center rounded-lg bg-plum-600 text-white">
              <svg viewBox="0 0 24 24" className="size-4" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                <path d="M5 16l4-5 4 3 6-8" />
              </svg>
            </span>
            CashLens
          </Link>
          <span className="hidden truncate text-xs text-ink-faint sm:inline">
            Independent concept · Synthetic data
          </span>
        </div>
        <nav className="flex shrink-0 items-center gap-0.5 whitespace-nowrap text-sm sm:gap-1" aria-label="Account">
          {demoAvailable ? (
            <Link href="/demo" className="rounded-lg px-2.5 py-1.5 text-ink-soft hover:bg-lavender-100 hover:text-ink">
              Demo
            </Link>
          ) : null}
          {!accountsAvailable || loading ? null : session ? (
            <>
              <Suspense fallback={<WorkspaceLink href="/workspace" />}>
                <WorkspaceNavLink />
              </Suspense>
              {hasDiscovery ? (
                <Link
                  href="/discovery"
                  className="rounded-lg px-2.5 py-1.5 text-ink-soft hover:bg-lavender-100 hover:text-ink"
                >
                  Discovery
                </Link>
              ) : null}
              {isDiscoveryAdmin ? (
                <Link
                  href="/admin/discovery"
                  className="rounded-lg px-2.5 py-1.5 text-ink-soft hover:bg-lavender-100 hover:text-ink"
                >
                  Discovery admin
                </Link>
              ) : null}
              {isAdmin ? (
                <Link
                  href="/admin/ai-usage"
                  className="rounded-lg px-2.5 py-1.5 text-ink-soft hover:bg-lavender-100 hover:text-ink"
                >
                  AI usage
                </Link>
              ) : null}
              <span className="hidden max-w-[12rem] truncate px-2 text-xs text-ink-faint md:inline">
                {session.user.email}
              </span>
              <button
                type="button"
                onClick={signOut}
                className="rounded-lg px-2.5 py-1.5 text-ink-soft hover:bg-lavender-100 hover:text-ink"
              >
                Log out
              </button>
            </>
          ) : (
            <>
              <Link href="/login" className="rounded-lg px-2.5 py-1.5 text-ink-soft hover:bg-lavender-100 hover:text-ink">
                Log in
              </Link>
              <Link
                href="/signup"
                className="ml-1 rounded-xl bg-plum-600 px-2.5 py-1.5 text-sm font-semibold text-white transition hover:bg-plum-500 sm:px-3"
              >
                Create account
              </Link>
            </>
          )}
        </nav>
      </div>
    </header>
  );
}

function WorkspaceLink({ href }: { href: string }) {
  return (
    <Link href={href} className="rounded-lg px-2.5 py-1.5 text-ink-soft hover:bg-lavender-100 hover:text-ink">
      My workspace
    </Link>
  );
}

/** Keeps a selected workspace in the link while the reader is inside the workspace pages. */
function WorkspaceNavLink() {
  const pathname = usePathname();
  const search = useSearchParams();
  const selected = pathname.startsWith("/workspace") ? readWorkspaceSelection(search).id : null;
  return <WorkspaceLink href={withWorkspace("/workspace", selected)} />;
}
