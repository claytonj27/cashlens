"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { type FormEvent, Suspense, useState } from "react";

import { destinationAfterLogin } from "@/lib/discovery-api";
import { getSupabase } from "@/lib/supabase";
import { useSession } from "@/lib/use-session";
import { buttonClass, Card, Field, inputClass, Notice } from "@/components/ui";

/**
 * Set a password on a session that arrived by email link: a recovery for an existing account,
 * or an invite for a new one (`?welcome=1`, which only changes the wording). Afterwards the
 * person goes where a fresh sign-in would go: a stakeholder with core work left lands on their
 * Discovery, everyone else on the workspace.
 */
export default function ResetPasswordPage() {
  return (
    <Suspense fallback={null}>
      <ResetPasswordForm />
    </Suspense>
  );
}

function ResetPasswordForm() {
  const router = useRouter();
  const params = useSearchParams();
  const welcome = params.get("welcome") === "1";
  const { loading, session } = useSession();
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const supabase = getSupabase();
    if (!supabase) return;
    setBusy(true);
    setError(null);
    const { data, error: updateError } = await supabase.auth.updateUser({ password });
    if (updateError) {
      setBusy(false);
      setError(updateError.message);
      return;
    }
    const token = session?.access_token ?? null;
    const destination = await destinationAfterLogin(token);
    void data;
    router.replace(destination);
  }

  return (
    <main className="mx-auto flex w-full max-w-md flex-1 flex-col gap-6 px-4 py-10 sm:px-6">
      <h1 className="t-page">{welcome ? "Choose your password" : "Choose a new password"}</h1>
      <Card>
        {loading ? (
          <p className="text-ink-muted">{welcome ? "Setting up your account…" : "Checking your reset link…"}</p>
        ) : !session ? (
          <div className="flex flex-col gap-3">
            <Notice tone="error">{welcome ? "This invitation link has expired or was already used." : "This page needs a valid reset link."}</Notice>
            <Link href="/forgot-password" className="text-sm text-plum-600 hover:underline">
              {welcome ? "Ask for a new invitation, or reset your password by email" : "Request a new one"}
            </Link>
          </div>
        ) : (
          <form onSubmit={submit} className="flex flex-col gap-4">
            {welcome ? (
              <p className="text-sm text-ink-muted">
                Your account is ready. Choose a password to use from now on; after this you will be taken straight to your questions.
              </p>
            ) : null}
            <Field label={welcome ? "Password" : "New password"}>
              <input type="password" required minLength={8} autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} className={inputClass} />
            </Field>
            {error ? <Notice tone="error">{error}</Notice> : null}
            <button type="submit" disabled={busy} className={buttonClass}>
              {busy ? "Saving…" : welcome ? "Save password and continue" : "Save password"}
            </button>
          </form>
        )}
      </Card>
    </main>
  );
}
