# Discovery Portal: phase 1 handoff

Date: 2026-09-20. Phase 1 of the [implementation plan](discovery-implementation-plan.md)
is implemented and verified. **Nothing is committed**: the author authorises that step.

Phase 1 is the foundation and nothing user-visible: migration 0025, application roles read from
`public.profiles`, the authorization dependencies every later route will compose, the portal's
configuration switch, the two additive fields on `/api/me`, and the one admin route the interface
will use to decide whether to draw a link. No question bank, no stakeholder account, no portal
page, no admin view. See [ADR 0014](decisions/0014-discovery-portal.md) for why a
feature named for a customer lives inside CashLens at all.

---

## 1. Files changed

### New

| File | What |
|---|---|
| `db/migrations/0025_discovery.sql` | The schema. Section 2. |
| `db/checks/discovery_rls_check.py` | Access and structural checks against the real database. Section 4.3. |
| `services/api/app/discovery/__init__.py` | Package docstring. |
| `services/api/app/discovery/auth.py` | Roles, guards, the two resolvers. Section 3. |
| `services/api/app/discovery/admin.py` | The `/api/admin/discovery` router, carrying only `/session` in this phase. |
| `services/api/tests/test_discovery.py` | Phase 1 unit tests. Section 4.1. |
| `docs/discovery-phase1-handoff.md` | This document. |

The four design documents (`docs/discovery-*.md`, `docs/decisions/0014-*.md`) were
written in the design phase and are untracked in the working tree alongside this work.

### Modified

| File | Change |
|---|---|
| `services/api/app/config.py` | Three settings: `discovery_enabled` (default false), `discovery_self_assign` (default false), `discovery_default_template` (default `founder-lead`). `integration_presence()` reports `discovery_enabled` so the startup log says whether the portal exists. |
| `services/api/app/main.py` | Registers the discovery admin router. Two lines. |
| `services/api/app/private.py` | `DiscoverySummary` model; `role` and `discovery` on `Me`, both additive with defaults; `_discovery_summary()`. |
| `services/api/tests/test_public_demo_only.py` | One test: the portal is refused by the public-demo gate even when switched on. |
| `services/api/.env.example` | Documents the three settings, as every other setting is. |
| `CLAUDE.md` | The naming instruction now applies to public surfaces and points at ADR 0014; the status paragraph, which said milestone 10 was on a branch and production was a scaffold, now says what is true. **Note:** this file is git-ignored (`.gitignore` line 123) and not tracked, so the edit is local to this checkout and will not travel with a commit. That was already so; the file's own claim to be "checked into the codebase" is not true of this repository. Worth deciding deliberately. |
| `docs/planning/IMPLEMENTATION-CHECKLIST.md` | A milestone section for the portal with phase 1 ticked and phases 2 to 6 listed. |

### Explicitly not modified

`services/api/app/workspaces.py`, `services/api/app/auth.py`, `services/api/app/admin.py`,
`services/api/app/supabase.py`, anything under `services/api/app/imports/`, `services/api/app/ai/`
or `services/api/app/analysis/`, `apps/web/src/proxy.ts`, every existing migration, and every
frontend file.

---

## 2. Migration 0025

Applied to the development database on 2026-09-20 and recorded in `schema_migrations`.
**Production reads the same Supabase project** (risk R3 in the plan), so the tables now exist in
what production reads; `DISCOVERY_ENABLED` is unset there, so no route reaches them.

Purely additive. No existing table altered, no existing function replaced.

| Object | Detail |
|---|---|
| Four enum types | `app_role`, `discovery_question_type`, `discovery_sensitivity`, `underwriting_decision` |
| `profiles` | One row per auth user, `role` default `member`. Backfilled for every existing user; a `security definer` trigger on `auth.users` creates one for each new user. |
| `discovery_templates`, `discovery_sections`, `discovery_template_sections` | A template is an ordered list of sections through the join table, so the shared founder sections are one set of rows referenced by both founder templates. |
| `discovery_questions` | Keyed by globally unique `question_key`; `unique (id, section_id)` for the composite reference. Constraints: `high_sensitivity_is_never_core`; `options_match_type`, which **requires** options for `single_select`, `multi_select`, `ranking` **and `scenario_decision`** (the verified correction; a scenario's options are its reason codes) and forbids them elsewhere; `scenario_brief_matches_type`. |
| `discovery_assignments` | One per person per template. `current_section_id` is nullable and bound compositely: `(template_id, current_section_id)` references `discovery_template_sections (template_id, section_id)`, named `discovery_assignments_template_id_current_section_id_fkey`, `on delete set null (current_section_id)`. `MATCH SIMPLE` keeps null valid. |
| `discovery_responses` | Current answer per (assignment, question). Four composite foreign keys prove the user owns the assignment, the template is the assignment's, the section is in the template, and the question is in the section. `response_answer_matches_type` on the JSON shape; currency in integer minor units. |
| `discovery_response_history` | Append-only, no foreign key so it outlives a delete, written by `record_discovery_response_history()` on `before update or delete`. |
| `discovery_scenario_responses` | Typed columns; `maximum_exposure_minor bigint`; `confidence between 1 and 5`; `reason_codes text[]` bounded to 20. |
| `discovery_scenario_response_history` | Append-only, all seven typed fields, written by `record_discovery_scenario_history()` on `before update or delete`. A cascaded delete from the parent fires it. |
| `discovery_sessions` | Visits; no answer content. |
| Privileges | `revoke all` from `anon` and `authenticated` on all eleven; `grant select, insert, update, delete` to `service_role`; RLS enabled on all eleven; **no policies**; **no SECURITY DEFINER role helper**. Both history trigger functions and the profile trigger function are revoked from `public`, `anon` and `authenticated`. |
| `notify pgrst, 'reload schema'` | Last statement. |

---

## 3. Authorization implemented

`services/api/app/discovery/auth.py`. FastAPI is the boundary: the API holds the service role,
which bypasses RLS, so nothing here leans on RLS and nothing should be read as though it did.

| Name | Behaviour |
|---|---|
| `role_of(supabase, user_id)` | Reads `profiles.role`. Absent row is `member`. A stored value outside the enum is `member`. Never elevates on missing or bad data. |
| `current_role` / `CurrentRole` | The caller's role; 401 without a session; never 403. |
| `require_role(*allowed)` | Dependency factory; the caller's id when their role is allowed, else 403, the same 403 for every disallowed role. |
| `DiscoveryAdmin` | `Annotated[str, Depends(require_role("admin"))]`. The **per-route** guard. Never a router-level dependency. |
| `require_discovery()` | 404 unless `DISCOVERY_ENABLED`. Composed with `refuse_on_public_demo` on the admin router. |
| `AssignmentScope` | Frozen: `assignment_id`, `user_id`, `template_id`, `role`. |
| `resolve_own_assignment(assignment_id, ...)` / `Mine` | The caller's own assignment, filtered on `user_id = caller` in the query. A stranger, an admin, a guessed id and a malformed id all get 404. |
| `resolve_assignment_for(user_id, _admin: DiscoveryAdmin, ...)` | A named stakeholder's assignment for an admin only. 403 before any read for everyone else, then 404. Keyed on user id, so the two resolvers take different path parameters and a stakeholder route cannot be handed admin scope without changing its URL. |

The revision-1 `resolve_any_assignment` is not present, and a test asserts it is absent.

**The admin session rule.** `services/api/app/discovery/admin.py` mounts `/api/admin/discovery`
with router-level dependencies `require_client_header`, `refuse_on_public_demo`,
`require_discovery` only. `GET /session` takes `CurrentRole` and answers
`{"is_discovery_admin": bool}`: 401 anonymous, 200 with `false` for `member` and
`stakeholder`, 200 with `true` for `admin`, 404 whenever the portal is off or the deployment
is public-demo. No privileged operation exists in this phase; every one that ships later takes
`_admin: DiscoveryAdmin` in its own signature.

**`/api/me`.** When `DISCOVERY_ENABLED` is true: `role` from `profiles`, `discovery` from the
caller's assignment (`assignment_id`, `template_id`, `title` with the assignment's override
winning, `status`, `core_complete` derived from the cached `status`), or `null` with no
assignment. When it is false: `role: "member"`, `discovery: null`, and **no table is read**.

**`ADMIN_USER_IDS` is untouched.** It still gates `/api/admin/session` and `/api/admin/ai/*`
alone; a `profiles` admin who is not in it gets 403 there, and a test says so.

---

## 4. Tests added

### 4.1 `services/api/tests/test_discovery.py`, 34 test functions, 37 cases

Subclasses `FakeBackend` from the import tests with `profiles`, `discovery_templates` and
`discovery_assignments` added, plus two sign-ins the base fake lacks (an admin, and an account
with no profile row). A test-local FastAPI app exposes the three dependencies directly so their
mechanics are driven through real FastAPI resolution without a phase-2 route.

| Group | What is asserted |
|---|---|
| Roles | missing profile → `member`; explicit member → `member`; stakeholder → `stakeholder`; author → `admin`; an out-of-enum value → `member`; the role comes from `profiles`, not `ADMIN_USER_IDS`; `ADMIN_USER_IDS` still gates the AI dashboard alone; the three settings default off. |
| Deployment | `DISCOVERY_ENABLED` false → 404 for everyone including the admin, and `/api/me` reports the neutral values; `PUBLIC_DEMO_ONLY` → 404 even with the portal on (also in `test_public_demo_only.py`). |
| Admin session | anonymous 401; visitor cookie 401; missing client header 403; parametrised `false` for no-profile, member and stakeholder, `true` for admin, all 200. |
| Route enumeration | Every `(method, path)` under `/api/admin/discovery` is derived from `app.openapi()`. The exclusion set is exactly `{("GET", "/api/admin/discovery/session")}` and has exactly one entry. Every remaining operation is parametrised to assert 403 for a stakeholder. |
| The guard mechanism | `DiscoveryAdmin` driven directly: 403 for stakeholder, member and no-profile; 401 anonymous; the id back for an admin. |
| `/api/me` | Role and assignment for a stakeholder; the existing shape intact for an ordinary account; `member` for no profile; `core_complete` follows the cached status; `title_override` wins. |
| Resolvers | Own: 200 for the owner; 404 for a stranger, an admin, a guessed id, a malformed id; 401 anonymous. For: 403 for stakeholder, member, no-profile; 401 anonymous; 200 for admin; 404 for a person with no assignment or a malformed id. The two take different path parameters; `resolve_any_assignment` does not exist. |

### 4.2 `services/api/tests/test_public_demo_only.py`

One test added: the portal is refused by the public-demo gate itself, with `DISCOVERY_ENABLED`
on, so the refusal is attributable to that gate.

### 4.3 `db/checks/discovery_rls_check.py`, 84 checks

Against the real development database, one transaction as `postgres`, rolled back. Follows
`db/checks/rls_check.py`. Groups:

| Group | Checks |
|---|---|
| The `auth.users` trigger | Two fresh users get profiles; the default is `member`. |
| Question constraints | Scenario without options refused; scenario without brief refused; text with brief refused; text with options refused; high-sensitivity core refused. |
| The resume pointer | Null allowed; a section in the template allowed; a section outside it **refused by the named key**, read back from `GET STACKED DIAGNOSTICS`; clearing to null allowed; removing the template-section row nulls the pointer and leaves `template_id` intact; a section a template uses cannot be deleted. |
| Responses, four keys | Wrong user refused; wrong template refused; section outside template refused; question outside section refused; type mismatch refused; skip with answer refused; second answer to the same question refused. |
| Generic history | First answer writes no row; a change writes exactly one row carrying the previous answer, version, type and `updated`; the current row holds the new value; the history table has no foreign key. |
| Scenario history | First answer writes no row; each of the **seven typed fields** changed on its own writes one more row carrying the value it replaced; the history table has all seven columns; confidence out of range refused; wrong user refused; deleting the parent writes a `deleted` row in both history tables through the cascade and the scenario row is gone. |
| Privileges | RLS enabled on all eleven; zero policies; `service_role` holds all four verbs on all eleven; `anon` and `authenticated` hold none of seven privileges on any; no role helper function exists; as `anon` and as an authenticated stakeholder every table is **permission denied**, not zero rows; authenticated insert, update, delete and a role change all denied; and a temporary `grant select` still yields zero rows on the stakeholder's own row, which is what makes "RLS is the second lock" a fact. |

---

## 5. Exact commands run

```bash
# from the repository root
services/api/.venv/bin/python db/migrate.py status        # 0025 pending
services/api/.venv/bin/python db/migrate.py apply         # applied 0025_discovery.sql
services/api/.venv/bin/python db/migrate.py status        # 0025 applied, no drift
python3 db/checks/discovery_rls_check.py                  # system Python 3.10.0
services/api/.venv/bin/python -c "import ast; ast.parse(open('db/checks/discovery_rls_check.py').read())"

# from services/api
./.venv/bin/ruff check .
./.venv/bin/ruff format --check .
./.venv/bin/python -m pytest -rfs

# from apps/web (no web file changed; run because the /api/me contract grew)
npm run lint
npm run typecheck
npm run build
```

---

## 6. Results

| Check | Result |
|---|---|
| `db/migrate.py apply` | `applied 0025_discovery.sql`; status shows `0025 customer_discovery applied`, no drift |
| `discovery_rls_check.py` | **84/84 discovery checks passed** |
| `ruff check .` | All checks passed |
| `ruff format --check .` | 89 files already formatted |
| `pytest -rfs` | **494 passed, 1 skipped**, 1 warning (the pre-existing anyio deprecation) |
| `npm run lint` | clean |
| `npm run typecheck` | clean |
| `npm run build` | compiled, 16 of 16 pages generated |

The one skipped case is `test_every_protected_operation_refuses_a_stakeholder`, reported by
pytest as "got empty parameter set". It is parametrised over the privileged discovery admin
operations derived from `app.openapi()`, and in this phase there are none: `/session` is the only
operation and is the documented exclusion. It is armed, not inconvenient: the first privileged
route that ships appears in its parameter set on its own and must answer 403. The guard mechanism
it will exercise is proved now by `test_the_admin_guard_refuses_everyone_but_an_admin`.

---

## 7. What could not be run

Nothing required was skipped. Two things did not apply:

- `db/checks/discovery_flow_check.py` (plan 10.4) drives the stakeholder routes through the real
  API. Those routes are phase 2, so the check has no subject yet and was not written.
- The R1 drift test (every id in `ADMIN_USER_IDS` also has `profiles.role = 'admin'` after
  seeding) is a test of the seed script, which is phase 4.

---

## 8. Deviations from the approved design

None to the schema, the authorization model, the question-sharing model or the phases. Six
implementation details the design left open or that this phase settled:

1. **`/api/me` when the portal is off.** The design specified the two fields and their defaults
   but not the value of `role` on a deployment without the portal. Implemented as `role:
   "member"`, `discovery: null`, **with no table read**. Reasons: every existing `/api/me`
   consumer and every existing test fake is untouched ("no existing caller breaks"); a deployment
   without the portal does not pay a Data API round trip on every signed-in page view for a
   feature it does not have; and `member` is the never-elevate answer.
2. **The composite foreign key is named explicitly**
   (`discovery_assignments_template_id_current_section_id_fkey`), which is the name Postgres would
   generate. The design's SQL left it unnamed; the plan's check asserts on the name, so naming it
   removes a dependency on generator behaviour. Not a schema difference.
3. **Trigger and function names** the design did not fix: `create_profile_for_new_user()` with
   trigger `auth_users_create_profile`; the history functions as named in the schema document.
4. **The admin router is mounted in phase 1 with only `/session`.** The plan scheduled
   `discovery/admin.py` for phase 5, but the phase-1 test requirements (the admin session rule and
   the route enumeration) need the router to exist. The privileged operations remain phase 5.
5. **`db/checks/discovery_rls_check.py` was written and run in phase 1** rather than phase 3,
   because the phase-1 requirements named the structural, history and privilege checks. It will be
   run again at the phase-3 gate; running it earlier is more verification, not less.
6. **Two additive touches outside the plan's modified-files list:** `integration_presence()`
   reports `discovery_enabled`, so the startup log says whether the portal exists; and
   `.env.example` documents the three settings, as it does every other setting.

---

## 9. Blockers for phase 2

None technical. For the record:

- The migration is live in the Supabase project production reads. `DISCOVERY_ENABLED` is unset
  in production, so nothing reaches the new tables; the durable fix (a separate production
  project) remains not done, as before.
- Two decisions from the plan are still open and are not blockers for phase 2: the per-identity
  daily AI cap (R6) and the repository-visibility review (R7).

---

## Proposed commit message

Not committed. For the author to apply or amend.

```
feat: the Discovery portal's foundation (phase 1)

An internal customer-specific workspace inside CashLens, named for the
customer only behind authentication (ADR 0014). This phase is the
foundation and nothing user-visible: the schema, the roles, the guards,
and the one admin route the interface will use to decide whether to
draw a link. The portal itself, its question bank and its stakeholder
accounts follow in later phases behind a security gate.

Migration 0025 adds eleven tables, all service-role only: RLS enabled,
no grant to anon or authenticated, no policies, no SECURITY DEFINER
role helper. FastAPI is the authorization boundary, because the API
holds the service role and RLS is bypassed on that path. Ownership and
membership are structural: a response carries four composite foreign
keys, and an assignment's resume pointer is bound to its own template's
section list. History is written by triggers on both the generic and
the typed scenario answer, so no caller can skip it. A scenario
question's options are its reason codes and are required.

Roles live in profiles, read by Python on every request; an absent or
unknown value is member. Two resolvers take different path parameters,
so a stakeholder route cannot be handed admin scope without changing
its URL. The admin guard is per-route: /session answers a boolean to
any signed-in caller, and every operation that ships later names the
guard in its own signature. The route list the tests check is derived
from the OpenAPI document, so an unguarded route fails rather than
ships.

/api/me gains role and discovery, additively; where the portal is off
both are their neutral values and no table is read.

Verified: migration applied and recorded; 84/84 checks against the
development database covering the trigger, every constraint, both
history triggers field by field, and every privilege as anon and as an
authenticated stakeholder including a temporary grant that still
yields zero rows; 494 unit tests pass with one armed parametrised case
empty until the first privileged route exists; ruff, eslint, tsc and
the production build clean.
```
