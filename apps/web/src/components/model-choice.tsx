"use client";

import { useEffect, useState } from "react";

import { fetchRoutes, type RouteOption } from "@/lib/ai-api";

/**
 * Which model answers, for a reviewer comparing two of them on the demo businesses.
 *
 * The server decides what may be offered here, from the business's own data mode. A private
 * workspace comes back with one option, so this renders nothing at all rather than showing a
 * choice that would be refused — there is no way to route someone's real bank data, and a
 * disabled control that hints otherwise is worse than no control.
 */
export function useRoutes(workspaceId: string, token: string | null) {
  const [options, setOptions] = useState<RouteOption[]>([]);
  const [chosen, setChosen] = useState<string>("direct");

  useEffect(() => {
    let live = true;
    fetchRoutes(workspaceId, token)
      .then((result) => {
        if (live) setOptions(result.options);
      })
      // A missing choice is not an error worth showing: the default route still works.
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [workspaceId, token]);

  return { options, chosen, setChosen };
}

type Props = {
  options: RouteOption[];
  chosen: string;
  onChoose: (key: string) => void;
  disabled?: boolean;
};

export function ModelChoice({ options, chosen, onChoose, disabled }: Props) {
  if (options.length < 2) return null;
  const note = options.find((o) => o.key === chosen)?.note;

  return (
    <div className="flex flex-wrap items-center gap-2">
      <label htmlFor="ai-route" className="text-xs text-ink-muted">
        Answer with
      </label>
      <select
        id="ai-route"
        value={chosen}
        disabled={disabled}
        onChange={(event) => onChoose(event.target.value)}
        className="rounded-lg border border-line bg-white px-2 py-1 text-xs text-ink"
      >
        {options.map((option) => (
          <option key={option.key} value={option.key}>
            {option.label}
          </option>
        ))}
      </select>
      {note ? <span className="text-[11px] text-ink-muted">{note}</span> : null}
    </div>
  );
}
