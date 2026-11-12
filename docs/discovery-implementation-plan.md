# Discovery Portal: implementation plan

Status: **revision 2, implementation-ready.** No code, migration, fixture or account has been
created. Phase 1 is the next step.

Changes from revision 1, and the two corrections made at final review, are listed in
[architecture section 9](discovery-architecture.md#9-changes-from-revision-1). Both
corrections touch this document: the admin router's guards in 5.1, and the tests in 10.1 and
10.3.

Companion documents: [architecture](discovery-architecture.md),
[schema](discovery-schema.md),
[ADR 0014](decisions/0014-discovery-portal.md).

---

## 1. Existing files to reuse

Nothing in this list is rewritten. These are the things the feature is built out of.

### Backend

| File | Reused for |
|---|---|
| `services/api/app/auth.py` | `current_user`, `CurrentUser`, `require_client_header`, `require_features`, `refuse_on_public_demo`, `bearer_token`. Discovery routes compose all of them and add one. |
| `services/api/app/workspaces.py` | `resolve_workspace()` at line 78 is the **template** for `resolve_own_assignment()`: identity from the session, 401 without identity, 404 for anything the caller cannot reach, a frozen scope object as the return. Also `_as_uuid()`. |
| `services/api/app/admin.py` | The shape of an admin router: a guard returning the caller's id, a `/session` route answering a boolean rather than 403, bounded reads with `SCAN_LIMIT` and truncation reported, and an explicit field allowlist so a private column cannot be selected by accident. |
| `services/api/app/supabase.py` | `SupabaseClient.rest`, `rpc`, `count`, `rest_all` (which refuses an unordered query, so pagination cannot truncate silently), `auth_user` with its short-lived cache. No new client. |
| `services/api/app/config.py` | `Settings` and `get_settings`. Three new fields, all optional, all safe by default. |
| `services/api/app/main.py` | Router registration. Two new lines. |
| `services/api/app/synthetic/seed.py` | The seed pattern: read JSON fixtures, derive deterministic ids from stable keys, build one SQL transaction, refuse to clobber unless asked. Also `stable_id()` from `app/synthetic/generator.py`. |
| `services/api/app/analysis/metrics.py` | The precedent for pure functions over plain data, which is how `progress.py` is written and why it is testable without a database. |
| `services/api/app/imports/cashflow_routes.py` | The most recent self-contained router sharing an existing prefix and existing authorization: the shape to copy. |

### Frontend

| File | Reused for |
|---|---|
| `apps/web/src/lib/api.ts` | `api<T>()` and `ApiError`. Every discovery call goes through it. No new fetch wrapper. |
| `apps/web/src/lib/use-session.ts` | `useSession()`. No new session handling. |
| `apps/web/src/components/workspace-shell.tsx` | The template for `discovery-shell.tsx`: Suspense boundary, session states, a loaded callback, the not-signed-in and not-available branches already written correctly. |
| `apps/web/src/components/ui.tsx` | `Card`, `CardTitle`, `Stat`, `Chip`, `Notice`, `Field`, `FieldGroup`, `Disclosure`, `Empty`, `ActionRow`, `inputClass`, `selectClass`, `textareaClass`, `buttonClass`, `secondaryButtonClass`, `Icon`. The questionnaire needs no new primitive. |
| `apps/web/src/components/cashflow-review.tsx` | A working progress bar with the right ARIA attributes. Lift it into `ui.tsx` as `ProgressBar` rather than writing a second one. |
| `apps/web/src/components/site-header.tsx` | The `isAdmin` pattern: ask the server, store the answer with the token it was given for, draw a link. Discovery adds one link the same way. |
| `apps/web/src/lib/imports-api.ts` | The typed-client shape: exported types, one function per endpoint, a `root()` helper. |
| `apps/web/src/lib/analytics.ts` | `EXCLUDED` and `routeLabel`. One prefix added, one branch added. |

### Database and checks

| File | Reused for |
|---|---|
| `db/migrate.py` | Applies `0025`. No change to the tool. |
| `db/migrations/0002_foundation.sql` | The revoke and grant conventions, and the stated precedent for service-role-only tables with RLS and no policies. |
| `db/migrations/0018_ai_runs_overrides_and_usage.sql` | The append-only history stance, and composite foreign keys that make a guessed id useless. |
| `db/checks/rls_check.py` | The pattern for verifying access for real: one transaction as `postgres`, throwaway rows and a throwaway auth user, `set local role` to `anon` and `authenticated`, assert, roll back. |
| `db/checks/import_flow_check.py` | Creating and deleting a temporary auth user through the Auth admin API, which both the account seed and the flow check need. |
| `services/api/tests/conftest.py` | `isolated_settings` (clears env, ignores the developer's `.env`) and the `db` fixture. |
| `services/api/tests/test_imports.py` | `FakeBackend`: a small PostgREST, Auth and Storage over `httpx.MockTransport`. Subclass it, as `tests/test_cashflow.py` already does. (`tests/test_admin_dashboard.py` subclasses a different fake, `FakeDb` from `tests/test_ai_brief.py`, which `conftest.py`'s `db` fixture also drives.) |
| `services/api/tests/test_public_demo_only.py` | The existing parameterised deployment-gate test. Discovery paths are added to it rather than duplicated. |

---

## 2. Schema and migrations

One migration, `db/migrations/0025_discovery.sql`, fully specified in
[schema](discovery-schema.md) section 10.

Four enum types, **eleven tables**, two history triggers, one `auth.users` profile trigger, the
revoke and grant block, RLS on all eleven, **no policies and no role helper**, and a closing
`notify pgrst, 'reload schema'`.

**No existing table is altered and no existing function is replaced.** Purely additive, which is
what makes it safe to apply to the development database that production also reads.

```bash
python3 db/migrate.py status
python3 db/migrate.py apply
```

---

## 3. Auth changes

### 3.1 What changes

Nothing about sign-in, sessions, tokens, password reset or the Supabase Auth configuration. The
three accounts are ordinary Supabase Auth users.

What is added is **a role, read from a table by Python**, and two dependencies that use it.

New file `services/api/app/discovery/auth.py`:

```python
Role = Literal["admin", "stakeholder", "member"]

def current_role(user: CurrentUser, supabase: Supabase) -> Role:
    """The caller's application role, from profiles. Absent means 'member'."""

def require_role(*allowed: Role):
    """A dependency factory. 403 when the caller's role is not in `allowed`."""

def require_discovery() -> None:
    """404 unless DISCOVERY_ENABLED. Composed alongside require_features."""

@dataclass(frozen=True)
class AssignmentScope:
    assignment_id: str
    user_id: str
    template_id: str
    role: Role

def resolve_own_assignment(assignment_id, user, supabase) -> AssignmentScope:
    """The caller's OWN assignment, or 404. The choke point for every stakeholder route."""

def resolve_assignment_for(user_id, user, supabase) -> AssignmentScope:
    """A named stakeholder's assignment, for an admin only. 403 for anyone else.

    Keyed on the stakeholder's user id, not on an assignment id, because the admin surface is
    organised by person: /api/admin/discovery/stakeholders/{user_id}/responses. Nothing an
    admin calls takes an assignment id, so there is no admin variant of the `Mine` dependency
    and no route that accepts either scope.
    """

Mine = Annotated[AssignmentScope, Depends(resolve_own_assignment)]
```

Two separately named resolvers rather than one with a flag, so a route's signature says which
scope it has and no single code path can be widened by adding a parameter. They also take
**different arguments**, which is the stronger version of the same idea: a stakeholder route
cannot accidentally be given admin scope, because it would have to change its path parameter to
do it. An admin calling a `Mine` route gets the same 404 as anybody else.

**There is no SQL role helper.** With no RLS policies there is no SQL caller for one, and a
`security definer` function taking an arbitrary user id is exactly where widening goes
unnoticed. See [schema](discovery-schema.md) section 6.3.

### 3.2 Three new settings

```python
# Discovery portal. Off by default, so a deployment is only ever opted in deliberately and the
# public synthetic demo never carries it.
discovery_enabled: bool = False
# Whether an account may self-assign a template. Off: assignments come from the seed.
discovery_self_assign: bool = False
# Fallback template for a self-assigned or throwaway account, as phase 2 uses. The three named
# stakeholders are assigned per person by the seed script (3.4 step 5), not from this setting.
discovery_default_template: str = "founder-lead"
```

`require_discovery()` sits alongside `require_features()` and `refuse_on_public_demo()`, so the
portal is absent from the public demo deployment three times over.

### 3.3 The existing admin allowlist is untouched

`ADMIN_USER_IDS` and `require_admin` in `services/api/app/admin.py` keep gating the AI usage
dashboard exactly as they do. Discovery admin authorization comes from `profiles.role = 'admin'`.

Two notions of admin, deliberately, because the alternative is editing working, tested
authorization code for a feature that does not need it. See risk R1 for the test that stops them
drifting and the phase that converges them.

### 3.4 Account creation: invite links only

Decision D4: **Supabase invite links. No preassigned temporary passwords.** The stakeholder sets
their own password. The temporary-password fallback described in revision 1 is removed from this
plan; if it is ever wanted it is a new request.

New script `scripts/seed_customer_accounts.py` (not `db/checks/`, because it is not a check):

```bash
python3 scripts/seed_customer_accounts.py --dry-run
python3 scripts/seed_customer_accounts.py --only lead
python3 scripts/seed_customer_accounts.py
```

Reads from the environment, never from a file in the repository:

```
STAKEHOLDER_LEAD_EMAIL
STAKEHOLDER_PARTNER_EMAIL
STAKEHOLDER_ENGINEER_EMAIL
STAKEHOLDER_ADMIN_EMAIL              # the author's existing account, matched not created
SUPABASE_URL
SUPABASE_SERVICE_ROLE_KEY
```

For each stakeholder, in order:

1. Look the email up through the Auth admin API. If a user exists, **use it**; never create a
   duplicate, never touch a password.
2. If none exists, create one with `email_confirm: true` and **no password**, then generate an
   invite link (`POST /auth/v1/admin/generate_link`, `type: "invite"`). Print it to the terminal
   for the author to send. Never written to a file, never committed.
3. Upsert `profiles` with `role = 'stakeholder'` and `first_name`.
4. Call the existing `provision_private_business` RPC, so the stakeholder has **their own**
   private business and can upload and classify (decision D3). This is the step that is easy to
   forget and that makes the difference between "can log in" and "can use the prototype".
5. Insert one `discovery_assignments` row: `founder-lead` for Lead, `founder-partner` for Partner,
   `engineering-engineer` for Engineer.

For the author: match `STAKEHOLDER_ADMIN_EMAIL` to an existing verified user, **refuse if there is
none**, and set `profiles.role = 'admin'`. Never create it, never touch its password.

**What the script never does:** write a password to disk, print a service role key, modify an
existing user's password, create a shared business, or grant anything on GitHub or Vercel.

---

## 4. Access control at the database

Fully specified in [schema](discovery-schema.md) section 6. Revised from revision 1.

- `anon` and `authenticated` get **nothing**: no select, no insert, no update, no delete, on any
  of the eleven tables.
- `service_role` gets the four verbs.
- RLS is **enabled on all eleven tables with no policies**, matching the stated precedent in
  migration 0002 for `visitors`, `workspaces`, `raw_events`, `sync_batches`, `connections` and
  `transaction_sources`.
- No `security definer` role helper ships.

The policies revision 1 proposed are preserved in
[schema appendix A](discovery-schema.md#appendix-a-policies-if-direct-access-is-ever-required)
as the work to do if a proven requirement ever needs direct access, along with three rules for
doing it safely.

The threat model that results is [architecture section 4](discovery-architecture.md#4-threat-model),
with eleven enumerated attacks and a residual-risk statement. The short version: **Python stops the
application path, grants stop the direct path, and RLS is dormant insurance against a future
accidental grant.**

---

## 5. Routes

### 5.1 API

New router `services/api/app/discovery/routes.py`, prefix `/api/discovery`, dependencies
`[require_features, refuse_on_public_demo, require_discovery]`.

| Method | Path | Scope | Purpose |
|---|---|---|---|
| GET | `/api/discovery/me` | signed in | Role, assignment id, title, progress, current section, core completion. Answers for a `member` too, with `assignment: null`. |
| GET | `/api/discovery/assignments/{id}` | `Mine` | Overview: the template's sections in order, per-section progress, estimated minutes, which are core. |
| GET | `/api/discovery/assignments/{id}/sections/{slug}` | `Mine` | That section's **visible** questions after server-side branch evaluation, each with the caller's current answer and, for a scenario question, its `scenario_brief`. |
| PUT | `/api/discovery/assignments/{id}/responses/{question_id}` | `Mine` | Save one answer, including the scenario composite. Idempotent. History is written by the triggers. Requires the client header. |
| POST | `/api/discovery/assignments/{id}/responses/{question_id}/skip` | `Mine` | Explicitly skip an optional question. Refused for a core question. |
| GET | `/api/discovery/assignments/{id}/responses` | `Mine` | Every answer the caller has given. |
| GET | `/api/discovery/assignments/{id}/responses/{question_id}/history` | `Mine` | The caller's own change history for one question, including scenario fields. |
| POST | `/api/discovery/assignments/{id}/sessions` | `Mine` | Open or touch a session row. Fire and forget; a failure never blocks a save. |

New admin router `services/api/app/discovery/admin.py`, prefix `/api/admin/discovery`.

**Router-level dependencies are the deployment guards only:**
`[require_client_header, refuse_on_public_demo, require_discovery]`. **`require_role("admin")` is
a per-route dependency, not a router-level one.**

Revision 2's first draft put the role check on the router and also promised that `/session`
would answer any signed-in caller without 403. Those cannot both hold: a router-level dependency
runs before the handler, so `/session` would have refused every non-admin, which is the one
caller it exists to answer.

The fix is the shape the existing `services/api/app/admin.py` already uses. Its router carries
only `require_client_header` and `refuse_on_public_demo`; `admin_session()` takes `CurrentUser`
and answers a boolean, while `ai_usage()` and `ai_runs()` each take `_admin: AdminUser`. Reading
a handler's signature tells you whether it is guarded. Discovery copies that exactly:

```python
DiscoveryAdmin = Annotated[str, Depends(require_role("admin"))]

router = APIRouter(
    prefix="/api/admin/discovery",
    tags=["discovery-admin"],
    dependencies=[
        Depends(require_client_header),
        Depends(refuse_on_public_demo),
        Depends(require_discovery),
    ],
)

@router.get("/session")
def discovery_session(user: CurrentUser, supabase: Supabase) -> dict[str, bool]:
    """Whether this signed-in caller may read the discovery admin views.

    Never 403: it is the question, not a protected answer. 401 without a session, because an
    anonymous caller has no role to report.
    """
    return {"is_discovery_admin": current_role(user, supabase) == "admin"}

@router.get("/progress", response_model=ProgressView)
def progress(supabase: Supabase, _admin: DiscoveryAdmin) -> ProgressView:
    ...
```

| Method | Path | Guard | Purpose |
|---|---|---|---|
| GET | `/api/admin/discovery/session` | **authenticated only** | `{"is_discovery_admin": bool}` for any signed-in caller, so the header knows whether to draw a link. 401 anonymous, 200 with `false` for `stakeholder` and `member`, 200 with `true` for an admin. The only unguarded route on this router. |
| GET | `/api/admin/discovery/progress` | `DiscoveryAdmin` | All three: status, core done over core total, answered total, last activity. |
| GET | `/api/admin/discovery/stakeholders/{user_id}/responses` | `DiscoveryAdmin` | One stakeholder's answers, paginated. |
| GET | `/api/admin/discovery/comparison` | `DiscoveryAdmin` | Shared questions grouped by `question_id`, plus cross-template pairs grouped by `compare_key`. |
| GET | `/api/admin/discovery/scenarios/disagreements` | `DiscoveryAdmin` | Scenario questions where decisions differ. |
| GET | `/api/admin/discovery/scenarios/changes` | `DiscoveryAdmin` | Where somebody changed an underwriting decision, from the scenario history. |
| GET | `/api/admin/discovery/gaps` | `DiscoveryAdmin` | Unanswered core questions, by priority. |
| GET | `/api/admin/discovery/rule-candidates` | `DiscoveryAdmin` | Scenario questions where everyone agreed at confidence 4 or better. |
| GET | `/api/admin/discovery/requirements` | `DiscoveryAdmin` | Answers on the engineering template carrying a `data_key`. |

`/session` still sits behind `require_discovery` and `refuse_on_public_demo`, so a deployment
without the portal answers 404 to everyone, admin included. Being unguarded means unguarded
**by role**, not unguarded by deployment.

Every admin read is bounded and paginated, following `admin.py`'s `SCAN_LIMIT` and its
truncation reporting.

### 5.2 Frontend

| Route | File |
|---|---|
| `/discovery` | `apps/web/src/app/discovery/page.tsx` |
| `/discovery/[sectionSlug]` | `apps/web/src/app/discovery/[sectionSlug]/page.tsx` |
| `/discovery/answers` | `apps/web/src/app/discovery/answers/page.tsx` |
| `/admin/discovery` | `apps/web/src/app/admin/discovery/page.tsx` |
| `/admin/discovery/[userId]` | `apps/web/src/app/admin/discovery/[userId]/page.tsx` |

`/admin/discovery` inherits the existing `/admin` analytics exclusion. `/discovery` needs the
prefix added.

---

## 6. Components

New, all client components:

| File | Purpose |
|---|---|
| `apps/web/src/components/discovery-shell.tsx` | Session, role and assignment loading, plus the not-signed-in, not-assigned and loading branches. Mirrors `workspace-shell.tsx`. |
| `apps/web/src/components/discovery-overview.tsx` | Welcome line, personalized title, estimate, section list with per-section progress, Core emphasised, Resume. |
| `apps/web/src/components/discovery-section.tsx` | One section: the visible questions, Save & Continue, Save & Exit, unsaved-change warning. |
| `apps/web/src/components/question-field.tsx` | One question. Dispatches on `question_type` to ten simple inputs. Renders `helper_text`, and for `sensitivity = 'high'` an explicit optional marker, a short reason, and a Skip control. |
| `apps/web/src/components/scenario-field.tsx` | The `scenario_decision` composite: the `scenario_brief` rendered as the case, then decision, confidence, reason codes, maximum exposure and the three free-text fields. |
| `apps/web/src/components/discovery-progress.tsx` | Overall and per-section progress, and the core versus deep-dive split. |
| `apps/web/src/components/admin-discovery-view.tsx` | The admin read surface. |

Changed:

| File | Change |
|---|---|
| `apps/web/src/components/ui.tsx` | Add `ProgressBar`, lifted verbatim from `cashflow-review.tsx`, and use it in both places. |
| `apps/web/src/components/site-header.tsx` | One link reading **Discovery** for a `stakeholder` or `admin` caller, drawn the way the existing `AI usage` link is. Never the word the customer, per ADR 0014. |

---

## 7. Server and API changes to existing files

Five edits, all additive:

1. **`services/api/app/main.py`** (2 lines): import and register the two new routers.
2. **`services/api/app/config.py`** (about 10 lines): the three settings from 3.2.
3. **`services/api/app/private.py`** (about 6 lines): two optional fields on the existing `Me`:

   ```python
   class Me(BaseModel):
       ...
       role: str = "member"
       discovery: DiscoverySummary | None = None   # assignment_id, core_complete, title
   ```

   Optional with defaults, so no existing caller breaks and `workspace-shell.tsx` keeps
   compiling untouched.
4. **`apps/web/src/app/login/page.tsx`** (about 8 lines): after a successful sign-in, read
   `/api/me` and route to `/discovery` when `role === "stakeholder" && !discovery.core_complete`,
   otherwise keep `/workspace`. A failed or slow `/api/me` falls through to `/workspace`, because
   a routing preference must never block a sign-in.
5. **`apps/web/src/lib/analytics.ts`** (2 lines): `"/discovery"` added to `EXCLUDED`, and a
   `/discovery/[section]` branch in `routeLabel()` so a section slug cannot travel even if the
   exclusion is ever loosened.

**Not changed:** `workspaces.py`, `admin.py`, `imports/`, `ai/`, `analysis/`, `auth.py`,
`supabase.py`, any existing migration, the proxy.

---

## 8. Question seed strategy

Decision D2: **fixtures are committed and version controlled.** The repository is private
([ADR 0002](decisions/0002-private-repository-and-deployment.md)). No `local-data` indirection
is added now; that complexity is only worth paying for if the repository ever goes public, which
is risk R7.

Fixtures, in the shape [schema](discovery-schema.md) section 9 specifies:

```
fixtures/discovery/sections/*.json     one file per section, with its questions
fixtures/discovery/templates.json      ordered section slugs per template
```

Loader `services/api/app/discovery/seed.py`:

```bash
cd services/api && .venv/bin/python -m app.discovery.seed --check    # validate, no writes
cd services/api && .venv/bin/python -m app.discovery.seed            # insert or update
cd services/api && .venv/bin/python -m app.discovery.seed --retire   # also retire vanished questions
```

Properties, each a property of the loader rather than of the operator's care:

- **Idempotent.** Ids derive from `stable_id("section", slug)` and `stable_id("question",
  question_key)`, the same helper `app/synthetic/generator.py` already uses. Running twice
  changes nothing.
- **Shared once.** A section named by two templates is inserted once and joined twice. This is
  the mechanism that makes the 70 founder questions literally the same rows.
- **Version-aware.** Changed `question_text` increments `question_version` in place. Existing
  responses keep the version they were written against.
- **Non-destructive.** A vanished question is retired, not deleted, if any response references
  it. Deleting would cascade away an answer.
- **Validated before writing.** `--check` runs the same validation, added to CI next to the
  existing `python -m app.synthetic.generate --check`, so a malformed fixture fails a pull
  request rather than a seed run. The cheapest test in the plan.

Validation covers: globally unique `question_key` and section `slug`; `order` contiguous within
a section; options present for the four types that offer a choice,
`scenario_decision` included, and absent otherwise; `scenario_brief` present for
`scenario_decision` and absent otherwise; **`branch_condition` referencing a question in the
same section and earlier in its order**; no question with `sensitivity: high` and
`required_core: true`; and every section slug named by a template existing as a file.

**Answers never appear in a fixture, in any scenario.** Neither does real financial data: every
`scenario_brief` is a synthetic case (decision D3), which is what makes it committable and
identical for everyone who sees it.

---

## 9. Admin views

Two pages, `/admin/discovery` and `/admin/discovery/[userId]`, mapping onto the endpoints in 5.1
and the queries in
[schema](discovery-schema.md) section 8:

1. **Progress.** Three rows: name, template, status, core done over core total, answered total,
   last activity. What gets looked at daily.
2. **Gaps.** Unanswered core questions by priority, grouped by stakeholder. What drives a
   follow-up message.
3. **Scenario disagreements.** Where decisions differ, with each stakeholder's decision and
   confidence side by side. The most valuable view in the set, and the reason scenario answers
   are typed columns.
4. **Changed minds.** Where somebody revised an underwriting decision, from the scenario
   history. New in revision 2 and only possible because of the trigger in
   [schema](discovery-schema.md) 4.10.
5. **Rule candidates.** Where everyone agreed at confidence 4 or better. Labelled in the
   interface as candidates, not rules: agreement among three people is a signal, not a policy.
6. **Lead versus Partner.** The 70 shared questions, grouped by `question_id`.
7. **Engineering requirements.** Engineer's answers carrying a `data_key`.
8. **One stakeholder.** `/admin/discovery/[userId]` for one person's full set, with change
   history where an answer was revised.

What the admin surface does **not** do, following `admin.py`'s stance: it does not select a
column it does not display, it does not read any financial data, and it does not become a
general query tool. Every endpoint returns a fixed shape.

---

## 10. Tests

### 10.1 `services/api/tests/test_discovery.py`

Modelled on `tests/test_cashflow.py`: subclass `FakeBackend`, add the discovery tables, drive
routes directly with real sessions.

**Isolation, the requirement, tested from both ends:**

- Lead's token on Partner's assignment id: 404 from every stakeholder route, enumerated by
  parameter.
- Lead's token on **every protected admin route**: 403. The highest-value test in the file, and
  the one detail that makes it work: **the route list is derived from the application, not typed
  by hand.**

  ```python
  PROTECTED_ADMIN_ROUTES = sorted(
      path for path in app.openapi()["paths"]
      if path.startswith("/api/admin/discovery")
      and path != "/api/admin/discovery/session"   # answers a boolean to anyone signed in
  )
  ```

  A route added later without `DiscoveryAdmin` appears in this list automatically and fails,
  rather than shipping unguarded. A hand-written list would not have caught it, which is the
  whole point.
- **`/api/admin/discovery/session` is the one documented exclusion**, and has its own tests
  rather than being merely omitted: anonymous gets 401; `member` and `stakeholder` each get
  200 with `{"is_discovery_admin": false}`; the admin gets 200 with `true`; and with
  `DISCOVERY_ENABLED` unset everyone including the admin gets 404. A test also asserts the
  exclusion list has exactly one entry, so a second unguarded route cannot be quietly added to
  it.
- Lead's token asking for a response id belonging to Partner: 404.
- A `member` account (an ordinary public signup) on every discovery route **except**
  `/api/discovery/me`: 404 or 403. `/api/discovery/me` answers 200 with `assignment: null` and
  names no template. It is the second documented exclusion alongside the admin `/session` route,
  and has its own test.
- An anonymous caller: 401. A visitor cookie with no user: 401.
- An admin token on the admin routes for all three stakeholders: 200. And an admin gets **404**,
  not 200, from every `Mine` route in 5.1, because `resolve_own_assignment()` filters on
  `user_id = caller`. The admin surface is scoped by `user_id` and does not share those routes;
  a test asserts the 404 so nobody later "fixes" it by widening the stakeholder resolver.

**Role:**

- No profile row means `member`, and `member` reaches nothing.
- Role comes from the table, not `ADMIN_USER_IDS`; and the AI usage dashboard is still gated by
  `ADMIN_USER_IDS` and is **not** opened by `profiles.role = 'admin'` alone, until phase 6
  converges them deliberately.

**Shared sections, new in revision 2:**

- Lead's and Partner's responses to a shared question carry the **same `question_id`**.
- Lead cannot answer a question from Partner's specific section: the composite keys reject it and
  the API 404s before it gets there.
- Editing a shared question bumps one version, and both stakeholders see the same version.
- A branch condition referencing a question outside its section is rejected by the loader.

**Answers and history:**

- Each of the eleven question types round-trips, and each rejects a wrongly typed value.
- `currency` rejects a non-integer rather than rounding it.
- Changing an answer writes exactly one history row carrying the **previous** value.
- Answering once writes no history row.
- History carries the `question_version` and `question_type` in force at the time.
- A question edited to version 2 leaves an existing response on version 1.
- `multi_select` and `ranking` reject a value not in the question's options.

**Scenario history, new in revision 2 and required:**

- Changing `decision` writes a history row with the previous decision, confidence and reason
  codes.
- Changing only `confidence`, only `reason_codes`, only `maximum_exposure_minor`, or only one of
  the three free-text fields, each writes a history row carrying every previous typed value.
  Seven parameterised cases, one per field.
- Deleting the parent response preserves the scenario history row, through the cascade.
- A scenario answer rejects an out-of-range confidence and an unknown reason code.

**Progress and the soft gate:**

- `core_complete` ignores questions hidden by a branch, and questions with `retired_at` set.
- A skip satisfies a non-core question and is refused on a core one.
- **Resume position.** Saving in a section sets `current_section_id`, reopening `/discovery`
  returns it, and an attempt to save against a section outside the
  assignment's template is refused with the same 404 the choke point gives everything else a
  caller cannot reach. The API-level refusal is what a caller sees; the structural guarantee
  behind it is asserted against the real database in 10.3, because the fake enforces no foreign
  keys.
- `/api/me` reports `role` and `discovery.core_complete` correctly, and keeps its existing shape
  for an ordinary account.
- Core completion flips the login destination, and **nothing 404s or redirects** at `/workspace`
  before or after. The gate is soft, and a test says so.

**Deployment gates:**

- Every discovery route is 404 when `DISCOVERY_ENABLED` is unset.
- Every discovery route is 404 when `PUBLIC_DEMO_ONLY` is set, even with a valid stakeholder
  token. Added to `tests/test_public_demo_only.py` rather than duplicated.

### 10.2 `services/api/tests/test_discovery_seed.py`

Fixture validation without a database: unique keys and slugs, contiguous order, options matching
type, `scenario_brief` matching type, same-section backward-only branch references, no
high-sensitivity core question, every template's section slugs existing. Plus: re-running the
loader over the same fixture produces no change; a changed question text bumps the version
rather than rewriting history; and **a section named by two templates is inserted once**.

### 10.3 `db/checks/discovery_rls_check.py`

The part the unit tests **cannot** answer, because the fake has no grants, no foreign keys and
no triggers, and because the API bypasses RLS anyway. Two things are checked here: **who may
reach these tables**, and **what the tables refuse to store**. Follows `db/checks/rls_check.py`:
one transaction as `postgres`, three throwaway auth users with profiles, assignments and
responses, then `set local role` to each of `anon` and `authenticated` with
`request.jwt.claims` set, then roll back.

**Access**, for each of the eleven tables:

- As `authenticated` with Lead's claims: `select` raises **permission denied**, not "zero
  rows". The grant is the control, and the test should distinguish the two.
- As `anon`: permission denied.
- As `authenticated`: `insert`, `update` and `delete` all refused.
- With a **temporary** `grant select on discovery_responses to authenticated` inside the same
  rolled-back transaction: still zero rows, proving RLS-with-no-policies is the second lock.
  This is the assertion that makes the "dormant insurance" claim real instead of asserted.
- Everything rolled back, verified by re-reading the grants afterwards.

**Structural integrity**, as `postgres`, so the constraint rather than a permission is what
refuses:

- **Setting `current_section_id` to a section outside the assignment's template raises a foreign
  key violation.** Take a section that belongs only to `founder-partner`, try to make it the current
  section of a `founder-lead` assignment, and assert the write fails on
  `discovery_assignments_template_id_current_section_id_fkey`. New in this revision, and the
  reason `current_section_id` is now a composite reference.
- Setting it to a section the template **does** contain succeeds, so the constraint is not
  merely refusing everything.
- Leaving it null succeeds, confirming `MATCH SIMPLE` keeps "not started yet" valid.
- Deleting the `discovery_template_sections` row for the section an assignment currently points
  at nulls `current_section_id` and leaves `template_id` intact.
- A response whose `user_id` is not its assignment's owner is refused, and so is one whose
  question belongs to a section outside its template. The four composite keys on
  `discovery_responses`, asserted rather than assumed.
- Updating a response writes exactly one `discovery_response_history` row, and updating a
  scenario answer writes exactly one `discovery_scenario_response_history` row, proving the
  triggers fire for a raw SQL writer and not only for the API.

### 10.4 `db/checks/discovery_flow_check.py`

Follows `import_flow_check.py`: create throwaway users through the Auth admin API, drive the
real API, delete everything afterwards. Covers the whole journey including save, exit, resume, a
changed answer and its history, a changed scenario decision and its history, and the admin
comparison, against the real database.

### 10.5 Frontend

Typecheck and lint under existing CI. Plus one analytics assertion in the existing verification
suite: navigating to `/discovery` and `/discovery/[section]` dispatches **no** GA event, and no
section slug appears in any payload.

### 10.6 CI

One line added to `.github/workflows/ci.yml`, next to the two existing fixture checks:

```yaml
      - run: python -m app.discovery.seed --check
```

---

## 11. Implementation phases

Revised so something is visible and usable at phase 2 instead of phase 4, with the security gate
kept in front of any real stakeholder data.

**Phase 1: decision record, migration, roles, authorization foundation.**
[ADR 0014](decisions/0014-discovery-portal.md) written **first**. Migration `0025`
applied. `profiles` with backfill and trigger. `discovery/auth.py` with `current_role`,
`require_role`, `require_discovery` and both resolvers. The `role` field on `/api/me`. Tests for
role resolution and for a missing profile defaulting to `member`.
*Reviewable as: "the application has roles, and nothing else changed."*
Nothing user-visible.

**Phase 2: thin vertical slice.**
One **throwaway test account** (not a stakeholder), one template, one section, about five
questions covering `short_text`, `boolean`, `single_select`, `currency` and `scenario_decision`.
`/discovery` and one section page. Save, exit, resume. The login redirect and the header link.
The analytics exclusion.
*Reviewable as: "a person can log in, answer five questions, leave and come back, and the answer
is still there."*
**This is the first end-to-end proof, and it is deliberately small enough to throw away.**

**Phase 3: security and isolation, the review gate.**
The full `test_discovery.py` isolation suite including the parameterised "stakeholder reaches
no admin route". `discovery_rls_check.py` run against the development database, including the
temporary-grant assertion. The scenario-history tests, all seven field cases.
`discovery_flow_check.py`.
*Reviewable as: "the boundary holds, proved at the API and at the database, and no revision of
an answer can be lost."*
**This is the gate. No real stakeholder account is created and no real question bank is seeded
until it is clean.**

**Phase 4: the real thing.**
Full question banks: the shared founder sections, Lead's 30, Partner's 30, Engineer's 80. All three
real accounts seeded with `scripts/seed_customer_accounts.py`, invite links sent. Full section
UX: overview, per-section progress, all eleven question types, the scenario composite with its
brief, `/discovery/answers`.
*Reviewable as: "Lead, Partner and Engineer are working through it."*

**Phase 5: admin views.**
`discovery/admin.py` and the admin pages: progress, gaps, disagreements, changed minds, rule
candidates, comparison, engineering requirements.
*Reviewable as: "the author can see what the three of them said, and where they disagree."*

**Phase 6: convergence and tidy-up (optional, later).**
Fold `ADMIN_USER_IDS` and `profiles.role = 'admin'` into one mechanism, with the env allowlist as
the bootstrap that seeds the profile. A decision record, not a drive-by. See risk R1.

### The rule that spans the phases

**No real stakeholder data exists before phase 3 passes.** Phase 2 uses a throwaway account and
throwaway questions; both are deleted at the end of phase 3. If phase 3 finds a hole, the only
thing at risk is test data.

---

## 12. Exact files expected to change

### New

```
db/migrations/0025_discovery.sql
db/checks/discovery_rls_check.py
db/checks/discovery_flow_check.py
docs/decisions/0014-discovery-portal.md            (written; see repository)

fixtures/discovery/templates.json
fixtures/discovery/sections/*.json                          (one file per section)

scripts/seed_customer_accounts.py

services/api/app/discovery/__init__.py
services/api/app/discovery/auth.py
services/api/app/discovery/routes.py
services/api/app/discovery/admin.py
services/api/app/discovery/models.py
services/api/app/discovery/answers.py
services/api/app/discovery/progress.py
services/api/app/discovery/seed.py
services/api/tests/test_discovery.py
services/api/tests/test_discovery_seed.py

apps/web/src/app/discovery/page.tsx
apps/web/src/app/discovery/[sectionSlug]/page.tsx
apps/web/src/app/discovery/answers/page.tsx
apps/web/src/app/admin/discovery/page.tsx
apps/web/src/app/admin/discovery/[userId]/page.tsx
apps/web/src/lib/discovery-api.ts

apps/web/src/components/discovery-shell.tsx
apps/web/src/components/discovery-overview.tsx
apps/web/src/components/discovery-section.tsx
apps/web/src/components/question-field.tsx
apps/web/src/components/scenario-field.tsx
apps/web/src/components/discovery-progress.tsx
apps/web/src/components/admin-discovery-view.tsx
```

29 files, plus one fixture per section under `fixtures/discovery/sections/`.

### Modified (9)

| File | Change | Size |
|---|---|---|
| `services/api/app/main.py` | Register two routers | 2 lines |
| `services/api/app/config.py` | Three settings | ~10 lines |
| `services/api/app/private.py` | `role` and `discovery` on `Me` | ~6 lines |
| `services/api/tests/test_public_demo_only.py` | Discovery paths in the existing parameterised refusal test | ~3 lines |
| `apps/web/src/app/login/page.tsx` | Conditional post-login destination | ~8 lines |
| `apps/web/src/lib/analytics.ts` | One exclusion, one route branch | 2 lines |
| `apps/web/src/components/site-header.tsx` | Discovery link for the two roles | ~10 lines |
| `apps/web/src/components/ui.tsx` | `ProgressBar`, lifted from `cashflow-review.tsx` | ~20 lines |
| `.github/workflows/ci.yml` | Fixture check | 1 line |

Also updated as documentation: `CLAUDE.md` (its naming instruction now points at ADR 0014, and
its status paragraph is stale in two other ways) and
`docs/planning/IMPLEMENTATION-CHECKLIST.md`.

### Explicitly not modified

`services/api/app/workspaces.py`, `services/api/app/auth.py`, `services/api/app/admin.py`,
`services/api/app/supabase.py`, anything under `services/api/app/imports/`,
`services/api/app/ai/` or `services/api/app/analysis/`, `apps/web/src/proxy.ts`, and every
existing migration.

---

## 13. Risks and unknowns

**R1. Two notions of admin.** `ADMIN_USER_IDS` gates the AI dashboard; `profiles.role` gates
discovery. An account could be one and not the other.
*Mitigation:* a test asserting every id in `ADMIN_USER_IDS` also has `profiles.role = 'admin'`
after seeding, so they cannot silently diverge. Phase 6 converges them deliberately.
*Severity: medium. Certain to occur if left.*

**R2. The service role key remains a full compromise.** Unchanged from today, and the honest
ceiling on every claim in the threat model.
*Mitigation:* none available in this feature. The key lives only in server-side hosting
settings, never in a `NEXT_PUBLIC_` variable, a log or the repository.
*Severity: high impact, unchanged likelihood, out of scope.*

**R3. Production and development share one Supabase project.** Verified during the last release
and re-verified on 2026-09-20: the same four `data_mode = 'demo'` slugs appear in both
(`comparison-bench`, `juniper-kitchen`, `northline-services`, `willow-studio`). Note the count is
four, not the three the application offers: `comparison-bench` exists in the database for model
comparison and is not one of the three shipped fixtures, so counting `fixtures/provider/` gives
the wrong answer. So stakeholder answers sit in the same database as the public demo.
*Mitigation:* the migration is additive, `anon` has no grant on any discovery table, and
`DISCOVERY_ENABLED` is off by default so the public deployment does not carry the routes. The
durable fix is a separate production project, already recorded as not done.
*Severity: medium. Pre-existing, not introduced here.*

**R4. Supabase's built-in mailer rate-limits.** Three invite links is well inside it, but
re-sending during setup will start failing.
*Mitigation:* the seed script prints links rather than relying on delivery.
*Severity: low.*

**R5. A questionnaire is where scope grows.** Conditional logic, rich text, attachments, comment
threads and reminder emails are each one small request away.
*Mitigation:* the phase boundaries, and the position that `branch_condition` supports exactly
two forms and may reference only the same section. Anything more expressive is a new decision
record.
*Severity: medium. Likely.*

**R6. Three stakeholders exercising the prototype spend real money.** A 1,500-row cash-flow
classification measured $1.72 against the shared $25 milestone ledger.
*Mitigation:* the existing reservation caps bound it per identity and globally. The committed
default for the per-identity daily cap is **$0.25**, less than one full run; the production
deployment was set to **$8** during the last release, about four runs a day each, and that value
is recorded only in the hosting settings, not in the repository. The author should set it
deliberately and write it down before invites go out.
*Severity: low, but needs a decision.*

**R7. The repository contains the name and the question bank.** Decision D2 keeps fixtures
committed, which is right while the repository is private ([ADR 0002](decisions/0002-private-repository-and-deployment.md)).
*Mitigation:* **making the repository public requires revisiting [ADR 0014](decisions/0014-discovery-portal.md) first.**
Recorded here so that is a step rather than a discovery.
*Severity: low now, high if the repository's visibility changes without the review.*

**R8. Question wording will change after answers exist.** It always does.
*Mitigation:* `question_version` copied onto every response, and a loader that increments rather
than rewrites. Comparison groups on `question_id`, which survives any wording change.
*Severity: low, because it is designed for.*

**R9. Shared sections make an edit affect two people at once.** Editing a shared founder question
changes it for Lead and Partner simultaneously, which is the point, and also means a careless
edit has twice the blast radius.
*Mitigation:* the version bump is visible in the admin views, and history preserves what each
person answered under the old wording. New in revision 2, and the cost of the chosen design.
*Severity: low.*

**R10. 100 questions is a lot of screen.** Nothing here makes answering 100 questions pleasant;
it makes it possible and resumable.
*Mitigation:* the core-versus-deep-dive split, per-section progress, and the soft gate mean the
first useful milestone is the core set. Whether the deep dive is ever finished is a content
question, not an engineering one.
*Severity: unknown, and outside what this design can settle.*

### Decisions still open

Only two, both carried into [architecture section 8](discovery-architecture.md#8-remaining-open-questions):
the per-identity daily AI cap (R6), and the repository-visibility review (R7). Everything else
from revision 1 was settled at review and is recorded as D1 to D8.
