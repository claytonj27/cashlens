"use client";

import { useEffect, useState } from "react";

import { checkBackendHealth, HEALTH_PATH, type HealthResult } from "@/lib/health";

type Status = { state: "checking" } | HealthResult;

const timeFormat = new Intl.DateTimeFormat(undefined, {
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
});

function StatusPill({ status }: { status: Status }) {
  const base = "inline-flex items-center gap-2 rounded-full px-3 py-1 text-sm font-medium";
  if (status.state === "checking") {
    return (
      <span className={`${base} bg-lavender-100 text-ink-muted`}>
        <span aria-hidden className="size-2 animate-pulse rounded-full bg-lavender-300" />
        Checking
      </span>
    );
  }
  if (status.state === "connected") {
    return (
      <span className={`${base} bg-plum-600 text-white`}>
        <span aria-hidden className="size-2 rounded-full bg-white" />
        Connected
      </span>
    );
  }
  return (
    <span className={`${base} bg-amber-100 text-amber-900`}>
      <span aria-hidden className="size-2 rounded-full bg-amber-500" />
      Unavailable
    </span>
  );
}

export function BackendStatus() {
  const [status, setStatus] = useState<Status>({ state: "checking" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    checkBackendHealth().then((result) => {
      if (!cancelled) setStatus(result);
    });
    return () => {
      cancelled = true;
    };
  }, [attempt]);

  function retry() {
    setStatus({ state: "checking" });
    setAttempt((n) => n + 1);
  }

  return (
    <section
      aria-labelledby="backend-status-heading"
      className="rounded-2xl border border-line bg-white p-6 shadow-card"
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 id="backend-status-heading" className="t-section">
          Backend connectivity
        </h2>
        <StatusPill status={status} />
      </div>
      <p className="mt-1 text-sm text-ink-muted">
        Requests <code className="rounded bg-lavender-50 px-1">{HEALTH_PATH}</code> on this origin, which is
        proxied to the Python API. Process health only.
      </p>

      <div role="status" aria-live="polite" className="mt-5 text-sm">
        {status.state === "checking" && <p className="text-ink-muted">Checking the API…</p>}

        {status.state === "connected" && (
          <dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-1">
            <dt className="text-ink-muted">Service</dt>
            <dd className="text-ink">{status.payload.service}</dd>
            <dt className="text-ink-muted">Version</dt>
            <dd className="text-ink">{status.payload.version}</dd>
            <dt className="text-ink-muted">Environment</dt>
            <dd className="text-ink">{status.payload.environment}</dd>
            <dt className="text-ink-muted">Checked</dt>
            <dd className="text-ink">{timeFormat.format(status.checkedAt)}</dd>
          </dl>
        )}

        {status.state === "unavailable" && (
          <div className="flex flex-col gap-3">
            <p className="text-ink">
              <span className="font-medium">The API is not reachable.</span> {status.reason}
            </p>
            <p className="text-ink-muted">
              Start it from <code className="rounded bg-lavender-50 px-1">services/api</code> with
              uvicorn, or check the API_ORIGIN setting. Last attempt at{" "}
              {timeFormat.format(status.checkedAt)}.
            </p>
            <div>
              <button
                type="button"
                onClick={retry}
                className="rounded-lg bg-plum-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-plum-500 focus:outline-none focus-visible:ring-2 focus-visible:ring-lavender-300 focus-visible:ring-offset-2"
              >
                Retry
              </button>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
