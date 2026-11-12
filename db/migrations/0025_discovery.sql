-- 0025: the Discovery portal's storage (phase 1: roles, the question bank, answers).
--
-- Design: docs/discovery-schema.md, revision 2. ADR 0014 records why a feature named
-- for a customer sits inside CashLens at all. Nothing here is user-visible on its own; this is
-- the foundation the portal is built on in later phases.
--
-- Eleven tables, four enum types, three trigger functions. Purely additive: no existing table
-- is altered and no existing function is replaced, which is what makes it safe to apply to the
-- development database that production also reads.
--
-- Three positions this migration takes, each stated once here and enforced below:
--
-- * Every table is SERVICE ROLE ONLY. RLS is enabled, no grant reaches anon or authenticated,
--   and there are NO client policies. That is the house pattern from migration 0002 for a table
--   the browser never reads (visitors, workspaces, raw_events, sync_batches, connections,
--   transaction_sources). FastAPI is the only data path, and it holds the service role, which
--   bypasses RLS: so on that path Python is the control, and RLS here is dormant insurance
--   against a future accidental grant rather than an active check.
--
-- * Ownership and membership are STRUCTURAL. A response carries four composite foreign keys
--   that together prove the answering user owns the assignment, the question sits in a section,
--   the section sits in the assignment's template. An assignment's resume pointer is bound to
--   its own template's section list the same way. None of this is a matter of the API's care.
--
-- * History is written by TRIGGERS, not by application code, on both the generic answer table
--   and the typed scenario table. A trigger cannot be skipped by any caller, including a manual
--   SQL fix or a service-role script.
--
-- No SECURITY DEFINER role helper is defined. With no policies there is no SQL caller for one,
-- and a definer function taking an arbitrary user id is exactly where widening goes unnoticed.
-- Python reads public.profiles with the service role, which is the normal server-side path.

-- ---------------------------------------------------------------------------------- types --
create type public.app_role as enum ('admin', 'stakeholder', 'member');

create type public.discovery_question_type as enum (
  'short_text', 'long_text', 'boolean', 'single_select', 'multi_select',
  'number', 'currency', 'percentage', 'scale', 'ranking', 'scenario_decision'
);

create type public.discovery_sensitivity as enum ('low', 'medium', 'high');

create type public.underwriting_decision as enum ('approve', 'deny', 'manual_review');

-- ------------------------------------------------------------------------------- profiles --
-- One row per auth user. The only new authorization surface. `member` is the default for every
-- account that is neither the author nor a named stakeholder, which is every account that signs
-- up on the public site, and it grants nothing this feature exposes.
create table public.profiles (
  user_id uuid primary key references auth.users (id) on delete cascade,
  role public.app_role not null default 'member',
  display_name text check (display_name is null or length(display_name) between 1 and 80),
  first_name   text check (first_name is null or length(first_name) between 1 and 40),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
comment on table public.profiles is
  'Application role per auth user. Read by Python on every request; there is no SQL caller. It is not membership in any business: business access still comes from business_memberships.';
comment on column public.profiles.first_name is
  'So the portal can say "Welcome, Lead" without reading an email address into a page. Set by the seed, never by self-service.';

-- Backfill: every account that already exists gets a member profile, so no signed-in caller is
-- ever without one. Python still treats an absent row as member, because a code path that
-- assumed presence would fail open the first time the trigger below did not fire.
insert into public.profiles (user_id)
  select u.id from auth.users u
  on conflict (user_id) do nothing;

-- Each new account gets a profile at creation. Security definer so the insert succeeds
-- whichever role the auth server writes the user as; search_path pinned so `public.profiles`
-- cannot be shadowed.
create function public.create_profile_for_new_user() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  insert into public.profiles (user_id) values (new.id)
    on conflict (user_id) do nothing;
  return new;
end $$;
revoke all on function public.create_profile_for_new_user() from public, anon, authenticated;

create trigger auth_users_create_profile
  after insert on auth.users
  for each row execute function public.create_profile_for_new_user();

-- --------------------------------------------------------------------- the question bank --
create table public.discovery_templates (
  id uuid primary key default gen_random_uuid(),
  slug text not null unique check (slug ~ '^[a-z0-9][a-z0-9-]{2,60}$'),
  title text not null check (length(title) between 1 and 120),
  subtitle text check (subtitle is null or length(subtitle) <= 240),
  template_version integer not null default 1 check (template_version >= 1),
  estimated_minutes integer check (estimated_minutes is null or estimated_minutes between 1 and 600),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
comment on table public.discovery_templates is
  'What one stakeholder is assigned: an ordered list of sections, composed through discovery_template_sections. Two founder templates share their first sections and differ in their tails.';

-- A section belongs to no template. It is a reusable block of questions, and sharing it between
-- templates through the join table below is what makes the founder questions exist exactly once.
create table public.discovery_sections (
  id uuid primary key default gen_random_uuid(),
  -- Globally unique, because a section is not namespaced by a template. Conventionally
  -- prefixed by its pack: 'founder-core-business-model', 'lead-portfolio', 'eng-integrations'.
  slug text not null unique check (slug ~ '^[a-z0-9][a-z0-9-]{2,80}$'),
  title text not null check (length(title) between 1 and 120),
  description text check (description is null or length(description) <= 600),
  -- Core sections are emphasised in the portal and are what the soft gate measures.
  is_core boolean not null default false,
  estimated_minutes integer check (estimated_minutes is null or estimated_minutes between 1 and 240),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
comment on table public.discovery_sections is
  'A reusable block of questions. Shared between templates through discovery_template_sections, which is what makes the 70 founder questions exist exactly once.';

-- The join that shares. `order` lives here rather than on the section because it is a property
-- of this section inside that template, not of the section itself.
create table public.discovery_template_sections (
  template_id uuid not null references public.discovery_templates (id) on delete cascade,
  -- restrict, not cascade: deleting a section that a template uses should fail loudly.
  section_id uuid not null references public.discovery_sections (id) on delete restrict,
  "order" integer not null check ("order" >= 0),
  created_at timestamptz not null default now(),
  primary key (template_id, section_id),
  unique (template_id, "order")
);
comment on table public.discovery_template_sections is
  'Which sections a template contains, in order. Its primary key is what a response and an assignment reference to prove a section really is in the template in question.';

create table public.discovery_questions (
  id uuid primary key default gen_random_uuid(),
  section_id uuid not null references public.discovery_sections (id) on delete cascade,
  -- Globally unique and stable across wording changes. Conventionally
  -- 'founder.core.primary_customer', 'eng.integrations.plaid'.
  question_key text not null unique check (question_key ~ '^[a-z0-9][a-z0-9_.-]{1,80}$'),
  question_version integer not null default 1 check (question_version >= 1),
  question_text text not null check (length(question_text) between 1 and 1000),
  helper_text text check (helper_text is null or length(helper_text) <= 600),
  question_type public.discovery_question_type not null,
  -- [{"value": "...", "label": "..."}]. Empty for types that take no options.
  options jsonb not null default '[]'::jsonb,
  -- The synthetic case a scenario question asks about. Made-up applicant, made-up figures,
  -- committed in the fixture, identical for everyone who sees it. Never real financial data.
  scenario_brief jsonb,
  required_core boolean not null default false,
  priority integer not null default 50 check (priority between 0 and 100),
  sensitivity public.discovery_sensitivity not null default 'low',
  "order" integer not null check ("order" >= 0),
  -- Groups questions meant to be compared ACROSS templates. Two stakeholders answering a SHARED
  -- question do not need it: they answer the same row and are compared by question_id.
  compare_key text check (compare_key is null or compare_key ~ '^[a-z0-9][a-z0-9_.-]{1,80}$'),
  -- Where this answer would land if it ever became configuration. Null until it does.
  data_key text check (data_key is null or data_key ~ '^[a-z0-9][a-z0-9_.-]{1,120}$'),
  -- {"question_key": "...", "equals": <json>} or {"question_key": "...", "in": [...]}.
  -- Evaluated server-side. May reference only a question in the SAME section; the seed loader
  -- enforces that, because a shared section must be self-contained to be safely shared.
  branch_condition jsonb,
  -- How a question leaves the bank without taking anyone's answer with it.
  retired_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),

  unique (section_id, "order"),
  -- The composite key a response references, proving the question is in that section.
  unique (id, section_id),

  -- A question that cannot be skipped must not be one we said was sensitive enough to skip.
  constraint high_sensitivity_is_never_core
    check (not (sensitivity = 'high' and required_core)),

  -- Types that offer a choice need options; the rest must not carry any. `scenario_decision`
  -- belongs in the first group: its options are the reason codes an answer may cite, which is
  -- what discovery_scenario_responses.reason_codes is validated against by the API.
  constraint options_match_type check (
    case
      when question_type in ('single_select', 'multi_select', 'ranking', 'scenario_decision')
        then jsonb_typeof(options) = 'array' and jsonb_array_length(options) > 0
      else options = '[]'::jsonb
    end
  ),

  -- A scenario question carries its case. Nothing else may.
  constraint scenario_brief_matches_type check (
    (question_type = 'scenario_decision' and scenario_brief is not null)
    or (question_type <> 'scenario_decision' and scenario_brief is null)
  )
);
create index discovery_questions_by_section on public.discovery_questions (section_id, "order");
create index discovery_questions_compare on public.discovery_questions (compare_key)
  where compare_key is not null;
create index discovery_questions_live on public.discovery_questions (section_id)
  where retired_at is null;
comment on column public.discovery_questions.question_version is
  'Copied onto every response at write time. Editing the wording increments this in place; existing responses keep the version they answered.';

-- ---------------------------------------------------------------------------- assignments --
-- The scope object. One per person per template. Everything a stakeholder can read or write
-- hangs off one of these, resolved in Python through the caller's own user id.
create table public.discovery_assignments (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  -- restrict: deleting a template somebody has answered would orphan their work. Version it.
  template_id uuid not null references public.discovery_templates (id) on delete restrict,
  -- Overrides the template title for this person when set. Usually null.
  title_override text check (title_override is null or length(title_override) between 1 and 120),
  -- Nullable, and when set it must be a section this template actually contains; see below.
  current_section_id uuid,
  -- A cache of derived truth, written by the API after each save. The authority is the
  -- computation in progress.py.
  status text not null default 'not_started'
    check (status in ('not_started', 'in_progress', 'core_complete', 'complete')),
  started_at timestamptz,
  core_completed_at timestamptz,
  completed_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),

  -- Where the reader left off, proved against the template's own section list rather than
  -- against sections in general, so an assignment cannot point outside its template. Named
  -- explicitly so a check can assert on it; the name is the one Postgres would generate.
  -- MATCH SIMPLE means the pair is not checked while current_section_id is null, which is
  -- what keeps "not started yet" a valid state. On delete, only the pointer is nulled;
  -- template_id is not null and stays.
  constraint discovery_assignments_template_id_current_section_id_fkey
    foreign key (template_id, current_section_id)
    references public.discovery_template_sections (template_id, section_id)
    on delete set null (current_section_id),

  unique (user_id, template_id),
  -- The two composite keys every child row binds against.
  unique (id, user_id),
  unique (id, template_id)
);
create index discovery_assignments_by_user on public.discovery_assignments (user_id);
comment on table public.discovery_assignments is
  'One stakeholder, one template, their progress. The scope every stakeholder route resolves through the caller''s own user id; an admin reaches it by stakeholder id through a separate resolver.';

-- ------------------------------------------------------------------------------- answers --
-- The current answer, one row per (assignment, question), updated in place. History is kept by
-- the trigger below, not by application code, and the departure from the purely append-only
-- category_overrides pattern is deliberate: a questionnaire read is "my current answer to a
-- hundred questions", and pure append-only makes that a hundred ordered-limit-one reads.
create table public.discovery_responses (
  id uuid primary key default gen_random_uuid(),
  assignment_id uuid not null,
  -- Denormalised so ownership is a single-column fact. The composite key below forces it to
  -- equal the assignment's own user, so it cannot be spoofed even by a service-role write.
  user_id uuid not null,
  template_id uuid not null,
  section_id uuid not null,
  question_id uuid not null,

  -- Copied from the question at write time, never joined at read time. An answer records the
  -- wording and the type it was an answer to, so editing a question cannot retrospectively
  -- change what somebody meant.
  question_version integer not null check (question_version >= 1),
  question_type public.discovery_question_type not null,

  status text not null check (status in ('answered', 'skipped')),
  -- Canonical JSON per type; see the constraint below. Null for a skip and for
  -- scenario_decision, whose detail lives in its own table. A currency answer is a JSON number
  -- holding signed integer minor units, as transactions.amount_minor does.
  answer jsonb,
  -- The stakeholder's own note, distinct from any note inside a scenario answer.
  note text check (note is null or length(note) <= 2000),

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),

  -- Four composite keys, which together prove ownership and membership structurally:
  --   (assignment_id, user_id)      U is A's owner
  --   (assignment_id, template_id)  T is A's template
  --   (template_id, section_id)     S is in T
  --   (question_id, section_id)     Q is in S
  -- So Q is in a section of the assignment's template, and the answering user is the
  -- assignment's owner. Neither a guessed id nor a service-role bug can file one person's
  -- answer under another's assignment, or answer a question from a template nobody assigned.
  foreign key (assignment_id, user_id)
    references public.discovery_assignments (id, user_id) on delete cascade,
  foreign key (assignment_id, template_id)
    references public.discovery_assignments (id, template_id) on delete cascade,
  foreign key (template_id, section_id)
    references public.discovery_template_sections (template_id, section_id) on delete cascade,
  foreign key (question_id, section_id)
    references public.discovery_questions (id, section_id) on delete cascade,

  unique (assignment_id, question_id),

  constraint response_answer_matches_type check (
    case
      when status = 'skipped' then answer is null
      when question_type = 'scenario_decision' then answer is null
      when question_type = 'boolean' then jsonb_typeof(answer) = 'boolean'
      when question_type in ('number', 'currency', 'percentage', 'scale')
        then jsonb_typeof(answer) = 'number'
      when question_type in ('short_text', 'long_text', 'single_select')
        then jsonb_typeof(answer) = 'string'
      when question_type in ('multi_select', 'ranking')
        then jsonb_typeof(answer) = 'array'
      else false
    end
  )
);
create index discovery_responses_by_assignment on public.discovery_responses (assignment_id);
create index discovery_responses_by_user on public.discovery_responses (user_id);
create index discovery_responses_by_question on public.discovery_responses (question_id);
comment on table public.discovery_responses is
  'The current answer per (assignment, question), updated in place. Every prior value is preserved in discovery_response_history by a trigger no caller can skip.';

-- Append-only. No foreign key to discovery_responses, deliberately: the point of this table is
-- to outlive the row it describes, including a delete.
create table public.discovery_response_history (
  id uuid primary key default gen_random_uuid(),
  response_id uuid not null,
  assignment_id uuid not null,
  user_id uuid not null,
  question_id uuid not null,
  -- The version and type as they were when this superseded value was written, so an old row
  -- stays interpretable after the question is edited.
  question_version integer not null,
  question_type public.discovery_question_type not null,
  status text not null,
  answer jsonb,
  note text,
  change text not null check (change in ('updated', 'deleted')),
  -- When the superseded value was written, and when it stopped being current.
  valid_from timestamptz not null,
  valid_to timestamptz not null default now(),
  created_at timestamptz not null default now()
);
create index discovery_response_history_by_response
  on public.discovery_response_history (response_id, valid_to desc);
create index discovery_response_history_by_user
  on public.discovery_response_history (user_id);
comment on table public.discovery_response_history is
  'Every superseded answer, written by trigger. An answer written once and never changed produces no row here: there is no change to understand.';

create function public.record_discovery_response_history() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  insert into public.discovery_response_history (
    response_id, assignment_id, user_id, question_id, question_version, question_type,
    status, answer, note, change, valid_from, valid_to
  ) values (
    old.id, old.assignment_id, old.user_id, old.question_id, old.question_version,
    old.question_type, old.status, old.answer, old.note,
    case when tg_op = 'DELETE' then 'deleted' else 'updated' end,
    old.updated_at, now()
  );
  if tg_op = 'DELETE' then return old; end if;
  new.updated_at := now();
  return new;
end $$;
revoke all on function public.record_discovery_response_history() from public, anon, authenticated;

create trigger discovery_responses_history
  before update or delete on public.discovery_responses
  for each row execute function public.record_discovery_response_history();

-- Typed columns, not JSON, because the admin surface has to answer "where do two people
-- disagree" with a query over columns rather than a scan over blobs.
create table public.discovery_scenario_responses (
  response_id uuid primary key
    references public.discovery_responses (id) on delete cascade,
  assignment_id uuid not null,
  user_id uuid not null,
  question_id uuid not null,

  decision public.underwriting_decision not null,
  confidence smallint not null check (confidence between 1 and 5),
  -- Validated against the question's own options by the API before the write.
  reason_codes text[] not null default '{}',
  -- Signed integer minor units, like every other money value in this schema. Optional.
  maximum_exposure_minor bigint,
  additional_information_needed text check (
    additional_information_needed is null or length(additional_information_needed) <= 2000),
  what_would_change_my_decision text check (
    what_would_change_my_decision is null or length(what_would_change_my_decision) <= 2000),
  notes text check (notes is null or length(notes) <= 2000),

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),

  foreign key (assignment_id, user_id)
    references public.discovery_assignments (id, user_id) on delete cascade,
  constraint reason_codes_bounded check (cardinality(reason_codes) <= 20)
);
create index discovery_scenario_by_question on public.discovery_scenario_responses (question_id);
create index discovery_scenario_by_user on public.discovery_scenario_responses (user_id);
comment on column public.discovery_scenario_responses.maximum_exposure_minor is
  'Signed integer minor units. A currency question that stored dollars as a float would be the one place the money rule was broken.';

-- Append-only, every typed column. A change of mind on an underwriting case is the single most
-- interesting event this feature can record, and it must be impossible to lose.
create table public.discovery_scenario_response_history (
  id uuid primary key default gen_random_uuid(),
  response_id uuid not null,
  assignment_id uuid not null,
  user_id uuid not null,
  question_id uuid not null,

  decision public.underwriting_decision not null,
  confidence smallint not null,
  reason_codes text[] not null default '{}',
  maximum_exposure_minor bigint,
  additional_information_needed text,
  what_would_change_my_decision text,
  notes text,

  change text not null check (change in ('updated', 'deleted')),
  valid_from timestamptz not null,
  valid_to timestamptz not null default now(),
  created_at timestamptz not null default now()
);
create index discovery_scenario_history_by_response
  on public.discovery_scenario_response_history (response_id, valid_to desc);
create index discovery_scenario_history_by_question
  on public.discovery_scenario_response_history (question_id);

create function public.record_discovery_scenario_history() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  insert into public.discovery_scenario_response_history (
    response_id, assignment_id, user_id, question_id,
    decision, confidence, reason_codes, maximum_exposure_minor,
    additional_information_needed, what_would_change_my_decision, notes,
    change, valid_from, valid_to
  ) values (
    old.response_id, old.assignment_id, old.user_id, old.question_id,
    old.decision, old.confidence, old.reason_codes, old.maximum_exposure_minor,
    old.additional_information_needed, old.what_would_change_my_decision, old.notes,
    case when tg_op = 'DELETE' then 'deleted' else 'updated' end,
    old.updated_at, now()
  );
  if tg_op = 'DELETE' then return old; end if;
  new.updated_at := now();
  return new;
end $$;
revoke all on function public.record_discovery_scenario_history() from public, anon, authenticated;

-- A cascaded delete from discovery_responses fires this too, so a deleted scenario answer is
-- preserved alongside its parent.
create trigger discovery_scenario_responses_history
  before update or delete on public.discovery_scenario_responses
  for each row execute function public.record_discovery_scenario_history();

-- -------------------------------------------------------------------------------- sessions --
-- Visits, so the published "estimated completion time" can be checked against measured elapsed
-- time rather than guessed forever. Carries no answer content.
create table public.discovery_sessions (
  id uuid primary key default gen_random_uuid(),
  assignment_id uuid not null,
  user_id uuid not null,
  started_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now(),
  ended_at timestamptz,
  saves integer not null default 0 check (saves >= 0),
  foreign key (assignment_id, user_id)
    references public.discovery_assignments (id, user_id) on delete cascade
);
create index discovery_sessions_by_assignment
  on public.discovery_sessions (assignment_id, started_at desc);

-- ------------------------------------------------------------------- privileges and RLS --
-- Service role only. The project's default privileges hand anon and authenticated TRUNCATE,
-- REFERENCES and TRIGGER on new tables, which RLS does not restrict, so the revoke is not
-- optional. No client policies follow: RLS enabled with nothing to permit denies every row to
-- every non-superuser role, which is a stronger default than a permissive policy. If a future
-- migration adds a grant by mistake, these tables still return nothing until a policy is
-- written deliberately. What stops direct access today is the absence of a grant; RLS is the
-- second lock.
revoke all on table
  public.profiles, public.discovery_templates, public.discovery_sections,
  public.discovery_template_sections, public.discovery_questions,
  public.discovery_assignments, public.discovery_responses,
  public.discovery_response_history, public.discovery_scenario_responses,
  public.discovery_scenario_response_history, public.discovery_sessions
  from anon, authenticated;

grant select, insert, update, delete on table
  public.profiles, public.discovery_templates, public.discovery_sections,
  public.discovery_template_sections, public.discovery_questions,
  public.discovery_assignments, public.discovery_responses,
  public.discovery_response_history, public.discovery_scenario_responses,
  public.discovery_scenario_response_history, public.discovery_sessions
  to service_role;

alter table public.profiles                            enable row level security;
alter table public.discovery_templates                 enable row level security;
alter table public.discovery_sections                  enable row level security;
alter table public.discovery_template_sections         enable row level security;
alter table public.discovery_questions                 enable row level security;
alter table public.discovery_assignments               enable row level security;
alter table public.discovery_responses                 enable row level security;
alter table public.discovery_response_history          enable row level security;
alter table public.discovery_scenario_responses        enable row level security;
alter table public.discovery_scenario_response_history enable row level security;
alter table public.discovery_sessions                  enable row level security;

-- No client policies. Service role only, in the style of visitors, workspaces, raw_events,
-- sync_batches, connections and transaction_sources in migration 0002.

notify pgrst, 'reload schema';
