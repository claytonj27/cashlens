"use client";

import Link from "next/link";
import { type FormEvent, useState } from "react";

import { getSupabase, siteOrigin, supabaseConfigured } from "@/lib/supabase";
import { buttonClass, Card, Field, inputClass, Notice } from "@/components/ui";

function explain(code: string | undefined, message: string): string {
  if (code === "email_address_not_authorized")
    return "This deployment can send confirmation emails only to approved addresses. Sign-ups from other addresses need email delivery configured first.";
  if (code === "over_email_send_rate_limit") return "Too many confirmation emails were sent recently. Try again in a few minutes.";
  if (code === "weak_password") return "Choose a stronger password (at least 8 characters).";
  if (code === "user_already_exists") return "An account with this email already exists. Log in instead.";
  return message;
}

export default function SignupPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sent, setSent] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const supabase = getSupabase();
    if (!supabase) return;
    setBusy(true);
    setError(null);
    const { data, error: signUpError } = await supabase.auth.signUp({
      email,
      password,
      options: { emailRedirectTo: `${siteOrigin()}/auth/callback` },
    });
    setBusy(false);
    if (signUpError) {
      setError(explain(signUpError.code, signUpError.message));
      return;
    }
    if (data.user && data.user.identities && data.user.identities.length === 0) {
      setError("An account with this email already exists. Log in, or reset your password.");
      return;
    }
    setSent(true);
  }

  if (!supabaseConfigured) {
    return (
      <main className="mx-auto w-full max-w-md flex-1 px-4 py-10 sm:px-6">
        <Notice tone="error">Account creation is not configured for this deployment.</Notice>
      </main>
    );
  }

  return (
    <main className="mx-auto flex w-full max-w-md flex-1 flex-col gap-6 px-4 py-10 sm:px-6">
      <div><p className="t-eyebrow mb-1.5">Your private workspace</p><h1 className="t-page">Create your account</h1></div>
      <Card>
        {sent ? (
          <Notice tone="success">
            Check your email for a confirmation link. After confirming you can log in and set up your private workspace.
          </Notice>
        ) : (
          <form onSubmit={submit} className="flex flex-col gap-4">
            <Field label="Email">
              <input type="email" required autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} className={inputClass} />
            </Field>
            <Field label="Password">
              <input type="password" required minLength={8} autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} className={inputClass} />
            </Field>
            {error ? <Notice tone="error">{error}</Notice> : null}
            <button type="submit" disabled={busy} className={buttonClass}>
              {busy ? "Creating account…" : "Create account"}
            </button>
            <p className="text-sm text-ink-muted">
              We will email you a confirmation link. Already have an account?{" "}
              <Link href="/login" className="text-plum-600 hover:underline">
                Log in
              </Link>
              .
            </p>
          </form>
        )}
      </Card>
    </main>
  );
}
