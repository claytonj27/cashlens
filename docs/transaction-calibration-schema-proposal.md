# Discovery: transaction calibration, schema proposal

Status: **implemented as migrations `0026` and `0027` on 2026-09-20** (phase 3.5A). Sections
2–6 are the migrations as applied, with two differences recorded in the
[handoff](discovery-phase3-5-handoff.md): `underwriting_signal` is `not null` (an
approved decision), and the write function in section 7 was written rather than sketched, with
a `p_status` parameter so a typed skip goes through it too. Section 10's tables remain future
work. This is section 14 of the
[transaction calibration design](transaction-calibration-design.md), in the detail an
implementer needs: the DDL, the migration ordering, the two constraints on migration `0025` that
must be widened, the fixture shape, the loader rules and the additions to the real-database
checks.

Read the design document first. Nothing here is separable from it.

Conventions are taken from the existing 25 migrations and from
[the phase 1 schema](discovery-schema.md), not invented: service-role-only tables with
RLS and no policies, money as signed integer minor units, composite foreign keys so a copied
value cannot be spoofed, check constraints that encode a promise the application makes, and
history written by a trigger no caller can skip.

---

## 1. Why this is two migrations

`ALTER TYPE ... ADD VALUE` adds `transaction_calibration` to the existing
`public.discovery_question_type` enum. PostgreSQL permits that inside a transaction block, but
**the new value cannot be referenced by anything in the same transaction** — it fails with
*unsafe use of new value of enum type*. Two check constraints in section 5 name the literal.

`db/migrate.py` applies **each migration file in its own `psql --single-transaction` run**
(`cmd_apply` loops over the pending files, one call each). So the split is by file, not by run:
both migrations still apply in a single `python db/migrate.py apply`, and `status` shows them as
two rows in `schema_migrations`.

```
0026_discovery_transaction_calibration_type.sql    one statement: the enum value
0027_discovery_transaction_calibration.sql         the types, the tables, the trigger,
                                                   the grants, the two widened constraints
```

`0026` must stay alone and must never be merged into another file. A one-line comment in the file
saying so is cheaper than rediscovering this.

---

## 2. Migration 0026

```sql
-- 0026: the transaction calibration question type, and nothing else.
--
-- This file exists alone on purpose. Postgres will not let a value added to an existing enum be
-- referenced in the transaction that added it, and db/migrate.py runs one transaction per file,
-- so the constraints in 0027 that name 'transaction_calibration' need this to have committed
-- first. Do not merge this into another migration; it will fail with "unsafe use of new value of
-- enum type" and the failure will look like a constraint problem rather than an ordering one.
--
-- Design: docs/transaction-calibration-design.md and its schema proposal.

alter type public.discovery_question_type add value if not exists 'transaction_calibration';

notify pgrst, 'reload schema';
```

Adding a value to an enum is not reversible: `ALTER TYPE ... DROP VALUE` does not exist. Rolling
back means recreating the type, which is why section 11 treats rollback as "drop what `0027`
made and leave the word unused".

---

## 3. Migration 0027: the new types

Four new enum types. These are created fresh in the same migration that uses them, which is
allowed — the restriction in section 1 applies only to values added to a **pre-existing** type.

```sql
-- Ordinal, and declared in order, so `materiality > 'medium'` and `order by materiality` mean
-- what they read as, and the distance in section 7 of the design is arithmetic on the position.
create type public.underwriting_materiality as enum
  ('immaterial', 'low', 'medium', 'high', 'critical');

-- Nominal. `unknown` is a real answer and is not the same as `neutral`: one is "no effect",
-- the other is "I cannot say from this".
create type public.underwriting_signal as enum
  ('positive', 'neutral', 'negative', 'unknown');

-- What the line would do to a decision, as opposed to how much it matters.
create type public.underwriting_impact as enum
  ('none', 'contributes_positive', 'contributes_negative', 'manual_review',
   'potential_hard_decline');

-- What the system should do with a line like this. One field rather than three booleans; see
-- the design, section 3.
create type public.discovery_handling_recommendation as enum
  ('auto_classify', 'ai_review', 'human_review', 'ask_borrower');
```

Extending any of these later has the same two-file constraint as section 1. That is the price of
the ordering and of a closed vocabulary, and it is why `changed_feature` and the eleven
categories below are text with a check constraint instead.

---

## 4. Migration 0027: the tables

### 4.1 `discovery_transaction_cases`

One row per calibration question: the transaction the respondent reads, and the metadata the
controlled-variant analysis groups by. Keyed by `question_id`, so it is one-to-one with the
question and adds no column to `discovery_questions`, which would be null for every other type.

```sql
create table public.discovery_transaction_cases (
  question_id uuid primary key
    references public.discovery_questions (id) on delete cascade,

  -- ---- the controlled-variant metadata; see design section 6 ----
  -- A family is one baseline plus variants that differ from it in exactly one named feature.
  case_family text not null check (case_family ~ '^[a-z0-9][a-z0-9-]{2,60}$'),
  variant text not null check (variant ~ '^[a-z0-9][a-z0-9-]{0,60}$'),
  -- Null marks the family's baseline. Otherwise it names the one column that differs from it,
  -- and the seed loader proves the claim by comparing the two rows column by column.
  changed_feature text check (changed_feature is null or changed_feature in (
    'amount_minor', 'day_of_week', 'time_of_day', 'business_vertical', 'merchant',
    'counterparty', 'payment_rail', 'recurrence_pattern', 'frequency_30d', 'descriptor',
    'account_type', 'related_transactions', 'direction')),

  -- Every committed case is made up. The loader refuses anything else, in --check as well as on
  -- a real run, because a calibration case is a transaction row and CLAUDE.md does not allow one
  -- from a real statement in this repository. The column exists rather than being assumed so an
  -- authorised path could one day be expressed without a migration, and `source` is what such a
  -- row would have to name.
  synthetic boolean not null default true,
  source text check (source is null or length(source) between 1 and 200),
  constraint a_real_case_must_name_its_authorisation check (synthetic or source is not null),

  -- ---- the business the transaction belongs to ----
  business_vertical text check (business_vertical is null or
    business_vertical ~ '^[a-z0-9][a-z0-9-]{1,40}$'),
  business_description text check (business_description is null or
    length(business_description) <= 400),
  account_type text check (account_type is null or account_type in ('checking', 'savings')),

  -- ---- the transaction ----
  -- Arithmetic, not judgement, exactly as in migration 0024: direction is computed from the
  -- amount before anyone sees the row, and the constraint below keeps the two in step.
  direction text not null check (direction in ('inflow', 'outflow')),
  amount_minor bigint not null,
  descriptor text not null check (length(descriptor) between 1 and 200),
  merchant text check (merchant is null or length(merchant) <= 80),
  counterparty text check (counterparty is null or length(counterparty) <= 80),
  payment_rail text check (payment_rail is null or payment_rail in
    ('ach', 'card', 'zelle', 'wire', 'check', 'cash', 'internal', 'other')),
  posted_date date,
  -- Stored rather than derived from posted_date, so a variant can change the day of the week
  -- without moving the date and dragging every other date-shaped field with it.
  day_of_week smallint check (day_of_week is null or day_of_week between 0 and 6),
  time_of_day time,
  memo text check (memo is null or length(memo) <= 300),

  -- ---- the pattern a single row cannot carry ----
  frequency_30d integer check (frequency_30d is null or frequency_30d >= 0),
  frequency_90d integer check (frequency_90d is null or frequency_90d >= 0),
  average_amount_minor bigint,
  amount_min_minor bigint,
  amount_max_minor bigint,
  recurrence_pattern text check (recurrence_pattern is null or recurrence_pattern in
    ('none', 'weekly', 'biweekly', 'semimonthly', 'monthly', 'irregular')),

  -- Display only. Nothing groups by these, so they stay JSON: the rows shown alongside (the
  -- debit a credit reverses, the three prior transfers) and any remaining context such as
  -- "the counterparty shares the applicant's surname".
  related_transactions jsonb not null default '[]'::jsonb
    check (jsonb_typeof(related_transactions) = 'array'),
  context jsonb not null default '{}'::jsonb check (jsonb_typeof(context) = 'object'),

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),

  unique (case_family, variant),
  -- The composite key a calibration response binds against, so the direction copied onto the
  -- answer cannot disagree with the direction of the case it answers.
  unique (question_id, direction),

  constraint direction_matches_the_amount check (
    (direction = 'inflow' and amount_minor > 0) or
    (direction = 'outflow' and amount_minor < 0)
  ),
  constraint the_amount_range_contains_the_average check (
    amount_min_minor is null or amount_max_minor is null or
    (amount_min_minor <= amount_max_minor
     and (average_amount_minor is null
          or average_amount_minor between amount_min_minor and amount_max_minor))
  )
);

create index discovery_transaction_cases_by_family
  on public.discovery_transaction_cases (case_family);
create index discovery_transaction_cases_by_vertical
  on public.discovery_transaction_cases (business_vertical)
  where business_vertical is not null;
create index discovery_transaction_cases_by_merchant
  on public.discovery_transaction_cases (merchant) where merchant is not null;

-- At most one baseline per family. At least one is the loader's job: a partial unique index can
-- forbid a second, but it cannot require a first.
create unique index discovery_transaction_cases_one_baseline
  on public.discovery_transaction_cases (case_family) where changed_feature is null;

comment on table public.discovery_transaction_cases is
  'The synthetic transaction a calibration question asks about, plus the family and variant metadata that make controlled comparison possible. One row per question; never real statement data.';
```

### 4.2 `discovery_transaction_calibration_responses`

The typed answer, in exactly the shape `discovery_scenario_responses` uses: primary key on the
parent response, denormalised ownership columns bound by a composite key, and history by trigger.

```sql
create table public.discovery_transaction_calibration_responses (
  response_id uuid primary key
    references public.discovery_responses (id) on delete cascade,
  assignment_id uuid not null,
  user_id uuid not null,
  question_id uuid not null,

  -- Copied from the case at write time so "the category is valid for this direction" is a
  -- row-level check rather than a cross-table lookup the API has to remember to do. The
  -- composite foreign key below forces the copy to equal the case's own direction, which is the
  -- same device discovery_responses.question_type already uses for its answer-shape check.
  direction text not null check (direction in ('inflow', 'outflow')),

  -- One of the eleven cash-flow buckets, as text, because this vocabulary belongs to the
  -- classifier (services/api/app/ai/prompts.py) rather than to Discovery, and because the two
  -- lists must stay identical or every comparison becomes a mapping table. A real-database check
  -- asserts this constraint equals those tuples; see section 9.
  classification_category text not null,
  classification_confidence smallint not null check (classification_confidence between 1 and 5),

  underwriting_materiality public.underwriting_materiality not null,
  underwriting_signal public.underwriting_signal,
  decision_impact public.underwriting_impact,

  -- Validated against the question's own options by the API before the write, as scenario
  -- reason codes are.
  reason_codes text[] not null default '{}',

  needs_more_context boolean not null default false,
  context_needed text check (context_needed is null or length(context_needed) <= 2000),
  reasoning text check (reasoning is null or length(reasoning) <= 2000),
  handling_recommendation public.discovery_handling_recommendation,

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),

  foreign key (assignment_id, user_id)
    references public.discovery_assignments (id, user_id) on delete cascade,
  foreign key (question_id, direction)
    references public.discovery_transaction_cases (question_id, direction) on delete cascade,

  constraint calibration_reason_codes_bounded check (cardinality(reason_codes) <= 20),

  -- The eleven, and which six are legal for this direction. One constraint does both jobs.
  constraint calibration_category_matches_direction check (
    case direction
      when 'inflow' then classification_category in (
        'Organic revenue', 'Internal transfers', 'Loans income', 'Personal income',
        'Overdraft/Reversals', 'Unclassified')
      when 'outflow' then classification_category in (
        'Operating expense', 'Transfer out', 'Loan payments', 'Personal expense',
        'Payroll', 'Unclassified')
      else false
    end
  ),

  -- Naming the missing context without saying you need it is incoherent, and it would make the
  -- boolean untrustworthy as the denominator of an "ask the borrower" rate. The interface sets
  -- the flag when the text is entered.
  constraint context_needed_requires_the_flag check (
    context_needed is null or needs_more_context
  )
);

create index discovery_calibration_by_question
  on public.discovery_transaction_calibration_responses (question_id);
create index discovery_calibration_by_user
  on public.discovery_transaction_calibration_responses (user_id);
create index discovery_calibration_by_category
  on public.discovery_transaction_calibration_responses (classification_category);

comment on table public.discovery_transaction_calibration_responses is
  'One stakeholder''s reading of one transaction: what it is, how sure they are, how much it matters. Evidence, never ground truth; consensus is computed elsewhere and policy is decided by a person.';
```

### 4.3 `discovery_transaction_calibration_response_history`

Append-only, every typed column, no foreign key, written by a `before update or delete` trigger.
Identical in shape and intent to `discovery_scenario_response_history`: a changed calibration
label is as interesting as a changed underwriting decision, and history written by application
code is history a future code path can skip.

```sql
create table public.discovery_transaction_calibration_response_history (
  id uuid primary key default gen_random_uuid(),
  response_id uuid not null,
  assignment_id uuid not null,
  user_id uuid not null,
  question_id uuid not null,

  direction text not null,
  classification_category text not null,
  classification_confidence smallint not null,
  underwriting_materiality public.underwriting_materiality not null,
  underwriting_signal public.underwriting_signal,
  decision_impact public.underwriting_impact,
  reason_codes text[] not null default '{}',
  needs_more_context boolean not null,
  context_needed text,
  reasoning text,
  handling_recommendation public.discovery_handling_recommendation,

  change text not null check (change in ('updated', 'deleted')),
  valid_from timestamptz not null,
  valid_to timestamptz not null default now(),
  created_at timestamptz not null default now()
);

create index discovery_calibration_history_by_response
  on public.discovery_transaction_calibration_response_history (response_id, valid_to desc);
create index discovery_calibration_history_by_question
  on public.discovery_transaction_calibration_response_history (question_id);

create function public.record_discovery_calibration_history() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  insert into public.discovery_transaction_calibration_response_history (
    response_id, assignment_id, user_id, question_id, direction,
    classification_category, classification_confidence, underwriting_materiality,
    underwriting_signal, decision_impact, reason_codes, needs_more_context,
    context_needed, reasoning, handling_recommendation,
    change, valid_from, valid_to
  ) values (
    old.response_id, old.assignment_id, old.user_id, old.question_id, old.direction,
    old.classification_category, old.classification_confidence, old.underwriting_materiality,
    old.underwriting_signal, old.decision_impact, old.reason_codes, old.needs_more_context,
    old.context_needed, old.reasoning, old.handling_recommendation,
    case when tg_op = 'DELETE' then 'deleted' else 'updated' end,
    old.updated_at, now()
  );
  if tg_op = 'DELETE' then return old; end if;
  new.updated_at := now();
  return new;
end $$;
revoke all on function public.record_discovery_calibration_history()
  from public, anon, authenticated;

-- A cascaded delete from discovery_responses fires this too, so a deleted calibration answer is
-- preserved alongside its parent. Phase 3 verified that ordering for the scenario pair against
-- the real database; the calibration pair needs the same check.
create trigger discovery_calibration_responses_history
  before update or delete on public.discovery_transaction_calibration_responses
  for each row execute function public.record_discovery_calibration_history();
```

---

## 5. Migration 0027: the two constraints on `0025`'s tables

These are the first alterations to objects migration `0025` created. Both are widenings, both are
`drop constraint` then `add constraint` on small tables, and both name the literal added in
`0026`, which is why they cannot be in that file.

```sql
-- A calibration question carries reason codes in `options`, exactly as a scenario question does,
-- so it joins the list of types that require them.
alter table public.discovery_questions drop constraint options_match_type;
alter table public.discovery_questions add constraint options_match_type check (
  case
    when question_type in ('single_select', 'multi_select', 'ranking',
                           'scenario_decision', 'transaction_calibration')
      then jsonb_typeof(options) = 'array' and jsonb_array_length(options) > 0
    else options = '[]'::jsonb
  end
);

-- A calibration answer's detail lives in its own table, so the generic row carries a null
-- answer, as a scenario answer already does. Without this every calibration response falls to
-- the constraint's `else false` and is rejected.
alter table public.discovery_responses drop constraint response_answer_matches_type;
alter table public.discovery_responses add constraint response_answer_matches_type check (
  case
    when status = 'skipped' then answer is null
    when question_type in ('scenario_decision', 'transaction_calibration') then answer is null
    when question_type = 'boolean' then jsonb_typeof(answer) = 'boolean'
    when question_type in ('number', 'currency', 'percentage', 'scale')
      then jsonb_typeof(answer) = 'number'
    when question_type in ('short_text', 'long_text', 'single_select')
      then jsonb_typeof(answer) = 'string'
    when question_type in ('multi_select', 'ranking') then jsonb_typeof(answer) = 'array'
    else false
  end
);
```

**`scenario_brief_matches_type` is deliberately left alone.** A calibration question must not
carry a `scenario_brief`; its stimulus is a row in `discovery_transaction_cases`. The existing
constraint already forbids a brief on any type but `scenario_decision`, which is exactly right,
and leaving it untouched keeps the two stimulus mechanisms from blurring.

---

## 6. Migration 0027: privileges and RLS

The same three lines every Discovery table gets, for the same reason: the project's default
privileges hand `anon` and `authenticated` TRUNCATE, REFERENCES and TRIGGER on new tables, which
RLS does not restrict, so the revoke is not optional; and RLS enabled with no policy to permit
anything is a stronger default than a permissive policy.

```sql
revoke all on table
  public.discovery_transaction_cases,
  public.discovery_transaction_calibration_responses,
  public.discovery_transaction_calibration_response_history
  from anon, authenticated;

grant select, insert, update, delete on table
  public.discovery_transaction_cases,
  public.discovery_transaction_calibration_responses,
  public.discovery_transaction_calibration_response_history
  to service_role;

alter table public.discovery_transaction_cases                            enable row level security;
alter table public.discovery_transaction_calibration_responses            enable row level security;
alter table public.discovery_transaction_calibration_response_history     enable row level security;

-- No client policies, as with the other eleven.

notify pgrst, 'reload schema';
```

The case table is question-bank data. A read grant on it would leak cases a respondent has not
been served, for the same reason a grant on `discovery_questions` would leak branch-hidden
questions. That is the third entry in the threat model's reasoning, unchanged.

---

## 7. The write path, and finding E7

The API writes two rows per calibration save — the generic parent and the typed child — exactly
as it does for a scenario, with a compensating delete if the second fails. The design recommends
replacing both with one database function in phase 3.5:

```sql
-- Not written yet. Sketch only, so the shape is agreed before it is implemented.
create function public.save_discovery_typed_response(
  p_assignment_id uuid, p_user_id uuid, p_template_id uuid, p_section_id uuid,
  p_question_id uuid, p_question_version integer,
  p_question_type public.discovery_question_type,
  p_typed jsonb
) returns uuid ...
```

Properties it must have, none of which are new authorization:

- It is called by the API **with the service role, after `Mine` has resolved the scope**. It is
  not a place where ownership is decided, and it takes the assignment and user the resolver
  already proved. The four composite foreign keys on `discovery_responses` remain the structural
  proof; the function does not restate them.
- It is `security definer` only if it needs to be (it does not: the service role already holds
  every verb), and it is revoked from `public`, `anon` and `authenticated` like the three
  existing trigger functions.
- Parent and child are inserted or updated in one statement block, so there is no window in which
  one exists without the other and no compensating delete to get wrong.
- Both typed types use it. Writing it for calibration alone would leave the scenario path on the
  old shape and double the code to maintain.

Until it exists, the phase 3 mitigation stands: a half-written typed answer is invisible to every
route, counts for nothing in progress, and is repaired by the next save.

---

## 8. Fixture shape and loader rules

The section file gains one optional block and each calibration question gains a
`transaction_case`. Reason codes are declared once per section and expanded by the loader onto
every calibration question in it, so the vocabulary has one home and the fixture does not repeat
twelve options forty times.

```json
{
  "note": "PHASE 3.5 TEST DATA. Disposable. Synthetic cases only.",
  "slug": "calibration-proof",
  "title": "Reading individual transactions",
  "is_core": true,
  "estimated_minutes": 12,
  "calibration_reason_codes": [
    {"value": "merchant_ambiguous",   "label": "The merchant could be either"},
    {"value": "amount_atypical",      "label": "The amount is unusual for this"},
    {"value": "timing_atypical",      "label": "The timing points one way"},
    {"value": "counterparty_personal","label": "The counterparty looks personal"},
    {"value": "recurrence_regular",   "label": "It recurs too regularly to be incidental"},
    {"value": "vertical_decides",     "label": "The business type settles it"},
    {"value": "need_more_context",    "label": "I would need to see more"}
  ],
  "questions": [
    {
      "question_key": "calib.uber.restaurant.baseline",
      "question_text": "What is this, and does it matter?",
      "question_type": "transaction_calibration",
      "required_core": true,
      "priority": 70,
      "sensitivity": "low",
      "order": 0,
      "transaction_case": {
        "case_family": "uber-restaurant",
        "variant": "baseline",
        "synthetic": true,
        "business_vertical": "restaurant",
        "business_description": "A twelve-table neighbourhood restaurant, open six days.",
        "account_type": "checking",
        "direction": "outflow",
        "amount_minor": -1840,
        "descriptor": "UBER *EATS 8N2K4",
        "merchant": "Uber",
        "payment_rail": "card",
        "posted_date": "2026-03-14",
        "day_of_week": 6,
        "time_of_day": "22:41",
        "frequency_30d": 11,
        "amount_min_minor": -2400,
        "amount_max_minor": -900,
        "average_amount_minor": -1680,
        "recurrence_pattern": "irregular"
      }
    },
    {
      "question_key": "calib.uber.restaurant.tuesday",
      "question_text": "What is this, and does it matter?",
      "question_type": "transaction_calibration",
      "required_core": false,
      "order": 4,
      "transaction_case": {
        "case_family": "uber-restaurant",
        "variant": "tuesday-morning",
        "changed_feature": "day_of_week",
        "day_of_week": 2,
        "time_of_day": "09:15",
        "posted_date": "2026-03-17",
        "…": "every other field identical to the baseline"
      }
    }
  ]
}
```

A variant inherits the baseline's fields and overrides only what it changes — the loader
materialises the full row before writing, so the database holds two complete, independently
readable cases rather than a diff.

**Loader validation, all in `--check` and therefore in CI.** Each rule exists because the
alternative is a property that depends on the fixture author's care.

| Rule | Why |
|---|---|
| A `transaction_calibration` question has a `transaction_case`; no other type has one | The type tells you what the row is |
| `synthetic` is true; a non-synthetic case is refused outright | `CLAUDE.md`: no real statement data in the repository |
| `case_family` and `variant` unique together; exactly one baseline per family | Paired analysis needs a control |
| `changed_feature` is one of the thirteen, and is null exactly on the baseline | Grouping by feature has to mean something |
| **A variant differs from its baseline in the named feature and in nothing else** | This is what makes "controlled" structural rather than claimed. `day_of_week` may move `posted_date` and `time_of_day` with it, since a day is not a date; every other pairing is one column |
| `direction` agrees with the sign of `amount_minor` | Direction is arithmetic, as in `0024` |
| Amounts are integers in minor units | The money rule; a float in a fixture is a rounding bug waiting to happen |
| `day_of_week` 0–6; frequencies ≥ 0; `min ≤ average ≤ max` | Cheap coherence, caught before a respondent sees nonsense |
| Reason codes expand from the section, and every calibration question ends with at least one option | `options_match_type` would refuse it at the database otherwise, after the transaction started |
| A `branch_condition` may not reference a `transaction_calibration` or `scenario_decision` question | Their generic answer is null, so the condition could never hold. True of scenarios today and currently unvalidated |
| Variants of one family are at least three questions apart in `order` | Adjacency measures consistency rather than judgement (design §6; open decision 6, so this rule ships only if adopted) |

---

## 9. Additions to the real-database checks

`db/checks/discovery_rls_check.py`

- The `TABLES` tuple grows from eleven to fourteen. Three assertions currently compare against
  the literal `11`; replace the literal with `len(TABLES)` in the same change, so the next table
  cannot be added without its privilege assertions.
- The same permission-denied, insert/update/delete-refused, RLS-enabled, no-policy and
  stray-grant-still-yields-zero-rows assertions, applied to the three new tables.
- New: the category vocabulary drift test. Read `pg_get_constraintdef` for
  `calibration_category_matches_direction`, extract the quoted literals for each direction, and
  assert they equal `prompts.CASHFLOW_INFLOW_CATEGORIES` and
  `prompts.CASHFLOW_OUTFLOW_CATEGORIES`. Without it the classifier's list and the calibration
  list drift the first time a bucket is renamed, and every metric in the design's section 11
  silently starts measuring a mapping error.
- New: the direction constraints. An inflow category on an outflow case is refused by
  `calibration_category_matches_direction`; a response whose `direction` disagrees with its case
  is refused by the composite foreign key. Both as `postgres`, so the constraint rather than a
  permission is what refuses.
- New: the trigger. One update writes exactly one calibration history row carrying the previous
  values; deleting the parent response writes a `deleted` row through the cascade.

`db/checks/discovery_isolation_check.py`

- Extend the disposable gate bank with one calibration family, and run the existing
  cross-stakeholder attack set against it: two founders answering the same calibration
  `question_id` get two rows, neither visible to the other, with independent progress.
- Walk the calibration history field by field, as the scenario walk already does.

---

## 10. What is deliberately not in these migrations

| Object | Phase | Why not now |
|---|---|---|
| `calibration_consensus` | 5 | Derived from evidence. It can be dropped and recomputed, so it should not exist before the rules that compute it are settled by looking at real answers. |
| `calibration_predictions` | 5 | Produced by an evaluation harness that does not exist. It needs the classifier version, prompt version, model id and mode, and those are the harness's to define. |
| `calibration_rule_candidates` | 6+ | Under its own decision record. Nothing should make it easy to write a rule into a table before the promotion path is agreed. |
| Anything touching `import_rows.cashflow` | — | Discovery does not reach into the product's data. Preserving the model's category through a human correction is a classifier-side change in its own milestone. |

The three layers stay in three places, and a table that does not exist cannot be joined as if it
were ground truth.

---

## 11. Rollback

`0027` is dropped by dropping what it made, in dependency order: the trigger, the function, the
three tables, the four enum types, and the two constraints restored to their `0025` text. That
leaves the database as it was, apart from one unused word in
`public.discovery_question_type`.

`0026` is not reversible: PostgreSQL has no `ALTER TYPE ... DROP VALUE`. Removing the value means
recreating the type and rewriting every column that uses it, which is not worth doing for an
unused enum label. If the feature is abandoned, the honest end state is the word left in the type
with a comment saying so.

Both migrations are additive to every existing row: no data is rewritten, the two widened
constraints only admit more, and nothing in production reaches any of it while `DISCOVERY_ENABLED`
is unset.

---

## 12. Summary of what a reviewer is approving

| | Count |
|---|---|
| Migrations | 2 (`0026`, `0027`) |
| New enum values on an existing type | 1 |
| New enum types | 4 |
| New tables | 3 (14 Discovery tables in total, from 11) |
| New trigger functions | 1 |
| Constraints altered on `0025`'s tables | 2, both widenings |
| Existing tables whose columns change | 0 |
| New client-facing routes | 0 |
| New grants to `anon` or `authenticated` | 0 |
| New RLS policies | 0 |
| SECURITY DEFINER functions taking a user id | 0 |
