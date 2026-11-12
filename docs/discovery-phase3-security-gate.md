# Discovery Portal: phase 3 security gate

Date: 2026-09-20. Phase 3 of the [implementation plan](discovery-implementation-plan.md)
is the review gate between synthetic phase 2 data and the real stakeholders. This document is
its output. **Nothing is committed**: the author authorises that step.

Everything below was proved by a test or a real-database check that actually ran on
2026-09-20, or is marked as inspection. The handoff documents were not trusted where the code
could be read; three claims in them were tightened as a result (section I).

---

## A. Verdict

**READY FOR PHASE 4.**

No BLOCKER or HIGH finding is open. Five LOW findings were found by the adversarial suite and
fixed in this phase (E1–E5), one MEDIUM from phase 2 is closed with a regression test at both
levels (E6), one LOW is accepted with a documented mitigation (E7), and the rest are INFO.

Three conditions travel with the verdict; none is a security hole, each is a step:

1. The disposable phase 2 template (`phase2-discovery-test`) is still loaded in the shared
   Supabase project. Phase 4 should retire or remove it when the real banks are seeded, so the
   real portal never lists a test template.
2. The phase 4 account script must create accounts by invite link (decision D4) and must never
   run the throwaway sweep (`remove_account`) against a real stakeholder: history outlives
   deletion by design.
3. The per-identity AI cap (R6) is still a hosting-settings value with no record in the
   repository. Decide and write it down before invites go out. Not a portal issue.

---

## B. Authorization matrix

Enforced by `tests/test_discovery_gate.py::test_authorization_matrix`, which enumerates every
`(method, path)` under `/api/discovery` and `/api/admin/discovery` from `app.openapi()` (exactly
nine; `test_the_matrix_covers_every_operation_the_application_exposes` asserts the count, so a
tenth route appears in the matrix on its own) and drives each with six identities. 54 cases.

Identities: **anonymous** (client header, no token); **member** (ordinary signup, explicit
`member` profile); **no profile** (no `profiles` row at all); **stakeholder, other** (a second
stakeholder with their own business and assignment); **admin** (`profiles.role = 'admin'`);
**stakeholder, owner** (the assignment's owner).

| Operation | anonymous | member | no profile | stakeholder (other) | admin | stakeholder (owner) | Guard responsible |
|---|---|---|---|---|---|---|---|
| `GET /api/discovery/me` | 401 | 200, `assignment: null` | 200, `null` | 200, **their own** | 200, `null` | 200 | `current_user`; `role_of` reads `profiles`; names no template to anyone without an assignment |
| `GET /api/admin/discovery/session` | 401 | 200 `false` | 200 `false` | 200 `false` | 200 `true` | 200 `false` | `current_user`; `CurrentRole`; router: `require_client_header`, `refuse_on_public_demo`, `require_discovery` |
| `GET /api/discovery/assignments/{id}` | 401 | 404 | 404 | 404 | 404 | 200 | `Mine = resolve_own_assignment()`: `id` **and** `user_id = caller` in the query |
| `GET …/sections/{slug}` | 401 | 404 | 404 | 404 | 404 | 200 (404 for a slug outside the template) | `Mine`; section looked up in the assignment's own template |
| `GET …/responses` | 401 | 404 | 404 | 404 | 404 | 200 | `Mine` |
| `GET …/responses/{q}/history` | 401 | 404 | 404 | 404 | 404 | 200 | `Mine`; history filtered by `assignment_id` |
| `PUT …/responses/{q}` | 401 | 404 | 404 | 404 | 404 | 200 / 422 | `Mine`; `require_client_header` (403 without it); `_find_question` (404 outside the visible bank); `answers.py`; Pydantic `strict`, `extra=forbid` |
| `POST …/responses/{q}/skip` | 401 | 404 | 404 | 404 | 404 | 200 optional / 422 core | `Mine`; `require_client_header`; `required_core` |
| `POST …/sessions` | 401 | 404 | 404 | 404 | 404 | 200 | `Mine`; `require_client_header` |

Expected equals actual in every cell. Every 404 body was also asserted to contain no template
title or section slug.

**What ownership derives from.** The session's user id, through `Mine`, and nothing else.
`test_ownership_derives_from_the_session_and_nothing_the_client_sends` sends `user_id` and
`email` in the query, `x-user-id`, `x-user-email` and `x-profile-name` headers, and `user_id` /
`assignment_id` in the body: the owner still gets their own data, the stranger still gets 404,
and a body field the model does not name is refused with 422 rather than read.

**Deployment switches** (`test_every_operation_under_every_switch`, 27 cases, driven as owner,
other stakeholder and admin):

| Switch | Stakeholder operations (8) | `GET /api/admin/discovery/session` |
|---|---|---|
| `DISCOVERY_ENABLED=false` | 404 | 404 |
| `PUBLIC_DEMO_ONLY=true` | 404 | 404 |
| `FEATURES_ENABLED=false` | 404 | 200 (answers the caller's own boolean; the existing `/api/admin` router composes the same guards; INFO E10) |

`test_the_rest_of_cashlens_is_unaffected_by_the_portal_switch`: with the portal off, `/api/me`
reports `member` / `null` and reads no discovery table, `/api/private/workspace` answers 200,
`/api/admin/session` answers as before.

---

## C. Attacks attempted

Unit attacks run against `GateBackend`: two stakeholders (A, C), two templates sharing one
section and each holding a private section, a branched question, one question of every type.
Real-database attacks run in `db/checks/discovery_isolation_check.py` with two throwaway
stakeholders, each with their own auth user, profile, private business and assignment, on a
disposable `phase3-gate-*` bank loaded by the real seed loader.

| # | Attack | Expected | Actual | Control that stopped it | Test proving it |
|---|---|---|---|---|---|
| C1 | C reads A's overview, both sections, responses, history | 404, nothing of A in the body | 404 ×6, no answer sentinel, no private-section name | `Mine` | gate: `test_nothing_of_a_is_disclosed_or_changed_by_c`; DB: `b_gets_404_from_every_route_on_as_assignment`, `no_a_answer_or_section_name_reaches_b` |
| C2 | C writes into A's assignment: PUT on a shared question, PUT on A's private question, skip, session | 404, nothing written or changed | 404 ×4; A's rows byte-identical before and after; no session row; no row for C | `Mine`; `require_client_header` | same tests; DB: `as_answers_are_unchanged_after_the_attacks` |
| C3 | The inverse, A on C's assignment | 404 | 404 ×10, no C sentinel | `Mine` | gate: `test_nothing_of_c_is_disclosed_or_changed_by_a`; DB: `a_gets_404_from_every_route_on_bs_assignment_and_sees_nothing` |
| C4 | C manipulates A's current section | impossible: no route sets it, and C's writes are 404 | A's `current_section_id` unchanged after every attack | `Mine`; the pointer is a side effect of the owner's own save | gate C1 test asserts the pointer |
| C5 | A points their own resume pointer at the other template's section | impossible through the API (the save is 404); refused by the database | 404; service-role `PATCH current_section_id` → **409** on `discovery_assignments_template_id_current_section_id_fkey` | `_find_question`; composite FK | gate: `test_a_question_from_the_other_stakeholders_private_section_is_unreachable`; DB: `db_refuses_a_current_section_outside_the_template`; RLS check (named key) |
| C6 | A answers a question from C's private section through A's own assignment | 404, nothing written | 404 (PUT and skip), no row | `_find_question` walks only the caller's template | gate: same; DB: `a_question_from_the_other_private_section_is_404_through_ones_own_assignment` |
| C7 | A opens C's private section slug through A's own assignment | 404 | 404 | `_section_by_slug` over the caller's template | gate: same; DB: same |
| C8 | Use C's response id | no route takes a response id | n/a | surface does not exist (OpenAPI enumeration) | `test_the_matrix_covers_every_operation_the_application_exposes` |
| C9 | Service role files A's answer under C's user id | insert refused | **409** | composite FK `(assignment_id, user_id)` | DB: `db_refuses_an_answer_filed_under_another_user`; RLS check |
| C10 | Service role files an answer in a section outside the template | refused | **409** | composite FK `(template_id, section_id)` | DB: `db_refuses_a_section_outside_the_assignments_template` |
| C11 | Service role files an answer to a question outside its section | refused | **409** | composite FK `(question_id, section_id)` | DB: `db_refuses_a_question_outside_its_section` |
| C12 | Read the hidden branched question through any payload | absent, not flagged | absent from overview, section, responses and `/me`; its id and text appear nowhere; `progress.total` counts only visible | `progress.visible_questions` server-side | gate: `test_a_hidden_question_leaves_no_trace_in_any_payload`; DB: `hidden_question_is_absent_from_the_payload_not_merely_flagged` |
| C13 | Write to the hidden question directly (PUT, skip) | 404, nothing written | 404, 404 | `_find_question` only over visible questions | gate: same; DB: `hidden_question_cannot_be_answered_or_skipped` |
| C14 | A's gate answer reveals the branch for C | no: visibility is per assignment | revealed for A, still hidden for C, C's PUT still 404 | evaluated against the caller's own answers | gate: `test_a_branch_reveals_only_for_the_assignment_that_answered`; DB: `branch_reveals_for_a_who_answered_and_stays_hidden_for_b` |
| C15 | Read the question bank (hidden questions included) at the Data API with the stakeholder's own token | permission denied | **403** | no grant to `authenticated`; RLS enabled, no policy | DB: `own_token_cannot_read_the_question_bank_at_the_data_api`; RLS check ×11 tables |
| C16 | Share an answer by sharing a question | two rows, same `question_id`, distinct ids and owners; each sees only their own; progress independent | exactly that, at both levels | `unique (assignment_id, question_id)`; `Mine` | gate: `test_sharing_a_question_does_not_share_an_answer`, `test_progress_on_a_shared_section_is_independent`; DB: `shared_question_has_two_rows_with_distinct_ids_and_owners`, `each_stakeholder_sees_only_their_own_shared_answer`, `progress_on_the_shared_section_is_independent`, `shared_section_is_one_row_joined_by_two_templates` |
| C17 | Reword a shared question after answers exist | one definition bumps to v2; every existing answer keeps v1; a new answer records v2; history keeps v1 | exactly that, for both stakeholders | loader's version `case`; `question_version` copied at write time | gate: `test_editing_a_shared_question_bumps_one_definition_and_no_answer`; DB: `rewording_bumps_the_shared_question_to_version_2_once`, `existing_answers_keep_version_1_under_the_new_wording`, `a_new_answer_records_version_2_and_history_keeps_version_1` |
| C18 | Overwrite an answer silently | impossible | every superseded value in history, newest first; delete recorded as `deleted` | `before update or delete` triggers | DB: `generic_history_holds_each_superseded_value_newest_first`, `deleting_the_current_answer_records_it_as_deleted`; RLS check |
| C19 | Change each scenario field, then delete | seven history rows each carrying every prior value; delete records both halves | 7 entries matching the prior state field by field; 2 `deleted` entries | scenario trigger; cascade | DB: `scenario_history_holds_every_prior_value_for_each_field_changed`, `deleting_a_scenario_answer_records_both_halves_as_deleted`; RLS check (7 fields) |
| C20 | Half-save a scenario: typed insert fails | nothing remains | 502, generic row deleted again, progress unmoved, retry works | compensating delete; read path requires both rows | portal: `test_a_scenario_whose_typed_row_cannot_be_written_leaves_nothing_behind` |
| C21 | Half-update a scenario: typed update fails | previous coherent pair stands | 502, decision unchanged, no history row | typed row written first on update | gate: `test_a_failed_typed_update_leaves_the_previous_pair_standing` |
| C22 | Worst case: typed insert fails **and** the compensating delete fails | no route reports an answer; retry repairs | 502; an orphan generic row exists but section, overview, `/me` and responses all show unanswered and 0 progress; retry gives one generic + one typed | read path counts a scenario only with both rows; save updates the stale row | gate: `test_an_orphaned_generic_row_is_invisible_and_repaired` (finding E7) |
| C23 | Generic insert fails | nothing | 502, nothing | ordinary error path | gate: `test_a_failed_generic_write_stores_nothing` |
| C24 | 47 malformed answers across every type (oversized, wrong JSON type, unknown option, duplicate/missing ranking values, float currency, out-of-range percentage/scale, confidence 0/6/3.5/"3", unknown or repeated reason codes, 21 codes, float or string exposure, malformed scenario, unexpected fields, `note` too long, body as list or string) | 422 before any write | 422 ×47, no row, status unchanged | Pydantic (`strict`, `extra=forbid`) then `answers.py` | gate: `test_malformed_answers_are_refused_before_any_write` (47), portal (22) |
| C25 | `NaN`, `Infinity`, `-Infinity` as `number`, `percentage`, `currency`, confidence | 422 | 422 ×6 (was 502 / 500 before E2, E3) | `_is_number`; validation-error handler | gate: `test_non_finite_numbers_the_json_parser_admits_are_refused` |
| C26 | A stakeholder calls the one admin route; any protected admin route | 200 `false`; none exist | 200 `false`; the prefix holds exactly one operation | `CurrentRole`; OpenAPI enumeration | gate: `test_the_admin_prefix_holds_exactly_the_one_role_unprotected_operation`; phase 1 enumeration (armed, still empty) |
| C27 | Retire a question someone answered | answer survives; question leaves the section and the progress; answer still listed; cannot be answered again | exactly that with and without `--retire` | loader never deletes; `retired_at`; `is_live` | gate: `test_a_retired_question_keeps_its_answer_and_leaves_the_section`; DB: four `seed_*`/`retired_*` checks |
| C28 | Sweep history before deleting a user (the phase 2 regression) | rows left behind | 2 `deleted` rows left, as predicted; the right order leaves none | `remove_account`: user first, sweep last | gate: `test_account_removal_deletes_the_user_before_it_sweeps_history`; DB: `sweeping_history_before_the_user_leaves_the_cascades_records_behind`, `remove_account_deletes_the_user_first_and_leaves_nothing` |
| C29 | Get an answer or a question into a log line | never | `session_count_failed` and `upstream.failed` records carry ids and statuses only; no sentinel in any record | Discovery logs metadata only | gate: `test_no_answer_content_reaches_a_log_record` |
| C30 | Get a section slug or an answer into analytics | never | portal paths push nothing; `SECRET_SECTION_SENTINEL` never survives `routeLabel`; `PRIVATE_ANSWER_SENTINEL` in an event on a portal path pushes nothing; Discovery files import nothing from analytics | `EXCLUDED`, `routeLabel`, `sanitiseVercelEvent` (inspection: composes `isExcludedPath`) | web: `tests/analytics.test.mjs` (7 cases) |

---

## D. Real-database verification

All three checks ran against the development Supabase project on 2026-09-20, the flow and
isolation checks through a local API started with `DISCOVERY_ENABLED=true`.

| Check | Result | What it proves |
|---|---|---|
| `db/checks/discovery_rls_check.py` | **84/84** | RLS enabled on all eleven tables; **zero policies** (`no_policy_ships_on_any_discovery_table`); `service_role` holds the four verbs; `anon` and `authenticated` hold none of seven privileges; as `anon` and as an authenticated stakeholder every table is **permission denied**, not empty; insert/update/delete/role change denied; a temporary `grant select to authenticated` inside the rolled-back transaction still yields **zero rows** on the stakeholder's own row; every constraint, the named resume-pointer key, both triggers field by field. Unchanged since phase 1: nothing was added accidentally. |
| `db/checks/discovery_flow_check.py` | **33/33** | The phase 2 journey: recognised, landed, workspace open, save, leave, re-sign-in, hydrated resume, core complete, skip, edits with trigger-written history, member and anonymous refused, own token refused at the Data API (403), service-role spoof refused (409), cascade-delete history recorded, throwaway data and users gone. |
| `db/checks/discovery_isolation_check.py` (new) | **38/38** | Section C rows marked DB: cross-stakeholder attacks both ways, shared question, private sections, branching per assignment, the four composite keys and the confidence constraint as the service role (409/400), generic and scenario history through every field and delete, versioning with answers in place, retirement with and without `--retire`, the cleanup-order regression shown and then done right, fixtures removed, phase 2 template untouched, no orphan for either user. |

Direct access (section 15 of the brief): re-verified by the RLS check above. No Discovery policy
exists; the accidental-grant case still returns zero rows; the grant is rolled back and the
check re-reads the grants afterwards.

---

## E. Open findings

Severity scale: BLOCKER, HIGH, MEDIUM, LOW, INFO. Nothing at BLOCKER or HIGH.

| # | Severity | Finding | Status |
|---|---|---|---|
| E1 | LOW | Save bodies used Pydantic's defaults: an unknown field (`user_id`, `assignment_id`) was silently ignored, and lax coercion accepted `"3"` as a confidence and `"10"` as an exposure. Nothing was ever authorised by such a field, but "ignored" is the wrong answer to a spoof attempt. | **Fixed.** `SaveRequest` and `ScenarioAnswer` are `strict=True, extra="forbid"`; 422 in both cases. Tested (C24). |
| E2 | LOW | `number` accepted `NaN` and `Infinity` (Python's JSON parser admits them) and surfaced a 502 from jsonb; `number` was unbounded. | **Fixed.** `_is_number` requires a finite value; `NUMBER_ABS_MAX = 1e15`. Tested (C25). |
| E3 | LOW | FastAPI's default validation-error response echoes the offending input; with `NaN` inside, encoding the 422 failed and the client got a 500. | **Fixed.** `app/main.py` handles `RequestValidationError` with `type`, `loc`, `msg` only, no input echo. Tested (C25). |
| E4 | LOW | The loader's upsert did not carry `section_id`, so a question moved between section files was silently left where it was, and the retire clause could then retire it while its new section re-activated it. | **Fixed.** The upsert sets `section_id`; where any answer references the question the composite key refuses the transaction loudly. Tested (`test_a_question_moved_between_sections_moves_or_fails_loudly`). |
| E5 | LOW | `--check` passed a fixture containing the dollar-quoting tag `$cl$`, which the real run then refused after validation. | **Fixed.** `validate` builds the SQL once and reports the problem; `main` also catches it. Tested. |
| E6 | MEDIUM (phase 2) | Cleanup swept history before the user delete whose cascade writes history. | **Closed.** One shared routine, `remove_account`, user first and sweep last, used by the script and the isolation check; a unit regression test on call order and a real-database demonstration of the wrong order leaving rows (C28). |
| E7 | LOW | A scenario save is two PostgREST writes, not one transaction. If the typed insert fails **and** the compensating delete fails, a generic row without its typed half remains in the table. | **Accepted with mitigation**, tested (C22): no route reports it as an answer, progress does not move, and the next save repairs it in place. Removing the window entirely means a single-transaction RPC for the scenario write; recommended as phase 5 hardening, not required for phase 4. |
| E8 | INFO | `GET …/responses/{q}/history` answers 200 with an empty list for a question id outside the caller's template. It is filtered by the caller's assignment, so it discloses nothing, and the same route is what makes a retired question's history readable. | Documented; no change. |
| E9 | INFO | `discovery_self_assign` and `discovery_default_template` are read by no code path. Both default safe. | Remove, or use in the phase 4 account script. |
| E10 | INFO | Under `FEATURES_ENABLED=false` the admin session route still answers its boolean, as the existing `/api/admin` router does; it composes `refuse_on_public_demo` and `require_discovery` but not `require_features`, per plan 5.1. | By design; tested. |
| E11 | INFO | The section slug travels in the request path and therefore in the platform's access logs (not answers, not question text), as import ids already do. Analytics never sees it. | Documented. |
| E12 | INFO | No per-question numeric semantics: a negative `currency` answer is accepted because money is signed by design, and nothing says a rent cannot be negative. | A `min`/`max` per question is a phase 4 content decision if wanted. |
| E13 | INFO | The reorder move-aside leaves a retired question's `order` growing by 100000 per seed run. Harmless within integer range (about 21,000 runs). | Documented. |
| E14 | INFO | Deleting a section's fixture file generates no statement for that section, so its questions stay live. Retire them first, then remove the file. | Documented. |
| E15 | INFO | History outlives a deleted user by design. The phase 3 sweep exists for throwaway accounts only. | Condition 2 in section A. |
| E16 | INFO | `discovery_sessions.ended_at` is never written (phase 2 limitation); a visit's end is its last `last_seen_at`. | Documented. |
| E17 | INFO | The string `sb_secret_` appears in the built web bundle. It is supabase-js's own guard that warns when a secret key is used in a browser; no key value is present (section H). | No change. |

**Where validation belongs**, as asked in the brief:

- **Pydantic** (`models.py`): the envelope. Which fields exist, their JSON types (strict), that no
  other field is present, the `note` length, the scenario's field types. Refuses before a route
  body runs.
- **`answers.py`**: the content against the question. An option that is offered, a ranking that
  is a permutation, an integer where money is expected, a finite number, a decision the enum
  knows, a confidence in range, reason codes from this question's options and at most 20, text
  within its limit. Needs the question row, so it cannot live in Pydantic.
- **Database constraints**: the second lock, for any writer including a service-role script:
  `response_answer_matches_type` (JSON shape per type), `confidence between 1 and 5`,
  `reason_codes_bounded`, text lengths, the `underwriting_decision` enum, the four composite
  keys. Proved by the RLS and isolation checks, not assumed.

---

## F. Existing known risks

Not new findings; recorded so they are not mistaken for ones.

- **Service role compromise remains catastrophic** (architecture T9, plan R2). The key lives only
  in server-side hosting settings; section H confirms it is in no tracked file, no web
  environment variable, no bundle and no log. Nothing in this feature changes the ceiling.
- **Development and production share one Supabase project** (plan R3). Stakeholder answers will
  sit in the database the public demo reads. `anon` has no grant, `DISCOVERY_ENABLED` is unset in
  production, the migration is additive. The durable fix, a separate production project, is
  still not done.
- **Two notions of admin until convergence** (plan R1, phase 6). `ADMIN_USER_IDS` gates the AI
  dashboard; `profiles.role` gates Discovery. Tested not to open each other.
- **Questionnaire scope growth** (plan R5). `branch_condition` supports exactly `equals` and `in`
  in the same section; anything more is a decision record.
- **Repository-publication review** (plan R7, ADR 0014). The repository contains the name and
  the question bank; making it public requires revisiting ADR 0014 first.
- **AI-budget configuration drift** (plan R6). The per-identity daily cap is $0.25 in the
  repository and $8 in production hosting settings, recorded nowhere.

---

## G. Cleanup confirmation

- Isolation check: users `gatecheck-a-*` and `gatecheck-b-*` deleted (`throwaway_users_deleted:
  0 left`); their two private businesses deleted; `leftovers()` empty across `profiles`,
  `discovery_assignments`, `discovery_responses`, `discovery_scenario_responses`,
  `discovery_sessions`, `discovery_response_history`, `discovery_scenario_response_history` for
  both users; the `phase3-gate-*` templates, sections and `phase3.gate.*` questions removed
  (`gate_fixtures_removed`); the phase 2 template still holds its five live questions
  (`phase_2_template_untouched`).
- Flow check: users `discoverycheck-a-*` / `-b-*` deleted, business deleted, no orphan
  (`throwaway_data_deleted`, `throwaway_users_deleted`).
- The phase 2 template is the one intentionally retained test fixture (condition 1). No account
  of any kind was created for Lead, Partner or Engineer, and no real question bank was seeded.

---

## H. Exact test commands and results

```bash
# from services/api
./.venv/bin/python -m app.discovery.seed --check      # fixtures valid: 1 template(s), 1 section(s), 5 question(s)
./.venv/bin/ruff check .                              # All checks passed!
./.venv/bin/ruff format --check .                     # 97 files already formatted
./.venv/bin/pytest                                    # 748 passed, 1 skipped, 1 warning
./.venv/bin/pytest tests/test_discovery.py            # 27 passed, 1 skipped   (phase 1; the armed admin enumeration)
./.venv/bin/pytest tests/test_discovery_seed.py       # 41 passed              (+2 this phase)
./.venv/bin/pytest tests/test_discovery_portal.py     # 60 passed              (phase 2)
./.venv/bin/pytest tests/test_discovery_gate.py       # 153 passed             (new: 23 functions)
DISCOVERY_ENABLED=true ./.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8010   # for the two flow checks; stopped after

# from the repository root
services/api/.venv/bin/python db/checks/discovery_rls_check.py                          # 84/84 discovery checks passed
API_BASE=http://127.0.0.1:8010 services/api/.venv/bin/python db/checks/discovery_flow_check.py        # 33/33 discovery flow checks passed
API_BASE=http://127.0.0.1:8010 services/api/.venv/bin/python db/checks/discovery_isolation_check.py   # 38/38 discovery isolation checks passed

# from apps/web
npm run lint        # clean
npm run typecheck   # clean
npm test            # 10 passed, 0 failed  (+2 this phase)
npm run build       # compiled; 17/17 pages; /discovery static, /discovery/[sectionSlug] dynamic
```

The one skipped case is phase 1's armed parametrised admin enumeration, still empty because no
privileged admin operation exists; `test_the_admin_prefix_holds_exactly_the_one_role_unprotected_operation`
and phase 1's `test_the_session_route_is_the_only_role_unprotected_operation` assert that. No
other test is skipped.

**Service-role boundary (brief section 14).** By search, with counts only and the value never
printed: the key value from `services/api/.env` appears in **0** of 657 files under
`apps/web/src`, `apps/web/public` and the built `apps/web/.next`; no web source file names
`SERVICE_ROLE`, `service_role` or `sb_secret`; `apps/web/.env.local` names no service-role
variable; the only `NEXT_PUBLIC_` variables in use are the Supabase URL and publishable key, the
GA id, the analytics switch and the public-demo flag; `git ls-files` tracks only the three
`.env.example` files and `.gitignore` covers `.env`, `.env.*` and `*.env`; the API's startup log
reports configuration as booleans (`"supabase_service_role_key": false` in the test log above);
`/api/health` returns no configuration. The two bundle hits for `sb_secret_` are supabase-js's
browser guard (E17).

**Analytics (brief section 12).** `tests/analytics.test.mjs` loads the real `analytics.ts` with
the tag configured and consent granted: every portal path excluded; `SECRET_SECTION_SENTINEL`
absent from `routeLabel` and `sectionLabel`; a page view and events with
`PRIVATE_ANSWER_SENTINEL` on a portal path push nothing; on a non-portal path the harness is
live and the payload carries no portal word and no unlisted field; and every Discovery page,
component and library file is asserted to contain no `analytics`, `gtag`, `dataLayer` or
`console.` reference. Vercel Web Analytics is mounted behind `sanitiseVercelEvent`, which drops
the event when `isExcludedPath` holds and rewrites the URL through `routeLabel` otherwise
(inspection of `components/analytics.tsx`; the two functions it composes are the tested ones).
Discovery adds no console logging; the API's only Discovery log line is
`discovery.session_count_failed` with an assignment id and an upstream status (C29).

---

## I. Deviations from approved design

None to the schema, the routes, the authorization model or the phases. Changes made in this
phase, all hardening found by the adversarial suite:

1. `app/discovery/models.py`: `SaveRequest` and `ScenarioAnswer` are `strict=True,
   extra="forbid"` (E1).
2. `app/discovery/answers.py`: `_is_number` (finite only) and `NUMBER_ABS_MAX` for `number` and
   `percentage` (E2).
3. `app/main.py`: a `RequestValidationError` handler that does not echo the input (E3). An
   existing file, five lines of behaviour; no test relied on the default body.
4. `app/discovery/seed.py`: the question upsert carries `section_id` (E4); `validate` catches
   the quoting tag (E5).
5. `scripts/discovery_test_account.py`: `remove_account` and `leftovers` extracted; `destroy`
   uses them (E6).
6. `tests/test_discovery_portal.py`: the fake no longer records a history row for a statement a
   queued gateway failure stopped, which is what Postgres does.

Three handoff claims tightened by reading the code rather than the documents:

- Phase 2 handoff §3 said validation was "strict"; extra fields and numeric strings were in fact
  lax at the Pydantic layer (E1). Now true.
- Phase 2 handoff §4 said the analytics exclusion held; it did, and now the Vercel path is also
  named and the Discovery files are proved analytics-free rather than asserted so.
- Phase 2 handoff §10 item 5 said the seed loader "never deletes"; correct, but it also never
  moved a question, which was undocumented (E4).

Phase 3 scope was respected: no product feature, no Lead, Partner or Engineer, no real question
bank, no admin view. New files: `services/api/tests/test_discovery_gate.py`,
`db/checks/discovery_isolation_check.py`, this document. Modified: the six above,
`services/api/tests/test_discovery_seed.py`, `apps/web/tests/analytics.test.mjs`,
`docs/planning/IMPLEMENTATION-CHECKLIST.md`.

---

## Proposed commit message

Not committed. For the author to apply or amend.

```
test: the Discovery portal's security and isolation gate (phase 3)

The review gate between synthetic phase 2 data and the real
stakeholders. No feature is added; what is added is proof, and five
small holes the proof found are closed.

An authorization matrix is enumerated from the OpenAPI document and
driven with six identities against every operation, so a route added
later is in the matrix on its own. Two stakeholders on two templates
sharing one section attack each other through every route and are
refused with nothing disclosed; sharing a question is shown not to
share an answer, at the API and in the database; a hidden branched
question is absent from every payload and unwritable; the four
composite keys and the scenario constraints refuse the service role
itself; both history triggers are exercised field by field through
create, update and delete; a rewording bumps one definition and no
answer; retirement keeps the answer and drops the question; and the
phase 2 cleanup-order regression is reproduced on the real database
and closed with one shared routine tested at both levels.

Hardening from the findings: save bodies are strict and refuse unknown
fields; NaN and Infinity are refused as answers rather than surfacing
as a 502 or, through the default validation handler's input echo, a
500; the seed loader moves a question between sections or fails
loudly on the composite key instead of ignoring the move, and its
--check catches the quoting tag.

Verified: 748 unit tests (153 new) with one armed admin enumeration
still empty; ten web tests including sentinel values through the real
analytics module; ruff, eslint, tsc and the production build clean;
84/84 database checks with zero policies and the accidental-grant case
still yielding nothing; 33/33 and 38/38 flow and isolation checks
through the running API against the development database, every
throwaway user, business and fixture removed afterwards. Verdict in
docs/discovery-phase3-security-gate.md: ready for phase 4.
```
