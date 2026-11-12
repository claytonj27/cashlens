# A tour of the code

Written to be read beside the repository, or used to walk someone else through it. It follows the
data rather than the folders: where a number comes from, what happens to it, and which file to
open at each step.

Two rules explain most design decisions here, and it is worth stating them before the tour.

**Money is computed in Python, never by the model.** Everything on screen comes out of
`services/api/app/analysis/`. The model is handed a finished fact sheet and asked for words.

**Scope comes from the session, never from the request.** No route accepts a business id from the
client or from the model. One function resolves it, and everything else depends on that function.

---

## 1. The shape of it

```
apps/web          Next.js 16, React 19. Presents and forwards. Never calculates.
services/api      FastAPI, Python 3.12. Every authoritative calculation, all AI orchestration.
db/migrations     23 SQL files, applied by db/migrate.py, checksum-tracked.
db/checks         Operational scripts run against a live API. Not unit tests.
docs/             Plans, decision records, diagrams, screenshots.
```

Roughly 17,000 lines of Python and 10,000 of TypeScript. The two deploy as separate Vercel
projects from one repository.

### Where to start reading

If you have ten minutes, read these four files in this order:

1. `services/api/app/workspaces.py` → `resolve_workspace()` — the authorization choke point
2. `services/api/app/analysis/metrics.py` — the money arithmetic
3. `services/api/app/ai/ask.py` → `run_turn()` — the bounded tool loop
4. `services/api/app/ai/validate.py` → `validate()` — what stops a made-up number

---

## 2. How a request gets in

A browser only ever calls its own origin. There is exactly one `fetch` to the API in the whole
frontend.

**`apps/web/src/lib/api.ts`** (57 lines) is that one call site. Relative path,
`credentials: "same-origin"`, `cache: "no-store"`, and an `x-cashlens-client: web` header the API
insists on. It throws `ApiError` carrying status, a safe request id, and structured detail.

**`apps/web/src/proxy.ts`** (62 lines) is Next 16's proxy convention, the renamed middleware. It
matches `/api/:path*` and forwards to `API_ORIGIN`. Three things it does that matter:

- Builds the destination from the matched pathname only, so a request cannot select another host.
- Strips any `x-vercel-protection-bypass` header a client supplies, then sets its own from the
  server-side `API_PROTECTION_BYPASS`. The bypass never reaches the browser.
- On Vercel, a missing or unparseable `API_ORIGIN` returns 503 rather than silently proxying.

**`services/api/app/main.py`** (159 lines) mounts nine routers and holds the exception handlers.
`/api/health` is process health only and touches no database.

**`services/api/app/auth.py`** (138 lines) is small and worth reading in full:

| Function | What it does |
|---|---|
| `current_user` | Validates a bearer token against Supabase Auth, 401 otherwise |
| `visitor_from_request` | Reads the `cl_visitor` HttpOnly cookie, only if well-formed and known |
| `ensure_visitor` | Resumes or creates a demo visitor, sets the cookie |
| `require_client_header` | Rejects anything without the client header |
| `require_features` | 404s every product route where features are off |
| `refuse_on_public_demo` | 404s private paths on a synthetic-only deployment |

---

## 3. The authorization choke point

**`services/api/app/workspaces.py`** → `resolve_workspace()` at line 78. Every workspace-scoped
route in the application depends on this one function, which is the reason the rules hold.

It takes a workspace id from the URL and returns a `WorkspaceScope`, or raises. The logic:

1. Parse the id as a UUID. Malformed → 404, not 400. A bad id should not confirm anything.
2. If there is a bearer token: validate it, then look up the workspace **filtered by that user
   id**, then separately confirm a row in `business_memberships`. Two checks, not one.
3. If there is no token: read the visitor cookie and look up the workspace **filtered by that
   visitor id**.
4. Load the business, filtered on `deletion_requested_at is null`. This is what makes a pending
   deletion stop every read immediately.
5. On a synthetic-only deployment, refuse any business whose `data_mode` is not `demo`.

The returned `WorkspaceScope` carries `workspace_id`, `business_id`, `data_mode`, `identity`
(`visitor` or `user`) and `identity_id`. That last field is what model spend is counted against,
because a person spends, and one person can hold several workspaces.

**Why it matters:** a guessed-but-valid workspace id belonging to someone else returns 404, and
no route can widen its own scope, because none of them ever sees a business id from the client.

---

## 4. Where the numbers come from

Everything under `services/api/app/analysis/`. All of it is pure functions over a list of
transactions, which is what makes it testable and deterministic.

### `metrics.py` (327 lines) — `cash-2`

| Function | Returns |
|---|---|
| `monthly()` | Money in, out, net and count per calendar month, with a `complete` flag |
| `by_category()` | Gross inflow and outflow per category, plus the transaction ids behind each |
| `compare_months()` | One month against the previous, per category, on net effect |
| `recurring()` | Descriptions seen ≥ 3 times at a steady gap, with an estimated next date |
| `coverage()` | How much usable history exists, and what it does not support |
| `matched_transfers()` | Transfers between the owner's own accounts, netted from both sides |

Two details worth pointing out to another engineer:

`by_category()` returns **gross** `inflow_minor` and `outflow_minor` separately. A category can
have both. The interface computes net from them, which is a bug I had to fix: an earlier version
showed whichever side was non-zero, so a category with $3,200 in and $1,275 out was reported as
`+$3,200`.

`recurring()` uses the **median** gap to decide whether a pattern is regular enough to report,
but the **mean** gap and mean amount to describe it. A median-by-median estimate drifts away from
what the months actually show.

### `forecast.py` (341 lines) — `forecast-1`

`choose_anchor()` picks the most recent usable balance on or before the analysis date, preferring
a bank-observed current balance and falling back to a statement closing balance with `is_live`
false. It sums one row per account **from the same day**, which is why the projection's starting
figure can legitimately be lower than the headline balance, which uses each account's latest
known figure across mixed dates.

`build()` projects 30 days from that anchor using flat daily rates derived from complete months
with recurring items removed first, then adds the recurring items back as dated events. Scenarios
are applied as modifications to that path; nothing is written.

### `categorize.py` (326 lines) — `rules-2`

Keyword rules only. `names_a_counterparty()` is the interesting one: a description that names only
*how* money moved, a bare deposit or a cheque number or a wire reference, is deliberately left
uncategorised rather than filed somewhere plausible.

### `fingerprint.py` — `fp-1`

`compute()` hashes every input the figures were derived from. A stored AI answer whose fingerprint
no longer matches the current data is shown as stale rather than silently regenerated. This is the
single staleness mechanism in the application.

### `analysis/routes.py` (538 lines)

`GET /api/workspaces/{id}/analysis` assembles all of the above into one response with a `versions`
block. `POST .../analysis/scenario` runs the same calculations with a scenario applied and returns
both paths. It writes nothing.

---

## 5. The AI layer

This is the part worth the most attention. `services/api/app/ai/` is about 7,000 lines, the
largest thing in the repository.

### The provider boundary

**`provider.py`** defines a `ModelProvider` protocol: `name`, `live`, and one method `complete()`.
Four adapters implement it:

| Adapter | Notes |
|---|---|
| `openai_direct.py` | The real one. `max_retries=0`, because retries are handled above it |
| `openrouter.py` | Demo-only, currently hidden from the interface |
| `simulated.py` | Deterministic outcomes for tests, refused when `APP_ENV=production` |
| `replay.py` | Recorded responses |

**`factory.py`** chooses by `settings.ai_provider`, typed `Literal["openai","replay","simulated"]`.
OpenRouter is deliberately *not* a value, because it is not a deployment-wide choice.

**`routing.py`** (164 lines) decides per request. `resolve()` refuses OpenRouter for any non-demo
workspace **before** checking configuration, and never substitutes the direct provider. No
fallback, no rerouting: a comparison whose result silently came from the other model is worse
than no result. `available()` lists what a control may offer, gated by
`OFFER_OPENROUTER_IN_CONTROLS`.

### Money before dispatch

**`runs.py`** (246 lines) and migration `0020`.

`reserve()` calls the SQL function `reserve_ai_usage`, which takes two advisory locks in a fixed
order and checks four caps inside one transaction:

1. In-flight concurrency per identity
2. Cumulative milestone total, with no time window at all
3. Rolling 24-hour spend for this identity
4. Rolling 24-hour spend for the whole deployment

Settled rows count what they cost; unsettled unexpired rows count their estimate. `settle()` with
a null cost keeps the held estimate and marks the row `uncertain`, so a timeout does not quietly
free budget. `release()` sets zero and is used only when the request never left the process.

`start_run()` writes the `ai_runs` row **before** dispatch with status `running`, so a call that
dies still leaves a record. `finish_run()` completes it. `mode` is derived from the adapter's own
`live` flag rather than its name, which is what stops a replayed answer being reported as live.

### The fact sheet

**`signals.py`** (626 lines) turns computed metrics into a `FactSheet` of `Signal` objects, each
with an id, a label, values, and up to eight evidence transaction ids. Ids look like
`month:2026-08`, `chg:suppliers`, `fc:lowest`, `rec:payroll`. Those ids are the vocabulary the
model must quote back.

### The bounded loop

**`ask.py`** (1,423 lines) → `run_turn()` at line 741. The contract is in two places, which is
worth knowing before you go looking:

```python
# services/api/app/ai/ask.py, lines 72-81
CAPABILITY = "ask"
MAX_TURNS_PER_CONVERSATION = 12
CONTEXT_TURNS = 6                  # earlier turns carried into a request
MAX_TRANSACTION_ROWS = 20
TOOL_RESULT_ALLOWANCE_TOKENS = 6000

# services/api/app/ai/prompts.py, lines 326-329
ASK_MAX_ROUNDS = 3
ASK_MAX_TOOL_CALLS = 4
ASK_MAX_SCENARIOS = 1
ASK_DEADLINE_SECONDS = 60.0
```

The loop bounds sit beside the prompt they are described in, so the text telling the model it has
three rounds and the code enforcing three rounds cannot drift apart.

`ToolBox` at line 294 dispatches four allowlisted tools, and `run()` raises `ToolError` for any
other name:

| Tool | Does |
|---|---|
| `facts` | The whole computed picture for the current figures |
| `transactions` | Individual rows, filtered, at most 20 |
| `scenario` | Runs a what-if through the same projection code, once per turn |
| `propose_category` | Proposes a category, creating an open suggestion; changes no figure |

**No tool takes a workspace or business argument.** Scope comes from the closure over the resolved
scope. That is the whole defence against a model widening its own access, and it is structural
rather than a prompt instruction.

The loop breaks with status `invalid_output` when rounds are exhausted and `deadline` when the
clock runs out. Both are enforced in code, not asked for in the prompt.

`check_numbers()` at line 221 and the `NumberCheck` dataclass implement the refusal I am most
pleased with. A figure known **only** from a recurring pattern is returned as `unconfirmed`, not
computed. History shows a payment has arrived before; it does not show that this is the one the
owner meant. So the model is made to ask rather than substitute.

### Validation

**`validate.py`** (617 lines) → `validate(text, sheet, output_model)`. Four layers:

| Layer | Checks | On failure |
|---|---|---|
| `schema` | The output parses into the expected shape | Whole answer rejected |
| `evidence` | Cited transaction ids exist in this workspace | Whole answer rejected |
| `numbers` | Figures match the fact sheet exactly | That claim withheld, counted |
| `support` | The wording is consistent with the cited fact | That claim withheld, counted |

`resolve_figure()` resolves a reference anywhere in the sheet, not just within the claim's own
fact. `_quotes_raw_number()` uses digit-boundary matching so `500` does not match inside
`$5,500.00`.

**Be honest about the gaps when you explain this.** The number check reads a claim's structured
figures and money and percentages inside a claim's text. It does not read percentages or unmarked
numbers in the summary line, nor any number inside a caveat or follow-up question. Support is a
keyword test, not comprehension. The README says so too.

---

## 6. Statement imports

`services/api/app/imports/` is about 3,000 lines, and the most state-machine-like part.

| File | Role |
|---|---|
| `routes.py` | Eleven endpoints covering the whole lifecycle |
| `csvparse.py` | Column mapping, 12 date formats, sign conventions |
| `pdfparse.py` | Text extraction via pypdf. No OCR, no model |
| `extract.py`, `extract_worker.py` | Bounded, leased, resumable extraction with capped attempts |
| `matching.py` | `match_rows()` finds duplicate candidates; `assess_statement()` reconciles totals |
| `cashflow_routes.py` | Cash-flow buckets over a staged import: run, read, correct, export |

The order is: create → upload to Storage with a signed URL → mark uploaded → map columns →
review rows → confirm. Confirmation is pinned to a `draft_revision`, so a confirm against a stale
revision is refused, and it is atomic: a failed confirm inserts no rows.

`matching.py` presents duplicates as **candidates**. The owner decides. Heuristic matching is
never presented as certainty.

### Sign conventions, and why `csvparse.py` argues with the file

A statement says nothing about whether a positive number is money arriving or money leaving, and
guessing wrong inverts every figure downstream. So `propose_mapping()` works it out from the
balance column, and where it cannot it says `sign_unverified` rather than assuming.

Two things it has to get right first. Header candidates are ranked by **which name matched**, not
by column position, or a file carrying both `Transaction ID` and `Transaction Name` uses the id as
the description. And the balance candidate is the one that **actually moves**: an account's
`Current Balance` repeated identically on every row cannot verify anything, and taking the first
candidate by header position would choose it over a real running balance further right.

`_sign_from_running_balance()` then asks whether `balance[i] - balance[i-1]` equals the amount or
its negation. When the balance only moves once a day — a common export shape, the day's closing
balance printed beside each of that day's transactions — every row-to-row delta is zero and the
question is unanswerable row by row. `_sign_from_daily_balance()` asks it of whole days instead,
and requires five days to agree one-sidedly before it will say.

### Cash-flow classification

`services/api/app/ai/cashflow.py`. One question per batch of rows: which bucket, and why.

The division of labour is the design. **Direction is arithmetic**, computed from the amount in
`direction_of()` before a batch is built, told to the model as settled and re-checked after it
answers. **The bucket is judgement**, which is what the model is for. **The last word is the
owner's**, and a correction is what the export uses.

`validate_items()` guarantees one verdict per candidate: an id nobody asked about is discarded, a
duplicate answer is taken once, a category belonging to the other direction is refused, and a row
nobody answered for still gets a result. Each of those falls back to `Unclassified` rather than
dropping a transaction, because a row that cannot be classified still has to appear in the review
and in the export.

Fifteen hundred rows do not fit in one serverless request. `classify_import()` runs for a deadline
and returns how far it got; a row whose `cashflow` is null is a row still to do, which is the whole
of the resume logic — no job table, no lease, nothing running after the response. A wave is only
dispatched when there is room for it to finish inside the deadline, and `batch_size_for()` derives
the batch from `ai_max_output_tokens`, because a truncated answer is unparseable and takes every
row in its batch down with it.

`db/checks/cashflow_check.py` drives all of this against the development database with real model
calls, which is where two failures that a green test suite had missed actually surfaced.

---

## 7. The frontend

`apps/web/src/`. Every page is a client component except the root layout and the landing page.

### Structure

```
app/                17 routes. Pages are thin; they pick a shell and pass hrefs.
components/         Where the work is.
lib/                API clients, formatters, feature flags, analytics.
proxy.ts            The only request interception.
```

### The shells

`demo-shell.tsx` and `workspace-shell.tsx` do the same job for the two paths: load the workspace,
render `workspace-nav.tsx` around it, and hand the section a `workspaceId` and a token. They are
why the same four section components serve both demo and private businesses unchanged.

`workspaceSections()` and `demoSections()` build the four section URLs and carry the `?workspace=`
selection through, which is what keeps a bookmark working.

### The big components

**`cash-dashboard.tsx`** (966 lines) is Overview. Composes `CashHero`, `ForecastSection` with
`PathChart`, `InsightsCard`, then `BriefCard`, `ReviewCard` and `CfoPanel`. The interesting part
is `ForecastSection`: the typed scenario values live in the parent so closing and reopening the
workspace keeps them, and a `fromChat` stamp lets a conversation-applied scenario populate the
form **without** overwriting what the owner typed.

**`analysis-view.tsx`** (817 lines) is the Analysis section: `MonthlyHistory` (an SVG bar chart
with a real dollar axis and an accessible table beneath), `MonthComparison`, `CategoryBreakdown`
and `RecurringActivity`.

**`cfo-panel.tsx`** (488 lines) is the conversation. A column of header, scrolling thread, pinned
composer. One paid turn per send, locked while in flight by both a state flag and a ref, because
two clicks in the same tick both see the old state.

**`import-review.tsx`** (938 lines) is the staged import review, and the densest file in the
frontend.

### Libraries

| File | Holds |
|---|---|
| `lib/api.ts` | The single fetch, and `ApiError` |
| `lib/analysis-api.ts` | Response types and `money()`, which never does arithmetic on a string |
| `lib/ai-api.ts` | Brief, classify, scenario and ask types, plus trace formatters |
| `lib/analytics.ts` | Consent, route sanitising, the fixed event parameter list |
| `lib/features.ts` | `featuresConfigured`, `accountsAvailable`, `demoAvailable` |

`components/ui.tsx` is the shared vocabulary: one card, one title, one figure style, one primary
button, plus the inline icon set.

---

## 8. Database

23 migrations in `db/migrations`, applied by `db/migrate.py`, which shells out to `psql`, tracks
SHA-256 checksums in `schema_migrations`, and refuses a changed applied file as drift.

The ones worth knowing:

| Migration | Adds |
|---|---|
| `0002_foundation` | businesses, workspaces, accounts, transactions, visitors |
| `0006_statement_imports` | document_imports, import_rows, transaction_sources |
| `0017_reserve_and_create_import` | Quota reservation and import creation in one transaction |
| `0018_ai_runs_overrides_and_usage` | ai_runs, usage_reservations, category_overrides |
| `0020_milestone_total_budget` | The cumulative cap, with no time window |
| `0023_ai_conversations` | ai_conversations, ai_turns |

The API reaches Postgres through the Supabase Data API over HTTP with the service-role key, not a
direct connection. The service role **bypasses row security**, which is exactly why the membership
checks in `resolve_workspace` are mandatory rather than defence in depth.

---

## 9. How to demo the code itself

A route worth walking someone along, in this order:

1. `proxy.ts` — the browser never touches the API origin, and the bypass never reaches the bundle
2. `workspaces.py:78` — one function decides scope, and everything depends on it
3. `metrics.py:163` — `by_category` returns gross both ways, and why that mattered
4. `ask.py:294` — four tools, none of which takes a scope argument
5. `ask.py:221` — the unconfirmed-figure refusal, structural rather than prompted
6. `runs.py:58` — money reserved before dispatch, four caps in one transaction
7. `validate.py` — four layers, two different failure consequences
8. `admin.py:47` — the run columns the dashboard is allowed to select, and the ones it is not

The last one is a good closing note: `RUN_FIELDS` deliberately omits `output`, `safe_input` and
`input_summary`, so no question or answer can reach the operator dashboard even by accident, and
a test asserts it.
