"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { type FormEvent, Suspense, useEffect, useState } from "react";

import { api, ApiError } from "@/lib/api";
import { featuresConfigured } from "@/lib/features";
import {
  createImport,
  MAX_UPLOAD_BYTES,
  markUploaded,
  uploadToStorage,
  type NewAccount,
} from "@/lib/imports-api";
import { useSession } from "@/lib/use-session";
import {
  readWorkspaceSelection,
  withWorkspace,
} from "@/lib/workspace-selection";
import { type Account } from "@/lib/workspace-api";
import { ScaffoldNotice } from "@/components/scaffold-notice";
import {
  buttonClass,
  Card,
  Field,
  inputClass,
  Notice,
  secondaryButtonClass,
} from "@/components/ui";

type Me = {
  business: { id: string; name: string } | null;
  workspace_id: string | null;
};
type Summary = { workspace_id: string; business: { name: string } };
type Target = { workspaceId: string; businessName: string };

export default function StartImportPage() {
  return (
    <Suspense
      fallback={
        <main className="mx-auto w-full max-w-3xl flex-1 px-4 py-10 sm:px-6 text-ink-muted">
          Loading…
        </main>
      }
    >
      <StartImportView />
    </Suspense>
  );
}

function StartImportView() {
  const router = useRouter();
  const search = useSearchParams();
  // The workspace named in the link, resolved by the API's authorized resolver. A link that
  // names nothing usable is unavailable here too; the default business never stands in for it.
  const { present: askedForWorkspace, id: selected } =
    readWorkspaceSelection(search);
  // A link that names a workspace but no usable one resolves to nothing, exactly as the
  // API answers it; the account's default business is never put in its place.
  const unusableSelection = askedForWorkspace && !selected;
  const { loading, session } = useSession();
  const token = session?.access_token ?? null;
  const [target, setTarget] = useState<Target | null>(null);
  const [noBusiness, setNoBusiness] = useState(false);
  const [unavailable, setUnavailable] = useState(false);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [accountChoice, setAccountChoice] = useState<string>("new");
  const [newAccount, setNewAccount] = useState<NewAccount>({
    name: "",
    account_type: "checking",
    mask: null,
  });
  const [file, setFile] = useState<File | null>(null);
  const [step, setStep] = useState<
    "idle" | "creating" | "uploading" | "parsing"
  >("idle");
  const [error, setError] = useState<string | null>(null);
  // Set once the file is in Storage: a retry only asks the API to read it again, never re-creates the import.
  const [uploadedImportId, setUploadedImportId] = useState<string | null>(null);
  const [activeImportId, setActiveImportId] = useState<string | null>(null);

  useEffect(() => {
    if (!featuresConfigured || !token) return;
    if (unusableSelection) return;
    let active = true;
    const load = async () => {
      let resolved: Target | null = null;
      if (selected) {
        const summary = await api<Summary>(
          `/api/workspaces/${encodeURIComponent(selected)}`,
          { token },
        );
        resolved = {
          workspaceId: summary.workspace_id,
          businessName: summary.business.name,
        };
      } else {
        const me = await api<Me>("/api/me", { token });
        if (!me.workspace_id || !me.business) {
          if (active) setNoBusiness(true);
          return;
        }
        resolved = {
          workspaceId: me.workspace_id,
          businessName: me.business.name,
        };
      }
      const list = await api<Account[]>(
        `/api/workspaces/${resolved.workspaceId}/accounts`,
        { token },
      );
      if (!active) return;
      setTarget(resolved);
      const importable = list.filter(
        (a) => a.source_type === "csv_import" || a.source_type === "pdf_import",
      );
      setAccounts(importable);
      if (importable.length > 0) setAccountChoice(importable[0].id);
    };
    load().catch((caught: unknown) => {
      if (!active) return;
      if (
        selected &&
        caught instanceof ApiError &&
        (caught.status === 404 || caught.status === 422)
      ) {
        setUnavailable(true);
        return;
      }
      setError(
        caught instanceof ApiError
          ? caught.describe()
          : "Could not load your workspace.",
      );
    });
    return () => {
      active = false;
    };
  }, [token, selected, unusableSelection]);

  const keptWorkspace = selected && target ? target.workspaceId : null;
  const workspaceHref = withWorkspace("/workspace/statements", keptWorkspace);

  async function stage(importId: string) {
    if (!token || !target) return;
    setStep("parsing");
    const staged = await markUploaded(target.workspaceId, importId, token);
    const query = new URLSearchParams({ workspace: target.workspaceId });
    if (staged.duplicate_of) query.set("duplicate", "1");
    router.push(
      `/workspace/imports/${staged.duplicate_of ?? staged.id}?${query.toString()}`,
    );
  }

  function explain(caught: unknown): string {
    if (caught instanceof ApiError) {
      if (
        caught.status === 409 &&
        caught.detail &&
        typeof caught.detail.active_import_id === "string"
      ) {
        setActiveImportId(caught.detail.active_import_id);
        return "An import is already in progress. Resume or cancel it first.";
      }
      return caught.describe();
    }
    return caught instanceof Error
      ? caught.message
      : "The import could not be started.";
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!token || !target || !file) return;
    setError(null);
    setActiveImportId(null);
    if (file.size === 0 || file.size > MAX_UPLOAD_BYTES) {
      setError("Choose a CSV or PDF file between 1 byte and 10 MB.");
      return;
    }
    try {
      setStep("creating");
      const body =
        accountChoice === "new"
          ? {
              new_account: { ...newAccount, mask: newAccount.mask || null },
              file_label: file.name,
              declared_bytes: file.size,
            }
          : {
              account_id: accountChoice,
              file_label: file.name,
              declared_bytes: file.size,
            };
      const created = await createImport(target.workspaceId, token, body);
      setStep("uploading");
      await uploadToStorage(created.upload, file);
      setUploadedImportId(created.import.id);
      await stage(created.import.id);
    } catch (caught) {
      setStep("idle");
      setError(explain(caught));
    }
  }

  async function retryReading() {
    if (!uploadedImportId) return;
    setError(null);
    try {
      await stage(uploadedImportId);
    } catch (caught) {
      setStep("idle");
      setError(explain(caught));
    }
  }

  if (!featuresConfigured) {
    return (
      <main className="mx-auto w-full max-w-3xl flex-1 px-4 py-10 sm:px-6">
        <ScaffoldNotice />
      </main>
    );
  }
  if (loading)
    return (
      <main className="mx-auto w-full max-w-3xl flex-1 px-4 py-10 sm:px-6 text-ink-muted">
        Checking your session…
      </main>
    );
  if (!session) {
    return (
      <main className="mx-auto flex w-full max-w-3xl flex-1 flex-col gap-4 px-4 py-10 sm:px-6">
        <Notice tone="info">Log in to import a statement.</Notice>
        <Link href="/login" className={buttonClass}>
          Log in
        </Link>
      </main>
    );
  }
  if (unavailable || unusableSelection) {
    return (
      <main className="mx-auto flex w-full max-w-3xl flex-1 flex-col gap-4 px-4 py-10 sm:px-6">
        <Notice tone="error">
          That workspace is not available for this account, so nothing can be
          imported into it. Check the link, or open your own workspace below.
        </Notice>
        <div>
          <Link href={withWorkspace("/workspace/statements", keptWorkspace)} className={buttonClass}>
            Open my workspace
          </Link>
        </div>
      </main>
    );
  }

  return (
    <main className="mx-auto flex w-full max-w-3xl flex-1 flex-col gap-6 px-4 py-10 sm:px-6 sm:px-8">
      <Link
        href={workspaceHref}
        className="text-sm text-plum-600 hover:underline"
      >
        ← My workspace
      </Link>
      <div>
        <p className="text-sm font-medium uppercase tracking-wide text-plum-600">
          Import statement
        </p>
        <h1 className="text-3xl font-semibold tracking-tight text-ink">
          Upload a CSV export or a text-based PDF statement
        </h1>
        {target ? (
          <p className="mt-1 text-ink-muted">
            Transactions will be added to{" "}
            <span className="font-medium text-ink">{target.businessName}</span>.
          </p>
        ) : null}
      </div>
      <Notice tone="info">
        CSV exports and text-based PDF statements for one checking or savings
        account, up to 10 MB, 20 pages and 5,000 rows. Scanned or photographed
        PDFs have no readable text and are refused; password-protected files
        must be unlocked first. The file goes straight from your browser into
        private storage that only you can reach through this app; nothing is
        sent to an AI model and nothing is read by OCR. Rows are shown for your
        review with their page and line, and nothing is added to your
        transactions until you confirm. The original file is deleted after you
        confirm or cancel, so the page and line references are what remains as
        evidence.
      </Notice>
      {error ? (
        <div className="flex flex-col gap-2">
          <Notice tone="error">{error}</Notice>
          {uploadedImportId ? (
            <div className="flex flex-wrap gap-3">
              <button
                type="button"
                onClick={retryReading}
                className={buttonClass}
                disabled={step !== "idle"}
              >
                Try reading the file again
              </button>
              <Link
                href={withWorkspace(
                  `/workspace/imports/${uploadedImportId}`,
                  keptWorkspace,
                )}
                className={secondaryButtonClass}
              >
                Open this import
              </Link>
            </div>
          ) : null}
          {activeImportId ? (
            <Link
              href={withWorkspace(
                `/workspace/imports/${activeImportId}`,
                keptWorkspace,
              )}
              className={secondaryButtonClass}
            >
              Open the import in progress
            </Link>
          ) : null}
        </div>
      ) : null}
      {noBusiness ? (
        <Notice tone="info">
          Create your private business from your workspace first.
        </Notice>
      ) : null}
      <Card>
        <form onSubmit={submit} className="flex flex-col gap-4">
          <Field label="Account">
            <select
              value={accountChoice}
              onChange={(e) => setAccountChoice(e.target.value)}
              className={inputClass}
            >
              {accounts.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}
                  {a.mask ? ` ···${a.mask}` : ""}
                </option>
              ))}
              <option value="new">New account…</option>
            </select>
          </Field>
          {accountChoice === "new" ? (
            <div className="grid gap-3 sm:grid-cols-3">
              <Field label="Account name">
                <input
                  type="text"
                  required
                  maxLength={120}
                  value={newAccount.name}
                  onChange={(e) =>
                    setNewAccount({ ...newAccount, name: e.target.value })
                  }
                  className={inputClass}
                  placeholder="Operating checking"
                />
              </Field>
              <Field label="Type">
                <select
                  value={newAccount.account_type}
                  onChange={(e) =>
                    setNewAccount({
                      ...newAccount,
                      account_type: e.target
                        .value as NewAccount["account_type"],
                    })
                  }
                  className={inputClass}
                >
                  <option value="checking">Checking</option>
                  <option value="savings">Savings</option>
                </select>
              </Field>
              <Field label="Last four digits (optional)">
                <input
                  type="text"
                  inputMode="numeric"
                  pattern="[0-9]{4}"
                  maxLength={4}
                  value={newAccount.mask ?? ""}
                  onChange={(e) =>
                    setNewAccount({
                      ...newAccount,
                      mask: e.target.value || null,
                    })
                  }
                  className={inputClass}
                  placeholder="6812"
                />
              </Field>
            </div>
          ) : null}
          <Field label="Statement file (CSV or PDF)">
            <input
              type="file"
              accept=".csv,.pdf,text/csv,application/pdf"
              required
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              className={inputClass}
            />
          </Field>
          <div className="flex flex-wrap gap-3">
            <button
              type="submit"
              className={buttonClass}
              disabled={
                step !== "idle" || !file || !target || uploadedImportId !== null
              }
            >
              {step === "idle"
                ? "Upload and review"
                : step === "creating"
                  ? "Preparing…"
                  : step === "uploading"
                    ? "Uploading…"
                    : "Reading rows…"}
            </button>
            <Link href={workspaceHref} className={secondaryButtonClass}>
              Cancel
            </Link>
          </div>
        </form>
      </Card>
    </main>
  );
}
