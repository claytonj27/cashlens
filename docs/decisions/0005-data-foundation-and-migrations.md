# ADR 0005: Data foundation, migration workflow and synthetic datasets

- Status: accepted
- Date: 2026-09-12
- Deciders: project author
- Related: [ADR 0001](0001-project-scope.md), [ADR 0004](0004-deployment-topology-and-protection.md), [data contract](../data-contract.md)

## Context

Milestone 3 needs a reproducible schema that later financial calculations,
AI features and private statement imports can build on, plus three synthetic
business datasets. The planning documents propose overlapping schemas (the
synthetic baseline and the import extension) and list many tables that no
current feature depends on. The development project already held the M2 probe
table, applied by hand, and its default privileges give the API roles
truncate, references and trigger on every new table.

## Decisions

Migration workflow
- Plain SQL files in `db/migrations`, named `NNNN_name.sql`, applied in order
  by `db/migrate.py`, which records version and SHA-256 checksum in
  `public.schema_migrations`, runs each file and its tracking row in one
  transaction under a transaction-scoped advisory lock (so applied SQL can
  never exist without its history row and concurrent runners serialize),
  refuses files that contain their own transaction control, and refuses
  drift. The M2 probe migration was registered as a baseline on the
  development project rather than re-run; the from-empty harness proves the
  same file creates the probe objects on a fresh database. Nothing is ever
  reset or dropped.
- Connection method used in M3: the direct connection string from
  `services/api/.env` (the `db.<ref>.supabase.co` host over IPv6) as the
  `postgres` role, through `psql`, for migrations, seeding and checks. The
  pooled connection for serverless hosting is deferred until the API opens
  database connections; its region comes from authorized project metadata or
  the dashboard, never from a guessed hostname.
- Rejected: the Supabase CLI (needs an interactive login and link, and a
  global install), Alembic (ORM-centred and adds runtime dependencies for a
  service that does not use an ORM), and any second framework alongside the
  existing plain-SQL files.

Schema scope
- Implemented now: businesses, business memberships, visitors, workspaces,
  connections, accounts, sync batches, raw events, transactions, transaction
  sources and balance snapshots. Everything else in the plans is deferred to
  the milestone that first needs it; the data contract lists each extension
  point so the deferred tables attach without reshaping the foundation.
- Scope is structural: `(id, data_mode)` on businesses and `(id, business_id)`
  on accounts and transactions let child tables carry composite foreign keys,
  so a row cannot be attached to another business's account and a membership
  cannot point at a demo business.

Ownership and privileges
- `data_mode` separates immutable demo seeds from private businesses. A
  trigger rejects every insert, update and delete on demo rows unless two
  conditions hold at once: the session sets
  `cashlens.allow_demo_mutation = 'on'` and both the session user and the
  current user are `postgres`, the operator role that migrations and the
  seed loader use over the direct connection. The API's routine credential
  reaches the database as `service_role` through the `authenticator` login,
  so it cannot lift immutability even if it sets the flag (migration 0003;
  verified on the development project and on the isolated cluster by
  connecting as `authenticator`). Client roles have no write grants at all,
  so the flag is meaningless to them.
- Private businesses are reachable only through `business_memberships`
  (authenticated users). Workspace constraints make a visitor workspace
  demo-only and a user workspace private-only and member-backed, so a browser
  identifier never authorizes private data.
- Every migration revokes the project's default table privileges from `anon`
  and `authenticated`, then grants exactly select on demo-readable tables,
  enables RLS with demo-read and member-read policies, and gives the service
  role full access. Visitors, workspaces, connections, sync batches, raw
  events and transaction sources have no client access at all. The API uses
  the service role and therefore checks membership itself on every private
  request.

Synthetic datasets
- One deterministic generator (`services/api/app/synthetic/generator.py`)
  with a fixed seed and a frozen demo date of 2026-08-31 covering six months
  from 2026-03-01. It writes a provider fixture per business (Plaid-like
  monthly pages of added records plus one delta page with modified and
  removed records, cursors and `has_more`) and a separate evaluation file
  with categories, case markers and expected figures.
- Provider transaction ids are opaque hashes; the evaluation file is the only
  place that maps them to labels, so nothing in application or model context
  reveals an answer.
- Every business carries the data-quality cases the blueprint asks for: a
  transfer pair across checking and savings, an ambiguous deposit, a
  financing inflow with repayments, unresolvable items, a duplicate delivery,
  a pending-to-posted change with a differing amount, a provider removal, a
  provider modification, a transaction that stays pending, and a stale
  savings snapshot that predates later activity.
- The provider sign convention mirrors Plaid (positive means money leaving
  the account); the canonical store keeps inflows positive. Opening balance
  plus posted, non-removed activity equals each stored current balance;
  available balance excludes pending outflows.
- Seeding: `app.synthetic.seed` applies each page in order and writes the
  canonical rows with full provenance (a sync batch per page, a raw event per
  operation, a transaction source per transaction) in one transaction under
  the demo-mutation flag, using stable ids so reloads are deterministic. It
  is a bootstrap; milestone 5 replaces the in-process page application with
  the incremental adapter and its replay, restart and rollback semantics.

## Fixture calibration

The first generated parameters produced an overdrawn Northline Services
(customer receipts too small and too rare for its payroll) and an implausibly
profitable Juniper Kitchen. Receipts and settlements were retuned, Juniper
gained quarterly sales-tax payments and a one-time equipment repair, and the
result keeps every account positive throughout the six months while leaving
modest cushions: Juniper closes at about $26.6k against $11.1k of September
rent and payroll, Northline at about $35.5k with roughly two weeks of payroll,
materials and rent ahead of an expected $14.5k customer receipt, and Willow
at about $30.5k with a planned equipment purchase. A delayed receipt or an
extra expense therefore creates visible cash pressure without an overdrawn
baseline. Each business keeps its distinct pattern: daily processor
settlements and supplier debits, irregular client payouts with subscriptions,
and lumpy customer receipts with materials and payroll.

Record counts per business, so provider history is not mistaken for
canonical rows (one duplicate delivery per business adds a raw event but no
transaction; a modification bumps a revision; removals keep their row with
`removed_at` set):

| Business | Raw events | Pages | Transaction rows | Posted, live | Pending, live | Removed | Modified |
|---|---|---|---|---|---|---|---|
| Juniper Kitchen | 327 | 7 | 323 | 320 | 1 | 2 | 1 |
| Northline Services | 214 | 7 | 210 | 207 | 1 | 2 | 1 |
| Willow Studio | 135 | 7 | 131 | 128 | 1 | 2 | 1 |

## Consequences

- The three businesses are loaded in the development project as immutable
  demo data and are readable by any client role; private rows are invisible
  to non-members. `db/checks/rls_check.py` asserts this against the live
  database and is part of the milestone gate.
- Committed fixtures must match a fresh build; CI runs the generator in
  check mode so the seed cannot drift from the code silently.
- The API still has no database client. Reading these rows from the service
  and the per-request membership check arrive with the milestones that use
  them.
- Authenticated Data API behaviour was verified only at the database level
  with the `authenticated` role and simulated claims. The end-to-end test
  with real test users signed in through Supabase Auth, using the access
  tokens that sign-in returns, is scheduled for M4; it does not require the
  project's signing secret.
- `db/checks/apply_from_empty.py` starts a throwaway local PostgreSQL
  cluster with Supabase-like roles, an auth shim and the same default
  privileges, applies the whole migration history from nothing, runs the RLS
  checks and optionally the seed loader, then destroys the cluster. It is the
  proof that the history works from an empty database.

## Open questions

1. Pooled connection string for serverless hosting before the API opens
   database connections in M5; the region is read from authorized project
   metadata or the dashboard at that time.
2. Whether visitor identity lives in an HttpOnly cookie issued by the API or
   in the web app's session; both keep the identifier server-readable.
3. The exact revision-invalidation rules for deferred financial snapshots.
