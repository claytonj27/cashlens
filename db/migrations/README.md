# Database migrations

Plain SQL files named `NNNN_name.sql`, applied in order by `db/migrate.py`,
which records each file's version and SHA-256 checksum in
`public.schema_migrations`. A changed applied file is reported as drift and
refused. The runner never drops, resets or re-runs anything, and each file
runs in one transaction with its tracking row.

Show what is applied, pending or drifted on the target database:

```bash
python3 db/migrate.py status
```

Apply every pending file in order (routine setup, and the only step a fresh
database needs):

```bash
python3 db/migrate.py apply
```

Record a file as applied without running it. This exists only to adopt a
migration that was verified to be already present by other means, as was
done once for `0001_ops_probe.sql` on the development project after it had
been applied by hand in M2. It is not part of routine setup, and a fresh
database must never use it; `apply` runs 0001 there like any other file:

```bash
python3 db/migrate.py baseline 0001
```

The target is `SUPABASE_DB_URL` from the environment or `services/api/.env`
(the direct connection string). Only the development project is targeted by
that file; production databases get migrations through a deliberate,
recorded step.

History on the development project:

- `0001_ops_probe.sql`: applied by hand on 2026-09-12 (M2), recorded as a baseline.
- `0002_foundation.sql`: businesses, memberships, visitors, workspaces, connections, accounts, sync batches, raw events, transactions, transaction sources, balance snapshots, with RLS, grants and the demo-immutability guard (M3).
- `0003_demo_guard_authority.sql`: the demo-mutation bypass requires a direct `postgres` operator session as well as the session flag (M3).
- `0004_session_provisioning.sql`: `ensure_demo_workspace` and `provision_private_business`, executable by the service role only (M4).
- `0005_provisioning_fixes.sql`: the same functions with column-first conflict resolution and definer rights, after 0004 failed through the Data API (M4).
- `0006_statement_imports.sql`: `document_imports`, `import_jobs`, `import_rows`, the `import_row_id` origin on transaction sources, the `statement_opening` balance kind, job leases, atomic `confirm_import`, and the private `statements` bucket (guarded for plain clusters) (M6).
- `0007_sync_apply.sql`: `raw_events.canonical` and `apply_sync_batch`, the atomic per-connection application of a staged provider batch that commits the cursor (M6).
- `0008_import_job_lease_tokens.sql`: per-lease tokens on `import_jobs`; `acquire_import_job` returns the token and `finish_import_job` only closes the lease that holds it (M6 review fix).
- `0009_pdf_imports_and_guarded_staging.sql`: `pdf` imports, `import_rows.page_number` and `decision_by`, lease-guarded `stage_import` and `fail_import`, `confirm_import` refusing undecided rows and lending references (M7).
- `0010_confirm_import_source_type_cast.sql`: `confirm_import` casts the format-derived source type to the enum after 0009's version failed at runtime (M7 fix).
- `0011_additional_private_businesses.sql`: `business_memberships.provisioning_key` with a unique index per user, and `provision_private_business` gaining a defaulted third parameter so a caller-scoped key requests an additional business idempotently while the keyless call still resumes the earliest one (M7 support).
- `0012_reference_identity_and_atomic_cancellation.sql`: a partial unique index making a source reference identify at most one imported transaction per account whatever format it arrived in; `confirm_import` lending a reference under the same scope as its insert check and serializing confirmations per account with an advisory lock; and `cancel_import`, which ends an import, invalidates any lease still open and removes its staged rows in one transaction (M7 review fixes).

Connection used so far: the direct connection string (IPv6 host) as the
`postgres` role. The pooled connection for serverless hosting is configured
when the API first needs a database connection.

Checks: `python3 db/checks/rls_check.py` runs grant, policy and structural
assertions inside a rolled-back transaction on the target database;
`python3 db/checks/data_api_check.py` exercises the Data API with the
publishable key; `python3 db/checks/apply_from_empty.py [--seed]` applies
the whole history to a throwaway local cluster, runs the same checks and,
with `--seed`, proves the loader is idempotent and leaves unrelated rows
alone; `db/checks/import_flow_check.py`, `db/checks/sync_adapter_check.py`
and `db/checks/statement_pack_check.py` exercise the M6 import, adapter and
sequential statement flows on temporary resources through the running API.

Every migration must state its grants and RLS explicitly: this project's
default privileges give the API roles truncate, references and trigger on new
tables, which RLS does not restrict.
