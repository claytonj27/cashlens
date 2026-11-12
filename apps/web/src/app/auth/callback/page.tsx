"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { landingAfterEmailLink } from "@/lib/discovery-rules";
import { getSupabase } from "@/lib/supabase";
import { Card, Notice } from "@/components/ui";

/**
 * Landing point for confirmation, recovery and invite links (all use this exact URL,
 * which is what the Supabase redirect allowlist contains). With the implicit
 * flow the Supabase client reads the session from the URL fragment on load;
 * recovery links carry type=recovery in that fragment and also raise the
 * PASSWORD_RECOVERY event. The fragment never reaches any server, and the URL
 * is replaced before navigating on so tokens do not linger in history.
 */
export default function AuthCallbackPage() {
  const router = useRouter();
  const [problem, setProblem] = useState<string | null>(null);

  useEffect(() => {
    // State updates here happen from callbacks and timers, never synchronously in the effect body.
    const fail = (message: string) => window.setTimeout(() => setProblem(message), 0);
    const supabase = getSupabase();
    if (!supabase) {
      const pending = fail("Sign-in is not configured for this deployment.");
      return () => window.clearTimeout(pending);
    }
    // Read the fragment before the auth client consumes it (it clears the hash only after a
    // network round trip, so this runs first on every load).
    const hash = new URLSearchParams(window.location.hash.replace(/^#/, ""));
    const query = new URLSearchParams(window.location.search);
    const errorCode = hash.get("error_code") ?? query.get("error_code");
    const described = hash.get("error_description") ?? query.get("error_description");
    if (errorCode || described) {
      // Error fragments never carry tokens, but they should not linger in history either.
      window.history.replaceState(null, "", window.location.pathname);
      const message =
        errorCode === "otp_expired"
          ? "This link has expired or was already used. Links work once and only for a limited time."
          : (described ?? "This link is invalid.").replace(/\+/g, " ");
      const pending = fail(message);
      return () => window.clearTimeout(pending);
    }
    // A recovery link and an invite both arrive without a usable password: both go on to the
    // set-password page, an invite with a welcome so the page can say what it is.
    const linkType = hash.get("type") ?? query.get("type");
    const landing = landingAfterEmailLink(linkType);
    const target = landing === "/auth/reset-password" && linkType === "invite" ? "/auth/reset-password?welcome=1" : landing;
    const hasGrant = hash.has("access_token") || query.has("code") || hash.has("error");
    let done = false;
    const finish = (to: string) => {
      if (done) return;
      done = true;
      window.history.replaceState(null, "", window.location.pathname);
      router.replace(to);
    };
    const { data: subscription } = supabase.auth.onAuthStateChange((event, session) => {
      if (event === "PASSWORD_RECOVERY") finish("/auth/reset-password");
      else if (session) finish(target);
    });
    supabase.auth.getSession().then(({ data }) => {
      if (data.session) finish(target);
    });
    const timer = window.setTimeout(
      () => {
        if (!done) setProblem(hasGrant ? "This link could not be completed. It may have expired or already been used." : "This page only completes email links. Open the link from your email, or log in.");
      },
      hasGrant ? 12000 : 2000,
    );
    return () => {
      clearTimeout(timer);
      subscription.subscription.unsubscribe();
    };
  }, [router]);

  return (
    <main className="mx-auto flex w-full max-w-md flex-1 flex-col gap-6 px-6 py-12">
      <Card>
        {problem ? (
          <div className="flex flex-col gap-3">
            <Notice tone="error">{problem}</Notice>
            <p className="text-sm text-ink-muted">
              <Link href="/login" className="text-plum-600 hover:underline">
                Log in
              </Link>{" "}
              or{" "}
              <Link href="/forgot-password" className="text-plum-600 hover:underline">
                request a new link
              </Link>
              .
            </p>
          </div>
        ) : (
          <p className="text-ink-muted">Finishing sign-in…</p>
        )}
      </Card>
    </main>
  );
}
