# Discovery Portal: phase 2 handoff

Date: 2026-09-20. Phase 2 of the [implementation plan](discovery-implementation-plan.md)
is implemented and verified. **Nothing is committed**: the author authorises that step. Phase 1
([handoff](discovery-phase1-handoff.md)) is untouched underneath it.

Phase 2 is the thin vertical slice. One throwaway stakeholder can sign in, be recognised as
`stakeholder`, land on `/discovery`, see one template with one section of five synthetic test
questions, answer them one at a time, leave, sign back in, resume where they stopped with every
saved answer shown, and open the ordinary CashLens workspace at any point without being blocked.
No real question bank, no real stakeholder account, no invite link, no admin view: those are
phases 4 and 5, behind the phase 3 gate.

---

## 1. Files changed

### New

| File | What |
|---|---|
| `fixtures/discovery/templates.json` | One template, `phase2-discovery-test`, naming one section. Marked PHASE 2 TEST DATA. |
| `fixtures/discovery/sections/phase2-core-test.json` | The section: five synthetic questions, one each of `short_text`, `boolean`, `single_select`, `currency`, `scenario_decision`. Section 2. |
| `services/api/app/discovery/seed.py` | The question-bank loader: `--check`, idempotent upserts on stable ids, wording change bumps `question_version`, nothing answered is deleted, `--retire` opt-in. Section 2. |
| `services/api/app/discovery/answers.py` | What counts as an answer to each of the eleven question types. Section 3. |
| `services/api/app/discovery/progress.py` | Branch visibility, answered, core complete, the cached status. Pure functions. Section 3. |
| `services/api/app/discovery/models.py` | The response and request shapes. |
| `services/api/app/discovery/routes.py` | The `/api/discovery` router: eight stakeholder routes, every assignment-scoped one on `Mine`. Section 3. |
| `apps/web/src/lib/discovery-api.ts` | The browser's typed calls to the routes above; nothing here sends a user id. |
| `apps/web/src/lib/discovery-rules.ts` | Import-free rules: where sign-in lands, when the link is drawn, typed money to minor units. Unit-tested. |
| `apps/web/src/components/discovery-shell.tsx` | Session, portal view, and the three ways there is nothing to show. The soft gate lives here, in one direction. |
| `apps/web/src/components/discovery-overview.tsx` | `/discovery`: welcome, title, progress, section cards, Resume, Open CashLens. |
| `apps/web/src/components/discovery-section.tsx` | `/discovery/[sectionSlug]`: one question at a time; Previous, Skip (optional only), Save & exit, Save & continue; resume from the database. |
| `apps/web/src/components/discovery-progress.tsx` | Bar, count, core chip. |
| `apps/web/src/components/question-field.tsx` | The input for each non-scenario type. |
| `apps/web/src/components/scenario-field.tsx` | The synthetic brief and the decision, confidence, reason codes, exposure, three notes. |
| `apps/web/src/app/discovery/page.tsx`, `apps/web/src/app/discovery/[sectionSlug]/page.tsx` | The two pages, client components like the rest of the app. |
| `apps/web/tests/analytics.test.mjs` | The analytics exclusion, asserted on the real module. Section 5. |
| `apps/web/tests/discovery-rules.test.mjs` | The rules above. |
| `scripts/discovery_test_account.py` | `create` / `destroy` a throwaway stakeholder account. Section 6. |
| `db/checks/discovery_flow_check.py` | The slice end to end against the development database through the running API, 33 checks. Section 7.4. |
| `services/api/tests/test_discovery_seed.py` | 22 test functions, 39 cases. Section 7.1. |
| `services/api/tests/test_discovery_portal.py` | 33 test functions, 60 cases. Section 7.2. |
| `docs/discovery-phase2-handoff.md` | This document. |

### Modified

| File | Change |
|---|---|
| `services/api/app/main.py` | Registers the `/api/discovery` router. Two lines. |
| `apps/web/src/app/login/page.tsx` | After a successful sign-in, reads `/api/me` and routes to `/discovery` or `/workspace`; any failure to read falls through to `/workspace`. |
| `apps/web/src/components/site-header.tsx` | A neutral **Discovery** link, drawn the way the AI usage link is: from a server answer tied to the current token. |
| `apps/web/src/lib/analytics.ts` | `/discovery` added to `EXCLUDED`; `routeLabel` reduces `/discovery/<anything>` to `/discovery/[section]`; `sectionLabel` gives `discovery`. |
| `apps/web/src/components/ui.tsx` | `ProgressBar` lifted out of the cash-flow review's `RunProgress`, which now uses it. |
| `apps/web/src/components/cashflow-review.tsx` | Uses `ProgressBar`. No behaviour change. |
| `apps/web/package.json` | `"test": "node --test tests/*.test.mjs"`. No new dependency: the tests load TypeScript through the `typescript` package already installed. |
| `.github/workflows/ci.yml` | `npm test` in the web job; `python -m app.discovery.seed --check` in the API job. |
| `docs/planning/IMPLEMENTATION-CHECKLIST.md` | Phase 2 ticked. |

### Explicitly not modified

Migration 0025 and every other migration; `app/discovery/auth.py` and `app/discovery/admin.py`;
`app/private.py`, `app/config.py`, `app/auth.py`, `app/workspaces.py`, `app/supabase.py`;
`apps/web/src/proxy.ts`; every workspace, import, analysis and AI file on either side;
`db/checks/discovery_rls_check.py`; the four design documents and ADR 0014.

---

## 2. The test template and the seed loader

**The fixture is disposable.** One template (`phase2-discovery-test`, "Discovery (phase 2
test)", estimated 10 minutes) with one core section (`phase2-core-test`) of five questions:

| Order | Key | Type | Core | Sensitivity | Note |
|---|---|---|---|---|---|
| 0 | `phase2.test.short_text` | `short_text` | yes | low | |
| 1 | `phase2.test.boolean` | `boolean` | yes | low | |
| 2 | `phase2.test.single_select` | `single_select` | yes | low | four options |
| 3 | `phase2.test.currency` | `currency` | **no** | **high** | the one optional question, so the skip control and the high-sensitivity rule have a subject |
| 4 | `phase2.test.scenario` | `scenario_decision` | yes | low | five reason-code options; the brief is a synthetic restaurant applicant: revenue $70,000/month, stable six months, one NSF in 90 days, $2,000/month existing debt; `"synthetic": true` is in the data itself |

Every file carries "PHASE 2 TEST DATA" and a test asserts it. No real the customer information.

**The loader** (`python -m app.discovery.seed`) follows `app.synthetic.seed` in shape and reuses
its literal quoting and database lookup.

| Property | How |
|---|---|
| Validates before writing | `validate()` returns every problem as one line; `--check` stops there and reads no database. Refuses: unknown type or sensitivity; choice types (`single_select`, `multi_select`, `ranking`, `scenario_decision`) without options; other types with options; `scenario_decision` without `scenario_brief`; any other type with one; high sensitivity on a core question; a branch condition referencing a later question or another section; duplicate keys across sections; order gaps; a template naming a missing section or one twice; a file whose name and slug differ; invalid JSON. |
| Deterministic ids | `stable_id("discovery", "template"\|"section"\|"question", key)`, the same uuid5 helper the synthetic seed uses. The same fixture builds byte-identical SQL. |
| Idempotent | Every insert is `on conflict (id) do update`. Loaded twice against the development database: five questions, all `question_version = 1`, none retired (flow check). |
| Wording change | `question_version` increments in place when `question_text` differs; responses keep the version they were written against. |
| Never deletes answers | No `delete` on questions, sections or templates. A template-section link the fixture dropped is removed only where no response references it. A question the fixture no longer names is left alone and reported by `raise notice`; `--retire` sets `retired_at` instead of deleting, and naming it again clears `retired_at`. |
| Reordering | Existing `order` values are moved aside first, because `unique (section_id, "order")` would collide half way. |
| One transaction | `psql --single-transaction`, `ON_ERROR_STOP`, ends with `notify pgrst, 'reload schema'`. |

CI runs `python -m app.discovery.seed --check`.

---

## 3. The stakeholder API

Router `/api/discovery`, tag `discovery`, router-level dependencies `require_features`,
`refuse_on_public_demo`, `require_discovery`. The three write routes add `require_client_header`.

| Method and path | What | Scope |
|---|---|---|
| `GET /me` | The caller's role, first name, and their assignment overview or `null`. Answers every signed-in caller; names no template to anyone without one. | `CurrentUser` |
| `GET /assignments/{id}` | Overview: title, status, sections with progress, `current_section_slug`, `resume_section_slug`. | `Mine` |
| `GET /assignments/{id}/sections/{slug}` | The section's **visible** questions in order, each with the caller's current answer hydrated. A question a branch hides is absent from the payload. | `Mine` |
| `PUT /assignments/{id}/responses/{question_id}` | Save one answer. Idempotent. 422 before any write when the answer does not fit. | `Mine` |
| `POST …/responses/{question_id}/skip` | Skip an optional question. 422 for a core one. | `Mine` |
| `GET /assignments/{id}/responses` | Every current answer. | `Mine` |
| `GET …/responses/{question_id}/history` | The trigger-written history, generic and scenario, newest first. | `Mine` |
| `POST /assignments/{id}/sessions` | Open a visit or touch the open one. Fire and forget from the browser. | `Mine` |

`Mine` is phase 1's `resolve_own_assignment`: the assignment id is looked up **together with the
session's user id**, so a stranger, an admin, a guessed id and a malformed id get the same 404.
No route takes a user id from a body, query or path; a test smuggles one in both and gets 404.

**Answer validation** (`answers.py`) is strict and runs before a row is touched: text trimmed
and non-empty within its limit; `boolean` a JSON boolean; `single_select` one offered value;
`multi_select` offered values once each; `ranking` every offered value exactly once; `currency`
an integer number of minor units (a float is refused, not rounded); `number`, `percentage` (0–100),
`scale` (integer 0–10); `scenario_decision` a decision in `approve | deny | manual_review`,
integer confidence 1–5, reason codes from the question's own options without repeats and at most
20, `maximum_exposure_minor` integer minor units or null, the three notes at most 2000
characters. The database's `response_answer_matches_type` remains the second check.

**A scenario answer is two rows, never half.** A new answer writes the generic row, then the
typed row; if the typed write fails the generic row is deleted again and the request answers 502
with nothing stored (tested with a queued upstream failure). An existing answer writes the typed
row first so a failure leaves the previous coherent pair standing. The read path counts a
scenario as answered only when both rows exist, so a typed row that ever went missing shows the
question again rather than a hollow answer, and the next save repairs it in place.

**History is the triggers'.** The routes update in place with `PATCH` and never write a history
row. The history route reads what `record_discovery_response_history()` and
`record_discovery_scenario_history()` wrote. The real triggers are exercised by the flow check
(section 7.4); the unit fake imitates them so the route can be asserted without a database.

**Progress** (`progress.py`) is pure and is the authority; `discovery_assignments.status` is a
cache refreshed after each save (a deliberately stale cache is corrected by the next save, and a
test says so). `started_at`, `core_completed_at` and `completed_at` are written once, on first
reaching each milestone. `current_section_id` is set to the section just saved; the resume
pointer is that section unless it is complete, else the first section with work left, else null.

---

## 4. The portal pages

Both pages are client components in the house shape (`DiscoveryShell` around content, as
`WorkspaceShell` is), on the existing primitives (`Card`, `PageHeader`, `Chip`, `Notice`,
`Empty`, the button classes, the new `ProgressBar`).

**`/discovery`.** "Welcome, {first_name}" (or "Welcome"), the template title and subtitle,
overall progress with the core chip, the estimated time, one card per section with its own
count, estimate and state, **Resume** (or Start, or Review answers) to the server's
`resume_section_slug`, and **Open CashLens** to `/workspace`. A notice says the two are separate.

**`/discovery/[sectionSlug]`.** One question at a time: position ("Question 2 of 5"), the
section's progress, core/optional and sensitivity chips, a Saved or Skipped chip when a stored
answer exists, the question, helper text, the input. Controls: **Previous** (a step back, or the
overview from the first question), **Skip this question** (optional questions only), **Save &
exit** (saves, then `/discovery`), **Save & continue** (saves, re-reads the section from the
server, steps on; on the last question, back to the overview). Opening a section lands on the
first question without an answer, or the top when it is finished. Every saved answer is hydrated
from the response the server sends; unsaved typing on other questions survives a save; nothing
is kept in `localStorage`.

**The soft gate.** The shell points a person with nothing to answer at CashLens; it never points
a person with something to answer away from CashLens. Nothing in `/workspace`, upload or
classification consults the portal. A server test asserts `/api/private/workspace` answers 200
for a stakeholder with the core unfinished, and the flow check asserts it against the real API.

**When there is nothing to show.** Portal off → 404 from every route → "Discovery is not
available here", with Open CashLens. Member, or no assignment → "Nothing to answer yet", with
Open CashLens. No session → Log in. A section not in the template → back to Discovery.

---

## 5. Sign-in, the header, analytics

**Sign-in.** `destinationFor(me)` in `discovery-rules.ts`: `/discovery` only when
`role === "stakeholder"` **and** the account holds an assignment **and** its core is not
complete; `/workspace` otherwise. Every deployment without the portal reports `member` from
`/api/me` (phase 1), so the rule is inert there, and a failure to read `/api/me` at all also
lands on `/workspace`, which is where sign-in always went. Member behaviour is unchanged.

**Header.** The link reads **Discovery** and appears when `/api/me` reports `stakeholder` or
`admin`; it is tied to the token it was answered for, like the AI usage link. No public surface
carries the customer's name.

**Analytics.** `/discovery` is in `EXCLUDED`, so no page view and no event is sent from any
portal path. Independently, `routeLabel` reduces `/discovery/<slug>` to `/discovery/[section]`,
so a section slug could not travel even if the exclusion were edited. `tests/analytics.test.mjs`
loads the real `analytics.ts` with the tag configured and consent granted and asserts: every
portal path is excluded and nothing else newly is; the slug never survives reduction; a page
view on a portal path pushes nothing to `dataLayer`; an event fired while on a portal path pushes
nothing whatever it carries; and elsewhere the harness is live, a pushed payload carries no
portal word (`discovery`, `phase2`, `core-test`, `restaurant`, `approve`, `customer`) and an
unlisted field such as `question_text` or `answer` is dropped.

---

## 6. The throwaway account: creation and destruction

`scripts/discovery_test_account.py`, reading `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` from
the environment or `services/api/.env`, as the checks do.

```bash
# the template must be loaded first (idempotent)
services/api/.venv/bin/python -m app.discovery.seed

# create: the password comes from the environment and is printed nowhere, stored nowhere
DISCOVERY_TEST_PASSWORD='<at least 12 characters>' \
  services/api/.venv/bin/python scripts/discovery_test_account.py create --with-business
#   created auth user <id> <discovery-test-xxxxxx@example.org>
#   profile role stakeholder, first name 'Tester'
#   assigned 'Discovery (phase 2 test)' (assignment <id>)
#   provisioned private business <id> 'Discovery test business xxxx'

# then: apps/web `npm run dev` with the API running under DISCOVERY_ENABLED=true,
# sign in at /login with that address and the password you chose; you land on /discovery.

# destroy: the auth user goes first (its cascade removes the profile, assignment, answers and
# visits, and the delete triggers record every answer), then the history rows, then a check
# that nothing of the account remains
services/api/.venv/bin/python scripts/discovery_test_account.py destroy --email <that address>
```

`create` refuses an address that already exists and a password under 12 characters; `--email`
names the address (default random at `example.org`); `--first-name` defaults to Tester.
`destroy` deletes only businesses named `Discovery test business …` unless `--force`, refuses an
`admin` account unless `--force`, and exits 1 if any row of the account is left. Exercised once
during verification (create with a business, then destroy; "nothing of that account remains").
No real email, no real invite; invite links are phase 4.

`db/checks/discovery_flow_check.py` creates and destroys its own two users the same way and
needs no account from this script.

---

## 7. Tests added

### 7.1 `services/api/tests/test_discovery_seed.py`, 22 functions, 39 cases

The committed fixture validates and is the five-type slice; every file is marked test data and
the scenario is marked synthetic in its data; the high-sensitivity question is the optional one;
`--check` reads no database. Ids are deterministic; the same fixture builds the same statements.
The SQL: seven upserts on `id`, one on the join key, the version-bump `case`, no delete on
questions, sections, templates or responses, the guarded link delete, retirement opt-in and
timestamped, `retired_at = null` on re-naming, the reorder move-aside, `notify pgrst` last.
Eighteen malformed questions each refused with the named problem; a valid scenario accepted;
branch rules (earlier, same section); unique keys across sections; contiguous orders; template
naming rules; section slug and emptiness. The CLI exits 1 on a malformed directory, a file whose
name and slug differ, and invalid JSON.

### 7.2 `services/api/tests/test_discovery_portal.py`, 33 functions, 60 cases

`PortalBackend` is the phase 1 fake with all eleven tables, the committed fixture loaded through
`load_bank()`, one extra branched question, and both history triggers imitated (including the
cascade from a deleted generic row).

| Group | What is asserted |
|---|---|
| `/me` and overview | Stakeholder: role, first name, template, status, resume pointer, five visible of six, four core. Member, no-profile, admin: role and `null`. Anonymous 401. Portal off: 404 on read and write. Overview: 200 owner, 404 stranger, admin, malformed; 401 anonymous. |
| The section | Five questions in order, no answers, brief marked synthetic, reason codes present, the currency question optional and high. Unknown slug and a stranger: the same 404. **The branched question is absent from the payload, its text nowhere in it, and a direct write to it is 404; answering the boolean `true` reveals it (total 6) and lets it be answered; `false` hides it again, its stored answer kept but not counted.** |
| Saving | Stored with owner, template, section, type; status `in_progress`, `current_section_id`, `started_at`. Client header required (403). Idempotent: one row after three saves, history holds each superseded value as `updated`, a stranger's history request is 404. One question's save leaves the others alone. Stranger, admin, no-profile 404 and nothing written; anonymous 401. Malformed id, unknown id, retired question: 404, nothing written. |
| Validation | 22 refusals at 422 with no row, no typed row and the status unchanged: empty and blank text, wrong JSON types, over-long text, missing answer, boolean as string or number, an option not offered, a list for a single select, currency as float, string, boolean and out of range, a scenario sent as `answer`, an unknown decision, confidence 0, 6 and 3.5, an unoffered and a repeated reason code, a float exposure, an over-long note. Accepted shapes stored canonically. |
| Scenarios | Two rows read back as one; the section and the list carry it. A change of mind keeps the earlier decision, codes and exposure in history. **A typed write that fails leaves nothing behind and the retry works.** A missing typed half is not reported as answered and is repaired in place. |
| Skipping | Core refused at 422 naming the core; optional skipped, counted as answered, not as core. Skipping an answered question keeps the answer in history; re-answering keeps the skip. Scoped like everything else; the hidden question cannot be skipped. |
| Progress and the gate | Core complete after the four core answers, `core_completed_at` set, `completed_at` not; `/api/me` reports it. A skip completes the optional question and the assignment. Milestone timestamps written once. **The workspace answers 200 while the core is incomplete.** A stale cached status is corrected by the next save. |
| Resume | Current and resume pointers after one save; every answer hydrated from the section read; nothing left to resume when complete. |
| Sessions | Opened once, touched after; saves counted; a stranger 404; **a session write failure does not lose a save.** |
| Enumeration | Every `(method, path)` under `/api/discovery/assignments/` from `app.openapi()`: exactly seven, none under `/api/admin/`; each refuses a stranger, an admin and a no-profile account with 404 and an anonymous caller with 401, and a `user_id` smuggled in the query and the body changes nothing. The phase 1 admin enumeration still has exactly one exclusion. |

### 7.3 `apps/web/tests/*.test.mjs`, 8 cases

Section 5 for analytics; `discovery-rules.test.mjs` for the sign-in destination (seven cases in
one test), the link rule, and money parsing including refusals of three decimals, a trailing
point, a leading point and exponent notation, and the round trip through `moneyInput`.

### 7.4 `db/checks/discovery_flow_check.py`, 33 checks, against the real database through the running API

Loads the template with the real loader twice (second run idempotent); creates users A and B
through the Auth admin API; A's profile exists as `member` from the trigger and is set to
`stakeholder`; A is assigned; A provisions a private business through the API; `/api/me`
reports the role and the assignment with the core incomplete; **the workspace is open**;
`/api/discovery/me` shows one template, one section, five questions, four core; the section lists
the five types unanswered; three answers saved; a fractional currency and an unoffered option
refused; a visit recorded; **A signs in again**: core still incomplete (so sign-in would route to
`/discovery`), resume points at the section, the three answers are hydrated from the database;
the scenario completes the core and `/api/me` says so; a core skip refused; the optional skip
completes; an edit preserves the previous answer in history; a changed decision preserves the
earlier one; **history rows were written by the database triggers** (2 generic, 1 scenario);
one current row per question; B gets 404 on read and write and `null` on `/me`; anonymous 401;
A's own token at the Data API is 403; the service role cannot file an answer under B (409, the
composite key); on cleanup the cascade delete is recorded by the triggers (5 generic, 1
scenario) before history is cleared; nothing of either user or the business remains.

---

## 8. Exact commands run

```bash
# from services/api
./.venv/bin/python -m app.discovery.seed --check
./.venv/bin/ruff check .
./.venv/bin/ruff format --check .
./.venv/bin/pytest
./.venv/bin/python -m app.discovery.seed                       # loads the test template (dev DB)
DISCOVERY_ENABLED=true ./.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8010   # for the flow check, stopped after

# from the repository root
services/api/.venv/bin/python db/checks/discovery_rls_check.py
API_BASE=http://127.0.0.1:8010 services/api/.venv/bin/python db/checks/discovery_flow_check.py
DISCOVERY_TEST_PASSWORD="$(openssl rand -base64 18)" services/api/.venv/bin/python scripts/discovery_test_account.py create --email discovery-test-scriptcheck@example.org --with-business
services/api/.venv/bin/python scripts/discovery_test_account.py destroy --email discovery-test-scriptcheck@example.org

# from apps/web
npm run lint
npm run typecheck
npm test
npm run build
```

## 9. Results

| Check | Result |
|---|---|
| `seed --check` | `fixtures valid: 1 template(s), 1 section(s), 5 question(s)` |
| `seed` (development database) | `seeded 5 question(s) in 13 statements, one transaction`; second run identical, versions unchanged (flow check) |
| `ruff check .` | All checks passed |
| `ruff format --check .` | 96 files already formatted |
| `pytest` | **593 passed, 1 skipped**, 1 warning (the pre-existing anyio deprecation). 494 + 99 new. |
| `discovery_rls_check.py` | **84/84** (re-run after seeding; unchanged from phase 1) |
| `discovery_flow_check.py` | **33/33** on the second run. See "what the first run found" below. |
| `discovery_test_account.py` create / destroy | created, assigned, business provisioned; destroyed, "nothing of that account remains" |
| `npm run lint` | clean |
| `npm run typecheck` | clean |
| `npm test` | **8 passed, 0 failed** |
| `npm run build` | compiled; the route table lists `/discovery` (static) and `/discovery/[sectionSlug]` (dynamic) |

The one skipped case is phase 1's armed admin enumeration, still empty because phase 2 adds no
privileged admin operation; `test_the_admin_route_enumeration_still_has_one_exclusion` asserts
that. No other test is skipped.

**What the first flow-check run found.** The first run passed 31 of 32: cleanup deleted the
history rows *before* the auth user, and the user's cascade then deleted the answers, whose
delete triggers wrote six fresh history rows after the sweep. The check and the account script
now delete the user first and clear history last, and the check asserts the cascade's records
(5 generic, 1 scenario) before clearing them, which is one more proof of the delete triggers on
the real database. The six rows the first run left on the phase 2 test questions were removed
with one statement scoped to those question ids and to users that no longer exist; nothing else
was touched. Recorded here rather than hidden.

**Local Node.** `.nvmrc` pins Node 24 (CI); the local run used Node 21.7.1. The tests use only
`node:test` and the installed `typescript` package, both available on 20 and later.

---

## 10. Deviations from the approved design

None to the schema, the authorization model, the phases or the eight routes. Details this phase
settled, each additive:

1. **`GET /api/discovery/me` answers every signed-in caller** with `role`, `first_name` and
   `assignment | null`, so the shell has one read and a member sees "nothing to answer" rather
   than an error. It names no template to anyone without an assignment.
2. **The header link and the sign-in destination both read `/api/me`** (phase 1's `role` and
   `discovery`) rather than a new endpoint; the link shows for `stakeholder` and `admin`.
3. **Save & continue with nothing entered** on an optional question performs the skip (the
   explicit Skip button also exists); on a core question it is refused in the browser with a
   message before any request. The server refuses a core skip regardless.
4. **All eleven types are validated server-side** and rendered in the browser incidentally
   (`long_text`, `multi_select`, `number`, `percentage`, `scale`), because `answers.py` covers
   the enum and the inputs were cheap. `ranking` renders a "not in this phase" notice; no ranking
   question exists in phase 2.
5. **`discovery_sessions.saves` is incremented best-effort** on each save against the open visit;
   a failure there is logged and never fails the save. `ended_at` is never set in this phase:
   there is no "leave" call, so a visit's end is its last `last_seen_at`.
6. **`sectionLabel` gives `discovery`** for portal routes, a third analytics touch beyond the two
   the plan listed, so a coarse label could never carry a section name either.
7. **`ProgressBar` is in `ui.tsx`** and the cash-flow review's bar uses it, as the plan intended.
8. **Web unit tests exist for the first time** (`apps/web/tests/*.test.mjs`, `npm test`, in CI),
   with no new dependency. The plan asked for an analytics test and the repository had no
   harness; this is the smallest one that runs the real module.
9. **`scripts/` gains a second script.** `CLAUDE.md` describes the folder as holding the diagram
   helper; the account script is the phase 2 mechanism the plan named and is documented in
   section 6.
10. **The flow check runs the seed loader itself** (twice, proving idempotence) rather than
    requiring the operator to have run it.

---

## 11. Blockers and what remains for phase 3

None technical. For the record:

- **The test template is loaded in the shared Supabase project**, which production reads.
  `DISCOVERY_ENABLED` is unset in production, so no route reaches it, and it holds only synthetic
  test data. It can be removed at phase 4 by retiring or by deleting the template rows once no
  responses reference them; the loader deliberately does not do that on its own.
- **History outlives deletion by design**, so a throwaway account's history rows need the explicit
  sweep both scripts perform. A real stakeholder's history must never be swept; phase 4's
  deletion story should say so.
- Phase 3 is the gate: the full isolation suite, `discovery_rls_check.py` (84/84 today), and the
  flow check (33/33 today) run together, with `PUBLIC_DEMO_ONLY` and the off switch re-asserted,
  before any real question bank or real account exists. Nothing in this phase is a real
  stakeholder's data.
- The two open decisions from the plan (R6 per-identity AI cap, R7 repository visibility) are
  unchanged and are not blockers.

---

## Proposed commit message

Not committed. For the author to apply or amend.

```
feat: the Discovery portal's thin slice (phase 2)

One throwaway stakeholder can now sign in, be recognised, land on
/discovery, answer five synthetic test questions one at a time, leave,
sign back in and resume with every answer shown, and open the CashLens
workspace at any point. No real question bank, account or admin view
yet; those follow the phase 3 gate.

The question bank loads from committed fixtures through an idempotent
loader: stable ids, upserts, a wording change bumps the version in
place, nothing answered is ever deleted, retirement is opt-in, and
--check validates without a database (now in CI). The fixture is one
disposable template of five questions marked as test data, its
scenario a synthetic applicant.

Eight stakeholder routes under /api/discovery, every assignment-scoped
one resolved through the caller's own user id, so a stranger, an admin
and a guessed id get the same 404 and no user id is ever taken from
the browser. Branch visibility is applied server-side and a hidden
question is absent from the payload. Answers are validated strictly
before any write; money is integer minor units and a float is refused.
A scenario answer is two rows and never half: a failed typed write
removes the generic row again. History is written by the database
triggers alone. Progress is computed from the answers and the cached
status follows it.

Two pages on the existing primitives: an overview with progress,
sections, Resume and Open CashLens; a section shown one question at a
time with Previous, Skip, Save & exit and Save & continue, resuming
from the database and keeping nothing in the browser. Sign-in routes a
stakeholder with core work left to the portal and everyone else to the
workspace as before; a neutral Discovery link appears in the header;
/discovery is excluded from analytics and a section slug cannot
survive route reduction. A script creates and destroys a throwaway
account with the password from the environment and stored nowhere.

Verified: 593 unit tests (99 new) with one armed admin enumeration
still empty; eight web tests on the real analytics module; ruff,
eslint, tsc and the production build clean; 84/84 database checks;
and 33/33 flow checks through the running API against the development
database, including re-sign-in resume, trigger-written history on edit
and on cascade delete, a member and an anonymous caller refused, the
stakeholder's own token refused at the Data API, and the service role
unable to file an answer under another user.
```
