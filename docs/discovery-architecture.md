# Discovery Portal: architecture

Status: **revision 2, implementation-ready.** Nothing in this document is implemented yet, and
migration 0025 has not been written. The design is settled; the next step is phase 1 of the
[implementation plan](discovery-implementation-plan.md#11-implementation-phases).

Changes from revision 1 are listed in section 9, including the two corrections made at final
review.

Companion documents: [schema](discovery-schema.md),
[implementation plan](discovery-implementation-plan.md),
[ADR 0014](decisions/0014-discovery-portal.md).

---

## 1. What this feature is

Three named the customer stakeholders get individual accounts on the existing CashLens application.
Each lands on a guided discovery questionnaire assigned to them, works through it across
sessions, and can open the CashLens prototype at any time to upload statements and run
classification. The author gets an admin view of progress, answers, disagreements and gaps.

It is an **internal customer-specific workspace inside CashLens**, not a product. The name
the customer appears only behind authentication; see [ADR 0014](decisions/0014-discovery-portal.md).

## 2. The architecture it has to fit

Verified by reading the code. The design below is shaped entirely by it.

### 2.1 Three processes, one direction of travel

```
browser ──┬── Supabase Auth (sign in, refresh, password reset) ── publishable key
          │
          └── /api/* ── apps/web/src/proxy.ts (Next middleware rewrite)
                            │  API_ORIGIN, server-side only
                            ▼
                     services/api (FastAPI)
                            │  service role key
                            ▼
                     Supabase Postgres (PostgREST Data API) + private Storage
```

The browser talks to Supabase for **authentication only**. Searching the codebase for a non-auth
use of the browser client returns exactly one hit, `uploadToStorage()` in
`apps/web/src/lib/imports-api.ts`, which pushes a file to a one-path signed URL. **No page reads
a table from the browser.** All data arrives over `/api/*`.

There are **no Next.js server actions and no Next.js route handlers**. `apps/web/src/proxy.ts` is
the only request interception, and it is a rewrite, not a handler. Discovery introduces neither,
or it becomes a second backend with a second authorization model.

### 2.2 The service role bypasses RLS

`services/api/app/supabase.py` constructs its client with the service role key, which bypasses
Row Level Security. So for every request arriving through the application, **RLS is not the
control**. The control is Python. This is the documented position in `CLAUDE.md`: *"Privileged
keys bypass RLS, so backend membership checks are mandatory wherever they are used."*

Revision 1 of this document then proposed narrow `authenticated` read policies so a stakeholder
could also read their own rows directly through PostgREST. **That has been removed.** Section 3.3
explains why, and section 4 is the threat model that results.

### 2.3 Authorization today: one choke point and one allowlist

**`resolve_workspace()` at `services/api/app/workspaces.py:78`** is the single place a workspace
id in a path becomes a scope. It takes the identity from the validated session, never from the
body or a query parameter, checks the membership, refuses a business awaiting deletion, applies
the synthetic-only gate, and returns a frozen `WorkspaceScope`. Every workspace-scoped route
depends on it. Discovery gets the same shape.

**There is no role system.** `membership_role` is an enum with exactly one value, `'owner'`.
Migration `0014` added `businesses.created_by` precisely because *"a membership alone never
carries it, because membership_role has only one value."*

The only thing resembling a role is the operator allowlist in `services/api/app/admin.py`:

```python
def require_admin(user: CurrentUser, settings: Config) -> str:
    allowed = settings.admin_ids          # from ADMIN_USER_IDS, a comma-separated env var
    if not allowed or user.id.lower() not in allowed:
        raise HTTPException(status_code=403, detail="not authorised")
    return user.id
```

Bound to the immutable auth user id, never to an email, checked on every admin endpoint.
`/api/admin/session` answers `{"is_admin": bool}` to any signed-in caller so the header knows
whether to draw a link; the comment there says what matters: *"Hiding a link is never the
control."*

### 2.4 Dependencies every product route composes

From `services/api/app/auth.py`: `require_features()` (404 where `FEATURES_ENABLED` is off),
`refuse_on_public_demo()` (404 for private paths where `PUBLIC_DEMO_ONLY` is set),
`require_client_header()` (CSRF), and `current_user()` yielding `AuthUser(id, email)`.

Discovery composes all four and adds `require_discovery()`. `require_client_header` sits on the
admin router and on the stakeholder routes that write, not on reads, which is how the existing
routers already split it.

### 2.5 Schema conventions

24 migrations in `db/migrations`, applied by `db/migrate.py`, which checksums applied files and
refuses a changed one as drift. Three patterns matter here.

**Service-role-only tables get RLS and no policies.** Migration 0002 ends with:

> `-- visitors, workspaces, connections, sync_batches, raw_events and transaction_sources`
> `-- intentionally have no client policies: service role only.`

That is the house pattern for a table the browser never reads, and it is the pattern discovery
now follows.

**Composite foreign keys that make a guessed id useless.** `classification_suggestions` carries
both `workspace_id` and `business_id` and references `workspaces (id, business_id)`. A row
cannot point at a workspace belonging to another business even if an id is guessed.

**Append-only decision history.** `category_overrides`: *"A new row supersedes the previous one
for that transaction by time; nothing is updated in place, so the history of who decided what
stays readable."*

### 2.6 The two flows a stakeholder exercises

**Statement upload.** Create import, browser uploads straight to private Storage with a one-path
signed token, mark uploaded, parse and stage under a leased job, map columns, review rows,
confirm. Confirmation is pinned to a `draft_revision` and is atomic.

**Classification.** Deterministic rules place what they can. A model proposes for the rest into
`classification_suggestions`, which changes no figure; a person's decision writes
`category_overrides`, and only that is read back. Separately `app/ai/cashflow.py` sorts every
row of an import into eleven cash-flow buckets and exports the reviewed result as CSV.

Both require a **private business**, which is what account creation must not forget.

### 2.7 Deployment and testing

Two Vercel projects, `cashlens-web` and `cashlens-api`, both deploying from `main`. CI is
`.github/workflows/ci.yml`: web runs lint, typecheck and build; api runs `ruff check`,
`ruff format --check`, `pytest` and two synthetic fixture checks.

418 test functions across 25 files in `services/api/tests`, using a `httpx.MockTransport` fake of
PostgREST, Auth and Storage injected through `app.dependency_overrides[get_supabase]`.
`db/checks/*.py` hold end-to-end scripts against the real development database, including
`rls_check.py`, which switches Postgres roles inside a rolled-back transaction.

---

## 3. Design decisions

### 3.1 Roles live in a table, not an environment variable

`ADMIN_USER_IDS` works for one operator and one dashboard. It does not work here: three
stakeholder accounts would mean a second env var edited to onboard anyone, and the role has to
be readable in a query that joins responses to people.

So: a `profiles` table, one row per auth user, carrying `role app_role not null default 'member'`
where `app_role` is `('admin', 'stakeholder', 'member')`, read by Python on every request.

**The existing `ADMIN_USER_IDS` allowlist is left alone.** The AI usage dashboard keeps working
exactly as it does. This leaves two notions of admin; see risk R1 for the test that stops them
drifting and the phase that converges them.

### 3.2 Assignment is the scope, and it is the choke point

A stakeholder has an **assignment**: one row joining a user to a template, carrying their
progress. Everything they can read or write hangs off it.

```python
def resolve_assignment(assignment_id, user, supabase) -> AssignmentScope:
    """The caller's own assignment, or 404. Modelled on resolve_workspace()."""
```

An admin resolving someone else's assignment goes through a **different, explicitly named**
dependency, so no single code path can be widened by accident:

```python
# stakeholder routes, by assignment id, filtered to the caller
Mine = Annotated[AssignmentScope, Depends(resolve_own_assignment)]

# admin routes, by stakeholder id, because the admin surface is organised by person
def resolve_assignment_for(user_id, user, supabase) -> AssignmentScope: ...
```

Reading a route's signature tells you which it has, and the two take different path parameters,
so a stakeholder route cannot be handed admin scope without changing its URL. An admin calling a
`Mine` route gets the same 404 as anybody else; the admin surface does not share those routes.

### 3.3 FastAPI is the only data interface. Discovery tables are service-role only

Revision 1 proposed `grant select` to `authenticated` plus narrow RLS policies, so a stakeholder
could read their own rows directly through PostgREST. That is now **removed**, for four reasons:

1. **Nothing needs it.** The browser makes no data reads against Supabase, anywhere in the
   application. There is no runtime dependency to preserve.
2. **It would undermine server-side branching.** Branch conditions are evaluated in the API, and
   a hidden question is simply absent from the payload. A stakeholder who could `select * from
   discovery_questions` would see every question in their template, including ones the branch
   logic deliberately withheld. A read grant would leak the question bank's shape even though it
   leaked nobody's answers.
3. **It is a second authorization surface for one feature.** Two enforcement paths mean two
   places to get it right, two places to test, and two places for a future migration to loosen.
4. **It is against the house pattern.** `workspaces`, `visitors`, `raw_events`,
   `sync_batches`, `connections` and `transaction_sources` are all already service-role only
   with RLS enabled and no policies. Discovery is the same kind of table.

So the schema is: **RLS enabled on all eleven new tables (ten `discovery_*` plus `profiles`), no
grants to `anon` or `authenticated`, and no policies.** RLS with no policies denies every row to every
non-superuser role, which is a *stronger* default than a permissive policy: if someone later
adds a `grant select` by mistake, the tables still return nothing until a policy is written
deliberately.

The policies that revision 1 proposed are preserved in [schema appendix A](discovery-schema.md#appendix-a-policies-if-direct-access-is-ever-required)
as the work to do **if** a future feature genuinely needs direct access, so the thinking is not
lost and the bar for enabling it is explicit.

### 3.4 No SECURITY DEFINER role helper ships in 0025

Revision 1 proposed `public.app_role(p_user_id uuid default auth.uid())`. Two problems: the
parameter let any authenticated caller ask about any user id, and a definer function is exactly
where that kind of widening goes unnoticed.

With 3.3 in place there are no policies, so there is **no SQL caller for a role helper at all**.
Python reads `profiles` with the service role, which is the normal server-side query path. So
no role helper is defined in migration 0025.

If direct access is ever enabled, appendix A defines the helper as a **zero-argument**
`public.current_app_role()` reading `auth.uid()` only, with no way to ask about anyone else.

### 3.5 Shared sections, so the 70 founder questions exist exactly once

The question architecture is 70 shared founder questions plus 30 each for Lead and Partner, and
80 engineering questions for Engineer. The 70 must be *truly identical* across Lead and Partner, or
the comparison views compare two things that merely look alike.

Revision 1's schema made the **template** both the unit of assignment and the unit of question
ownership, which cannot express "shared by two templates" without duplicating rows. Three ways
out were considered.

| Option | Shape | Verdict |
|---|---|---|
| Duplicate the 70 into two templates, kept in step by the seed loader | Two rows per shared question | **Rejected.** Identity would be a property of the loader's care, not of the database. Two rows can drift through any direct write, and `question_version` would have to be synchronised by hand. Against the grain of a codebase that makes guarantees structural. |
| Multiple assignments per stakeholder (`founder-shared` + `lead-specific`) | One row per question, two assignments per person | **Rejected.** Zero schema change, but every runtime invariant becomes plural: progress, resume, `current_section`, core completion, the soft gate, the login redirect and every admin view all become "across my assignments". Five or six places for a bug, to avoid one join table. |
| **Sections become the shareable unit** | One row per question, one assignment per person | **Chosen.** |

A template is an **ordered list of sections**, and a section may appear in more than one
template:

```
discovery_templates ──< discovery_template_sections >── discovery_sections ──< discovery_questions

founder-lead   = [ founder-core-1 … founder-core-N , lead-1 … lead-M ]
founder-partner     = [ founder-core-1 … founder-core-N , partner-1   … partner-M   ]
engineering      = [ engineering-1  … engineering-K ]
                     └─────────── the same section rows ───────────┘
```

Why sections rather than a new "question set" entity: sections already exist, and are already
the unit of navigation, progress and estimated time. Sharing at section granularity needs one
join table and no new concept. A separate set entity would make it templates → sets → sections →
questions, four levels where three do the job.

What this buys, and it is the whole point: Lead's and Partner's responses to a shared question
carry **the same `question_id`**. Comparison is an equality on a primary key, not a match on a
text label. Versioning is one row's `question_version`, so the two can never disagree. A
question edited once is edited for both, and neither can drift.

The cost is one join table and one extra denormalised column on `discovery_responses`; see
[schema](discovery-schema.md) section 4.4. It is a real schema change from revision 1.

**One constraint follows from sharing.** A `branch_condition` may reference only a question **in
the same section**. A shared section can sit at different positions in different templates, so a
condition pointing at another section would be answerable in one template and unanswerable in
another. Same-section-only makes a shared section self-contained, which is what makes it safe to
share. The loader enforces it.

### 3.6 Scenario history is complete, and written by a trigger

Revision 1 left scenario-field revisions partly uncovered: the response-history trigger captured
the parent row, but `decision`, `confidence`, `reason_codes`, `maximum_exposure_minor` and the
three free-text fields live in their own table and were not versioned.

That is now fixed in the same migration. `discovery_scenario_response_history` is an append-only
table written by a `before update or delete` trigger on `discovery_scenario_responses`, carrying
every typed column. It is not optional and not a later enhancement.

The reason it is a trigger rather than application code is the same reason it is for responses:
history written by application code is history a future code path can skip, including a manual
SQL fix or a service-role script. A trigger cannot be skipped by any caller.

### 3.7 Shared underwriting examples are synthetic scenarios, not shared financial data

Lead and Partner must answer the **same** underwriting case for a disagreement to mean anything.
The way to do that is not a shared business holding shared uploads; it is a scenario question
carrying its own synthetic case.

So a `scenario_decision` question carries `scenario_brief jsonb`: a made-up applicant, made-up
figures, made-up history, committed in the fixture and identical for everyone who sees it. A
check constraint requires it for that question type and forbids it for every other.

Each stakeholder still gets **their own private business** for exercising the prototype, so
their uploads are isolated from one another. Nothing operational is shared.

### 3.8 Core versus deep dive, and the soft gate

Each question carries `required_core boolean`. Core completion is derived, never a stored flag
someone can forget to update:

```
core_complete(assignment) = no visible question with required_core = true is unanswered
```

Visible means after branch evaluation, so a skipped branch cannot block completion forever.

The gate is soft. `/discovery` is the **post-login destination** for a `stakeholder` user until
core is complete. It is not a guard: the header keeps its normal links, `/workspace`, `/demo`,
uploads and classification are reachable at any time by link or by typing the URL, and nothing
redirects away from `/workspace`. Once core is complete the destination becomes `/workspace` and
`/discovery` stays in the header.

Implemented as two optional fields on the existing `/api/me` response, `role` and `discovery`,
which is what the login redirect reads. The portal's own richer `GET /api/discovery/me` is a
separate route serving the portal itself.

### 3.9 Sensitivity is structural

`sensitivity` is `('low', 'medium', 'high')`, with a check constraint:

```sql
constraint high_sensitivity_is_never_core check (not (sensitivity = 'high' and required_core))
```

So "clearly marked optional" cannot drift into "optional in the interface but required by the
completion calculation". The interface additionally shows an explicit Skip control and a short
line saying why the question is being asked.

### 3.10 Typed columns for scenario answers

`scenario_decision` could be JSON in the generic answer column. It should not be: the admin
surface has to answer "where do Lead and Partner disagree", which is a query over columns, not a
scan over blobs. `maximum_exposure_minor` is **signed integer minor units**, because `CLAUDE.md`
requires it of all money and a float would be the one place the rule broke.

### 3.11 Saving is boring, and progress is computed

`Save & Continue` and `Save & Exit` are the same write: one `PUT` per question, idempotent, no
draft state and no separate autosave path. `Resume` is opening `/discovery` again, because the
assignment knows `current_section_id`. A `discovery_sessions` table records visits, which is
what lets the published "estimated completion time" be checked against measured elapsed time
rather than guessed forever.

### 3.12 Nothing here goes near the AI budget

Discovery makes no model calls, and touches no reservation, run row or milestone budget. The
stakeholders' use of the prototype does, through the existing reserve-before-dispatch path,
unchanged. See risk R6.

---

## 4. Threat model

Stated after the revision in 3.3, because the revision changes it materially.

### 4.1 Assets

1. Stakeholder answers, including scenario decisions and their history. Commercially sensitive
   to the customer.
2. The question bank, including questions a branch condition is currently withholding.
3. Role assignments in `profiles`.

### 4.2 Actors and paths

| Actor | Credential | Path available |
|---|---|---|
| Anonymous visitor | none | `/api/*` unauthenticated; PostgREST with the publishable key and no JWT |
| `member` (any public signup) | own JWT | `/api/*`; PostgREST with publishable key + own JWT |
| `stakeholder` stakeholder | own JWT | same |
| `admin` (the author) | own JWT | same |
| Operator | service role key | everything |

### 4.3 What each path yields, after the revision

| # | Attack | Outcome | Enforced by |
|---|---|---|---|
| T1 | Lead calls `/api/discovery/assignments/{partner_id}` with his own valid token | 404 | **Python.** `resolve_own_assignment()` filters on `user_id = caller`. |
| T2 | Lead calls any protected `/api/admin/discovery/*` route | 403 | **Python.** `require_role("admin")` as a per-route dependency on every route except `/session`, which answers a boolean to any signed-in caller and is the documented exclusion. See [plan 5.1](discovery-implementation-plan.md#51-api). |
| T3 | Lead calls PostgREST directly: `GET /rest/v1/discovery_responses` with the publishable key and his JWT | permission denied for table | **Grants.** `authenticated` has no privilege on any discovery table. |
| T4 | Anyone calls PostgREST directly with the publishable key and no JWT | permission denied for table | **Grants.** `anon` has no privilege either. |
| T5 | A future migration adds `grant select ... to authenticated` by mistake | zero rows | **RLS.** Enabled with no policies denies everything until a policy is written deliberately. |
| T6 | Lead reads questions a branch condition withheld | not possible | **No read path.** The API never serves a hidden question, and T3 closes the direct route. |
| T7 | A service-role bug files Partner's answer under Lead's assignment | insert rejected | **Composite foreign keys.** A response's `user_id` must equal its assignment's owner. |
| T8 | An answer is changed and the earlier value lost | not possible | **Triggers.** History on both the response and the scenario tables, unskippable by any caller. |
| T9 | Someone obtains the service role key | full compromise | **Nothing.** Unchanged from today and out of scope for this feature. |
| T10 | A stakeholder reads another's uploaded statements | 404 | **Existing.** `resolve_workspace()`, unchanged; each stakeholder holds their own private business. |
| T11 | An assignment is pointed at a section from somebody else's template, so a resume link crosses templates | write rejected | **Composite foreign key.** `discovery_assignments (template_id, current_section_id)` references `discovery_template_sections (template_id, section_id)`. Null stays valid under `MATCH SIMPLE`. |

### 4.4 Residual risk

**T9 is unmitigated and is the honest ceiling on every claim here.** The key lives only in
server-side hosting settings, is never in a `NEXT_PUBLIC_` variable, never in a log and never in
the repository. That is the existing posture for every table in this database, and this feature
neither improves nor worsens it.

**Production and development share one Supabase project.** So stakeholder answers will sit in
the same database as the public synthetic demo. The additive migration, the absence of any
`anon` grant and the `DISCOVERY_ENABLED` default all bound the exposure, but the durable fix is
a separate production project, which remains not done. See risk R3.

**RLS is dormant, not active, on the application path.** Stated plainly so nobody reads the
policy list in appendix A and concludes it is running. There are no policies in 0025. What
stops T3 and T4 is the absence of a grant; what would stop T5 is RLS with nothing to permit it.

---

## 5. How the pieces sit in the existing structure

### 5.1 Backend

A new package `services/api/app/discovery/`, mirroring `app/imports/`:

```
app/discovery/
  __init__.py
  auth.py       require_role(), resolve_own_assignment(), resolve_assignment_for()
  routes.py     stakeholder routes
  admin.py      admin routes, mounted under /api/admin/discovery
  models.py     Pydantic request and response models
  answers.py    answer validation per question_type, and the scenario shape
  progress.py   pure functions: visible questions, core completion, percentages
  seed.py       idempotent question-bank loader, mirrors app/synthetic/seed.py
```

`progress.py` is pure functions over plain data, like `app/analysis/metrics.py`, which is what
makes the completion arithmetic testable without a database.

Two routers registered in `app/main.py`, one line each, next to the existing ten.

### 5.2 Frontend

```
app/discovery/page.tsx                 the stakeholder portal
app/discovery/[sectionSlug]/page.tsx   one section
app/discovery/answers/page.tsx         the caller's own previous answers
app/admin/discovery/page.tsx           admin overview
app/admin/discovery/[userId]/page.tsx  one stakeholder's answers
components/discovery-shell.tsx         session, role, assignment. Mirrors workspace-shell
components/discovery-overview.tsx      welcome, title, estimate, sections, progress, Resume
components/discovery-section.tsx       one section's questions
components/question-field.tsx          one question, dispatching on question_type
components/scenario-field.tsx          the scenario_decision composite, including the brief
components/discovery-progress.tsx      overall and per-section progress
components/admin-discovery-view.tsx    the admin read surface
lib/discovery-api.ts                   typed client, mirrors imports-api.ts
```

All client components using `useSession()` and `api()` from `lib/api.ts`, which is what every
existing page does. `components/ui.tsx` already provides every primitive needed except a
progress bar, and `cashflow-review.tsx` already contains one to lift.

### 5.3 One deliberate departure from an existing pattern

`category_overrides` is purely append-only: newest row wins, reads sort by time. Discovery does
not do that.

A questionnaire read is "show me my current answer to each of 100 questions". Purely append-only
makes that a per-question `order by created_at desc limit 1`, a hundred times, per render. So
discovery keeps **current-answer tables updated in place** (`discovery_responses` and
`discovery_scenario_responses`) plus **append-only history tables written by triggers**.

The trigger is what makes the departure an improvement rather than a compromise: it cannot be
skipped by any caller, which is a stronger guarantee than the pattern it departs from.

### 5.4 Analytics

`apps/web/src/lib/analytics.ts` excludes screens carrying private material by prefix. `/admin`
already covers `/admin/discovery`. `/discovery` must be added, because answers to underwriting
questions are the customer's commercial material and a section slug could travel in a page title.
Two lines, the exclusion and a `/discovery/[section]` branch in
`routeLabel()`, plus a test asserting it.

---

## 6. What this design deliberately does not do

- **No parallel auth.** Supabase Auth, as used today.
- **No hard gate.** Nothing redirects a stakeholder away from the prototype.
- **No direct database access for the browser.** FastAPI is the sole data interface.
- **No new analytics pipeline, job queue or scheduler.**
- **No model calls.**
- **No change to** `resolve_workspace()`, the import flow, the classification flow, the AI
  budget, or the existing admin dashboard.

---

## 7. Decisions taken at review

| # | Decision |
|---|---|
| D1 | CashLens is the public name everywhere. Discovery is used only behind authentication. Recorded in [ADR 0014](decisions/0014-discovery-portal.md). |
| D2 | Question-bank fixtures are committed and version controlled; the repository is private. Answers never appear in a fixture or any repository file. No `local-data` indirection now. |
| D3 | Lead, Partner and Engineer each get their own private business. No shared operational business. Shared underwriting examples are synthetic scenario briefs (3.7). |
| D4 | Supabase invite links only. No preassigned temporary passwords. The stakeholder sets their own password. |
| D5 | Discovery tables are service-role only: no grants, no policies, RLS enabled (3.3). |
| D6 | No SECURITY DEFINER role helper in 0025 (3.4). |
| D7 | Sections are the shareable unit, so the 70 founder questions exist once (3.5). |
| D8 | Scenario history is trigger-written and complete, in 0025 (3.6). |

## 8. Remaining open questions

1. **Per-identity daily AI cap.** Two numbers are in play and neither is obviously right. The
   committed default is **$0.25** (`ai_identity_daily_cost_cap_micro = 250_000` in
   `services/api/app/config.py`), which is less than one full 1,500-row cash-flow run. The
   `cashlens-api` production deployment was set to **$8** during the last release, about four
   such runs a day each, and that value lives only in the hosting settings: it is recorded
   nowhere in the repository. Three stakeholders draw on the same $25 milestone ledger. Worth
   setting deliberately, and worth writing down, before invites go out. See risk R6.

2. **When the repository goes public.** ADR 0014 holds while the repository is private. Making
   it public requires revisiting that decision first, because the repository itself contains the
   name and the question bank.

3. **Whether the deep dive is ever finished.** 100 questions is a lot of screen, and no schema
   fixes that. The core-versus-deep-dive split makes the first useful milestone the core set.
   This is a content question, not an engineering one.

## 9. Changes from revision 1

| Area | Revision 1 | Revision 2 |
|---|---|---|
| Direct PostgREST access | `grant select` to `authenticated` plus narrow RLS policies | No grants, no policies, RLS enabled. FastAPI is the sole interface. Policies moved to schema appendix A as future work. |
| Role helper | `app_role(p_user_id uuid default auth.uid())`, callable with any id | Not shipped. Zero-argument form documented in appendix A only. |
| Scenario history | Noted as an open item, "one extra block if wanted" | Required in 0025: own table, own trigger, every typed column. |
| Question structure | One template per stakeholder group, `compare_key` for comparison | Sections shared across templates; shared questions are the same rows, compared by `question_id`. |
| Templates | 2 | 3 (`founder-lead`, `founder-partner`, `engineering-engineer`), composed from shared and specific sections. |
| Scenario stimulus | Not modelled | `scenario_brief jsonb` on the question, synthetic, committed in the fixture. |
| Branch conditions | Any earlier question in the template | Same section only, so a shared section is self-contained. |
| Threat model | Two paragraphs inside a section | Its own section with eleven enumerated attacks and a residual-risk statement. |
| Phases | Nothing visible until phase 4 | Thin vertical slice at phase 2; security gate at phase 3 before any real stakeholder data. |
| Naming conflict | Open question | Decided and recorded in ADR 0014. |

### Two corrections at final review

| Area | As first drafted in revision 2 | Corrected |
|---|---|---|
| Admin session route | `require_role("admin")` as a router-level dependency, while `/session` was also promised to answer any signed-in caller. Contradictory: the router dependency runs first, so `/session` would have refused the one caller it exists for. | Router carries the deployment guards only. `require_role("admin")` is a per-route dependency on every route except `/session`, matching the shape `services/api/app/admin.py` already uses. The protected-route test derives its list from `app.openapi()` minus that one documented exclusion, so a new unguarded route fails rather than ships. |
| `current_section_id` | Referenced `discovery_sections (id)` directly, with the invalid state acknowledged and left to the API to avoid. | References `discovery_template_sections (template_id, section_id)` compositely, so an assignment structurally cannot point outside its own template. Null stays valid under `MATCH SIMPLE`; removing a section from a template nulls the pointer and leaves `template_id` intact. |

These are the only changes since the revision-2 draft. No other schema change was introduced.
