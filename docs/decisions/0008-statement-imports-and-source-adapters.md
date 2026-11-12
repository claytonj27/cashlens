# ADR 0008: Statement imports and source adapters

- Status: accepted
- Date: 2026-09-13
- Deciders: project author
- Related: [ADR 0005](0005-data-foundation-and-migrations.md), [ADR 0006](0006-accounts-sessions-and-provisioning.md), [ADR 0007](0007-workspace-reads-and-pagination.md), [data contract](../data-contract.md), [statement import plan](../planning/CashLens-Statement-Import-Plan.md)

## Context

Milestone 6 adds the first real source of private data: an owner uploads a
CSV export of a bank account, reviews what was read, and confirms it into the
canonical transactions that the M5 views already serve. The same milestone
gives the synthetic provider a proper adapter with incremental-sync
semantics, so that fixture data and uploaded data reach the canonical model
through one contract and the seed loader no longer applies pages in Python.
Production stays gated off (ADR 0004 amendment); everything here runs on the
development project and protected previews.

## Decisions

Scope
- CSV only. PDF extraction, OCR, model-assisted extraction and classification
  are later milestones; the upload page says so and offers no controls for
  them. USD only, one account per import, new accounts created only when an
  import is confirmed.
- Limits enforced server-side and on the bucket: 10 MB per file, 5,000 rows,
  50 columns, 500 characters per cell. One import in progress per business.

Upload path
- The API creates the import record, generates the object path
  (`<business id>/<import id>.csv`) and asks Storage for a signed upload
  token bound to that path. The browser uploads straight to the private
  `statements` bucket with that token; file bytes never pass through the web
  proxy or the API. The token is issued only to a signed-in owner of the
  workspace, expires with Storage's two-hour window, and is refused for any
  other path.
- The bucket is private with no client policies at all: neither the
  publishable key nor a user's access token can read, list or write objects.
  The API reads the object with the service role only to parse it, refusing
  anything over the byte limit before reading it all. Filenames are reduced
  to a display label; MIME types are ignored; the type check is on bytes.

Parsing and review (`services/api/app/imports/csvparse.py`)
- Deterministic and pure, versioned as `csv-1`: type and encoding checks,
  delimiter detection, header validation, bounds, then a proposed column
  mapping (date, description, amount or debit/credit, running balance,
  source reference, currency), a date format that must parse every value,
  and a sign convention. Ambiguity is recorded as flags, never decided
  silently: a date column that parses as both month/day and day/month, a
  signed amount column with no running balance to verify it against, or an
  unrecognized column all surface in the review and the owner chooses.
- Money is parsed from decimal text (negatives, parentheses, trailing minus,
  CR/DR, thousands separators, currency symbols) into signed minor units,
  inflows positive; more than two decimals is rounded and flagged. Rows with
  an unreadable date or amount, a zero amount, a missing description or a
  non-USD currency are blocking until corrected or excluded. Nothing is
  skipped silently.
- A running balance column is treated as balance observations, never as
  transactions: each row's balance must follow from the previous one (else a
  `balance_break` flag), the file's opening balance is derived from the
  first row and the closing balance is the last row's. Both are stored at
  confirmation as `statement_opening` and `statement_closing` snapshots with
  `bank_observed` provenance and the statement's dates as `as_of`. They are
  shown as historical statement figures, never as today's cash.
- Staged rows keep the raw cells, the extraction, the owner's corrections
  (`reviewed`), flags and a decision. Every change bumps the import's draft
  revision; confirmation names the revision it reviewed.

Replay, overlap and conflicts (`services/api/app/imports/matching.py`)
- Exact file replay: the same bytes (SHA-256) already staged or confirmed
  for the same account return the existing import and delete the new
  object; nothing is imported twice.
- Retried confirmation: `confirm_import` is one database transaction. A
  repeated call for the same import and revision returns the recorded result
  and inserts nothing; a call for a different revision is refused.
- Source references: when the file carries a trusted per-transaction
  reference, a row whose reference already exists in the account is flagged
  `already_imported` (identical values) or `conflicting_revision` (values
  differ) and defaults to linking the row to the existing transaction as
  additional provenance. The owner may instead replace the existing values
  (revision + 1) or keep the row as new; keeping a duplicate reference is
  refused at confirmation and the whole confirmation is rolled back.
- Rows without a reference that match an existing transaction's date, amount
  and description are `duplicate_candidate` rows: kept by default, with the
  existing transaction shown so the owner can link or exclude. Identical rows
  inside one file are flagged `repeated_in_file` and kept: two legitimate
  payments can look the same. The file's period is compared with existing
  transactions and any overlap is counted in the summary.
- Matching never crosses businesses or accounts; the query for existing
  transactions carries the resolved business and account ids.

Authorization
- Every import route resolves the workspace through the caller's own
  identity (ADR 0007) and then requires a signed-in owner of a private
  business; visitors, other users, anonymous callers and guessed ids get 404
  or 401 exactly as the workspace reads do. Mutating routes require the
  client header. The database function checks membership again before
  publishing.
- `document_imports`, `import_jobs` and `import_rows` are service-role only,
  like the other provenance tables; statement contents never reach client
  roles through the Data API.

Jobs, cancellation and retention
- Parsing and confirmation run under leased `import_jobs` rows keyed by
  import and revision, so a concurrent repeat is refused while a lease is
  live and a failed step can be retried up to three times; invalid mapping
  input is rejected before a lease is taken. Processing is bounded to one
  request and no background work is started after a response.
- Cancelling an unconfirmed import deletes its staged rows and its object.
  A rejected file is deleted immediately and the import records a safe
  error code and message. The original object is deleted right after a
  successful confirmation; the staged rows remain as provenance.
- Abandoned uploads carry an `expires_at` (24 hours) and are cancelled when
  the owner next starts an import. There is no scheduled sweep yet: that
  and "delete my data" are the M8 privacy gate, and the interface makes no
  retention promise beyond what happens at confirmation and cancellation.

Source adapters (`services/api/app/ingestion/`)
- One canonical contract (`contract.py`): account reference, signed minor
  units with inflows positive, posted date, description, status, currency,
  pending predecessor. Provider-specific reading lives in the adapter; the
  synthetic adapter converts the fixture's Plaid-style sign convention and
  serves pages by cursor like a provider's sync endpoint.
- Incremental sync (`sync.py`): fetch every page from the committed cursor
  (bounded), restart from that cursor if the provider reports a pagination
  mutation (bounded), stage one batch of raw events (payload as received
  plus the canonical reading), then apply through `apply_sync_batch`
  (migration 0007) in one transaction that serializes per connection,
  refuses a batch that does not start at the committed cursor, applies
  added, modified and removed events with revision bumps and pending
  linkage, records a provenance source for every event and commits the next
  cursor. A failed apply marks the batch failed and moves nothing.
- The seed loader now stages and applies the fixture pages through that
  same function, so demo data and any future provider data share one
  persistence path. Transaction ids are assigned by the database; content
  stays deterministic. The shared demo businesses on the development project
  were not reseeded; the from-empty harness proves the new loader.

Web
- `/workspace/import` collects the account choice (existing imported
  account or a new one), the file and the upload notice, then runs create,
  direct upload and staging. `/workspace/imports/{id}` shows totals as read
  from the file, statement balances, warnings, the mapping form with the
  file's first rows, the staged rows with flags, per-row edit and decision
  controls, and explicit confirm and cancel actions. The workspace page lists
  imports and resumes an unfinished review. Evidence panels show the import
  row (file, row, line, extraction, whether corrected) for imported
  transactions.

## Verification

- Unit tests: parser (types, delimiters, encodings, money forms, date
  ambiguity, running-balance sign detection, balance breaks, blocking flags,
  bounds), matching (reference replay and conflict, shape candidates,
  in-file repeats), ingestion (sign conversion, paging, staging, restart on
  mutation, failed apply), and the import routes against an in-memory Data
  API, Auth and Storage stand-in that mirrors `confirm_import` (create,
  upload, stage, review, revisions, confirmation, idempotent repeat, atomic
  refusal, replay, cancellation, evidence, isolation).
- `db/checks/apply_from_empty.py --seed`: the whole history including
  migrations 0006 and 0007 applies to an empty cluster; the refactored seed
  loader is idempotent and leaves unrelated private rows alone.
- `db/checks/import_flow_check.py`: the real flow on the development
  project through the running API with temporary users, including the
  Storage token binding, denial for the publishable key and user tokens at
  Storage and the Data API, cross-user 404s, atomic refusal with a retry that
  commits once, evidence, balance observations and object deletion.
- `db/checks/sync_adapter_check.py`: the adapter and `apply_sync_batch` on
  a temporary private business: first sync, replay, later page with
  modification, removal and pending linkage, evaluation count, poisoned
  batch, pagination restart.
- `db/checks/statement_pack_check.py`: five sequential CSV statements for a
  temporary "Cedar Corner Cafe" business against the pack's expected results
  (per-file totals and balances, cumulative counts, monthly totals, account
  continuity, evidence, replay refusal). The pack itself stays outside the
  repository.
- Protected previews of the branch (commit `7890244`, branch-scoped
  variables): the same import flow and pack checks through the deployed
  proxy, and a scripted WebKit session that logged in as a temporary user,
  uploaded straight from the page to Storage, reviewed, confirmed and opened
  the evidence. Temporary users and businesses were deleted afterwards.

## Limitations

- CSV only; supported layouts are those the tests cover. One successful
  bank export is not broad bank-format support.
- A file without a source reference column relies on shape matching for
  overlap review; the owner decides, and legitimate repeats are kept.
- No scheduled cleanup, no "delete my data" and no quota accounting beyond
  the per-file and per-business limits above (M8).
- Balance observations from statements do not feed projections; that
  arrives with the calculations milestone, which must start from a dated
  anchor and say which kind it used.
- The upload token lifetime is Storage's fixed two hours; an owner who
  abandons the page keeps an `awaiting_upload` record until their next
  import starts.
- Transient upstream failures: the Supabase Data API can answer a gateway
  timeout (504) or similar for a single call. Reads now retry twice with a
  short back-off; writes never retry, so nothing is applied twice; failures
  that remain are answered as a JSON 502 with a reference id and a
  retryable flag, and an unread upload stays retryable from the review page.
  See the amendment below.

## Amendment 2026-09-13: the HTTP 500 after the first manual upload

Observed by the author on the M6 preview: after Upload and review with the
pack's January–April file into a new account, the page showed only
"HTTP 500".

Root cause, confirmed from the deployment's runtime logs (Vercel CLI) and
the import's records read read-only: the upload, parse and staging had all
succeeded (88 rows staged, parse job succeeded, object present). Ten seconds
later the review page's first request, `GET /api/me`, called the Data API
for the workspace row and received `504 Gateway Timeout` from Supabase. The
API had no handling for that outside the import routes, so the exception
became a bare 500 page and the review page showed "HTTP 500" with no way
forward. The earlier unexplained 500 during concurrent checks fits the same
class of transient upstream failure. The automated checks passed because the
failure is intermittent and not tied to the file or the account choice.

Changes: every request carries a reference (the platform request id when
present) returned as `X-Request-Id`; upstream failures answer a JSON 502
with the reference and a `retryable` flag, unexpected errors a JSON 500 with
the reference, both logged with method, path, error class and upstream
status only; idempotent reads retry twice on gateway errors and transport
failures, writes never do; every import step releases its job lease on any
failure; a transient failure while reading an upload leaves the file in
Storage and the import in `awaiting_upload`, and the review page offers
"Read the uploaded file" and "Cancel import" for that state; the start page
keeps the uploaded import and offers "Try reading the file again" so a retry
never creates a second import, and a 409 links to the import in progress;
errors shown to the owner carry the reference.

The author's import was not touched by the fix work: after a reload they confirmed it at 13:57 UTC and then the four remaining pack files, all on the pre-fix build; the workspace holds 180 posted transactions and five confirmed imports, and a re-upload of the May file was caught as an exact replay. The fixed build has been verified by automated checks only.

## Amendment 2026-09-13: review fixes before merging

Found in the pre-merge review of the full M6 diff:

- Lease release was not bound to the lease. `finish_import_job` matched a
  job by id and state only, so a worker whose lease had expired could close
  a newer lease that another worker had re-acquired for the same step.
  Migration 0008 gives every lease a token; `acquire_import_job` returns it
  and `finish_import_job` changes nothing unless the token matches, so a
  stale finish is a logged no-op. A job that already succeeded is reported
  as such and the step is not run again without a lease. Verified by a
  unit test against the in-memory stand-in and by the import flow check
  against the real function (expired lease re-acquired with a new token,
  stale finish refused, current finish accepted).
- Retry budget versus the request duration. Reads retry once (two attempts)
  with a 0.5 s pause and the client's 10 s timeout, so a single read costs
  at most about 20.5 s; parse and confirm leases last 180 s, inside the
  deployment's 300 s function limit (Hobby plan, fluid compute, `iad1`).
  Writes are never retried.
- Staging consistency. After writing staged rows the API counts them and
  refuses to mark the import staged unless the count equals the rows it
  parsed, so an interleaved concurrent staging cannot leave a mixed set.

