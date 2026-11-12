# CashLens implementation checklist

Status: milestones 1 to 9 integrated into `main`, and M10's vertical slice built on `feat/m10-ai-insights`, from which an **interview demo release** is cut — see [the demo release section](#interview-demo-release) for what is verified and what is not. `AI_ENABLED` is on for that branch's preview only; the paid calls made so far came from a local API against the same shared development database, and the preview's own generating routes are not verified end to end; production keeps product features switched off, holds no Supabase credentials, no AI variable and no model credential, and runs no migration or seeding step at deploy. **No cleanup schedule is activated, so automated retention is not operational**: see the [M8 privacy and retention plan](CashLens-M8-Privacy-and-Retention-Plan.md), [ADR 0010](../decisions/0010-retention-deletion-and-processing-limits.md) and the [financial methodology](CashLens-Financial-Methodology.md). Signup confirmation by email and custom SMTP remain open. Milestone 10 is designed on `docs/m10-ai-design` ([M10 AI design](CashLens-M10-AI-Design.md), [ADR 0011](../decisions/0011-grounded-ai-and-provider-boundary.md)); its slice is built and paid models have been called from a local API against the shared development database, within the approved $25 cumulative cap. Last updated September 14, 2026.

Public-release prerequisites still open. None of these is claimed until it is
observed, and none is closed by M8's design work:

- [ ] Fresh confirmation email → callback → onboarding, completed by the author on a protected preview. **Not reported.** Deferred after the mailer's rate limit was reached. The email-free callback check passed 14/14 with a temporary user, which exercises the callback, not delivery, and is not a substitute for this test.
- [ ] Custom SMTP through a transactional provider, with verified delivery to an external, non-team recipient. **Required before public signup.** Nothing purchased, configured or changed.
- [ ] The M8 privacy and cleanup gate, per the [M8 plan](CashLens-M8-Privacy-and-Retention-Plan.md): all three tasks are implemented on `feat/m8-retention-cleanup` and **unmerged**, and the cleanup schedule is **not activated**, so an abandoned upload holds its quota slot and its file until a cleanup run actually happens. The gate stays closed until the branch is merged and a schedule is deliberately switched on.
- [ ] Post-fix manual testing by the author of the M6 error-handling build, as a test in its own right: **not claimed.** The author's M7 manual acceptance ran on a build that contains those fixes, which is evidence but not this test.
- [ ] Production product features stay switched off (`FEATURES_ENABLED` unset, no Supabase credentials in production) until every item above is done.

Acceptance already recorded. Manual means the author performed it and reported
the result; automated means a check produced it. The two are never merged:

- [x] Manual, 2026-09-13: five-file CSV acceptance on the pre-fix M6 preview (`3a202ac`). 180 posted transactions, five confirmed imports, exact-file replay refused.
- [x] Manual, 2026-09-13: text-PDF acceptance. The first pack PDF was uploaded, reviewed, reconciled and confirmed, adding 88 transactions. The cross-format link decision and the evidence panel were not reported by hand and are covered automatically instead; the remaining monthly PDFs are deliberately held back for trend demonstrations. Details under M7.
- [x] Manual, 2026-09-13: recovery email → new password → successful login on a protected preview, reported by the author. Not reproduced by an automated run; the record is the author's report.
- [x] Automated, 2026-09-13: post-fix verification of the M6 error-handling fixes (`c603625`..`cd4a543`, `e418b57`). Scripted browser replay of the manual path, import flow check through the preview, regression tests.

This checklist sequences the work described in the
[statement import plan](CashLens-Statement-Import-Plan.md),
[engineering blueprint](AI-CFO-Engineering-Blueprint.md),
[flows and data model](AI-CFO-Flows-and-Data-Model.md) and
[development playbook](AI-CFO-Playbook.md). Where they differ, the
import plan wins. Each milestone ends with a code review and one or more
genuine commits made when the work is actually complete. Unchecked boxes are
plans, not progress. Nothing below has been implemented, deployed or tested.

Ordering rules
- Deployment feasibility is proven before any feature work.
- Private ownership is in place before import development and before any
  real upload is invited.
- Real public uploads open only after the privacy and cleanup gate passes.
- Optional OpenRouter, scanned PDFs and bank connections come after the
  import workflow is complete and verified.

## M0. Repository preparation

Proposed commit: `chore: initialize CashLens repository and planning documentation`

- [x] Starter pack copied into a fresh `cashlens` folder; Git initialized on `main` (local, uncommitted)
- [x] Supplied README, planning docs, diagrams and mockup preserved unchanged
- [x] `.gitignore`, `.editorconfig`, sanitized `.env.example`, this checklist and `docs/decisions/0001-project-scope.md` added; local project instructions kept untracked
- [x] Local link, image and ignore-rule checks run (results reported in the setup summary, not recorded as tests)
- [x] Repository visibility decided: one private GitHub monorepo, separately deployed Vercel application, reviewer access on request ([ADR 0002](../decisions/0002-private-repository-and-deployment.md))
- [x] Public-repository wording updated in the blueprint, playbook, flows document, ADR 0001, baseline diagram 01 and its Mermaid source; playbook asset paths corrected to the shipped layout
- [x] Author reviewed the prepared files and authorized the first commit and push to origin on 2026-09-12; the commit itself is recorded in Git history

Gate: README renders with relative images, no secrets or private material, status states design-only.

## M1. Scaffold runnable services (no features)

Commit: `chore: scaffold Next.js and FastAPI services` (feature branch `feat/m1-framework-scaffolding`, 2026-09-12)

- [x] Node 24 and Python 3.12 pinned in `.nvmrc`, `apps/web/package.json` engines and `services/api/.python-version`; choices recorded in [ADR 0003](../decisions/0003-scaffolding-runtimes-and-routing.md)
- [x] `apps/web`: Next.js 16.3.5 App Router, React 19, TypeScript strict, Tailwind 4 tokens; one page with name, description and backend connectivity (loading, connected, unavailable with retry)
- [x] `services/api`: FastAPI `GET /api/health` typed response with process status only; validated settings; JSON logging without secrets
- [x] Per-service `.env.example` files with loading documented; root example kept as the credentials reference
- [x] Lockfiles committed: `apps/web/package-lock.json`, `services/api/requirements.txt` and `requirements-dev.txt` (pip-tools)
- [x] Migration tool: plain ordered SQL files tracked by `db/migrate.py` (M3); no framework added
- [x] Lint, format, type-check and test commands documented in each service README and the root README
- [x] GitHub Actions: web lint, typecheck, build; API Ruff check, format check, pytest; read-only permissions; no keys or external services
- [x] Local frontend reaches the local API through the same-origin `/api/*` rewrite; API down renders an explicit unavailable state with retry

Gate: clean install from lockfiles, health endpoint responds, CI configuration present, no secrets or unnecessary dependencies. Checks run locally on 2026-09-12: ESLint, `tsc --noEmit`, `next build`, Ruff check and format, 11 pytest tests, HTTP checks through the rewrite with the API up and down, and a WebKit render of both page states. GitHub Actions has not run yet at the time of writing; the first push of the branch triggers it.

## M2. Deployment feasibility spike (gate before features)

Branch `feat/m2-deployment-spike`, 2026-09-12. Findings in [ADR 0004](../decisions/0004-deployment-topology-and-protection.md).

- [x] Two Vercel projects from one repository: `cashlens-api` (root `services/api`, FastAPI preset) and `cashlens-web` (root `apps/web`, Next.js preset); `/api/*` reaches the Python project through the web app's server-side proxy
- [x] Vercel connected to the private GitHub repository; Git-triggered preview builds work for both projects
- [x] `API_ORIGIN` on the web project: production value set; previews have no default and get a per-branch override (M2 branch set), so unrelated previews answer 503 instead of using production
- [x] Vercel installs the API from the locked `requirements.txt`; required removing `pyproject.toml`; entrypoint auto-detected from `app/main.py`
- [x] Python 3.12 and Node 24.x confirmed from build logs
- [x] Function limits recorded from official docs: 300 s maximum duration on Hobby, 4.5 MB request/response body, 500 MB Python bundle; not yet measured against CSV/PDF parsing because no parser exists
- [x] Deployed web preview calls the deployed Python health endpoint through the proxy; JSON and HTTP 200 verified; WebKit render shows Connected
- [x] Deployment protection: Vercel Authentication kept on for both projects at its default scope (previews and unique URLs); the preview API is reachable only with its bypass secret, held server-side by the web app; the production domains are public by decision; incoming bypass headers stripped; redirects passed back unfollowed; no secret in browser code, URLs or logs
- [x] Access model recorded in ADR 0004: private repository, public production website and demo, application-level authentication and authorization for private data, usage controls before paid AI, preview protection kept, custom domain optional, production health-only until feature access controls exist
- [x] Supabase read/write from the deployed API: insert, read, delete on the isolated `ops_probe` table via the REST interface with the service role; publishable key denied; migration `db/migrations/0001_ops_probe.sql`
- [x] Bounded OpenAI smoke test through the operator diagnostic: one call from the deployed API, `gpt-5-mini` served as `gpt-5-mini-2025-08-07`, 3.3 s, 23 tokens, about $0.000023 against a $0.10 authorization; no retries; diagnostics disabled again afterwards
- [x] Streaming and cookie handling through the proxy verified with synthetic content; safe error responses verified (503 when the API origin is missing, non-200 when the API is down)
- [x] Preview and production environment scopes separated on both projects; M2 secrets scoped to the M2 branch only
- [x] Client bundle inspected for absence of the API origin and secrets
- [x] Operator diagnostics (`/api/ops`) exist behind an enable flag and token; enabled only for the M2 branch preview during checks and disabled afterwards (404 verified)
- [ ] Scheduled cleanup: Vercel cron on Hobby runs at most once per day, which fits a 24-hour expiration policy; no cron route created or observed yet
- [ ] Direct browser-to-Storage uploads with signed URLs (required by the 4.5 MB body limit): untested, planned for M6
- [ ] Build timeline re-estimated after the spike
- [x] Production deployment: M2 merged to `main` (fast-forward, e702123) on 2026-09-12; both projects deployed a health-only scaffold with diagnostics explicitly disabled and no Supabase or OpenAI credentials in production; production domains are public under the default protection scope

Gate status: preview deployments, database round trip, one bounded model call, streaming, cookies and protection handling verified end to end; cleanup cron, uploads, the timeline re-estimate and production remain open.

## M3. Synthetic data and schema baseline

Branch `feat/m3-data-foundation`, 2026-09-12. Decisions in [ADR 0005](../decisions/0005-data-foundation-and-migrations.md); schema in the [data contract](../data-contract.md).

Demo fixture counts, reconciled read-only on 2026-09-13: Juniper Kitchen, Northline Services and Willow Studio hold 323, 210 and 131 transaction rows. Of those, 320, 207 and 128 are posted and not removed, which is what the workspace route returns and what earlier notes quoted; each business also carries one pending and two soft-removed rows so those states are exercised. The two figures count different things and neither is wrong; no fixture is changed to make them agree.

- [x] Data contract reconciling the baseline and import-plan schemas: entities, ownership boundaries, money and date conventions, provenance, revision and uniqueness rules, contradictions resolved
- [x] Migration workflow: ordered SQL files tracked by `db/migrate.py` in `schema_migrations`, each file and its history row in one transaction under an advisory lock; files with their own transaction control refused; M2 probe migration registered as a baseline on the development project and proven from empty by the harness
- [x] Migration `0003_demo_guard_authority.sql`: the demo-mutation bypass requires the flag and a direct `postgres` operator session; the API's service role cannot lift immutability (verified on both databases)
- [x] Migration `0002_foundation.sql`: businesses, business memberships, visitors, workspaces, connections, accounts, sync batches, raw events, transactions, transaction sources, balance snapshots, with composite scoped keys, explicit grants, RLS policies and the demo-immutability trigger; applied to the development project
- [ ] Deferred to their feature milestones with documented extension points: import staging (M6/M7), classifications and overrides (M10), assumptions, financial snapshots, scenarios and insights (M9/M10), AI runs and usage reservations (M10), experiments and analytics (M12)
- [x] Deterministic generator with fixed seed and frozen demo date 2026-08-31 for all three businesses (Juniper Kitchen, Willow Studio, Northline Services); opening plus activity reconciles to stored balances for every account
- [x] Plaid-like added/modified/removed pages with cursors and `has_more`, opaque transaction ids, evaluation labels and expected figures in a separate file outside application and model context
- [x] Data-quality cases in every business: transfer pair, ambiguous deposit, financing inflow, duplicate delivery, pending-to-posted change, provider removal, provider modification, remaining pending item, stale snapshot, unresolvable items
- [x] Fixtures seeded into the development project with full provenance (sync batches, raw events, transaction sources); committed fixtures verified against a fresh build; CI runs the generator in check mode
- [x] Database-level checks (`db/checks/rls_check.py`, 33 assertions): anonymous callers cannot read private rows or modify demo fixtures; authenticated user A cannot see user B's business, transactions or membership; cross-business account references and mismatched workspace modes are rejected structurally; private workspace creation requires a membership; the service role cannot lift demo immutability
- [x] Data API checks with the publishable key (`db/checks/data_api_check.py`): only demo rows readable, no writes, no access to visitors, workspaces, connections, sync batches, raw events, transaction sources, memberships or migration history
- [x] Structural workspace isolation: no client role can read or write `visitors` or `workspaces` through the database or the Data API, so a visitor cannot reach another visitor's workspace until a trusted identity mechanism exists in the API (M4)
- [ ] Correction-record isolation (overrides, assumptions, scenarios): not tested because those tables do not exist yet; tested when they arrive in M9/M10
- [ ] Authenticated Data API test: not performed in M3 because it needs signed-in test users; scheduled for M4 with normal Supabase Auth sign-in and the resulting access tokens (no signing secret needed). Database-level checks with the `authenticated` role stand in for now
- [x] Seed reliability on an isolated database: seeding twice yields identical counts, canonical rows and balances; unrelated private rows survive a reseed; seeding refuses to run over existing demo data without `--replace`

Gate: migrations apply from an empty database (`db/checks/apply_from_empty.py`, local throwaway cluster, including the M2 probe objects) and on the development project, fixtures reproduce byte for byte, balances reconcile, isolation and idempotency checks pass on both databases. Commands: `python3 db/migrate.py apply`, `.venv/bin/python -m app.synthetic.generate --check`, `.venv/bin/python -m app.synthetic.seed [--replace]`, `python3 db/checks/rls_check.py`, `python3 db/checks/data_api_check.py`, `python3 db/checks/apply_from_empty.py --seed`.

## M4. Private ownership, self-service accounts and demo sessions

Branch `feat/m4-private-ownership`, 2026-09-13. Decisions in [ADR 0006](../decisions/0006-accounts-sessions-and-provisioning.md).

- [x] Public landing page with Explore demo, Create account and Log in, in the CashLens style
- [x] Explore demo creates or resumes an anonymous visitor session (HttpOnly cookie) and offers the three synthetic businesses without sign-up; opening one creates or resumes that visitor's workspace and shows a minimal workspace shell (accounts, bank-observed balances, posted count)
- [x] Self-service registration: Supabase Auth email and password with confirmation kept on; confirmation callback, login, resend confirmation, logout and password reset with the standard client flows
- [x] After a confirmed sign-in, the private business, owner membership and workspace are created or resumed through an authorized, idempotent backend operation (`provision_private_business`, service role only, advisory lock per user); a business name is collected on first use
- [x] Demo transactions are never attached to a private business; the private workspace shows an honest empty state that uploads are coming next, with no upload control
- [x] Access tokens validated with the Auth server, `authenticated` audience required, cached per token hash within the token's expiry; business scope derived only from memberships
- [x] CSRF guard on state-changing routes (custom client header); `Cache-Control: no-store` on every API response; no tokens or keys in logs
- [x] `workspaces` accept exactly one of visitor or user with the business mode enforced; composite scoped keys (M3) unchanged
- [x] Tests: mocked Supabase unit tests for sessions, tokens, provisioning and CSRF; `db/checks/auth_isolation_check.py` with two temporary operator-created users signed in through normal Auth: API and Data API isolation between users and against anonymous callers, demo visitors isolated from each other, temporary data deleted afterwards
- [x] Authenticated Data API test with real access tokens (scheduled from M3): user A sees own private rows and demo rows only; user B and anonymous callers see nothing private; no user can read workspaces or call the provisioning function
- [x] Manual review on the protected preview, 2026-09-13, with the owner's retained development account (created by the operator through the Auth admin API with the address pre-confirmed, so no email step was exercised): login, business naming and private workspace creation work; the private workspace (Gift Genie) shows zero accounts and zero transactions
- [ ] Self-service signup verified end to end by a person on the protected preview. Observed 2026-09-13 on the M6 preview for the author's own second address: the confirmation email and a recovery email were delivered, but both links redirected to `localhost:3000` because the M6 callback was not on the redirect allowlist (ADR 0006 amendment); the account was confirmed and used, no password was changed. After the allowlist entry was added the same day, an email-free check with admin-generated links for a temporary user passed 14/14 against the protected M6 preview: confirmation completes and lands in the workspace, recovery lands on the new-password form and the password actually changes, a reused link shows an explanatory message. Still pending: the author's own retest with fresh confirmation and recovery emails. Delivery to the author's own address is not evidence of delivery to outside recipients
- [x] Supabase redirect allowlist holds exactly the local and branch-preview `/auth/callback` URLs used by confirmation, resend and recovery; no wildcards. The M6 preview callback was added on 2026-09-13 (the email-free check that lands on the preview callback proves it is honoured); the local and M4 entries remain
- [ ] Required before public self-service signup: custom SMTP through a transactional provider (Authentication → SMTP settings) and confirmation delivery to an external, non-team recipient verified end to end. Until then the default mailer (two emails per hour, pre-authorized recipients) serves only the owner's own tests. Provider selection and setup happen later; nothing is purchased and email confirmation stays on
- [x] CSRF in context: no CORS on the API, so cross-origin pages cannot send the client header; form posts and cookie-only requests to mutating routes answer 403 (tests)
- [x] Production feature gate: `FEATURES_ENABLED` resolves to off in production, so every demo, account, provisioning and workspace route answers 404 there before any Supabase call, and a web build without `NEXT_PUBLIC_SUPABASE_*` shows a scaffold notice in place of those features (ADR 0004 amendment, 2026-09-13)
- [ ] Private Storage bucket with owner-scoped policies: moved to M6 with the first upload
- [ ] Backend membership check on every private request: in place for the routes that exist; re-verify as each private route is added

Acceptance: a new person can create an account, confirm by email, log in, name their business and land in an empty private workspace; a visitor can explore all three demo businesses without an account; no path exposes another user's or visitor's data; production remains health-only.

## M5. Workspace data access and transaction views

Branch `feat/m5-workspace-data`, cut from the M4 head `41f3f18`, 2026-09-13; decisions in [ADR 0007](../decisions/0007-workspace-reads-and-pagination.md); depends on M4's sessions, token validation and provisioning and is reviewed and merged after M4. Proposed commits: `feat(api): add scoped workspace reads with keyset pagination`, `feat(web): add transaction views to demo and private workspaces`, `test(db): extend isolation checks to workspace reads`.

Already in place and not rebuilt: the canonical transaction model with provenance tables (M3); visitor cookies, access-token validation, the CSRF client header and no-store responses (M4); the demo workspace summary with accounts, bank-observed balances and the posted count; the private workspace shell with counts and the honest empty state; idempotent provisioning.

- [x] One workspace resolver: routes under `/api/workspaces/{id}` accept a workspace id and authorize it only through the caller's own identity (visitor cookie for that visitor's demo workspace, access token plus membership for a user workspace); any other id, including a guessed valid one, answers 404
- [x] `GET /api/workspaces/{id}/accounts`: accounts with the latest current and available balances, their `as_of` and provenance labels
- [x] `GET /api/workspaces/{id}/transactions`: keyset pagination ordered by posted date then id, filters for account, date range, status (posted by default, pending on request) and description text; removed rows excluded; each row carries amount, posted date, description, status, account, source type and revision; the response includes the next cursor and the filtered count
- [x] `GET /api/workspaces/{id}/transactions/{tx_id}`: detail with provenance (source type, raw event operation and receipt time, pending link, revision), the evidence hook that briefs and scenarios reuse later
- [x] Index: not added; the existing `(business_id, posted_date)` index serves demo volumes, and a `(business_id, posted_date desc, id desc)` index is added by migration only if measured preview latency needs it
- [x] Web: one shared transaction view (table, account and date filters, status badge, load more, detail panel) used by both the demo workspace page and the private workspace page; the private page keeps the honest empty state when nothing is imported; loading, error and empty states; keyboard accessible; responsive
- [x] M4 routes stay unchanged; new routes sit beside them
- [x] Tests: fake-Supabase unit tests for pagination boundaries, filters, scoping and cursor tampering; the isolation check extended so visitor B, user B, anonymous callers and guessed ids get 401 or 404 on another workspace's reads
- [x] Fixture reconciliation through the API: paging every transaction of each demo business returns the evaluation file's posted count and the amounts sum to the stored balance
- [x] Preview on the M5 branch with the same branch-scoped variables; production unchanged; CI green
- [x] Manual review on the protected preview, 2026-09-13 (owner): transaction filters, load more and the evidence panel work; the owner's private workspace shows zero accounts and zero transactions
- [ ] Isolated HTTP 500 on the demo workspace summary during the first browser run against the preview: not reproduced afterwards; cause unexplained (ADR 0007, limitations)
- [x] Workspace routes sit behind the production feature gate with the demo and account routes (ADR 0004 amendment): 404 in production before any identity or database work

Acceptance: a visitor pages through every transaction of a demo business with filters and opens one to see where it came from; a signed-in owner sees the same view empty; no path reads another workspace; totals paged through the API reconcile to the seeded balances.

## M6. Source adapters and reviewed CSV imports (first vertical slice)

Branch `feat/m6-statement-imports`, cut from `main` at `f731ff4`, 2026-09-13; decisions in [ADR 0008](../decisions/0008-statement-imports-and-source-adapters.md). Migrations `0006_statement_imports.sql` and `0007_sync_apply.sql` applied to the development project; production untouched and still gated off.

Adapter work moved here from the former M5, because the CSV import is the first real adapter and the synthetic adapter's replay semantics belong beside it:

- [x] Synthetic provider adapter converts its sign convention and publishes through the canonical contract (`app/ingestion/`), replacing the M3 seed loader's in-process page application: the loader now stages batches and applies them through `apply_sync_batch`
- [x] Incremental sync: stage all pages, atomic apply, cursor committed only after success, restart on pagination mutation (bounded), serialized per connection (row lock in `apply_sync_batch`)
- [x] Pending-to-posted linkage; pending excluded from posted totals; removals applied with revision bumps; dependent snapshots: none exist yet (M9), balances are re-published as bank-observed observations
- [x] No model call while an ingestion transaction is open: no model calls exist in ingestion; the apply step is a single database function
- [x] Tests: replay leaves totals unchanged, modification and removal apply, a poisoned batch keeps the prior cursor and rows, pagination mutation restarts (`tests/test_ingestion.py`, `db/checks/sync_adapter_check.py`, `db/checks/apply_from_empty.py --seed`)

CSV import:

- [x] `document_imports`, `import_jobs`, `import_rows` tables with revisions, leases, attempts, idempotency keys, expiration and safe error codes; service-role only with explicit grants and RLS; private `statements` bucket with no client policies
- [x] `create_import`: membership check through the workspace resolver, one import in progress per business, constrained one-path upload token, server-generated object path, import identity
- [x] `validate_upload`: byte-level type check, size, row and column bounds, ownership, leased parse job with capped attempts
- [x] CSV column-mapping preview: date, description, amount or debit/credit, running balance, source reference; explicit date format and sign interpretation; ambiguity flagged for the owner, never guessed silently
- [x] Draft rows with row provenance (raw cells, line number, extraction, corrections); validation flags for money, dates, currency, balance continuity, in-file repeats, overlap and reference conflicts
- [x] Review UI: file status, account label, statement dates, row count, credits/debits, balances as read, warnings; owner can correct dates, signs, amounts and descriptions, choose keep/exclude/link/replace per row, and re-map columns
- [x] Failures are explained (safe error code and message); rows are never silently skipped; blocking rows stop confirmation until corrected or excluded
- [ ] Downloadable synthetic CSV sample using the same upload path: not added; the public reviewer path arrives with the walkthrough milestone
- [x] Vertical slice: sign in, import one CSV, review, confirm, see the rows in the M5 views with evidence (brief arrives in M10)
- [x] Atomic confirmation (`confirm_import`, one transaction), idempotent repeat for the same revision, revision conflicts refused, exact-file replay returns the existing import, reference conflicts and shape duplicates surfaced for owner decision, legitimate repeats kept; original object deleted at confirmation, cancellation and failure
- [x] Verified on the development project through the running API: `db/checks/import_flow_check.py` (40 checks, temporary users deleted), `db/checks/sync_adapter_check.py` (14 checks, temporary businesses deleted), `db/checks/statement_pack_check.py` on the five-file Cedar Corner Cafe pack (57 checks: per-file totals and balances, cumulative counts, monthly totals, continuity, evidence, replay refused; temporary user and business deleted)
- [x] Preview on the M6 branch with branch-scoped variables (2026-09-13, commit `7890244`): a scripted WebKit session with a temporary user logged in, created a business, uploaded a CSV straight from the page to Storage, reviewed totals and balances, confirmed, saw the rows in the workspace table and opened import-row evidence; `import_flow_check.py` 40/40 through the deployed proxy (four runs, three concurrent); `statement_pack_check.py` 57/57 through the deployed stack; protection on, diagnostics 404, production untouched. One HTTP 500 on the staging call was seen once while three different checks ran concurrently and did not reproduce alone or under three concurrent runs; runtime logs are not reachable through the REST token, so its cause is unexplained
- [x] Manual CSV acceptance by the author, passed on the pre-fix preview (`3a202ac`) on 2026-09-13 between 13:57 and 14:04 UTC: the five pack files confirmed sequentially into one account (recorded as Bank Acct, no last-four digits), 180 posted transactions from 2026-01-01 to 2026-08-31, five confirmed imports, statement opening and closing observations continuous across files, and a re-upload of the May file caught as an exact replay without increasing the count. The first attempt's review page had shown "HTTP 500" at 13:50 UTC because the workspace read behind it hit a transient Data API gateway timeout (ADR 0008 amendment); the staged import was confirmed after a reload. The fixes in `c603625`..`cd4a543` were verified afterwards by automated checks only (a scripted browser replay of the same path with the January–April file for a temporary user, and the import flow check through the preview); the author has not yet exercised the fixed build manually

Gate: synthetic fixture and its CSV upload produce identical deterministic metrics (no metrics exist yet; the canonical rows are identical in shape and the pack reconciles); correction flow works; unsupported files return clear feedback.

## M7. Text-PDF statement imports with page evidence

Implemented 2026-09-13 on `feat/m7-pdf-imports` (cut from `main` at `8c5b86c`); decisions in [ADR 0009](../decisions/0009-pdf-statement-imports.md). Migrations 0009, 0010, 0011 and 0012 applied to the development project; production untouched and still gated off. Reuses the M6 pipeline end to end and adds a second deterministic parser.


Status of the plan's items:

- [x] Supported inputs and refusals as planned; the format is detected from bytes; refusal codes `not_pdf`, `pdf_malformed`, `pdf_encrypted`, `pdf_no_text_layer`, `too_many_pages`, `extraction_text_too_large`, `unsupported_document_type`, `pdf_layout_unsupported` (`app/imports/pdfparse.py`)
- [x] Dependency: `pypdf` 6.18.1 pinned (pure Python). Observed on the development machine: wheel 394 KB, import 122 ms, parse plus normalize 27 ms for the four-page pack file and 4 to 8 ms per single-page file. Deployed timing: see the preview item below
- [x] Deterministic extraction with page and line provenance, wrapped descriptions merged, repeated banners, headers and summary blocks ignored, statement metadata read independently for reconciliation; no OCR, no model calls
- [x] Reuse of the private lifecycle: same routes, `format = pdf`, `.pdf` object paths, `application/pdf` on the bucket, `import_rows.page_number`, replay by hash, cancellation and cleanup unchanged
- [x] Ambiguity and reconciliation: existing flags plus `pdf_line_unparsed`-class blocking rows; reconciliation reported as matched, mismatch (named problems) or unavailable; nothing invented to balance
- [x] Cross-format duplicate policy corrected (ADR 0009): identity evidence links by default, shape evidence needs an owner decision, one-to-one pairing preserves repeated payments, confirmation refuses undecided rows in the API and in the database, bulk decisions, reference lending when a referenced row is linked
- [x] Lease protection extended (ADR 0009): `stage_import` and `fail_import` verify the job, token and expiry inside the same transaction as their writes; a stale worker's staging, state change and object deletion are refused; 100 s processing budget inside the 180 s lease and the 300 s function limit
- [x] Web: PDF accepted with the notice updated, printed statement figures and reconciliation shown, page and line on every row, explicit per-row and bulk decisions, confirmation disabled while rows need a decision, page shown in evidence
- [x] Fixtures beyond the pack (`fixtures/statements/`, generated and checked in CI): the pack layout with wrapped lines and identical payments across two pages, a debit/credit layout with US dates and footer balances, a layout without balances and an unreadable amount, a missing-line file, a wrong-closing-balance file, an injected-text file, and refused files (credit card, 21 pages, image-only, encrypted, truncated). Coverage is this fixture list; no real bank format has been validated
- [x] Tests: 148 API tests (extraction over every fixture, continuation and page-footer handling, matching policy, PDF and cross-format route flows in both orders, repeated payments, refused decisions, cancellation state transitions, stale-lease staging refusal, deadline)
- [x] Development project through the running API with temporary resources, all deleted afterwards: import flow check 53/53 (PDF isolation for another user, stale worker cannot stage or fail, lease tokens); the five pack PDFs sequentially 72/72 (per-file totals and printed balances reconciled, cumulative 88, 110, 133, 157, 180, one account, page and line provenance, replay refused); CSV then PDF and PDF then CSV of May 20/20 and 22/22 (every row a candidate, confirmation refused until decided, one action links all, zero new transactions, both files as evidence); the five pack CSVs still 62/62
- [x] Migration 0010 fixed a runtime defect in 0009's `confirm_import` (uncast enum) found by these checks before any preview existed
- [x] Migration 0012 closed the review's database findings: the cross-format reference index, the widened lending check with a per-account advisory lock, and atomic cancellation. Verified by `db/checks/cancellation_race_check.py` 19/19 and `db/checks/import_flow_check.py` 54/54 on the development project, with the five pack PDFs still 72/72 and the cross-format runs 20/20 and 22/22
- [ ] Downloadable synthetic PDF sample through the same upload path: not added (walkthrough milestone)
- [x] Preview on the M7 branch with branch-scoped variables (2026-09-13, commit `da3eaad`): protection on, diagnostics 404, production untouched. Through the deployed proxy with temporary resources: import flow check 53/53, the five pack PDFs 72/72 (cumulative 88, 110, 133, 157, 180; replay refused), cross-format CSV then PDF 20/20 and PDF then CSV 22/22. A scripted WebKit session selected the May pack PDF, uploaded it from the page to Storage, saw the printed statement card and "Reconciles", 22 rows with page and line, confirmed 22 transactions, saw them in the table and opened evidence naming the PDF, row, page and line. Observed round trips for the four-page pack PDF through the proxy: upload to Storage 0.3 s, extract and stage 1.5 to 1.7 s, confirm 1.3 s (single samples, not a benchmark)
- [x] Limited workspace selection so a second private business can be tested without another signup (ADR 0006 amendment, migration 0011): keyed additional-business provisioning that is idempotent under retry, `businesses` on `/api/me`, an optional `workspace` on the private workspace route resolved by the existing resolver with no fallback, authoritative import location, and `?workspace=` on the workspace and upload pages with the business name shown. Verified by `db/checks/workspace_selection_check.py` 21/21 on the development project. No business switcher
- [x] Manual PDF acceptance by the author on the M7 preview (2026-09-13, head `68729dc`), as reported by the author and corroborated by a read-only look at the development project:
  - [x] The January to April pack PDF was uploaded, reviewed, reconciled and confirmed under the author's development login, into their original business (Gift Genie, new account "Operating"): 88 rows, none needing attention, printed opening $30,000 and closing $55,328, reconciliation matched, 88 transactions added (import `3bbc913b`, confirmed 16:40:53 UTC)
  - [x] Cross-format conflict detection was observed manually: the same PDF uploaded into the CSV-populated workspace under the second login stayed in review with all 88 rows as duplicate candidates needing a decision, and the statement marked as probably already imported (import `beec9206`). Nothing was linked or added. That workspace still shows 180 posted transactions and five confirmed CSV imports, and the pending import is preserved as it is
  - [ ] Completing the link-to-existing decision by hand: not reported. Both orders are covered automatically by the cross-format pack checks
  - [ ] Evidence panel inspection by hand (page and line shown after the file is gone): not reported. Covered automatically by the import flow check and by a scripted browser session on the preview
  - The remaining monthly PDFs are deliberately unimported: the author is keeping May to August for later trend demonstrations. The five-file sequence is covered automatically against the development project (72/72), so no manual repeat is needed
- [x] Pre-merge review of the diff against `main` (2026-09-13, head `68729dc`, 14 commits, 54 files; automated evidence reused, one targeted extraction probe added): duplicate identity and reference scope, one-to-one matching, lease ownership across database and Storage steps, extraction and independent reconciliation, provisioning idempotency and membership checks, workspace selection and import ownership, production gates. Production gates and deployment automation unchanged; among those files only `ci.yml` changed, adding the fixture check. Checked and found sound: identity links only on a trusted reference in the same account, shape matches always need a decision, one-to-one pairing keeps repeated payments, staging and failure verify the lease inside the writing transaction, a lease lost inside the failure path returns the 409 lease message without deleting the object, keyed provisioning resumes the same business under retry, and no workspace falls back to another. No blocking finding.
- [x] The review's findings fixed on the same branch (migration 0012; see [ADR 0009](../decisions/0009-pdf-statement-imports.md)):
  - [x] Page footers and other lines below the table no longer join a transaction's description. A continuation must sit on the same page, directly under the line it continues, within a multiple of the line spacing measured in that document, and text that repeats in the same place on more than one page is treated as page furniture. A line whose date, description and amount columns are all filled but unreadable is now kept and flagged instead of dropped, and a page carrying transaction-like lines with no column header is refused rather than silently skipped. New fixture `layout-a-page-footers.pdf` (footers printed inside the description column on every page) plus five unit tests; the fixture reproduces the defect against the previous parser. The five pack PDFs and all committed fixtures extract byte-identically to before the change
  - [x] One source reference identifies at most one imported transaction per account whatever format it arrived in, enforced by a partial unique index rather than by checks alone. Reference lending uses the same scope as the insert check, and confirmations into one account are serialized by an advisory lock so those checks decide the outcome instead of racing. Existing data was inspected read-only first and held no violation, so nothing was rewritten
  - [x] "Keep as new" is refused for a row whose reference the account already holds, per row and in bulk, with a message naming the rows; the option is not offered in the review page. Link, replace and exclude are unchanged, and the database still refuses the write on its own when the review carried no flag
  - [x] Cancellation is one database transaction (`cancel_import`) that ends the import, invalidates any lease still open and removes the staged rows, so a parse that returns afterwards is refused. Cancelling again is safe and retries a file removal that failed; a failed removal stays recorded as not done. Confirmation and cancellation cannot both succeed
  - [x] The workspace selection is carried by the upload page's error links and the header link, and a link naming an unusable workspace is reported as unavailable on both pages instead of falling back to the default business, matching what the API answers
  - [ ] Remaining limitation, not fixed: on a statement of one page, a footer printed one line below the last transaction, inside the description column and at the table's own line spacing, is still indistinguishable from a wrapped description by position and would be appended to that transaction. The repeated-position rule that catches footers only applies from two pages up. No fixture or pack file prints one there
  - [ ] Remaining limitation, not fixed: supported PDF layouts are exactly the committed synthetic fixtures plus the test pack's layout. No real bank export has been validated, and each new layout needs its own fixture before support is claimed
  - [ ] Open question, unchanged: parsing time on an adversarial PDF is bounded by the 10 MB file limit, the 20 page limit, the 500 KB extracted-text limit and the 300 second function limit. The 100 second processing deadline is checked between pages and between phases, so it does not interrupt extraction inside a single page. No pathological file has been measured. Carried into the M8 security and cleanup review with per-page processing isolation, resource exhaustion and lease expiry

Scope and supported inputs
- Supported at first: text-based bank statements for one USD checking or savings account per file, where the text layer yields one transaction per line (date, description, amount or debit/credit, optional running balance) with continuation lines for wrapped descriptions. The first fixture family is the pack's layout (ReportLab-generated, Helvetica, 1 to 4 pages, header lines for period, opening and closing balances). Support is claimed only for layouts covered by committed synthetic fixtures.
- Refused with an explained, safe error code, never guessed: encrypted or password-protected files (`pdf_encrypted`; the owner removes the password in a viewer and re-uploads; passwords are never accepted), files with no usable text layer such as scans and photographs (`pdf_no_text_layer`; OCR is not offered), more than 20 pages (`too_many_pages`), more than 500 KB of extracted text (`extraction_text_too_large`), documents that read as credit-card statements, invoices or spreadsheets (`unsupported_document_type`, keyword and structure heuristics), and text layouts from which no transaction line can be read (`pdf_layout_unsupported`, showing the first extracted lines so the owner can see why). Type detection is on bytes (`%PDF` header and structure), never on extension or MIME.
- No model calls and no OCR in M7. Bounded structured AI extraction for ambiguous layouts is a separately identified, paid path deferred to M10, behind its own flag, with quota reservation, identifier masking, disclosure in the upload notice and prompt-injection tests; it is not part of this milestone.

Deterministic extraction
- Library: a pure-Python extractor with word positions (`pdfplumber` on `pdfminer.six`, pinned in `requirements.txt`; `pypdf` as the fallback if build size or cold start on the preview measures badly). The choice, the measured cold start and the per-page extraction time on the preview go into ADR 0009 with the first commit.
- Steps, all pure and versioned as `pdf-1`: words with coordinates per page; lines by vertical position; the header line (Date, Description, Amount or Debit and Credit, Balance) gives column ranges; words assigned to columns by position; lines without a date or amount merged into the previous description; statement metadata (period, opening balance, closing balance, account last four) read by regular expressions from the header area; dates and money parsed by the CSV module's `parse_date` and `parse_money`; running-balance continuity by the CSV module's balance check. Output is the same `NormalizedRow` shape, so `normalize`, `summarize`, matching, staging, review and `confirm_import` are reused unchanged.
- Ambiguity stays reviewable: a date column that parses as month/day and day/month, a sign convention that no running balance verifies, an amount column that could be debit or credit, rows whose amount could not be read, and pages where the column ranges could not be found all produce the existing flags or new ones (`pdf_column_unresolved`, `pdf_line_unparsed`) and block or warn exactly as CSV flags do. Reconciliation: opening plus credits minus debits must equal the closing balance read from the statement text; a mismatch is `statement_totals_mismatch` (warning, shown with both figures); a balance chain with breaks suggests missing or misread rows and is flagged per row; a statement that carries no balances says so.
- Processing limits within the deployment: 10 MB per file (bucket and server), 20 pages, 5,000 rows, 500 KB of extracted text, extraction bounded to 60 s per request (`extraction_timeout`, retryable within the existing three-attempt lease), one import in progress per business, 180 s leases inside the 300 s function limit.

Reuse of the private import lifecycle
- Same create, signed one-path upload, uploaded, stage, review, confirm and cancel routes; `document_imports.format` gains `pdf`, `object_path` allows `.pdf`, the bucket allows `application/pdf` (migration 0009, guarded like 0006), and `import_rows.page_number` is added. The parse step dispatches on detected bytes; everything after normalization is shared. Exact-file replay by content hash, revisions, leases with tokens, object deletion at confirmation, cancellation and failure, and the 24-hour expiry are unchanged.
- Provenance: every staged row records `page_number`, `source_locator` as `page N line M`, and the line's extracted text in `raw`; evidence panels show "Statement <file>, page N, line M" with the extracted text, and that remains the evidence after the object is deleted.
- Cross-format overlap: a PDF usually has no per-transaction reference, so rows matching existing transactions by date, amount and description are `duplicate_candidate` rows as today. New statement-level signal: when most rows of a file are candidates and its period overlaps confirmed imports for the same account, the summary says the statement appears to be already imported (for example from a CSV export) and the default decision for candidate rows becomes link-to-existing, so confirming adds provenance and no new transactions; the owner can still keep rows as new. Never merged automatically; never across accounts or businesses.

Web
- The upload page accepts `.pdf` with the notice updated (text-based statements only, scans refused, nothing sent to an AI model). The review page shows the statement metadata as read, the reconciliation result, page and line locators on every row, a per-page "show extracted text" toggle for uncertain rows, and the same correction and decision controls; the mapping form offers date format and sign choices without column selects.

Fixtures and tests
- Deterministic synthetic PDF fixtures written by a small in-repo writer (uncompressed text objects, no new dependency) under `fixtures/statements/`: the pack layout reproduced from the provider fixtures, plus at least two further layouts (debit and credit columns; a summary block before the table with wrapped descriptions across a page break), and malformed files (image-only page, encrypted marker, no text layer, credit-card style, more than 20 pages, truncated file, a description containing instruction-like text that must stay text). Known-answer expectations sit beside them, outside application context.
- Unit tests: layout detection, continuation merging, metadata reading, every refusal code, reconciliation mismatch, ambiguity flags, injection text, bounds and timeouts. Route tests reuse the M6 stand-in with a PDF upload. Checks on the development project on isolated temporary resources only: the five pack PDFs sequentially into a temporary business (never the author's CSV-populated account), the same expected results as the CSVs; CSV-then-PDF and PDF-then-CSV of one month with no double count by default; isolation reused from the import flow check. Coverage is reported as the list of fixture layouts that pass, with extraction accuracy measured on those fixtures, not as bank support.

Proposed commits
1. `feat(db): accept pdf statement imports and keep page provenance` (migration 0009).
2. `feat(api): extract text-PDF statements deterministically with page and line provenance` (parser, refusals, dispatch, ADR 0009 with measured timings).
3. `feat(api): review cross-format overlap between CSV and PDF statements` (statement-level signal and default link decision).
4. `feat(web): upload and review text-PDF statements with page evidence`.
5. `test(db): verify PDF statements, cross-format overlap and unsupported files on the development project`.
6. `docs: record text-PDF import decisions and coverage`.

Acceptance
- The five pack PDFs import sequentially into an isolated business with the same counts, totals and balances as the CSVs (180 rows, balances continuous); re-uploading a file is refused as a replay.
- A CSV and the PDF of the same month never double-count by default, in either order; the owner can override per row.
- Every imported row carries page and line provenance and the evidence panel shows it after the file is gone.
- Scanned, encrypted, oversized, over-length and non-statement files are refused with the stated reasons; instruction-like text inside a description remains text.
- No model calls; production unchanged and still gated off; the deployed preview browser flow works with a pack PDF for a temporary user.

Manual service setup before M7: none. No new hosted service, purchase or credential is needed; the bucket's MIME allow-list changes through the tracked migration on the development project. The test pack stays outside the repository.

## M8. Duplicate resolution, atomic confirmation, quotas and the privacy gate

Designed 2026-09-13 on `docs/m8-privacy-design`; the plan is
[CashLens M8 privacy and retention plan](CashLens-M8-Privacy-and-Retention-Plan.md)
and the decisions are [ADR 0010](../decisions/0010-retention-deletion-and-processing-limits.md).
Three bounded tasks: retention and the sweep, owner-controlled deletion, then
processing safeguards.

Task 1 implemented 2026-09-13 on `feat/m8-retention-cleanup` (migration 0013), unmerged:

- [x] Retention windows, one per class: an upload that never reached review ends 24 hours after creation (`expires_at`); an import awaiting review ends seven days after it FIRST reached review (new `staged_at`, stamped once and never advanced, so viewing, re-mapping or retrying an import cannot extend its retention); confirmed transactions and their provenance never expire; a source file for a confirmed, cancelled or failed import follows the existing lifecycle and a failed removal is retried
- [x] Existing rows backfilled from `created_at`, the only immutable evidence available and a lower bound on staging time. `updated_at` was rejected because it advances when a row is reviewed, which would let looking at an import extend its retention. No row's age was reset to deployment time
- [x] `end_import` factored out of `cancel_import` so expiry and the owner's own cancellation share one implementation, including M7's lock order, lease invalidation and row removal. A parse that returns after an expiry is refused exactly as it is after a cancellation
- [x] Bounded, idempotent sweep: batch limits carried into SQL rather than fetched and sliced in Python, an advisory lock so two runs cannot interleave, and an optional business or visitor narrowing that filters on top of the retention conditions and never instead of them
- [x] Storage outcomes tracked separately from database transitions: `object_delete_attempts`, `object_delete_attempted_at` and `object_delete_error`. `object_deleted_at` is set only after Storage confirms, so a failure stays eligible and visible and is never reported as deleted
- [x] `visitors.last_seen_at` maintained by `touch_visitor`, throttled in the database to one write an hour per visitor, before any idle rule can mean anything. Idle visitors expire at 30 days; their demo workspaces cascade and the shared demo businesses are untouched
- [x] Cleanup behind its own switch. Reusing `OPS_DIAGNOSTICS_ENABLED` was inspected and rejected: it gates seven routes including a database probe and a paid model probe. `POST /api/maintenance/retention` uses `RETENTION_CLEANUP_ENABLED` and `RETENTION_CLEANUP_TOKEN`, both off by default, 404 when unset and 401 on a bad token
- [x] The opportunistic sweep in `create_import` removed: expiry is a deliberate bounded operation, not a side effect of someone else's upload
- [x] Verified: 156 API tests, and `db/checks/retention_check.py` 27/27 on the development project with temporary users, businesses, files and visitor sessions, all deleted afterwards. `db/checks/import_flow_check.py` still 54/54
- [ ] **No schedule exists and none is enabled.** Activation is the owner's, in plan section 4.6: set the branch-scoped preview variables, run it by hand narrowed to a temporary business, and only then consider `pg_cron`. A schedule must point at a stable alias, never a deployment URL or a feature branch's alias, or it stops silently when the branch moves

Task 2 implemented 2026-09-13 on the same branch (migration 0014), unmerged:

- [x] Deletion authority recorded explicitly in `businesses.created_by`, because `membership_role` has exactly one value and a membership therefore says only that someone belongs to a business. Backfilled only where a business has exactly one membership; ownership is never taken from the first row a query returns
- [x] Refusals, all server-side: no membership 404, a member who did not create it 403, more than one membership 409 (roles and invitations do not exist, so one member must not destroy another's data), and a demo business 404 on top of the `guard_demo_rows` trigger that already blocks INSERT, UPDATE and DELETE on `businesses`. The owner also types the business name and the server checks it
- [x] One idempotent request: authorize, set `deletion_requested_at` (which refuses every scoped read and write from that moment), copy every remaining file path into `storage_cleanup` **before** anything cascades, end imports in flight through `end_import` so an open lease is invalidated, then delete transactions first and imports after, because `transaction_sources` restricts the rows behind them
- [x] `storage_cleanup` has no foreign key on purpose: the paths live only on the rows deletion removes, so a queue that cascaded with them would lose exactly what it tracks. A failed removal stays retryable after the business and import rows are gone
- [x] The business row survives as a tombstone until its last file is confirmed gone; a cleanup run then finalizes it. Nothing is reported finished early, and the status route answering 404 is what finished means
- [x] Provisioning never resumes or key-matches a business awaiting deletion, so asking again creates a new one instead of reviving it
- [x] Web: a deletion panel in the private workspace naming the business and what goes, requiring the typed name, showing removing, retrying and completed states accurately, and navigating to a remaining business or first-business onboarding afterwards rather than back to the deleted workspace
- [x] Verified: 158 API tests and `db/checks/business_deletion_check.py` 29/29 on the development project, with `retention_check.py` 27/27, `workspace_selection_check.py` 21/21 and `import_flow_check.py` 54/54 still passing. Temporary users and businesses deleted afterwards
- [ ] Auth-account deletion is deliberately **not** included: `businesses` has no user foreign key, so removing the auth user would orphan the business and everything in it. It needs its own ordered design


Task 3 designed and measured 2026-09-13 on the same branch, **not implemented**:

- [x] Extraction boundaries traced in pypdf 6.18.1: `extract_text` decompresses the content stream and tokenizes it into operations before the first visitor callback. Measured on a synthetic page, 72% of the time ran before any callback and 120,000 text operators produced 155 callbacks, so a visitor-level deadline is a cooperative improvement and not a hard bound
- [x] Signals ruled out with evidence: the import route is a synchronous handler that Starlette runs on an AnyIO worker thread, where `signal.signal` raises `ValueError: signal only works in main thread of the main interpreter`. A thread cannot be killed either
- [x] Decompression memory is already bounded by pypdf during decompression, not after: `zlib_maximum_output_length` is passed as `max_length` to `zlib.decompressobj().decompress`. Default 75 MB, configurable, verified refusing at 256 KB. M8 lowers it and scopes it around extraction
- [x] Process isolation proven feasible by one bounded capability check on the protected preview against a synthetic allocation and spin, since removed: `fork` available, `RLIMIT_AS` raised `MemoryError`, `RLIMIT_CPU` killed a spinning child at 1.01 s, and a parent kill stopped, reaped and left no pid after 1.0 s
- [x] Decision recorded: extraction moves into a forked child with address-space and CPU limits, a lowered decompression limit and a parent wall-clock kill. Quotas become database-backed and advisory-locked, never process-local
- [x] Implemented 2026-09-13 (migrations 0015 to 0017). Extraction runs as a fresh interpreter, not a fork: inspecting the real path found the request on an AnyIO worker thread with a module-cached HTTP pool lock shared across threads, which a fork would copy in its locked state. The child gets address-space, CPU and decompression limits and an environment carrying no credential; the parent enforces the wall clock and kills and reaps on timeout and every failure path
- [x] Quotas are database-backed and atomic: the import row is the reservation and is inserted in the same transaction as the counts. Three active imports per user across businesses, ten starts an hour, forty a day, 50 MB outstanding, three attempts per job, all configurable and all exact. `import_starts` has no foreign key to businesses, so deleting one cannot reset a rate limit; an exact replay withdraws its initiation and ordinary cancellation does not
- [x] Two defects were caught by the development check before any preview: `min()` on a uuid column made every reservation fail (0016), and checking before inserting let two concurrent reservations both take the last slot (0017)
- [x] Verified: 168 API tests including real child processes, `db/checks/quota_check.py` 14/14, the five pack PDFs still 72/72 through the isolated extractor, import flow 54/54, deletion 29/29, retention 27/27, migrations from empty 33/33
- [x] Closing fixes from the pre-merge review (2026-09-13): a resource refusal (timeout, busy, unavailable, exhausted) no longer destroys the owner's upload — it answers 503 retryable and leaves the file in Storage with the import still `awaiting_upload`, which the previous code deleted while telling the owner nothing had changed; an exact replay no longer refunds its rate-limit initiation, which could be farmed in a loop with a known-duplicate file; the child's stderr is discarded rather than captured unbounded into the parent; and a deletion with nothing left to remove now finalizes in the request instead of waiting for a cleanup run that is not scheduled

Remaining limitations, recorded and deferred rather than fixed:

- [ ] `RLIMIT_AS` bounds address space rather than resident memory; the limits are per child, so a per-instance cap bounds concurrent extraction and nothing coordinates across instances; a parent killed without running its cleanup orphans its child, whose own CPU limit is then the backstop; a platform that refuses a limit falls back to the wall clock and says so
- [ ] No malicious PDF corpus has been run. The evidence is synthetic files, so extraction robustness is claimed only against those
- [ ] The extraction child is a resource boundary, not a containment boundary: it runs as the same OS user with the same filesystem and network reach as the parent
- [ ] Deleting a business releases its outstanding-byte reservation while the files are still in Storage, because `storage_cleanup` carries no size. A user could delete a business to free the 50 MB ceiling early. Bounded by the per-file and per-business limits; the fix is a size column on `storage_cleanup`
- [ ] The per-business "one import at a time" check is a read outside the reservation transaction, so two simultaneous creates in one business can both pass it. The per-user active limit still holds, so at most three exist
- [ ] A signed upload token issued before a deletion stays valid for two hours, so a file can land in Storage after the queue was drained and be orphaned
- [ ] `storage_cleanup` rows are stamped rather than removed when their object goes, and `import_starts` rows are pruned only when that same user uploads again, so both tables retain rows for accounts that go quiet
- [ ] A transient upstream failure part-way through a cleanup run aborts the run; work already done is not reported back
- [ ] `storage_delete` treats any non-error response as success without checking that the object was actually present
- [ ] A `GET` on the cleanup route answers 405 rather than 404 when cleanup is disabled, which reveals that the path exists
- [ ] The deferred Vercel-cron plan cannot drive this route as built: cron issues a GET with no body and no custom header, while the route is POST with a JSON body and an `X-Cleanup-Token` header. Any future cron needs a GET variant first

- [ ] **Automated retention is not operational.** There is no schedule, and enabling one is a deliberate step that has not been taken. Until a cleanup run actually happens, an abandoned upload holds its quota slot and its bytes for the full retention window, and an expired import stays eligible rather than removed. Nothing in the product claims otherwise

Proposed commits: `feat: confirm imports with duplicate review and reconciliation`, `feat: add import quotas and private data cleanup`

- [ ] Identical file hash within business/account returns the existing import
- [ ] Duplicate candidates across overlapping statements presented for owner decision: keep both or link to existing; provenance preserved for linked evidence
- [ ] Never deduplicate across businesses; heuristic matches never shown as certain
- [ ] `confirm_import`: reviewed revision check, authorization, idempotency, atomic publish; failed confirm inserts nothing
- [ ] Committed revisions invalidate dependent metrics and briefs
- [ ] Balance and period reconciliation with contradictions flagged; a balanced total is a check, not proof
- [ ] Tests: repeat confirmation, revision conflicts, overlap ambiguity, repeated legitimate payments, retry after failure without duplicate commits

Carried forward from the M7 review. Each now has a designed answer in the M8
plan; none may be marked resolved until the code exists and its checks have run:

- [ ] Per-page processing isolation: the 100 second deadline is checked between pages and phases, so it does not interrupt extraction inside a single page. Designed answer: move the deadline, the extracted-text cap and a chunk cap into the pypdf text visitor, which already fires per chunk (plan 3.3), plus one time-boxed feasibility check for a hard in-process bound (plan 3.4)
- [ ] Resource exhaustion under adversarial input: no pathological PDF has been measured, and a file-size limit does not bound CPU or memory. Designed answer: a decompressed content-stream budget per page, chunk caps, and new fixtures for a compression bomb and an extreme chunk count (plan 3.3, 3.5)
- [ ] Lease expiry behaviour: staging, failure and cancellation already verify the lease inside the writing transaction, and `_fail` calls the database before deleting the file, so a worker that lost its lease can neither write nor delete the source. What remains is that such a worker keeps consuming CPU until it returns or the platform terminates it at 300 seconds. Designed answer: keep the mechanism, add regression coverage, record the cost as a limitation (plan 3.6, 3.7)
- [ ] Expiry sweep: `expires_at` is recorded but only swept when the same owner starts another import. Designed answer: batched, idempotent sweep functions behind the existing operator token, triggered by `pg_cron` in development, because Vercel cron jobs target production deployments only and production must stay a credential-free scaffold (plan 4)
- [ ] Note: `visitors.last_seen_at` is never updated today, so any idle-based rule for anonymous sessions is inert until M8 maintains that column
- [ ] Note: deleting an auth user orphans their private businesses, because `businesses` has no user foreign key. Account deletion is deliberately outside M8 and needs its own ordered design

Privacy and cleanup gate (before any real public upload):

- [ ] Server-enforced limits confirmed from the M2 spike: file bytes, PDF pages, CSV rows, one job per user, extraction text, model spend
- [ ] Original-file retention: delete within 24 hours after confirmation; abandoned or failed files expire within 24 hours; requires the verified scheduled mechanism
- [ ] Evidence links show retained normalized rows and page references once files expire, not a broken preview
- [ ] Delete my data: files, staging, transactions, classifications, overrides, snapshots, insights, scenarios, AI payloads removed; pending jobs cancelled; caches cleared; only non-content operational status retained; provider backup retention described separately
- [ ] Upload notice: private storage, external AI processing, supported inputs, deletion
- [ ] Logs, analytics and CI fixtures contain no statement content, descriptions, amounts, storage URLs or private screenshots
- [ ] Tests: budget exhaustion, expiration, cancellation, deletion during an active job, complete removal verification

Gate: atomic commit, bounded retries, overlap handling and balance checks pass; isolation, quotas and cleanup verified end to end before any real upload is invited publicly.

## M9. Deterministic financial tools

Implemented 2026-09-14 and integrated into `main`. Definitions and limits are in
the [financial methodology](CashLens-Financial-Methodology.md); the calculation lives in
`services/api/app/analysis/` and the interface in `apps/web/src/components/cash-dashboard.tsx`.

- [x] A cash dashboard shown by the same component in demo and private workspaces, using the existing authorisation and workspace selection: latest known balances per account with their source and date, money in and out by month with a labelled chart, spending grouped by category and activity, this month against last, likely recurring payments, a 30-day projection and a baseline-versus-scenario comparison
- [x] Money is integer minor units end to end. Only posted, not-removed transactions enter a total; pending rows are reported separately. Provenance rows are never counted as transactions
- [x] Live bank balances and historical statement balances stay distinct, a statement closing balance is never presented as today's cash, available is never added to current, and balances from different dates are labelled latest-known with mixed dates
- [x] A month that has not finished by the analysis date is marked a part month and the comparison card warns before comparing it with a full one. Net cash flow is described as cash movement, not profit
- [x] Deterministic keyword categorisation, published in full to the owner. Unknown descriptions stay uncategorised rather than being guessed. Financing and owner draws are separated from operating activity, matched internal transfers are netted out of consolidated money in and out while still counting in total cash movement, and a transfer leg with no match is left in the totals
- [x] A 30-day projection from a dated anchor, with recurring payments placed on their next expected dates and removed from the flat everyday rate so nothing is counted twice. Inflow and outflow rates are kept apart so the receipts control scales what actually arrives. Too little or too stale history refuses with reasons instead of projecting
- [x] Three scenario controls: receipts by a percentage, an expected payment delayed by days, and a one-off cost. The delayed payment takes its amount and date from the owner, because bank history is not evidence of an unpaid invoice. Results report ending cash, the lowest point and its date, the difference from baseline and the difference in the low point, plus every assumption
- [x] Scenarios never write. They are computed per request for one workspace from the same read-only inputs, asserted by test and by the development check
- [x] An explicit analysis date, defaulting to the latest available data, with nothing after it ever read, so a historical walkthrough reports what was knowable then
- [x] Category rows carry the transaction ids behind them so a total links to its transactions through the existing views. Every request reads the database directly, so a confirmed import changes the dashboard with no reseed and no cache to invalidate
- [x] Typed, versioned responses (`analysis-1`, `cash-1`, `rules-1`, `forecast-1`) that M10 can explain without recomputing. No model calls and no OpenRouter in M9
- [x] Verified: 190 API tests, 19 of them known-answer cash arithmetic written out by hand, plus `db/checks/analysis_check.py` 18/18 on the development project over the whole statement pack. It confirms May's suppliers rising while receipts hold flat, June's equipment purchase with weaker receipts, July's financing lifting cash, and August's recovery; that an earlier analysis date sees no later month; that scenarios move the projection and no money; and that another user and an anonymous caller are refused. The reviewer-only expectations file is used as test expectations only and never reaches the application

- [x] Manual acceptance by the author on the M9 preview (2026-09-14, head `a23942c`), reported by them and reproduced exactly from the same code: Gift Genie showed 88 transactions and the 30 April statement balance of $55,328; the January to April monthly totals and the baseline projection matched; entering −20% customer receipts and submitting drew baseline and scenario on the same chart, ending at $57,289, a difference of −$7,182, with the lowest cash $53,038 on 3 May. All three scenario figures were re-derived from the committed code and match to the dollar
- [ ] Not manually reported, kept separate from the automated evidence: resetting a scenario back to the baseline, and navigating from a total to its supporting transactions. Both are covered automatically, the first by the scenario tests and the development check and the second by the existing evidence views, but neither has been confirmed by hand

Later interface polish, agreed as non-blocking for the merge:

- [ ] Dollar labels on the monthly chart's axis. Today the net figure is printed above each bar and the exact totals sit underneath, so no number is hidden, but the bars themselves carry no scale
- [ ] Clearer wording in the grouping section, which currently leans on the category names alone

Limitations, recorded rather than fixed:

- [ ] Cash only. No accrual accounting, profit, balance sheet or tax treatment, and equipment is reported as a cash outflow rather than capitalised
- [ ] The everyday rate is flat: no seasonality, day-of-week effect or growth is modelled
- [ ] Categorisation is keyword rules alone. Coverage is whatever the published list matches, and a new description stays uncategorised until a rule is added deliberately
- [ ] Scenarios are assumptions, not probabilities. Nothing here is a confidence interval

Gate: a worked example verified by hand; shifting receipts changes dates but preserves amounts; sources and assumptions are inspectable.

## M10. AI classification, briefs, scenarios, OpenRouter and AI evaluation

Designed 2026-09-14 on `docs/m10-ai-design`, and the first vertical slice built on `feat/m10-ai-insights`: [M10 AI design](CashLens-M10-AI-Design.md) and [ADR 0011](../decisions/0011-grounded-ai-and-provider-boundary.md). Three stages, each ending where a reviewer can stop.

**Status, reconciled 2026-09-14 at `a9996ff`.** Four levels, kept apart because they are different kinds of evidence:

| Level | What it covers | Evidence |
| --- | --- | --- |
| **Implemented and unit-tested** | `rules-2`; candidates, `classify-1`, suggestions, decisions and overrides applied in `analysis-2`/`cash-2`; the fingerprint; `brief-2`; scenario explanations (`scenario-explain-4`) with the `partial` status; four validity layers with cross-fact figure references, money-only raw-number check, declared percent fields, scenario-scoped causal permission; reservations with daily and milestone caps; run records with routing; the OpenRouter adapter and the demo-only route with no fallback; the reviewer model choice | `services/api/tests/test_ai_*.py`, **357 tests**, hosted CI green |
| **Tested against the local API and the development database** | The whole classification, brief and scenario flow on a throwaway demo business; the private-business refusal of `route=openrouter` on all three capabilities with zero reservations and zero runs, against a server with OpenRouter fully enabled; `ai_usage_check.py` 16/16; migrations 0018–0022 applied to `<project-ref>` | `db/checks/model_comparison.py`, `db/checks/ai_usage_check.py`, `db/checks/classify_dev_cases.py`, 2026-09-14 |
| **Verified on the deployed preview** | The API branch alias serves health, the demo list, a demo session, a workspace, the analysis, `ai/routes` (direct only, as configured), and read-only brief and suggestion lists — through the API's protection bypass, which is held on the **web** project as `API_PROTECTION_BYPASS` for every preview. Without it both aliases answer 302 to sign-in. The earlier note that "no bypass secret is configured" was wrong: it looked only at the API project's branch-scoped variables | `cashlens-api-git-feat-m10-ai-insights`, 2026-09-14 |
| **Not verified on the deployed preview** | The web preview → proxy → API path, and any generation through it. The web project's *own* automation bypass is a project-settings value the CLI cannot read, so this needs the owner: either open the web branch alias signed in, or copy that secret from Vercel → `cashlens-web` → Settings → Deployment Protection into the local shell as `WEB_BYPASS` (never into the repository) and re-run the M2 recipe in ADR 0004 | — |
| **Manually verified in a browser** | The owner observed a simulated brief render on Juniper Kitchen (earlier). Evidence links, the run trace, the review card, the scenario explainer, the model selector and the `partial` state have **not** been reported as clicked | — |
| **Live-provider-verified** | `gpt-5.2-2025-12-11` direct: brief, one clear and one ambiguous classification, a reduced-receipts scenario and a delayed-payment scenario, three rounds on frozen synthetic inputs, all four succeeding in the final round; the delayed-payment explanation went from rejected outright to 5 of 5 claims with no coverage gaps. OpenRouter: three live calls have since been made from a local API on `qwen/qwen3.8-max-0902` (two `brief`, one `ask`), **all three returning `invalid_output`** for $0.102118 in total; this row predates them | `local-data/model-comparison-*.json` (git-ignored), 2026-09-14 |

Model: **`gpt-5.2-2025-12-11`** direct, `reasoning_effort=low`, 1,600-token output allowance locally (4,000 on the preview). Comparison model on OpenRouter: `qwen/qwen3.8-max-0902`, chosen for verified `structured_outputs` and a single serving endpoint at $2.00/$6.00 per million, recorded dated in `services/api/app/ai/prices.json` and sent as the routing price ceiling.

**Spend ledger, read from `ai_milestone_spend()` on 2026-09-14.** $1.021771 spent of the approved $25, $23.978229 remaining, $0 held, nothing uncertain. Brief 11 reservations / $0.458964; classify 6 / $0.104529; scenario 15 / $0.458278. The five workflow judges made no provider calls (transcripts audited); the key in use lacks `api.usage.read`, so the ledger cannot be cross-checked against OpenAI billing from here; input tokens are priced at the full rate, never the cached discount.

Branch-scoped preview variables on `feat/m10-ai-insights`: the API has the development Supabase credentials, `AI_ENABLED`, `AI_PROVIDER=openai`, `AI_MODEL`, the reasoning and token settings, the three caps, `OPENAI_API_KEY`, `AI_OPENROUTER_ENABLED`, `OPENROUTER_MODEL`, `OPENROUTER_ALLOWED_MODELS` and `OPENROUTER_SITE_URL`; **`OPENROUTER_API_KEY` is not set**, so the route is refused there as `route_unconfigured` and the selector is not offered. The web has `API_ORIGIN` for the branch and `API_PROTECTION_BYPASS` for every preview. Deployment protection stays on, diagnostics stay off, production is untouched.

Pressing "Generate financial brief" is not a dry run: it reserves and dispatches. `python -m app.ai.estimate <workspace-id> --model <model>` builds the real fact sheet through the same code and prints the reservation that would be held, then stops — and refuses outright when the model has no recorded price.

**What the four validity layers actually establish.** They are not a semantic proof that a brief is correct. Precisely:

- **Schema** proves shape alone.
- **Evidence** proves every reference the model cited exists in the fact sheet it was given, and that a cited transaction belongs to the fact it was attached to. It does not prove the transaction is relevant.
- **Numbers** proves every quoted figure equals the value it points at, and that every dollar amount and percentage in the prose appears in the fact sheet. A figure written without a `$` or a `%` is not extracted and so is not checked.
- **Support** is a **keyword and phrase test, not comprehension**. It detects: a direction word contradicting the code-computed direction; a causal phrase on anything but a recorded data change; financing described with receipts vocabulary, or without financing vocabulary, or in the wrong direction; a statement balance called money in the bank today; an unfinished month compared with a full one; an estimate stated without a hedging word. It will pass a sentence that is fluent, on-vocabulary and still a poor reading of the business, and it cannot detect an omission. The judged layer that would address this is an evaluation-set measure and is not built.

Proposed commits: `feat: classify unplaced transactions with reviewable, evidenced suggestions`, `feat: generate validated financial briefs and plain-language scenarios`, `feat: compare providers on sealed cases and record the evaluation`

Stage 1: classification with provenance, and the run record
- [x] `rules-2`: counterparty tier so bare instrument words never place a row; `equipment lease` before `equipment`; contractors, repairs and maintenance, owner contributions; tier and reason codes published in the rules view
- [x] Candidates are only rows the rules could not place; bounded batches; computed context (recurrence, transfer hints, category counts, earlier owner decisions as facts); suggestions stored, never applied
- [x] Suggestion contract validated: ids in batch, allowed and direction-compatible category, evidence required for a suggestion, question or alternatives for unresolved; a failing item is dropped and the batch stands
- [~] `category_overrides`: append-only, workspace-scoped, composite `(workspace_id, business_id)` key, source and decider recorded, revision-bound, revert to rules; applied on read in `analysis-2` / `cash-2`; accept, reject and correct through the review card — apply-to-all with a batch id is not built
- [x] Input fingerprint computed on every analysis read; stored results labelled stale by comparison; open suggestions superseded by an override
- [x] Provider boundary: `ModelProvider.complete`, direct OpenAI, OpenRouter, replay, simulated and scripted adapters, no transport retries
- [x] `ai_runs` with the fact sheet and validated output, business-scoped with nullable workspace and import ids; reviewer route and the "How this was generated" disclosure; logs carry ids, counts and durations only
- [x] `usage_reservations` and `reserve_ai_usage` under global then identity advisory locks; per-identity and global daily cost caps, one in flight per identity, output-token cap, timeout, `AI_ENABLED` master switch — unit-tested and database-verified: two concurrent reservations grant exactly one, an unsettled hold keeps counting, and a cap of zero refuses before anything is held. The hold covers the whole bounded operation including the repair call, and a model with no recorded price is refused before dispatch rather than called at an assumed one
- [x] Deletion removes AI rows before transactions; visitor sweep cascades them; demo cache rows swept by age and prompt version — database-verified for deletion ordering and for a second business keeping its own rows; the visitor cascade was observed removing a temporary visitor's runs and corrections. There is no demo cache yet, so nothing sweeps one
- [ ] Development and held-out case files for every capability written and the held-out set sealed in `evals/SEAL.md` before any prompt is written
- [~] One fresh live classification call, inspected — done against the local API on a throwaway business (`GREENLEAF LANDSCAPING CREW LLC` suggested, `DEPOSIT 5591` unresolved, both models' rationales recorded); not yet through the deployed preview; production untouched

Stage 2: briefs and plain-language scenarios
- [x] Signals computed by code from `CashAnalysis` and the previous brief's input summary, with code-decided direction words, evidence ids and the allowed-number set
- [x] Brief contract with claims bound to signals; four validity layers reported separately; evidence failure rejects, numerical and support failures withhold and count; one repair attempt; coverage of financing, forecast and data-change signals measured — the judged layer is an evaluation-set measure only and is not built, as designed
- [x] `GET ai/brief` answers none, current, stale (with `changes_since`) or unavailable; update is a request, never automatic
- [~] Scenario explanation validated like a brief, with `partial` when essential coverage is missing — done; plain-language *interpretation* into the M9 controls is not built and is absorbed by the conversation panel's `scenario` tool (stage 4)
- [~] Failure matrix implemented and exercised in the browser: provider error, timeout, invalid output, insufficient data, superseded by a data change, budget and in-flight refusals, master switch — every one is unit-tested, and provider error, timeout, invalid output, staleness and the disabled state are request-path-verified against the real database through the web origin. None has been seen rendered in a browser
- [ ] Cedar pack walked month by month on a temporary business: April current, May confirmation makes it stale with the exact change, update cites the import, July binds the financing inflow, June names the one-off
- [~] One fresh live brief and one fresh live scenario, inspected — both done against the local API and development database on the frozen comparison fixture (final round: brief 4/5 claims, reduced receipts 4/5, delayed payment 5/5 with no coverage gaps); not yet repeated through the deployed preview

Stage 3: provider comparison, sealed evaluation, record
- [x] OpenRouter adapter behind the same interface: `allow_fallbacks: false`, `require_parameters: true`, `data_collection: "deny"`, `max_price` from the dated table; provider-reported cost labelled as the provider's; upstream recorded only when named; a private business through it answers 503 — asserted by 18 tests and against a running server with the route fully enabled (zero reservations, zero runs)
- [ ] The small development comparison's OpenRouter half: `qwen/qwen3.8-max-0902` on the same frozen inputs as the final direct round. No longer blocked on the key, which is now set locally: three exploratory calls were made and **every one returned `invalid_output`**, which is why the route is hidden from the interface. The comparison itself is still unrun, and needs in `services/api/.env` and as a branch-scoped preview variable, plus prepaid credit at openrouter.ai (≈$3 covers it). Not a benchmark; a side-by-side of validity, coverage, readability, latency and cost on four cases
- [ ] Four configurations on the sealed held-out set once; runtime candidate on the development set twice; `evals/DECISION.md` committed before the run; `evals/REPORT.md` with every metric per model, pinned versions, and the author's 20-claim sample with grader agreement
- [ ] Recordings make the CI evaluation run with no network and only deterministic checks
- [ ] Methodology gains the tier and override sections; README gains a measured evaluation section quoting the report and nothing beyond it
- [x] Production unchanged: gated off, no AI variable, no credential

Stage 4: the interactive AI CFO panel ([design §5A](CashLens-M10-AI-Design.md), [ADR 0013](../decisions/0013-interactive-ai-cfo-panel.md))
- [x] Migration 0023: `ai_conversations`, `ai_turns`, capability `ask`; deletion removes them before runs; visitor sweep cascades them — applied to development and verified there: deleting one workspace removed its conversation and 4 turns and left 20 turns on other workspaces, and `request_business_deletion` removed conversations, turns and runs while another business kept its own
- [x] `POST ai/ask`: one reservation per turn; at most three model rounds, four tool calls, one scenario computation, 60 s; four allowlisted typed tools (`facts`, `transactions`, `scenario`, `propose_category`) over existing code; scope from the session only
- [x] `ask-1` answer validated by the four layers against this turn's tool results; `clarify` for anything outside the three controls; `partial` as for explanations. The substitution refusal is **structural**, not a prompt instruction: a figure taken only from a recurring fact is `unconfirmed` and may be offered as a choice but not computed, because history shows a payment has arrived before and not that it is the one the owner meant
- [x] Agreed scenario assumptions carried within one conversation and reused on follow-up; announced and re-shown when the fingerprint changes; conversations are per workspace and never cross
- [x] `propose_category` creates an open suggestion under the turn's own run; the review card remains the only path to an override — tested: an accepted direction mismatch is refused, and a successful proposal moves no category total and writes no override
- [x] Deterministic suggested questions from the data; `GET ai/ask/suggestions` and `GET ai/ask/conversation`, neither of which spends
- [x] Layout: two-column top band on desktop (overview left, panel right), stacked on narrow screens; scenario results through the existing forecast card; per-turn "How this was answered" with the tool trace and the validation record. Typecheck, eslint and build pass; **not yet clicked in a browser**
- [x] Tests: tool argument validation, scope cannot be widened by an argument, injected text in a description changes no tool call, invented numbers rejected, round and call bounds, one scenario per turn, duplicate submission refused in flight, reservation covers the turn and settles at what both rounds cost, OpenRouter synthetic-only through a full turn with nothing reserved or sent, and the panel's round schema serialising through the OpenRouter adapter — **389 tests**
- [~] The acceptance journey of design §5A.1 — run live against the local API and the development database, four times; the final run answered all five turns (`answered`, `answered`, `clarifying`, `answered`, `answered`), 2–3 rounds each, $0.120160. Step 3 clarified with the settlement's own amount and date rather than substituting it; step 4 computed after the owner chose; step 5 carried the agreed $1,200.00 and 2026-08-31 forward and changed only the delay to 7 days. Turns 4 and 5 were all four layers green.
- [~] The same journey **through the deployed preview** — `GET ai/ask/suggestions` and `GET ai/ask/conversation` verified there through the API's bypass, returning this workspace's own computed questions. The generating `POST ai/ask` answers **429 `global_cap`**: the preview shares this development database, and today's local verification ($1.694125 rolling) plus the turn's conservative three-round hold exceeds the preview's branch-scoped daily cap. That is the control working. It clears when the rolling 24-hour window moves, or if the owner raises `AI_GLOBAL_DAILY_COST_CAP_MICRO` for this branch

Gate: every figure a model output displays is in its fact sheet and a test enforces it; every evidence link opens a real transaction of the workspace; a brief is stale by fingerprint the moment its inputs change and is never regenerated on its own; a correction moves exactly one category total in exactly one workspace and is described as saved, never learned; no paid call without a reservation a concurrent request cannot also obtain; every failure leaves the deterministic dashboard intact; real statements never reach OpenRouter; the conversation panel answers the journey of design §5A.1 with every figure checked, an unsupported scenario clarified rather than translated, and a proposed category applied only by the owner; the evaluation separates development from held-out, per model, with pinned versions and a pre-registered decision rule.

## Interview demo release

Cut from `feat/m10-ai-insights` on September 14, 2026 and served by the protected branch
preview. **This is a demonstration build, not a public release and not a completed M10
evaluation.** `main` and production are untouched. The handoff leads with the three synthetic
demo businesses, which need no account and no upload.

Access is a revocable Vercel shareable link on one preview deployment. Vercel Authentication
stays on for the projects; the link is the only exception and is withdrawn by switching that
deployment's Share setting back to "Only people with access". No automation bypass secret
appears in any URL or in the client bundle: the web proxy holds the API's bypass server-side.

### Public release shape

`main` carries this release and Vercel deploys it to the canonical production domain,
**https://cashlens-web.vercel.app**, which is public.

That deployment runs the **whole application**: the synthetic demos without sign-in, and
accounts, private workspaces and statement imports for anyone who creates one.
`PUBLIC_DEMO_ONLY` is deliberately unset there.

This was a decision taken on 2026-09-15 to have one shareable URL with full functionality. It
is recorded here because it opens paths whose own prerequisites are still open, and because the
earlier plan was the opposite:

- **Signup confirmation email delivery is still unverified**, and there is no custom mail
  provider. The built-in mailer has its own rate limit, so a burst of signups will fail.
- **No retention or cleanup schedule is running.** An uploaded statement and its staging rows
  stay until someone deletes them. The README says plainly not to upload a real statement.
- **The deployment shares a database with the author's own private businesses.** Separation
  between accounts is the application's authorization, checked on the server against business
  memberships, and not a separate database.
- **Paid AI is reachable by anyone who opens the page.** The existing caps bound it: a rolling
  per-identity daily cap, a rolling deployment-wide daily cap, and the cumulative milestone cap
  counted against the same shared ledger, so there is no second budget.

The synthetic-only mode remains implemented and tested, and is the safer shape whenever a
genuinely public release is wanted: it refuses every private path and every non-demo workspace
in the API rather than only in the interface.

### Implemented and verified

- The four workspace sections — Overview, Analysis, Transactions, Statements — for demo and
  private workspaces, with business switching that keeps the reader in the section they were in
- Deterministic figures: balances, monthly totals, categories, month comparison, recurring
  detection and the 30-day projection, all versioned and computed server-side
- The scenario controls: receipts change, one-time expense and delayed expected payment, each
  applying and resetting without touching a transaction or a balance
- The conversation panel: suggested questions, a bounded tool loop, per-turn validation reported
  in four layers, source chips and the "How this was answered" trace
- Structural refusal to substitute a recurring payment for an unspecified one: it clarifies
- Evidence navigation from a figure to the transactions behind it
- Accessibility of the shipped surfaces: zero contrast failures across landing, overview,
  analysis and transactions; every section reachable and visibly focusable by keyboard
- 389 API tests and the web lint, typecheck and build, green in hosted CI

### Implemented but incompletely evaluated

- **The OpenRouter route.** Wired end to end and restricted to synthetic demo workspaces, but
  every attempt so far returned `invalid_output` (three of three). For this release the model
  selector does not offer it at all: `routing.OFFER_OPENROUTER_IN_CONTROLS` is `False`, so with
  one route left the control renders nothing. Only the control is hidden. `resolve()` is
  untouched, so the route still answers an explicit request and `db/checks/model_comparison.py`
  can still drive it; the adapter, its pricing table, the synthetic-only restriction, the
  configuration and the recorded results are all unchanged.
- **The acceptance journey of design §5A.1**, which has been run live against the local API and
  the development database, not through the deployed preview end to end.
- **Model choice.** Direct OpenAI is the configured default because it is what has been
  exercised, not because a comparison selected it. No comparison has been completed, so neither
  provider is described as better.
- **Latency and cost profiles**, observed only from development runs on one dataset.
- **Private-business paths** — signup, import, review, confirm — implemented and tested, but the
  demo deliberately does not lead with them.

### Deferred, and not claimed

- M11 A/B testing and analytics: not started
- Held-out evaluation, the pre-registered decision rule and the recorded CI evaluation of M10
  stage 3: not run
- The provider comparison: unfinished
- Every public-release prerequisite listed at the top of this file, unchanged: signup
  confirmation email delivery, custom SMTP, the M8 privacy and cleanup gate with no activated
  cleanup schedule, and the M6 post-fix manual test

### The reviewer fallback account

A single account, `reviewer@cashlens.example`, exists on the development project as a fallback
for a reviewer who cannot complete signup — the confirmation email path is still unverified and
the mailer has its own limits. It was confirmed administratively through the Supabase admin API,
so **it demonstrates login, not that the signup email flow works**. Its credential is not
recorded here or anywhere in this repository.

It owns one private business, `Reviewer Workspace`, provisioned through the application's own
`/api/private/provision` and deliberately empty: no accounts, transactions, imports,
connections, balances, conversations or AI runs. It holds no membership in any other business
and no demo workspace. It is there so a reviewer can import a statement of their own.

Retire it after the review, **in this order**:

1. Sign in to the application as the reviewer account and delete `Reviewer Workspace` through its
   own "Delete this business" control, so its files, staging rows, caches and any pending jobs go
   with it. Wait for the deletion to report finished.
2. Only then delete the user in the Supabase dashboard under Authentication → Users.

The order matters and cannot be reversed. `delete_business` authorises against the signed-in
creator and answers `not_the_owner` otherwise, so removing the auth user first destroys the only
login that can delete the business. `businesses` carries no user foreign key, so the business
would then be orphaned rather than removed, which is the same trap recorded under M8 deletion.
Neither step touches owner data or any settled spend record.

### Known limitation a recipient could meet

On a phone, the Transactions page can be dragged sideways by about 218 px. The table's own
horizontal scrolling works; the page-level scroll comes from the six-column table's 640 px
minimum and is not contained by `overflow-x: clip` on the root, so it was left alone rather
than worked around under time pressure. Overview and Analysis do not do this.

## M11. A/B testing and analytics (demo mode only)

Proposed commits: `feat: instrument persistent overview layout experiment`, `feat: report experiment outcomes and AI execution traces`

- [ ] Stable server-recorded 50/50 assignment per anonymous participant and experiment version; persists across businesses and visits in one browser
- [ ] Variants differ only in the order of cash outlook and business insights
- [ ] Exposure recorded when the container renders; visibility is a diagnostic, not the denominator
- [ ] Primary metric: distinct eligible exposed participants opening any insight detail within 24 hours, over participants with a completed window
- [ ] Validated, deduplicated event envelope; server-assigned variant; no financial descriptions or chat text
- [ ] Replay, staff preview, simulated traffic and automated tests excluded
- [ ] Experiment restricted to synthetic demo mode; private-upload users get separate aggregate operational metrics and never count toward a layout winner
- [ ] Reporting view with sample counts, conversion, differences, uncertainty and an explicit insufficient-evidence state
- [ ] Tests: persistence, refresh, duplicates, spoofing, immature windows, preview exclusion

Gate: order swap only, real actions emit valid events, repeated delivery does not inflate counts, no claimed winner.

## M12. Final polish, verification, documentation and interview walkthrough

Proposed commit: `docs: publish verified demo walkthrough and evaluation`

- [ ] Final polish: responsive layout, keyboard access, loading, stale, empty and failure states across all three businesses and the private workspace
- [ ] End-to-end verification on the deployed path: three AI capabilities, business isolation, budget controls, source links, failure states
- [ ] README updated with the verified URL, actual screenshots, current diagrams, local setup, model configuration placeholders, measured evaluation and limitations
- [ ] Statement upload walkthrough with downloadable synthetic files and actual screenshots
- [ ] Supporting docs: data-contract, financial-definitions, experiment-design, evaluation
- [ ] Five-minute demo script and code-tour guide
- [ ] Honest list of what remains incomplete

Gate: fresh visitor can open the deployed demo; documented setup reproduces; no secrets; synthetic results clearly marked.

## M13. The Discovery portal (ADR 0014)

An internal customer-specific workspace inside CashLens, named for the customer only behind
authentication ([ADR 0014](../decisions/0014-discovery-portal.md)). Design, revision 2:
[architecture](../discovery-architecture.md), [schema](../discovery-schema.md),
[implementation plan](../discovery-implementation-plan.md). Off by default
(`DISCOVERY_ENABLED`); refused outright on a `PUBLIC_DEMO_ONLY` deployment.

- [x] Phase 1: migration 0025 (eleven service-role-only tables, RLS with no policies, two trigger-written history tables, the `auth.users` profile trigger), `profiles` roles read by Python, `app/discovery/auth.py` with the two resolvers and the per-route admin guard, `role` and `discovery` on `/api/me`, `GET /api/admin/discovery/session`. Verified 2026-09-20 against the development database; see [the phase 1 handoff](../discovery-phase1-handoff.md)
- [x] Phase 2: the thin vertical slice: a disposable five-question test template loaded by `app/discovery/seed.py` (idempotent, `--check` in CI), the eight stakeholder routes under `/api/discovery` all scoped through `Mine`, `/discovery` and `/discovery/[sectionSlug]` with save, save-and-exit and database-authoritative resume, the post-login redirect, the neutral header link, analytics exclusion with a test, `scripts/discovery_test_account.py` for a throwaway account, and `db/checks/discovery_flow_check.py`. Verified 2026-09-20 against the development database through the running API (33/33); see [the phase 2 handoff](../discovery-phase2-handoff.md)
- [x] Phase 3: the security and isolation gate. An OpenAPI-enumerated authorization matrix (six identities × nine operations), cross-stakeholder attacks both ways, shared-question and private-section isolation, server-side branching, adversarial validation, scenario atomicity, history through every field, versioning and retirement, deployment switches, logging and analytics sentinels, the cleanup-order regression: `tests/test_discovery_gate.py` (153 cases) and `db/checks/discovery_isolation_check.py` (38/38), with `discovery_rls_check.py` 84/84 and `discovery_flow_check.py` 33/33 re-run. Five LOW findings fixed. Verdict 2026-09-20: **ready for phase 4**; see [the security gate report](../discovery-phase3-security-gate.md)
- [x] Phase 3.5A: transaction calibration, the data model and the one-call typed write. Migrations `0026` (the enum value, alone) and `0027` (four types, three tables, the calibration history trigger, `save_discovery_typed_response`, two widened `0025` constraints), applied 2026-09-20; Discovery tables eleven to fourteen. Phase 3's E7 closed for both typed types. Loader proves a controlled variant differs from its baseline in exactly its named feature. Six-case synthetic proof fixture; the calibration card in the portal. Verified: 827 unit tests, 143/143 RLS, 31/31 calibration, 33/33 flow, 38/38 isolation; see [the phase 3.5A handoff](../discovery-phase3-5-handoff.md). Design: [design](../transaction-calibration-design.md), [schema proposal](../transaction-calibration-schema-proposal.md)
- [x] Phase 4 (prototype, real bank): the authored bank under `docs/question-bank/` (154 shared founder, 79 Lead, 79 Partner, 242 Engineer, 122 transaction cases, 34 borrower scenarios, 20 technical scenarios) converted by `scripts/build_discovery_fixtures.py` into 42 sections and three templates (`discovery-lead`, `discovery-partner`, `discovery-engineer`), each section named `<owner>-<core|rec|deep|cal|scen>-<theme>` so the landing page groups Core, Recommended, Deep dive and Calibration; the two proof sections marked `retired` in their fixtures and kept retired by the loader on every run; a click-to-rank control for ranking questions and a validator that accepts a ranking of the options that apply; `/api/discovery/me` serves the newest assignment; `discovery_test_account.py assign` gives an existing account a template. Seeded 2026-09-20 into the development database (741 questions, 128 cases, one transaction) and the reviewer account assigned the Lead template. Verified: `seed --check`, the five Discovery test files, ruff, web typecheck, lint and tests. Not done: no stakeholder account, no invitation, no admin view; the authored controlled-variant linkage, progressive reveals and follow-up rules are documentation-only (see the build script's report); the three real-database checks still name the proof templates and need updating before their next run
- [x] Phase 4 pre-invite cleanup (2026-09-20): founder Core cut from 43 and 42 items (about 40 minutes) to 29 and 28 (about 26 minutes) by moving seventeen items to Recommended in the Markdown, wording untouched, listed in the review summary. Migration `0028` adds four nullable columns to the case table so the authored relationships reach the runtime: a prose variant names its baseline and the feature the author changed (23 of them), an original keeps its reveal fact (25), and a reveal's second stage names its original and is proved equal to it in every column but context (one served, `tc-06`; `--all-reveals` on the build script serves the rest). None of the four is in the served field list. The loader's reorder shift became one-more-than-the-maximum, which closes the isolation check's previously unexplained transient (a dropped, unretired question kept its shifted position and collided). The three real-database checks that named the proof templates now load disposable fixtures of their own; the calibration check also proves the relationship columns and a reveal answered apart from its original. Verified 2026-09-20 against the development database: `seed --check` 742 questions and 129 cases; 368 unit tests; RLS 152/152, flow 35/35, calibration 41/41, isolation 39/39; web typecheck and 17 tests clean
- [x] Phase 4 pre-invite accounts (2026-09-20): a stakeholder-invite script (not in this snapshot) invites a real stakeholder through the Auth admin `generate_link` endpoint (type `invite`: account created without a password, link returned, no mail sent), sets the profile to `stakeholder` with names, ensures one assignment on a `discovery-*` template and refuses proof templates and admin profiles; the throwaway script refuses real stakeholder addresses and any account holding a real template. An invite link now lands where a recovery link does: `/auth/callback` sends `type=invite` to the set-password page, which routes on through the same post-login destination as the login form, so a stakeholder with Core work left lands on their Discovery. A separate public Vercel project, `<staging-project>`, deploys the app from the branch with the branch's environment, reviewer mode off, and the API's branch alias as its origin; its alias is reachable without Vercel's team login while its deployment URLs stay protected. Three accounts created and assigned, `not_started`, no answers. Verified: RLS 152/152, flow 35/35, calibration 41/41, isolation 39/39; 369 unit tests; web typecheck, lint and 19 tests. **Blocked:** Supabase honours only the Site URL as a redirect, so a generated link redirects to production until `https://<staging-site>/**` is added under Authentication → URL Configuration → Redirect URLs; links must be regenerated after that. Worked around 2026-09-21 at the author's request: `discovery_stakeholder.py set-password` gave each of the three accounts a generated password through the Auth admin API and confirmed the email at the same time (password sign-in is refused otherwise), for the author to send by hand; the passwords were printed once and recorded nowhere. Read back afterwards: all three confirmed, `stakeholder`, one assignment, `not_started`, no answers, no sign-in yet. A stakeholder therefore signs in at `https://<staging-site>/login` and lands on their Discovery through the login form's own rule; nobody has signed in as them to check this, the mechanism was proved on a throwaway only. The forgot-password flow still lands on production's callback until the allowlist entry exists
- [ ] Phase 3.5B (with phase 5): consensus computation, prediction comparison, admin calibration views
- [x] Phase 4 in production (2026-09-21): `DISCOVERY_ENABLED=true` set on the production API and redeployed; the three stakeholders sign in at the production login page and land on their Discovery; verified through the production web origin as a throwaway stakeholder account (API path 20/20, headless browser 17/17, then deleted) and by the four real-database checks against the production API (RLS 152/152, flow 35/35, calibration 41/41, isolation 39/39). Reviewer mode stays off there
- [x] Phase 5, the admin answer-analysis dashboard (2026-09-21, `feat/discovery-admin-dashboard`): `scripts/build_discovery_fixtures.py` now also emits `services/api/app/discovery/bank_metadata.json`, the authored "System use", "Semantic class", "Why we ask", "What it tells us" and "What this tests" lines keyed by item (755 items; fixtures byte-identical). `app/discovery/taxonomy.py` files every question by level, group, topic, business group, underwriting group, Engineer category and the nine engineering buckets, all as visible lookup tables; `app/discovery/analysis.py` is pure: deterministic comparison per answer type with configured tolerances (agree, roughly aligned, different, material disagreement, only-one, neither, not comparable for free text), founder rows over the 154 shared questions, calibration rows with controlled pairs and reveals, scenario rows with approve-against-deny, the engineering, engagement, business, underwriting and open-questions views, and the four CSVs. `app/discovery/admin.py` adds twelve GET routes under `/api/admin/discovery`, every one guarded by the per-route admin role; the dataset is every assignment on a `discovery-*` template except the caller's and any admin's, read with the service role, with no client parameter narrowing or widening it; an account holding more than one real template is listed as a reviewer and not compared. `/admin/discovery` on the web: ten tabs, client-side search and filters, a read-only detail drawer, exports; the header draws "Discovery admin" from `/session` only. Verified: 64 admin tests (the authorization matrix for every route as anonymous, member, no-profile, each stakeholder, reviewer and admin, with smuggled query parameters; read-only under every method; no email or id in any export; every comparison rule on literals; empty-state), the gate suite extended to 21 operations, 1020 API tests, web typecheck, lint and 24 tests; real-data smoke against the shared database (154 rows, 123 cases, 23 pairs, 1 reveal, 34 scenarios, 262 Engineer items); the dashboard driven in a headless browser against a local API and web as a throwaway admin and a throwaway stakeholder. Not done: no LLM summary by design; the underwriting and engineering groupings are keyword and key-list rules, reported next to each group, and thirteen underwriting questions still file under "Other"
- [x] Phase 5b, the response browser (2026-09-21, `feat/discovery-response-browser`): the dashboard's primary path is now respondent → questions → answers → analysis. A "Responses" tab second in the order: three respondent cards (Core, total, remaining Core, skipped, last activity), a summary header with the topic breakdown, every assigned question in the order asked with the answer in front, status (answered, unanswered, skipped, prefer not to answer), level, section, answered and updated times; filters, a section dropdown, search over question and answer text, "only answers with written comments"; an inline "Compare founder answer" for the 154 shared questions ("Awaiting Partner" when one side is missing); a deterministic "Important responses" panel from the metadata and comparison statuses (a question listed under every reason that applies); contextual links into the analysis tabs, which now read `?q=` and `?filter=`; an empty state that still lists the questions. The drawer shows every earlier value of an answer from the three history tables through a new admin route, `GET /api/admin/discovery/history/{assignment_id}/{question_id}`, scoped to the dataset's respondents and guarded like the rest. The Overview's cards and numbers open their views; "Recent responses" shows the latest answers with a preview and opens the drawer. The newest stakeholder assignment per template is the one compared, in template order. Verified: 1032 API tests (the history route in the matrix and the gate; 22 operations); web typecheck, lint and 25 tests; a synthetic Lead, Partner and Engineer answered through the real stakeholder API on the shared database (text, selects, ranking, a calibration case, a scenario changed once for history, a skip, a prefer-not-to-answer, ownership and a technical scenario) and the browser walk as a synthetic admin (25/25) plus the empty state for the real Lead; all synthetic accounts and answers deleted afterwards
- [x] Pre-publication hardening (2026-11-12): Next.js 16.3.5 → 16.4.6 (three critical RCE advisories, SSRF, DoS), sharp, source-map-js and zod lifted; pypdf 6.18.1 → 6.21.4 (24 advisories, all malformed-input DoS) pinned below 7 because the extractor imports a private module; certifi 2026.10.23. The interactive docs and the OpenAPI schema answer 404 in production; every web response carries X-Content-Type-Options, X-Frame-Options, a frame-ancestors policy, Referrer-Policy and Permissions-Policy; the demo workspace slug is checked against the schema's own rule before any query; the cash-flow export is named `*_classified.csv`. Dependabot and SECURITY.md added. Verified: 1034 API tests, web lint, typecheck, 25 tests and build. The public snapshot is described in ADR 0002's amendment
- [ ] Phase 4: the full question banks, three real accounts by invite link, the full section UX
- [ ] Phase 5: the admin views
- [ ] Phase 6: converge `ADMIN_USER_IDS` and `profiles.role` under a decision record

## Later work (not before the above)

- [ ] Scanned or image-only PDFs, photographs, password-protected documents
- [ ] Credit-card statements, invoices, spreadsheets, multi-currency
- [ ] Plaid or other bank connector: adapter, Link, token storage, webhooks, cursor sync, lifecycle handling
- [ ] Durable worker if bounded serverless processing proves insufficient

## Unresolved decisions

Recorded here so they are not mistaken for validated choices. See also
[docs/decisions/0001-project-scope.md](../decisions/0001-project-scope.md).

- Whether CSV/PDF parsing plus a bounded model call fits Vercel function limits, or a durable worker is needed
- Which scheduled cleanup mechanism (Vercel cron, Supabase scheduled function, or other) is verified to run
- Two-project Vercel routing details, streaming behavior and cookie handling through the rewrite
- Node and Python versions to pin; Python runtime supported by Vercel at scaffolding time
- Package manager, migration tool, linters and formatters
- OpenAI model ID and cost/latency profile; no model is assumed yet. The M10 design chooses the runtime model by a pre-registered comparison in its stage 3 (ADR 0011); until then it is a setting
- Exact demo limits (10 MB, 20 pages, 5,000 rows, one job per user) pending the feasibility spike
- Sequencing of financial tools relative to the CSV vertical slice, since a brief needs metrics
- Whether Willow Studio and Northline Services land in M3 or with the UI milestone
- Re-estimated timeline after the deployment spike; the earlier four-day target is not adopted

## Resolved decisions

- Repository visibility: private GitHub monorepo with reviewer access on request; separately deployed Vercel application ([ADR 0002](../decisions/0002-private-repository-and-deployment.md), 2026-09-12)
- Playbook asset paths: corrected on 2026-09-12 to the shipped layout (docs/mockups, docs/diagrams/08-11 and docs/diagrams/baseline)
