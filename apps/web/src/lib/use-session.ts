"use client";

import { useEffect, useState } from "react";
import type { Session } from "@supabase/supabase-js";

import { getSupabase, supabaseConfigured } from "@/lib/supabase";

export type SessionState = { loading: boolean; session: Session | null };

/** Current Supabase Auth session, kept in sync with sign-in, sign-out and token refresh. */
export function useSession(): SessionState {
  // When Supabase is not configured there is nothing to load, so start settled.
  const [state, setState] = useState<SessionState>({ loading: supabaseConfigured, session: null });
  useEffect(() => {
    const supabase = getSupabase();
    if (!supabase) return;
    let active = true;
    supabase.auth.getSession().then(({ data }) => {
      if (active) setState({ loading: false, session: data.session });
    });
    const { data: subscription } = supabase.auth.onAuthStateChange((_event, session) => {
      if (active) setState({ loading: false, session });
    });
    return () => {
      active = false;
      subscription.subscription.unsubscribe();
    };
  }, []);
  return state;
}
