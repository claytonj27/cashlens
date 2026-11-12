# Discovery Portal: phase 3.5A handoff

Date: 2026-09-20. Phase 3.5A of the [transaction calibration design](transaction-calibration-design.md)
is implemented and verified against the [schema proposal](transaction-calibration-schema-proposal.md).
**Nothing is committed**: the author authorises that step. No real stakeholder account, no real
question bank, no Phase 4 work.

What phase 3.5A is: the calibration data model, the one-call typed write that closes phase 3's
finding E7 for both typed question types, the loader rules that make a controlled variant
structural, a six-case synthetic proof fixture, the respondent's calibration card, and the tests
and real-database checks that prove all of it. What it is not: the real 48-case bank, Lead,
Partner or Engineer, consensus, prediction comparison, rule promotion, admin views.

---

## 1. Files changed

### New

| File | What |
|---|---|
| `db/migrations/0026_discovery_transaction_calibration_type.sql` | One statement: the enum value. Section 2. |
| `db/migrations/0027_discovery_transaction_calibration.sql` | Four types, three tables, one trigger, the write function, two widened constraints, privileges. Sections 3–6. |
| `fixtures/discovery/sections/phase35-calibration-proof.json` | Six synthetic cases, five families, one controlled variant. Section 7. |
| `db/checks/discovery_calibration_check.py` | 31 checks through the running API against the development database. Section 9. |
| `services/api/tests/test_discovery_calibration.py` | 61 cases. Section 8. |
| `apps/web/src/components/calibration-field.tsx` | The respondent's calibration card. |
| `docs/discovery-phase3-5-handoff.md` | This document. |

### Modified

| File | Change |
|---|---|
| `services/api/app/discovery/answers.py` | `Calibration` dataclass, `_calibration()` validation against the case's direction, the vocabularies (`CATEGORIES_BY_DIRECTION` imported from `app.ai.prompts`, not copied), `TYPED_TYPES`, `Normalised.typed_row()`, shared `_reason_codes()`. |
| `services/api/app/discovery/models.py` | `CalibrationAnswer` (strict, `extra="forbid"`), `calibration` on `SaveRequest`, `CalibrationAnswerView`, `TransactionCaseView` (no family metadata), `transaction_case` on `QuestionView`, `calibration` on `ResponseView` and `HistoryEntry`. |
| `services/api/app/discovery/routes.py` | `_read_bank` reads cases and calibration rows and applies the "both rows or no answer" rule to both typed types; `save_response` and `skip_response` call `save_discovery_typed_response` for typed questions; `response_history` reads calibration history; `_typed_call`. Plain answers unchanged. |
| `services/api/app/discovery/seed.py` | Rewritten around the same shape: `transaction_case` on questions, section-level `calibration_reason_codes` expanded onto each calibration question, `materialise_cases()` with variant inheritance and the one-feature proof, case value checks, case upserts, the branch-may-not-target-a-typed-question rule, and a loader that names psql's exit status when a run fails. |
| `fixtures/discovery/templates.json` | Second template, `phase35-calibration-test`. |
| `services/api/tests/test_discovery_seed.py` | Counts for the second template; 18 new cases for case materialisation and refusal. |
| `services/api/tests/test_discovery_portal.py` | The fake gains the three tables, calibration history, and an imitation of the write function (snapshot and restore, direction from the case, the row-level checks); two atomicity tests moved onto it. |
| `services/api/tests/test_discovery_gate.py` | Three atomicity tests moved onto the function; the orphan test plants its orphan directly; the cleanup-order regression asserts the third history table. |
| `db/checks/discovery_rls_check.py` | Fourteen tables; every literal `11` is `len(TABLES)`; 59 new checks for the calibration constraints, the composite direction key, the write function's atomicity, privileges and history; a `ctid` tiebreaker on the "latest history row" query. |
| `db/checks/discovery_isolation_check.py` | The retirement check now prints the seed run's streams on failure. |
| `scripts/discovery_test_account.py` | `--template` on `create`; `HISTORY_TABLES` and `ACCOUNT_TABLES` include the calibration tables (section 11). |
| `apps/web/src/lib/discovery-api.ts` | `TransactionCase`, `CalibrationAnswer`, the type union, `transaction_case` and `calibration` fields. |
| `apps/web/src/lib/discovery-rules.ts` | `CalibrationDraft`, `emptyCalibration`, `calibrationFromStored`, `calibrationRequest`, `timingLabel`, the four vocabularies. |
| `apps/web/src/components/discovery-section.tsx` | Dispatches `transaction_calibration` to the new field; hydrate and request through the rules. |
| `apps/web/tests/discovery-rules.test.mjs` | Three new tests: request assembly, hydration round trip, timing label. |
| `docs/planning/IMPLEMENTATION-CHECKLIST.md` | Phase 3.5A recorded. |
| `docs/transaction-calibration-design.md`, `…-schema-proposal.md` | Status lines updated: 3.5A implemented, sections 2–6 of the proposal are migrations 0026/0027. |

### Not modified

Migration 0025 and every earlier one; `app/discovery/auth.py`, `admin.py`, `progress.py`;
`app/private.py`, `app/config.py`, `app/auth.py`, `app/main.py`; every file under
`app/imports/`, `app/ai/`, `app/analysis/`; `apps/web/src/proxy.ts`; the phase 2 fixture.

---

## 2. Migration 0026

```sql
alter type public.discovery_question_type add value if not exists 'transaction_calibration';
```

Alone in its file, with a comment saying why: PostgreSQL will not let a value added to an
existing enum be referenced in the transaction that added it, and `db/migrate.py` runs one
transaction per file. Applied and recorded on 2026-09-20; `status` shows `0026 … applied`.

---

## 3. Migration 0027

Applied and recorded on 2026-09-20 in the same `apply` run, after 0026 committed. Purely
additive to every existing row. Contents, in order:

| Object | Detail |
|---|---|
| `underwriting_materiality` | enum, ordinal by declaration: immaterial, low, medium, high, critical |
| `underwriting_signal` | enum: positive, neutral, negative, unknown |
| `underwriting_impact` | enum: none, contributes_positive, contributes_negative, manual_review, potential_hard_decline |
| `discovery_handling_recommendation` | enum: auto_classify, ai_review, human_review, ask_borrower |
| `discovery_transaction_cases` | Section 4 |
| `discovery_transaction_calibration_responses` | Section 4 |
| `discovery_transaction_calibration_response_history` + `record_discovery_calibration_history()` + trigger | Section 4 |
| `options_match_type` widened | `transaction_calibration` joins the types that require options (its reason codes) |
| `response_answer_matches_type` widened | `transaction_calibration` joins `scenario_decision` as a type whose generic `answer` is null |
| `scenario_brief_matches_type` | **untouched**: a calibration question must not carry a brief, and the existing constraint already forbids one |
| `save_discovery_typed_response(…)` | Section 6 |
| Privileges | `revoke all` from `anon`, `authenticated` on the three tables; the four verbs to `service_role`; RLS enabled on all three; no policies; the function revoked from `public`, `anon`, `authenticated` and granted to `service_role` |

Discovery tables: eleven to **fourteen**.

---

## 4. Schema additions

**`discovery_transaction_cases`**, one row per calibration question, keyed by `question_id`.
Family metadata (`case_family`, `variant`, `changed_feature` from a fixed list of thirteen,
`synthetic` default true, `source`), the business (`business_vertical`, `business_description`,
`account_type`), the transaction (`direction`, `amount_minor`, `descriptor`, `merchant`,
`counterparty`, `payment_rail`, `posted_date`, `day_of_week`, `time_of_day`, `memo`), the
pattern (`frequency_30d`, `frequency_90d`, `average_amount_minor`, `amount_min_minor`,
`amount_max_minor`, `recurrence_pattern`), and two display-only jsonb columns
(`related_transactions`, `context`). Constraints: `direction_matches_the_amount`,
`the_amount_range_contains_the_average`, `a_real_case_must_name_its_authorisation`,
`unique (case_family, variant)`, at most one baseline per family (partial unique index), and
`unique (question_id, direction)`, which is what the answer's composite key binds against.

**`discovery_transaction_calibration_responses`**, primary key on the parent response, ownership
bound by `(assignment_id, user_id)` to the assignment as the scenario table is. `direction`
copied and bound by `(question_id, direction)` to the case, so the copy cannot disagree with the
case. The four required fields are `not null`; `classification_confidence between 1 and 5`;
`calibration_category_matches_direction` lists the eleven buckets by direction;
`calibration_reason_codes_bounded`; `context_needed_requires_the_flag`.

**`discovery_transaction_calibration_response_history`**, append-only, every typed column, no
foreign key, written by a `before update or delete` trigger, including through the cascade
from a deleted parent.

---

## 5. Calibration answer structure

The browser sends, on the existing `PUT /api/discovery/assignments/{id}/responses/{question_id}`:

```json
{"calibration": {
  "classification_category": "Personal expense",
  "classification_confidence": 4,
  "underwriting_materiality": "low",
  "underwriting_signal": "negative",
  "decision_impact": "contributes_negative",
  "reason_codes": ["merchant_ambiguous", "timing_atypical"],
  "needs_more_context": false,
  "context_needed": null,
  "reasoning": "A Saturday-night ride for a restaurant reads as personal.",
  "handling_recommendation": "ai_review"
}}
```

Required: `classification_category`, `classification_confidence`, `underwriting_materiality`,
`underwriting_signal`. Optional: the other six. **No `direction`**: a body that carries one is
refused as an unexpected field, and the stored direction is the case's, copied by the database.

Validation in three places, as the phase 3 report describes: Pydantic (`strict=True`,
`extra="forbid"`: a `"4"` is not a confidence, an unknown field is a 422); `answers.py` (the
category is one of the eleven and legal for the case's direction, read from the question's own
case row; confidence 1–5; the three enums; reason codes from the question's options, each once,
at most 20; text bounds; `context_needed` requires `needs_more_context`); and the database
(section 4). The response carries `calibration` with the stored direction; the section payload
carries `transaction_case` on the question with `categories` for its direction, and the stored
answer hydrated.

---

## 6. The transactional write (E7)

`public.save_discovery_typed_response(p_assignment_id, p_user_id, p_template_id, p_section_id,
p_question_id, p_question_version, p_question_type, p_status, p_note, p_typed) returns uuid`,
plpgsql, `search_path = public`.

- Upserts the generic row on `(assignment_id, question_id)` with `answer = null`, so the
  before-update trigger records history exactly as a `PATCH` did.
- For `answered`: upserts the typed row on `response_id` (scenario or calibration). For a
  calibration it **reads `direction` from the case**, raising if there is none. The typed
  before-update trigger records history.
- For `skipped`: deletes the typed row, so the before-delete trigger records it.
- Any error (a check constraint, the composite key, a bad enum, a missing case) aborts the whole
  call: **no generic row survives a refused typed write.** Proved on the real database by the
  RLS check (`no_generic_row_survives_a_failed_typed_write`, `…_wrong_direction_category`) and
  the calibration check (`a_typed_write_the_database_refuses_leaves_no_generic_row`), and in the
  fake for both types.
- Not an authorization surface: the API calls it with the service role after `Mine` has resolved
  the scope; it decides nothing about ownership, the four composite keys remain the proof, and it
  is revoked from `public`, `anon` and `authenticated`. Proved: `anon_cannot_execute_the_typed_write`,
  `authenticated_cannot_execute_the_typed_write`, `service_role_can_execute_the_typed_write`;
  and at the Data API, a stakeholder's own JWT gets 403 and an anonymous call 404.

`routes.py` uses it for every typed save and every typed skip; the scenario path moved onto it
with no change to what the caller sees. The compensating-delete branch is gone. The read path's
"a typed answer needs both rows" rule stays, extended to calibration, as a defence the function
makes unnecessary.

---

## 7. Proof fixture

`fixtures/discovery/sections/phase35-calibration-proof.json`, in the template
`phase35-calibration-test`. Every file and every case is marked **PHASE 3.5 TEST DATA** and
**SYNTHETIC**; every merchant relationship, counterparty name, amount and date is invented.

| Key | Family | Case | Core |
|---|---|---|---|
| `calib.uber.restaurant.baseline` | `uber-restaurant` | Restaurant, `UBER *TRIP 8N2K4`, −$18.40, card, Saturday 22:41, 11× in 30 days, $9–$24, irregular | yes |
| `calib.safeway.restaurant` | `safeway-restaurant` | Restaurant, Safeway, −$184.50, Tuesday 10:12, 4× in 30 days, weekly | yes |
| `calib.zelle.restaurant.recurring` | `zelle-restaurant` | Restaurant, Zelle to "M. Delacroix (invented name)", −$950, semimonthly, fixed amount, context note | yes |
| `calib.reversal.matched` | `reversal-matched` | Restaurant, `SQUARE INC 2039 CREDIT` +$129.99, with the matching −$129.99 debit two days earlier as a related row, no REVERSAL wording | yes |
| `calib.ach.unknown.high` | `ach-unknown` | Restaurant, `ACH DEBIT 8823 HLDGS LLC` −$15,000, once in 90 days | yes |
| `calib.uber.restaurant.tuesday` | `uber-restaurant` | **Variant**, `changed_feature: day_of_week`: Tuesday 09:15, 2026-03-17; everything else inherited | no |

Six cases, five families, one controlled variant, five positions apart from its baseline. Seven
section-level reason codes expanded onto every question. Loaded into the development database
(`seeded 11 question(s) in 32 statements`), idempotent on re-run.

**What the loader proves** (all in `--check`, so in CI): a variant inherits its baseline and
overrides only what it changes; the materialised variant differs from the baseline in exactly
the named feature (`day_of_week` may carry `posted_date` and `time_of_day`; `direction` may carry
the four amounts; nothing else carries anything); a variant that also changes another column, or
names a feature it leaves unchanged, or names an unknown feature, is refused; a family with no
baseline or two is refused; a non-synthetic case is refused; a float amount, a direction that
disagrees with the amount's sign, an unknown field, a bad rail or recurrence or day are refused;
a calibration question without a case, a plain question with one, and a branch condition
targeting any typed question are refused.

---

## 8. Tests added

| File | Cases | Groups |
|---|---|---|
| `tests/test_discovery_calibration.py` (new) | 61 | Payload: six cases with `transaction_case`, no `case_family`/`variant`/`changed_feature`/prediction anywhere, categories by direction, related rows and context; anchoring: nothing of the other stakeholder or any prediction in any route. Saving: minimal and full round trips, the stored direction is the case's, both rows written. Category/direction: inflow case accepts inflow buckets and refuses every outflow one and the reverse (`Unclassified` legal both ways); the fake's imitation of the row-level check refuses a bypass of the API. 34 malformed answers refused before any write (each required field missing, wrong types, out-of-range confidence, bad enums, unoffered or repeated or 21 reason codes, context without the flag, over-long text, a smuggled `direction`, `user_id`, `amount_minor`, unknown fields, non-object bodies). Atomicity: typed failure on insert and on update leaves nothing or the previous pair; function failure stores nothing; retry works. Shared question: two rows, two typed rows, two owners, differing category/confidence/materiality/signal, each sees only their own, independent progress; every route 404 for the other stakeholder, a member, an admin, a no-profile account and 401 anonymous, with nothing changed. History: one parameterised case per field (ten), delete records both halves, skip removes the typed row and records it, saving twice is one row. Progress: the five core cases complete the core, the variant completes the set. |
| `tests/test_discovery_seed.py` | 59 (+18) | The committed fixture is the phase 3.5 proof; one-feature variants pass; nine refusals (two-feature drift twice, undeclared change, unchanged declared feature, unknown feature, non-synthetic, unknown field, float amount, sign mismatch); exactly one baseline; direction carries the amounts; case/type coherence; section reason codes expand; a calibration question with none is refused; a branch may not target a typed question; case rows upsert on `question_id` with the full inherited row. |
| `tests/test_discovery_portal.py` | 60 | Fake extended; two atomicity tests now prove no generic row after a refused typed write. |
| `tests/test_discovery_gate.py` | 153 | Three atomicity tests on the function; cleanup-order regression names all three history tables. |
| `apps/web/tests/discovery-rules.test.mjs` | 13 (+3) | Request assembly (four required fields, `needs_more_context` set by text, no `direction`), hydration round trip, timing label. |

---

## 9. Real-database results

All on 2026-09-20 against the development Supabase project; the three flow checks through a
local API started with `DISCOVERY_ENABLED=true` and stopped after.

| Check | Result | Proves |
|---|---|---|
| `db/migrate.py apply` | `applied 0026…`, `applied 0027…`; status shows both applied, no drift | Two files, two transactions, in order |
| `discovery_rls_check.py` | **143/143** (was 84) | All fourteen tables exist, RLS on, zero policies, service_role four verbs, anon and authenticated none; `transaction_calibration` is an enum value; a calibration question needs options; a synthetic case is accepted; sign mismatch, unknown feature, unauthorised real case, second baseline refused; the write function refuses a bad confidence and a wrong-direction category **with no generic row surviving**, writes both rows together, copies the case's direction, updates in place on a second call with one generic and one calibration history row, removes the typed row on skip and records it; a direction not the case's is refused by the composite key; a category from the other direction and context without the flag are refused; every one of ten calibration fields written to history with its previous value; anon and authenticated cannot execute the function, service_role can; the calibration history has no foreign key |
| `discovery_calibration_check.py` (new) | **31/31** | 0026 recorded before 0027; type, tables, RLS, no policies, no grants; the category constraint's literals equal `prompts.CASHFLOW_INFLOW_CATEGORIES` and `CASHFLOW_OUTFLOW_CATEGORIES` exactly; the loader loads six synthetic cases in five families; the section serves each case with no family metadata and no prediction; the API refuses a wrong-direction category, confidence 6, a smuggled direction, a bad enum and a plain answer with nothing written; A and B answer the same `question_id` with two generic rows, two typed rows, two owners, different confidence, materiality and signal; each sees only their own; A gets 404 on every route of B's assignment and B's row is unchanged; progress independent; an inflow case round-trips with optional fields; ten field changes each preserved in history; a skip removes the typed row and records it; a typed write the database refuses leaves no generic row; the function is not callable with a stakeholder's token (403) or anonymously (404); the stakeholder's token reaches neither the cases nor the answers (403, 403); both users and every row of theirs removed; the committed fixture left in place |
| `discovery_flow_check.py` | **33/33** | The phase 2 journey, its scenario saves now through the function |
| `discovery_isolation_check.py` | **38/38** | Phase 3's suite, unchanged in substance. See section 11 for one transient |
| `discovery_test_account.py create --template phase35-calibration-test` / `destroy` | created, assigned the calibration template; destroyed, nothing remains | The throwaway path on the new template |

---

## 10. Full regression results

```
services/api:   seed --check          fixtures valid: 2 template(s), 2 section(s), 11 question(s), 6 transaction case(s)
                ruff check .          All checks passed!
                ruff format --check . 98 files already formatted
                pytest                827 passed, 1 skipped, 1 warning     (was 748; +79)
                  test_discovery 27 + 1 skipped · test_discovery_seed 59 · test_discovery_portal 60
                  test_discovery_gate 153 · test_discovery_calibration 61
repo root:      discovery_rls_check.py 143/143 · discovery_flow_check.py 33/33
                discovery_isolation_check.py 38/38 · discovery_calibration_check.py 31/31
apps/web:       lint clean · typecheck clean · npm test 13 passed, 0 failed · build 17/17 pages
```

The one skipped case is phase 1's armed admin enumeration, still empty because no privileged
admin operation exists. No other test is skipped. Deployment gates are unchanged and re-run by
the gate suite (27 cases): every discovery operation 404 under the three switches, the admin
session route as designed.

---

## 11. Deviations from the approved design

Product decisions in the task that amended the design document, implemented as decided:

1. **`underwriting_signal` is required**, not optional. Column `not null`; the four-field
   minimum is enforced by Pydantic, `answers.py` and the database.
2. **`handling_recommendation` is one enum**, as designed; no booleans.

Details settled in implementation, none changing the schema proposal's shape:

3. **Skips go through the function too.** The proposal's sketch covered saves; a typed skip is
   also two writes (delete the typed row, update the generic), so `p_status` was added and both
   paths use the one call.
4. **The case view carries `categories`** for its direction, computed from `prompts.py`, so the
   browser never holds its own copy of the vocabulary.
5. **The case view omits `case_family`, `variant` and `changed_feature`** (design §13: variants
   are not visually linked). `_read_bank` does not select them.
6. **Field names** follow the proposal where the task used descriptions: `descriptor` and `memo`
   for "transaction_name" and "memo_or_description"; `reasoning` for "free_text_reasoning".
7. **`direction` as a changed feature may carry the four amount columns**, since flipping it
   flips every sign; the proposal said as much for `amount_minor` and this extends it to the
   range and average.
8. **The proof fixture holds six cases**, the five suggested plus the variant, in five families.
9. **Variant spacing (design open decision 6) is not enforced**; the fixture keeps its variant
   five positions from the baseline by hand.
10. **The keyboard-first interaction** in design §13 is not built; the card uses the existing
    pill controls. Phase 4 UX.

Found and fixed during verification:

11. **The throwaway sweep did not know the calibration history table.** The first run of the
    calibration check left 14 orphaned history rows for the deleted users — the phase 2/3
    finding E6 recurring for a new table. `scripts/discovery_test_account.py` now sweeps a named
    `HISTORY_TABLES` tuple covering all three, `ACCOUNT_TABLES` covers the two new user-keyed
    tables, and the gate regression asserts both. The 14 rows were removed with one statement
    scoped to the phase 3.5 question ids and to users no longer in `auth.users`; nothing else
    was touched.
12. **The RLS check's "latest history row" query had no deterministic tiebreaker** inside one
    transaction (`now()` is constant), which showed as one wrong "previous value". Both history
    walks now order by `ctid` last.
13. **One transient in the isolation check.** On one run, its `--retire` seed subprocess exited
    1 having printed nothing after "fixtures valid", and the three retirement checks failed.
    The same run passed 38/38 on two subsequent executions and a standalone reproduction
    passed; the cause was not identified. Two changes so it cannot recur silently: the check
    now prints the subprocess's streams verbatim on failure, and the loader prints psql's exit
    status even when psql said nothing. Reported here rather than smoothed over.
14. **The vocabulary drift test's parser** initially captured the CASE branch labels
    (`'inflow'`, `'outflow'`) as categories; fixed. The constraint itself matched from the first
    run.

---

## 12. Open issues before real question-bank work

- **Retire or remove the two disposable templates** (`phase2-discovery-test`,
  `phase35-calibration-test`) when phase 4 seeds the real banks, so the real portal never lists
  test material. The loader deliberately does not delete; a phase 4 step should retire them.
- **Decision 6, variant spacing**: enforce a minimum gap in the loader, or accept adjacency.
- **Authoring rules for the real bank**: invented surnames only for family-transfer cases
  (design §15); a `case_family` naming convention; which of the seventeen families first
  (design §19.4).
- **Whether `handling_recommendation` is asked on every case or a subset** (design §3); today it
  sits behind the "more detail" disclosure on every case.
- **Two disposable throwaway-account paths** now exist (`--template`); the phase 4 invite-link
  script replaces both for real people and must never call `remove_account`.
- **Consensus, prediction comparison and rule candidates** are phase 5 and 6, untouched;
  `import_rows.cashflow` still overwrites the model's category on correction (design §9,
  decision 9).
- **`discovery_self_assign` and `discovery_default_template`** remain unused (phase 3 E9).

---

## Proposed commit message

Not committed. For the author to apply or amend.

```
feat: transaction calibration, the data model and the one-call typed write (phase 3.5A)

A second calibration layer beside the borrower scenario: one synthetic
bank row at a time, asked what it is in the classifier's own eleven
buckets and how much it matters when deciding whether to lend. This
phase proves the model on a six-case synthetic fixture; the real bank,
the real accounts and the comparison views follow.

Two migrations, because a value added to an existing enum cannot be
used in the transaction that adds it and the runner is one transaction
per file: 0026 adds the type, 0027 builds with it. Three tables: the
case, typed and queryable, with its family and variant metadata; the
answer, with direction copied from the case and bound back to it by a
composite key so the category-fits-direction check is a row-level
constraint; and its history, trigger-written like the others. Two
constraints on 0025's tables widened; scenario_brief_matches_type left
alone. Fourteen Discovery tables, all service-role only, no policies.

Phase 3's finding E7 is closed for both typed types: one database
function writes the generic row and the typed row together, reads a
calibration's direction from the case rather than the caller, and
handles skips the same way, so a refused typed write leaves no generic
row. It is revoked from anon and authenticated; FastAPI stays the
boundary.

The loader makes a controlled variant structural: a variant inherits
its baseline and must differ from it in exactly the feature it names,
or the fixture is refused in --check. Synthetic is enforced; a branch
may not target a typed question. The existing save route carries a
strict calibration object; no new route, grant or policy.

Verified: 827 unit tests (79 new) with one armed skip; 13 web tests;
ruff, eslint, tsc and the production build clean; 143/143 database
checks including the function's atomicity and privileges; 33/33,
38/38 and 31/31 flow, isolation and calibration checks through the
running API, two stakeholders answering one question with isolated
answers, every field's history, and everything throwaway removed. The
throwaway sweep now covers the calibration history table, which the
first check run showed it did not.
```
