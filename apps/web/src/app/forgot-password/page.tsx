"use client";

import { type FormEvent, useState } from "react";

import { getSupabase, siteOrigin, supabaseConfigured } from "@/lib/supabase";
import { buttonClass, Card, Field, inputClass, Notice } from "@/components/ui";

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sent, setSent] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const supabase = getSupabase();
    if (!supabase) return;
    setBusy(true);
    setError(null);
    const { error: resetError } = await supabase.auth.resetPasswordForEmail(email, {
      redirectTo: `${siteOrigin()}/auth/callback`,
    });
    setBusy(false);
    if (resetError) {
      setError(resetError.message);
      return;
    }
    setSent(true);
  }

  if (!supabaseConfigured) {
    return (
      <main className="mx-auto w-full max-w-md flex-1 px-4 py-10 sm:px-6">
        <Notice tone="error">Password reset is not configured for this deployment.</Notice>
      </main>
    );
  }

  return (
    <main className="mx-auto flex w-full max-w-md flex-1 flex-col gap-6 px-4 py-10 sm:px-6">
      <h1 className="t-page">Reset your password</h1>
      <Card>
        {sent ? (
          <Notice tone="success">If an account exists for that email, a reset link is on its way.</Notice>
        ) : (
          <form onSubmit={submit} className="flex flex-col gap-4">
            <Field label="Email">
              <input type="email" required autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} className={inputClass} />
            </Field>
            {error ? <Notice tone="error">{error}</Notice> : null}
            <button type="submit" disabled={busy} className={buttonClass}>
              {busy ? "Sending…" : "Send reset link"}
            </button>
          </form>
        )}
      </Card>
    </main>
  );
}
