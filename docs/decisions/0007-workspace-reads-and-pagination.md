# ADR 0007: Workspace reads, pagination, balances and evidence

- Status: accepted
- Date: 2026-09-13
- Deciders: project author
- Related: [ADR 0005](0005-data-foundation-and-migrations.md) (schema), [ADR 0006](0006-accounts-sessions-and-provisioning.md) (identities), [data contract](../data-contract.md)

## Context

Milestone 5 exposes a workspace's accounts, balances and transactions to the
web app for both demo visitors and signed-in owners, reusing the identities
and provisioning from M4. The reads must never widen access, must show
balances for what they are, and must page deterministically over live data.

## Decisions

Authorization
- Routes live under `/api/workspaces/{id}` and resolve the id only through
  the caller's own identity. A bearer token is validated first; if present
  and valid, the workspace must belong to that user and the user must hold a
  membership on its business. Without a token, the visitor cookie must own
  the workspace. A bearer token that fails validation is 401 even when a
  cookie exists, so a stale token cannot fall back to a demo identity.
- Missing identity is 401. Anything the caller cannot reach (another
  identity's workspace, a malformed id, an unknown id, another business's
  account or transaction) is 404, so responses never reveal whether a
  resource exists for someone else.
- Every Data API query carries the resolved business id. Account filters
  must name an account of that business; transaction ids and cursors are
  evaluated inside that scope only.

Pagination
- Keyset ordering by `posted_date` descending, then `id` descending. Ids are
  UUIDs, so the tie-break is total and duplicate dates page deterministically.
- Page size is 1 to 100, default 50. The response carries the filtered
  total, the page, a next cursor when more rows follow, and the read time.
- Pending rows carry the provider's authorization date in `posted_date`
  (the column is not null) and sort among posted rows by that date when the
  status filter asks for them. Removed rows are never returned.
- A cursor encodes the version, the last row's date and id, and a hash of
  the workspace id plus the applied filters. Reuse with another workspace,
  other filters or a different version is rejected with 400. Tampering with
  the position only moves the page start inside the same authorized set.
- Consistency: pages read live data and no snapshot is held. A row removed
  between requests is skipped; a row added after the first page is not
  surfaced on later pages of that walk; the total is a separate count and
  can differ from the rows walked when data changes mid-walk. The UI states
  this next to the table.
- Filters: account, date range (`from` must not exceed `to`), status
  (`posted` default, `pending`, `all`), and a description search restricted
  to letters, digits, spaces, apostrophes, ampersands and hyphens before it
  reaches the Data API. Invalid filters are 422.
- No new index: the existing `(business_id, posted_date)` index serves the
  demo volumes; a `(business_id, posted_date desc, id desc)` index is added
  through a migration if measured page latency on previews needs it.

Balances
- The accounts route returns, per account, the latest snapshot for each
  `(kind, provenance)` pair with its `as_of` and source. Kinds are shown as
  what they are (current, available, statement closing) and a missing
  balance is an empty list, never zero. A statement closing balance is never
  presented as available cash.
- Reconciliation used by the checks: opening balance (fixture) plus the
  signed posted, non-removed activity through the current snapshot's as-of
  date equals that snapshot's amount, per account.

Evidence
- The detail route returns the transaction, its provider id, the pending
  row it replaced (id, date, amount, status) and an allowlisted list of
  provenance events: operation, receipt time, provider transaction id and
  the sync batch's cursors and apply time. Raw provider payloads and any
  other fields are never returned.

Web
- One `WorkspaceData` component (accounts with balances, filter form, table,
  load-more, evidence panel with keyboard close and focus) is used by the
  demo workspace page and the private workspace page; the private page keeps
  its honest empty state. Loading and error states are derived from a
  request key so no state is set synchronously inside effects.

## Verification

- Unit tests against an in-memory PostgREST stand-in: identity precedence,
  404 policy, latest-balance selection, keyset order across duplicate dates
  without gaps, filters and validation, cursor binding and tampering,
  removal and insertion between pages, evidence allowlist.
- `db/checks/auth_isolation_check.py` (temporary users, real Auth): visitor
  B, user B and anonymous callers cannot read another workspace, its pages,
  its cursors or its evidence; demo ids are unreachable through a private
  workspace; guessed ids are 404.
- `db/checks/api_reconciliation_check.py`: every demo business paged
  through the API without duplicates or gaps to the evaluation file's posted
  count, strictly ordered, and every account reconciles opening plus posted
  activity to its bank-observed current balance with kind, provenance and
  timestamp present.
- Deployed previews of the branch (2026-09-13): the same demo flow through
  the web proxy for all three businesses, paging to the end without
  duplicates or gaps against the evaluation counts, filters, evidence,
  validation, visitor isolation, diagnostics 404 and production untouched;
  a WebKit click-through rendered balances and the transaction table.
- Manual review on the protected preview by the owner (2026-09-13):
  transaction filters, load more and the evidence panel work in a demo
  workspace; the owner's private workspace shows zero accounts and zero
  transactions.

## Limitations

- Counts and pages are separate queries over live data (see consistency).
- The description search is a substring match after sanitization; no
  ranking or full-text search.
- No per-route rate limiting yet; the routes are cheap reads, and usage
  controls arrive with the paid features that need them.
- Private workspaces are empty until imports exist (M6).
- One request during the first browser run against the branch preview on
  2026-09-13 answered HTTP 500 on the demo workspace summary. It did not
  recur in four later browser runs, in replays of the same request shape,
  or in the deployed API checks, and the Vercel runtime logs could not be
  read through the REST token, so the cause is unexplained. A recurrence is
  a new defect to be traced from the dashboard runtime logs.
