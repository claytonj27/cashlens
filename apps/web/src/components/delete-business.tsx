"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";

import { api, ApiError } from "@/lib/api";
import {
  buttonClass,
  Card,
  Field,
  inputClass,
  Notice,
  secondaryButtonClass,
} from "@/components/ui";

export type DeletionStatus = {
  business_id: string;
  business_name: string;
  status: "removing" | "completed";
  requested_at: string | null;
  data_removed_at: string | null;
  objects_pending: number;
  objects_retrying: number;
  transactions_removed: number | null;
  imports_ended: number | null;
};

type Props = {
  businessId: string;
  businessName: string;
  token: string;
  /** Called once the removal is finished, so the page can leave this workspace. */
  onFinished: () => void;
};

/**
 * Deleting a private business. The owner types the name, the server checks it again, and the
 * uploaded files finish in the background: the panel keeps saying "removing" until they are
 * actually gone, and says so plainly if a removal is being retried.
 */
export function DeleteBusiness({
  businessId,
  businessName,
  token,
  onFinished,
}: Props) {
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [state, setState] = useState<DeletionStatus | null>(null);

  const matches = typed.trim() === businessName;

  const poll = useCallback(async () => {
    try {
      const next = await api<DeletionStatus>(
        `/api/private/businesses/${businessId}/deletion`,
        { token },
      );
      setState(next);
      return next;
    } catch (caught) {
      // A business that has finished is cleared entirely, so it stops being found.
      if (caught instanceof ApiError && caught.status === 404) {
        onFinished();
        return null;
      }
      throw caught;
    }
  }, [businessId, token, onFinished]);

  useEffect(() => {
    if (!state || state.status !== "removing") return;
    const timer = setInterval(() => {
      poll().catch(() => undefined);
    }, 5000);
    return () => clearInterval(timer);
  }, [state, poll]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!matches || busy) return; // a second submit cannot start a second deletion
    setBusy(true);
    setError(null);
    try {
      const result = await api<DeletionStatus>(
        `/api/private/businesses/${businessId}/deletion`,
        {
          method: "POST",
          token,
          body: { confirm_name: typed.trim() },
        },
      );
      setState(result);
      if (result.status === "completed") onFinished();
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.describe()
          : "Could not delete this business.",
      );
    } finally {
      setBusy(false);
    }
  }

  if (state) {
    const retrying = state.objects_retrying > 0;
    return (
      <Card>
        <h2 className="t-section">
          Deleting {state.business_name}
        </h2>
        <Notice tone={retrying ? "error" : "info"}>
          {state.status === "completed"
            ? "Everything has been removed."
            : retrying
              ? `Your accounts, transactions and imports are gone. ${state.objects_pending} uploaded file${state.objects_pending === 1 ? "" : "s"} could not be removed yet and ${state.objects_pending === 1 ? "is" : "are"} being retried. Nothing is reported as deleted until it actually is.`
              : `Your accounts, transactions and imports are gone. ${state.objects_pending} uploaded file${state.objects_pending === 1 ? "" : "s"} still to remove.`}
        </Notice>
        {typeof state.transactions_removed === "number" ? (
          <p className="mt-3 text-sm text-ink-muted">
            {state.transactions_removed} transaction
            {state.transactions_removed === 1 ? "" : "s"} removed
            {typeof state.imports_ended === "number" && state.imports_ended > 0
              ? `, ${state.imports_ended} import${state.imports_ended === 1 ? "" : "s"} in progress cancelled`
              : ""}
            .
          </p>
        ) : null}
        <p className="mt-2 text-xs text-ink-muted">
          Your login is untouched. Backups kept by our database provider age out
          on their own schedule, so this does not erase it from those.
        </p>
      </Card>
    );
  }

  return (
    <Card>
      <h2 className="t-section">
        Delete this business
      </h2>
      <p className="mt-2 text-sm text-ink">
        Permanently removes <span className="font-medium">{businessName}</span>:
        its accounts, every transaction, every import and the statements you
        uploaded. This cannot be undone. Your login and any other business you
        own are not affected.
      </p>
      {!open ? (
        <button
          type="button"
          onClick={() => setOpen(true)}
          className={`${secondaryButtonClass} mt-3`}
        >
          Delete this business…
        </button>
      ) : (
        <form onSubmit={submit} className="mt-3 flex flex-col gap-3">
          <Field label={`Type ${businessName} to confirm`}>
            <input
              type="text"
              value={typed}
              onChange={(event) => setTyped(event.target.value)}
              className={inputClass}
              autoComplete="off"
              aria-label="Business name confirmation"
            />
          </Field>
          {error ? <Notice tone="error">{error}</Notice> : null}
          <div className="flex flex-wrap gap-3">
            <button
              type="submit"
              className={buttonClass}
              disabled={!matches || busy}
            >
              {busy ? "Deleting…" : "Delete permanently"}
            </button>
            <button
              type="button"
              onClick={() => {
                setOpen(false);
                setTyped("");
                setError(null);
              }}
              className={secondaryButtonClass}
              disabled={busy}
            >
              Keep it
            </button>
          </div>
        </form>
      )}
    </Card>
  );
}
