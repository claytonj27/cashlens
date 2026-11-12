-- 0027: transaction calibration, the second calibration layer of the Discovery portal.
--
-- Design: docs/transaction-calibration-design.md; DDL as proposed in
-- docs/transaction-calibration-schema-proposal.md. Requires 0026 to have committed,
-- because two constraints below name the enum value it added.
--
-- One transaction (a single row from a bank statement, or a pattern of them), synthetic, asked
-- two questions: what is it, in the classifier's own eleven buckets, and how much does it
-- matter when deciding whether to lend. Kept apart from scenario_decision, which asks about a
-- whole borrower. Three tables, four enum types, one history trigger, one write function, two
-- widened check constraints on tables 0025 created, and the same privileges every Discovery
-- table gets: service role only, RLS enabled, no policies.
--
-- Also the fix for the phase 3 finding E7: a typed answer was two PostgREST writes with a
-- compensating delete. save_discovery_typed_response() below writes the generic row and the
-- typed row in one function, so they commit together or not at all. It is called by the API
-- with the service role after ownership has been resolved; it is not an authorization surface,
-- and it is not executable by anon or authenticated.

-- ---------------------------------------------------------------------------------- types --
-- Ordinal, declared in order, so `materiality > 'medium'` and `order by materiality` mean what
-- they read as, and the distance between two answers is arithmetic on the position.
create type public.underwriting_materiality as enum
  ('immaterial', 'low', 'medium', 'high', 'critical');

-- Nominal. `unknown` is a real answer and is not `neutral`: one is "no effect", the other is
-- "I cannot say from this".
create type public.underwriting_signal as enum
  ('positive', 'neutral', 'negative', 'unknown');

-- What the line would do to a decision, as opposed to how much it matters.
create type public.underwriting_impact as enum
  ('none', 'contributes_positive', 'contributes_negative', 'manual_review',
   'potential_hard_decline');

-- What the system should do with a line like this. One field rather than three booleans.
create type public.discovery_handling_recommendation as enum
  ('auto_classify', 'ai_review', 'human_review', 'ask_borrower');

-- ------------------------------------------------------------------------------- the case --
-- One row per calibration question: the transaction the respondent reads, and the metadata the
-- controlled-variant analysis groups by. Keyed by question_id, one-to-one, so no column is added
-- to discovery_questions that would be null for every other type.
create table public.discovery_transaction_cases (
  question_id uuid primary key
    references public.discovery_questions (id) on delete cascade,

  -- A family is one baseline plus variants that differ from it in exactly one named feature.
  case_family text not null check (case_family ~ '^[a-z0-9][a-z0-9-]{2,60}$'),
  variant text not null check (variant ~ '^[a-z0-9][a-z0-9-]{0,60}$'),
  -- Null marks the family's baseline. Otherwise it names the one column that differs from the
  -- baseline, and the seed loader proves the claim by comparing the two rows column by column.
  changed_feature text check (changed_feature is null or changed_feature in (
    'amount_minor', 'day_of_week', 'time_of_day', 'business_vertical', 'merchant',
    'counterparty', 'payment_rail', 'recurrence_pattern', 'frequency_30d', 'descriptor',
    'account_type', 'related_transactions', 'direction')),

  -- Every committed case is made up. The loader refuses anything else, in --check as well as on
  -- a real run: a calibration case is a transaction row, and CLAUDE.md does not allow one from a
  -- real statement in this repository. The column exists so an authorised path could one day be
  -- expressed without a migration; `source` is what such a row would have to name.
  synthetic boolean not null default true,
  source text check (source is null or length(source) between 1 and 200),
  constraint a_real_case_must_name_its_authorisation check (synthetic or source is not null),

  -- The business the transaction belongs to.
  business_vertical text check (business_vertical is null
    or business_vertical ~ '^[a-z0-9][a-z0-9-]{1,40}$'),
  business_description text check (business_description is null
    or length(business_description) <= 400),
  account_type text check (account_type is null or account_type in ('checking', 'savings')),

  -- The transaction. Direction is arithmetic, exactly as in migration 0024: computed from the
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
  -- without dragging every other date-shaped field with it.
  day_of_week smallint check (day_of_week is null or day_of_week between 0 and 6),
  time_of_day time,
  memo text check (memo is null or length(memo) <= 300),

  -- The pattern a single row cannot carry.
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
  on public.discovery_transaction_cases (business_vertical) where business_vertical is not null;
create index discovery_transaction_cases_by_merchant
  on public.discovery_transaction_cases (merchant) where merchant is not null;
-- At most one baseline per family. At least one is the loader's job: a partial unique index can
-- forbid a second, but it cannot require a first.
create unique index discovery_transaction_cases_one_baseline
  on public.discovery_transaction_cases (case_family) where changed_feature is null;
comment on table public.discovery_transaction_cases is
  'The synthetic transaction a calibration question asks about, plus the family and variant metadata that make controlled comparison possible. One row per question; never real statement data.';

-- ----------------------------------------------------------------------------- the answer --
-- The typed answer, in the shape discovery_scenario_responses uses: primary key on the parent
-- response, denormalised ownership bound by a composite key, history by trigger.
create table public.discovery_transaction_calibration_responses (
  response_id uuid primary key
    references public.discovery_responses (id) on delete cascade,
  assignment_id uuid not null,
  user_id uuid not null,
  question_id uuid not null,

  -- Copied from the case at write time, by the write function and never from a caller, so
  -- "the category is valid for this direction" is a row-level check rather than a cross-table
  -- lookup. The composite key below forces the copy to equal the case's own direction, which is
  -- the device discovery_responses.question_type already uses for its answer-shape check.
  direction text not null check (direction in ('inflow', 'outflow')),

  -- One of the eleven cash-flow buckets, as text, because the vocabulary belongs to the
  -- classifier (services/api/app/ai/prompts.py) rather than to Discovery, and the two lists must
  -- stay identical or every comparison becomes a mapping table. A real-database check asserts
  -- this constraint equals those tuples.
  classification_category text not null,
  classification_confidence smallint not null
    check (classification_confidence between 1 and 5),

  underwriting_materiality public.underwriting_materiality not null,
  underwriting_signal public.underwriting_signal not null,
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

  -- Naming the missing context without saying you need it is incoherent, and would make the
  -- boolean untrustworthy as the denominator of an "ask the borrower" rate.
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

-- ---------------------------------------------------------------------------- the history --
-- Append-only, every typed column, no foreign key so it outlives a delete, written by a trigger
-- no caller can skip. A changed calibration label is as interesting as a changed underwriting
-- decision.
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
  underwriting_signal public.underwriting_signal not null,
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
-- preserved alongside its parent.
create trigger discovery_calibration_responses_history
  before update or delete on public.discovery_transaction_calibration_responses
  for each row execute function public.record_discovery_calibration_history();

-- --------------------------------------------------------- the two constraints from 0025 --
-- The first alterations to objects 0025 created. Both are widenings; both name the value 0026
-- added, which is why they cannot be in that file.

-- A calibration question carries reason codes in `options`, as a scenario question does, so it
-- joins the list of types that require them.
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

-- scenario_brief_matches_type is deliberately untouched: a calibration question must not carry
-- a scenario_brief, and the existing constraint already forbids one on any type but
-- scenario_decision.

-- ------------------------------------------------------------------ the typed write (E7) --
-- The generic row and the typed row, written together. Called by the API with the service role
-- after `Mine` has resolved the assignment; it decides nothing about ownership, and the four
-- composite foreign keys on discovery_responses remain the structural proof. The direction of a
-- calibration answer is read from the case here, never taken from the caller.
--
-- `on conflict ... do update` is what makes the save idempotent, and it is an UPDATE, so the
-- before-update triggers record history exactly as a PATCH did. A skip deletes the typed row
-- through the same function, so the before-delete trigger records that too.
create function public.save_discovery_typed_response(
  p_assignment_id uuid,
  p_user_id uuid,
  p_template_id uuid,
  p_section_id uuid,
  p_question_id uuid,
  p_question_version integer,
  p_question_type public.discovery_question_type,
  p_status text,
  p_note text,
  p_typed jsonb
) returns uuid
language plpgsql set search_path = public as $$
declare
  v_response_id uuid;
  v_direction text;
begin
  if p_question_type not in ('scenario_decision', 'transaction_calibration') then
    raise exception 'save_discovery_typed_response: % is not a typed question type',
      p_question_type using errcode = 'check_violation';
  end if;
  if p_status not in ('answered', 'skipped') then
    raise exception 'save_discovery_typed_response: status must be answered or skipped'
      using errcode = 'check_violation';
  end if;
  if p_status = 'answered' and (p_typed is null or jsonb_typeof(p_typed) <> 'object') then
    raise exception 'save_discovery_typed_response: an answer needs its typed detail'
      using errcode = 'check_violation';
  end if;

  insert into public.discovery_responses (
    assignment_id, user_id, template_id, section_id, question_id,
    question_version, question_type, status, answer, note
  ) values (
    p_assignment_id, p_user_id, p_template_id, p_section_id, p_question_id,
    p_question_version, p_question_type, p_status, null,
    case when p_status = 'answered' then p_note else null end
  )
  on conflict (assignment_id, question_id) do update
    set status = excluded.status,
        answer = null,
        note = excluded.note,
        question_version = excluded.question_version
  returning id into v_response_id;

  if p_status = 'skipped' then
    delete from public.discovery_scenario_responses where response_id = v_response_id;
    delete from public.discovery_transaction_calibration_responses
      where response_id = v_response_id;
    return v_response_id;
  end if;

  if p_question_type = 'scenario_decision' then
    insert into public.discovery_scenario_responses (
      response_id, assignment_id, user_id, question_id,
      decision, confidence, reason_codes, maximum_exposure_minor,
      additional_information_needed, what_would_change_my_decision, notes
    ) values (
      v_response_id, p_assignment_id, p_user_id, p_question_id,
      (p_typed->>'decision')::public.underwriting_decision,
      (p_typed->>'confidence')::smallint,
      array(select jsonb_array_elements_text(p_typed->'reason_codes')),
      (p_typed->>'maximum_exposure_minor')::bigint,
      p_typed->>'additional_information_needed',
      p_typed->>'what_would_change_my_decision',
      p_typed->>'notes'
    )
    on conflict (response_id) do update
      set decision = excluded.decision,
          confidence = excluded.confidence,
          reason_codes = excluded.reason_codes,
          maximum_exposure_minor = excluded.maximum_exposure_minor,
          additional_information_needed = excluded.additional_information_needed,
          what_would_change_my_decision = excluded.what_would_change_my_decision,
          notes = excluded.notes;
  else
    select c.direction into v_direction
      from public.discovery_transaction_cases c where c.question_id = p_question_id;
    if v_direction is null then
      raise exception 'save_discovery_typed_response: question % has no transaction case',
        p_question_id using errcode = 'foreign_key_violation';
    end if;
    insert into public.discovery_transaction_calibration_responses (
      response_id, assignment_id, user_id, question_id, direction,
      classification_category, classification_confidence, underwriting_materiality,
      underwriting_signal, decision_impact, reason_codes, needs_more_context,
      context_needed, reasoning, handling_recommendation
    ) values (
      v_response_id, p_assignment_id, p_user_id, p_question_id, v_direction,
      p_typed->>'classification_category',
      (p_typed->>'classification_confidence')::smallint,
      (p_typed->>'underwriting_materiality')::public.underwriting_materiality,
      (p_typed->>'underwriting_signal')::public.underwriting_signal,
      (p_typed->>'decision_impact')::public.underwriting_impact,
      array(select jsonb_array_elements_text(p_typed->'reason_codes')),
      coalesce((p_typed->>'needs_more_context')::boolean, false),
      p_typed->>'context_needed',
      p_typed->>'reasoning',
      (p_typed->>'handling_recommendation')::public.discovery_handling_recommendation
    )
    on conflict (response_id) do update
      set direction = excluded.direction,
          classification_category = excluded.classification_category,
          classification_confidence = excluded.classification_confidence,
          underwriting_materiality = excluded.underwriting_materiality,
          underwriting_signal = excluded.underwriting_signal,
          decision_impact = excluded.decision_impact,
          reason_codes = excluded.reason_codes,
          needs_more_context = excluded.needs_more_context,
          context_needed = excluded.context_needed,
          reasoning = excluded.reasoning,
          handling_recommendation = excluded.handling_recommendation;
  end if;
  return v_response_id;
end $$;

-- Not a browser surface. Executable by the service role only, like every write in this schema.
revoke all on function public.save_discovery_typed_response(
  uuid, uuid, uuid, uuid, uuid, integer, public.discovery_question_type, text, text, jsonb)
  from public, anon, authenticated;
grant execute on function public.save_discovery_typed_response(
  uuid, uuid, uuid, uuid, uuid, integer, public.discovery_question_type, text, text, jsonb)
  to service_role;

-- ------------------------------------------------------------------- privileges and RLS --
-- The same three blocks every Discovery table gets, for the same reasons as 0025: the project's
-- default privileges hand anon and authenticated TRUNCATE, REFERENCES and TRIGGER on new tables,
-- which RLS does not restrict, so the revoke is not optional; and RLS enabled with no policy to
-- permit anything is a stronger default than a permissive policy. The case table is question
-- bank data: a read grant on it would leak cases a respondent has not been served.
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

alter table public.discovery_transaction_cases                        enable row level security;
alter table public.discovery_transaction_calibration_responses        enable row level security;
alter table public.discovery_transaction_calibration_response_history enable row level security;

-- No client policies, as with the other eleven.

notify pgrst, 'reload schema';
