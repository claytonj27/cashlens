"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, useState } from "react";

import { getSupabase, siteOrigin, supabaseConfigured } from "@/lib/supabase";
import { destinationAfterLogin } from "@/lib/discovery-api";
import { buttonClass, Card, Field, inputClass, Notice, secondaryButtonClass } from "@/components/ui";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [unconfirmed, setUnconfirmed] = useState(false);
  const [resent, setResent] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const supabase = getSupabase();
    if (!supabase) return;
    setBusy(true);
    setError(null);
    setUnconfirmed(false);
    const { data, error: signInError } = await supabase.auth.signInWithPassword({ email, password });
    if (signInError) {
      setBusy(false);
      if (signInError.code === "email_not_confirmed") {
        setUnconfirmed(true);
        setError("This email address has not been confirmed yet.");
      } else {
        setError(signInError.code === "invalid_credentials" ? "Email or password is incorrect." : signInError.message);
      }
      return;
    }
    // A stakeholder with Discovery still to do lands on the portal; everyone else, and anyone
    // the server cannot answer for, lands on the workspace as before.
    const destination = await destinationAfterLogin(data.session?.access_token ?? null);
    setBusy(false);
    router.push(destination);
  }

  async function resend() {
    const supabase = getSupabase();
    if (!supabase) return;
    const { error: resendError } = await supabase.auth.resend({
      type: "signup",
      email,
      options: { emailRedirectTo: `${siteOrigin()}/auth/callback` },
    });
    setResent(!resendError);
    if (resendError) setError(resendError.message);
  }

  if (!supabaseConfigured) {
    return (
      <main className="mx-auto w-full max-w-md flex-1 px-4 py-10 sm:px-6">
        <Notice tone="error">Sign-in is not configured for this deployment.</Notice>
      </main>
    );
  }

  return (
    <main className="mx-auto flex w-full max-w-md flex-1 flex-col gap-6 px-4 py-10 sm:px-6">
      <div><p className="t-eyebrow mb-1.5">Your private workspace</p><h1 className="t-page">Log in</h1></div>
      <Card>
        <form onSubmit={submit} className="flex flex-col gap-4">
          <Field label="Email">
            <input type="email" required autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} className={inputClass} />
          </Field>
          <Field label="Password">
            <input type="password" required autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} className={inputClass} />
          </Field>
          {error ? <Notice tone="error">{error}</Notice> : null}
          {unconfirmed ? (
            <button type="button" onClick={resend} className={secondaryButtonClass} disabled={resent}>
              {resent ? "Confirmation email sent" : "Resend confirmation email"}
            </button>
          ) : null}
          <button type="submit" disabled={busy} className={buttonClass}>
            {busy ? "Logging in…" : "Log in"}
          </button>
          <p className="text-sm text-ink-muted">
            <Link href="/forgot-password" className="text-plum-600 hover:underline">
              Forgot your password?
            </Link>{" "}
            · New here?{" "}
            <Link href="/signup" className="text-plum-600 hover:underline">
              Create an account
            </Link>
            .
          </p>
        </form>
      </Card>
    </main>
  );
}
