import { createClient, type SupabaseClient } from "@supabase/supabase-js";

/**
 * Browser Supabase client for sign-up, sign-in, password reset and session
 * storage. It uses the publishable key, which is safe in the browser because
 * row security decides what it can read, and the implicit flow so that
 * confirmation and recovery links work on whichever device opens the email.
 * API calls carry the session's access token in the Authorization header;
 * the Python API validates it with the Auth server.
 */
const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
const key = process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY;

export const supabaseConfigured = typeof url === "string" && url.length > 0 && typeof key === "string" && key.length > 0;

let client: SupabaseClient | null = null;

export function getSupabase(): SupabaseClient | null {
  if (!supabaseConfigured || !url || !key) return null;
  if (!client) {
    client = createClient(url, key, {
      auth: { flowType: "implicit", persistSession: true, autoRefreshToken: true, detectSessionInUrl: true },
    });
  }
  return client;
}

export function siteOrigin(): string {
  return typeof window === "undefined" ? "" : window.location.origin;
}
