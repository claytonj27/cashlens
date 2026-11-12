"use client";

import { useEffect, useState } from "react";

import { ApiError } from "@/lib/api";

/**
 * One read for one tab, tied to the token it was made with. A 403 is a refusal, rendered as
 * such rather than as a fault; the server decides, the page only reports.
 */
export function useAdminData<T>(fetcher: (token: string | null) => Promise<T>, token: string | null, nonce = 0) {
  const [state, setState] = useState<{ key: string; data: T | null; error: string | null; refused: boolean } | null>(null);
  const key = `${token ? "t" : "anon"}|${nonce}`;
  useEffect(() => {
    if (!token) return;
    let live = true;
    fetcher(token)
      .then((data) => {
        if (live) setState({ key, data, error: null, refused: false });
      })
      .catch((caught: unknown) => {
        if (!live) return;
        const refused = caught instanceof ApiError && caught.status === 403;
        setState({ key, data: null, error: refused ? null : caught instanceof ApiError ? caught.describe() : "Could not load.", refused });
      });
    return () => {
      live = false;
    };
  }, [fetcher, token, key]);
  const current = state && state.key === key ? state : null;
  return { data: current?.data ?? null, error: current?.error ?? null, refused: current?.refused ?? false, loading: Boolean(token) && !current };
}
