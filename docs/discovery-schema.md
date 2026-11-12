# Discovery Portal: schema

Status: **revision 2, implementation-ready.** No migration has been written or applied.

Changes from revision 1, and the two corrections made at final review, are listed in
[architecture section 9](discovery-architecture.md#9-changes-from-revision-1). The one
that touches this document is `discovery_assignments.current_section_id`, now a composite
reference; see 4.6.

Proposed migration: `db/migrations/0025_discovery.sql`, applied by `db/migrate.py`,
which checksums applied files and refuses a changed one as drift. One migration, because the
tables are useless individually and a half-applied question bank is worse than none.

Companion documents: [architecture](discovery-architecture.md),
[implementation plan](discovery-implementation-plan.md).

---

## 1. Conventions this follows

Taken from the existing 24 migrations, not invented here.

- **Service-role-only tables get RLS enabled, no grants and no policies.** Migration 0002 ends:
  *"visitors, workspaces, connections, sync_batches, raw_events and transaction_sources
  intentionally have no client policies: service role only."* Every table below is that kind of
  table. See section 6.
- The project's default privileges hand `anon` and `authenticated` TRUNCATE, REFERENCES and
  TRIGGER on new tables, which RLS does not restrict, so the explicit revoke is not optional.
- Money is **signed integer minor units**, never a float.
- Composite foreign keys so a guessed id cannot cross a boundary, as `classification_suggestions`
  does with `(workspace_id, business_id)`.
- Check constraints encode promises the application makes, so the promise cannot drift.

---

## 2. Entity relationships

```
auth.users ─1:1─ profiles (role)
     │
     └─1:n─ discovery_assignments ──n:1── discovery_templates
                    │                            │
                    │                     discovery_template_sections   (the join that shares)
                    │                            │
                    │                     discovery_sections
                    │                            │
                    │                     discovery_questions
                    │                            │
                    ├─1:n─ discovery_responses ──┘
                    │            ├─1:1─ discovery_scenario_responses
                    │            │            └─1:n─ discovery_scenario_response_history  (trigger)
                    │            └─1:n─ discovery_response_history                        (trigger)
                    │
                    └─1:n─ discovery_sessions
```

Eleven tables: `profiles` plus ten `discovery_*`.

**The join table is the design.** A section belongs to no template; a template is an ordered list
of sections. That is what lets the 70 founder questions exist as one set of rows referenced by
both founder templates, so Lead's and Partner's answers to a shared question carry the same
`question_id`.

```
founder-lead      = [ fc-1 … fc-N , jo-1 … jo-M ]
founder-partner        = [ fc-1 … fc-N , al-1 … al-M ]
engineering-engineer  = [ en-1 … en-K ]
                        └── the same section rows ──┘
```

---

## 3. Types

```sql
create type public.app_role as enum ('admin', 'stakeholder', 'member');

create type public.discovery_question_type as enum (
  'short_text', 'long_text', 'boolean', 'single_select', 'multi_select',
  'number', 'currency', 'percentage', 'scale', 'ranking', 'scenario_decision'
);

create type public.discovery_sensitivity as enum ('low', 'medium', 'high');

create type public.underwriting_decision as enum ('approve', 'deny', 'manual_review');
```

`member` is the default for every account that is neither the author nor a named stakeholder,
which is every account that signs up on the public site. It grants nothing this feature exposes.

---

## 4. Tables

### 4.1 `profiles`

One row per auth user. The only new authorization surface.

```sql
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
```

`first_name` exists so the portal can say *Welcome, Lead* without the API reading an email
address into a page. Set by the seed, never by self-service.

**Backfill.** The migration inserts a `member` profile for every existing auth user, and a
`security definer` trigger on `auth.users` inserts one for each new user, so a signed-in account
never has a missing profile. Python still treats an absent profile as `member`, because a code
path that assumed presence would fail open the first time the trigger did not fire.

### 4.2 `discovery_templates`

```sql
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
```

Three templates seed initially:

| slug | title | assigned to |
|---|---|---|
| `founder-lead` | Business + Underwriting Discovery | Lead |
| `founder-partner` | Business + Underwriting Discovery | Partner |
| `engineering-engineer` | Engineering Discovery | Engineer |

The two founder templates carry the same `title` and share their first N sections. They are
separate templates because their **tails** differ, not their heads.

### 4.3 `discovery_sections`

A section belongs to no template. It is a reusable block of questions.

```sql
create table public.discovery_sections (
  id uuid primary key default gen_random_uuid(),
  -- Globally unique, because a section is no longer namespaced by a template. Conventionally
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
```

No `template_id` and no `"order"`: both moved to the join table, because both are properties of
*this section inside that template*, not of the section itself.

### 4.4 `discovery_template_sections`

```sql
create table public.discovery_template_sections (
  template_id uuid not null references public.discovery_templates (id) on delete cascade,
  -- restrict, not cascade: deleting a section that a template uses should fail loudly.
  section_id uuid not null references public.discovery_sections (id) on delete restrict,
  "order" integer not null check ("order" >= 0),
  created_at timestamptz not null default now(),
  primary key (template_id, section_id),
  unique (template_id, "order")
);
```

The primary key `(template_id, section_id)` is what `discovery_responses` references to prove a
section really is in the assignment's template.

### 4.5 `discovery_questions`

```sql
create table public.discovery_questions (
  id uuid primary key default gen_random_uuid(),
  section_id uuid not null references public.discovery_sections (id) on delete cascade,
  -- Globally unique and stable across wording changes. Renamed from revision 1's template_key,
  -- which no longer makes sense: a question belongs to a section, and a section to many
  -- templates. Conventionally 'founder.core.primary_customer', 'eng.integrations.plaid'.
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
  -- Groups questions meant to be compared ACROSS templates, for example a Engineer question that
  -- parallels a founder one. Two stakeholders answering a SHARED question do not need it: they
  -- answer the same row and are compared by question_id.
  compare_key text check (compare_key is null or compare_key ~ '^[a-z0-9][a-z0-9_.-]{1,80}$'),
  -- Where this answer would land if it ever became configuration. Null until it does.
  data_key text check (data_key is null or data_key ~ '^[a-z0-9][a-z0-9_.-]{1,120}$'),
  -- {"question_key": "...", "equals": <json>} or {"question_key": "...", "in": [...]}.
  -- Evaluated server-side. May reference only a question in the SAME section; see section 5.
  branch_condition jsonb,
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
  -- what `discovery_scenario_responses.reason_codes` is validated against.
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
```

`retired_at` is how a question leaves the bank without taking anyone's answer with it; see
section 9.

### 4.6 `discovery_assignments`

The scope object. One per person.

```sql
create table public.discovery_assignments (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  template_id uuid not null references public.discovery_templates (id) on delete restrict,
  -- Overrides the template title for this person when set. Usually null.
  title_override text check (title_override is null or length(title_override) between 1 and 120),
  -- Nullable, and when set it must be a section this template actually contains; see below.
  current_section_id uuid,
  status text not null default 'not_started'
    check (status in ('not_started', 'in_progress', 'core_complete', 'complete')),
  started_at timestamptz,
  core_completed_at timestamptz,
  completed_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),

  -- Where the reader left off, proved against the template's own section list rather than
  -- against sections in general. MATCH SIMPLE means the pair is not checked while
  -- current_section_id is null, which is what keeps "not started yet" a valid state.
  foreign key (template_id, current_section_id)
    references public.discovery_template_sections (template_id, section_id)
    on delete set null (current_section_id),

  unique (user_id, template_id),
  -- The two composite keys every child row binds against.
  unique (id, user_id),
  unique (id, template_id)
);
create index discovery_assignments_by_user on public.discovery_assignments (user_id);
```

`status` is a cache of derived truth, written by the API after each save. The authority is the
computation in `progress.py`; see section 7.

`on delete restrict` on `template_id`: deleting a template somebody has answered would orphan
their work. Version the template instead.

**`current_section_id` is bound to the template compositely**, against
`discovery_template_sections (template_id, section_id)`, which is that table's primary key. So an
assignment cannot point at a section its own template does not contain. Revision 2's first draft
referenced `discovery_sections (id)` directly and admitted the invalid state, reasoning that the
API would never write one. That is a matter of care rather than of structure, which is the wrong
side of the line everywhere else in this schema, so it is now a foreign key.

Three consequences worth stating:

- **Null stays valid.** A composite foreign key defaults to `MATCH SIMPLE`, which is satisfied
  without a lookup whenever any referenced column is null. A brand new assignment has no current
  section and needs none.
- **Removing a section from a template resets the pointer.** `on delete set null
  (current_section_id)` nulls that column alone, leaving `template_id` (which is `not null`)
  untouched. Resume then lands on the overview, which is the honest answer once the section the
  reader was in is no longer part of their template.
- **It does not stop a section being deleted outright**, because
  `discovery_template_sections.section_id` is already `on delete restrict`: a section in use by
  any template cannot be dropped at all.

### 4.7 `discovery_responses`

The current answer. One row per (assignment, question).

```sql
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
  -- scenario_decision, whose detail lives in its own table.
  answer jsonb,
  -- The stakeholder's own note, distinct from any note inside a scenario answer.
  note text check (note is null or length(note) <= 2000),

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),

  -- Four composite keys, which together prove ownership and membership structurally.
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
```

**What the four composite keys prove together.** A row says: I belong to assignment A, user U,
template T, section S, question Q.

| Key | Proves |
|---|---|
| `(assignment_id, user_id)` → `assignments (id, user_id)` | U is A's owner |
| `(assignment_id, template_id)` → `assignments (id, template_id)` | T is A's template |
| `(template_id, section_id)` → `template_sections (template_id, section_id)` | S is in T |
| `(question_id, section_id)` → `questions (id, section_id)` | Q is in S |

So Q is in a section of the assignment's template, and the response's user is the assignment's
owner. Neither a guessed id nor a service-role bug can file Partner's answer under Lead's
assignment, or answer a question from a template nobody assigned.

**Currency is minor units.** A `currency` answer is a JSON number holding signed integer minor
units, as `transactions.amount_minor` does. The API rejects a non-integer rather than rounding.

### 4.8 `discovery_response_history`

Append-only. Written by a trigger, not by application code.

```sql
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
```

No foreign key to `discovery_responses`, deliberately: the point of this table is to outlive the
row it describes, including a delete.

```sql
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

create trigger discovery_responses_history
  before update or delete on public.discovery_responses
  for each row execute function public.record_discovery_response_history();
```

An answer written once and never changed produces no history row, which is correct: there is no
change to understand. The first edit produces one row holding the original value.

### 4.9 `discovery_scenario_responses`

```sql
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
```

Typed columns, not JSON, because the admin surface has to answer "where do Lead and Partner
disagree" with a query rather than a scan. See section 8.

### 4.10 `discovery_scenario_response_history`

**Required, not optional.** Revision 1 left scenario revisions uncovered; this closes it.

```sql
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

create trigger discovery_scenario_responses_history
  before update or delete on public.discovery_scenario_responses
  for each row execute function public.record_discovery_scenario_history();
```

Every typed field is preserved: `decision`, `confidence`, `reason_codes`,
`maximum_exposure_minor`, `additional_information_needed`, `what_would_change_my_decision` and
`notes`. A change of mind on an underwriting case is the single most interesting event this
feature can record, and it is now impossible to lose.

Note the ordering interaction with 4.8: `discovery_responses` cascades to
`discovery_scenario_responses`, and a cascaded delete fires this `before delete` trigger, so a
deleted scenario answer is preserved too.

### 4.11 `discovery_sessions`

```sql
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
```

Records visits so the published estimate can be checked against measured elapsed time. Carries
no answer content.

---

## 5. Branch conditions may reference only the same section

```json
{"question_key": "founder.core.uses_plaid", "equals": true}
```

Evaluated **server-side** when the API builds a section payload. A hidden question is simply
absent from the response, which is what makes it genuinely hidden rather than merely
unrendered. (Section 6 closes the other route to seeing it.)

**The referenced question must be in the same section.** A shared section can sit at different
positions in different templates, so a condition pointing at another section would be answerable
in one template and unanswerable in another, and the same question would behave differently for
Lead and Partner. Same-section-only makes a shared section self-contained, which is exactly what
makes it safe to share. The seed loader enforces it, and also requires the referenced question
to come **earlier in the section's order**, so a forward reference cannot create a question that
can never be shown.

Two forms only, `equals` and `in`. Anything more expressive is a new decision record, not an
afternoon; see risk R5.

---

## 6. Grants and RLS: service role only

This is the revision the review asked for. Revision 1 proposed `grant select` to `authenticated`
plus narrow policies. That is removed.

```sql
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
```

### 6.1 Why no policies at all

Four reasons, in the order they matter:

1. **Nothing needs direct access.** The browser makes no data reads against Supabase anywhere in
   this application. There is no runtime dependency to preserve.
2. **A read grant would leak hidden questions.** Branch conditions are evaluated server-side; a
   stakeholder who could `select * from discovery_questions` would see every question in their
   template, including ones deliberately withheld. That leaks the bank's shape even though it
   leaks nobody's answers.
3. **Two enforcement surfaces for one feature is two places to get right.**
4. **RLS with no policies is a stronger default than a permissive policy.** If a future migration
   adds a `grant select` by mistake, these tables still return zero rows until somebody writes a
   policy deliberately. A permissive policy would have started returning data on the same
   mistake.

### 6.2 What this means for the security claim

| Path | Stopped by |
|---|---|
| `/api/discovery/...` with a valid token | **Python.** `resolve_own_assignment()`. RLS is bypassed here because the API holds the service role. |
| PostgREST directly, publishable key + any JWT | **Grants.** `authenticated` has no privilege on any of these tables. |
| PostgREST directly, publishable key, no JWT | **Grants.** `anon` has no privilege either. |
| A future accidental `grant select` | **RLS.** Enabled with no policies denies every row. |
| Anyone holding the service role key | Nothing. Unchanged from today, out of scope. |

RLS is therefore **dormant, not active**, on every path that exists today. Stated plainly so
nobody reads appendix A and concludes otherwise.

### 6.3 No role helper

Revision 1 proposed `public.app_role(p_user_id uuid default auth.uid())`. It is not in this
migration. With no policies there is no SQL caller for it, and a `security definer` function
that accepts an arbitrary user id is exactly the sort of thing that quietly becomes a lookup
service. Python reads `profiles` with the service role, which is the normal server-side path.

The zero-argument form, for if direct access is ever enabled, is in appendix A.

---

## 7. Derived values, and where they are computed

Nothing about progress is stored as the authority.

```
sections(assignment)         = template_sections for the assignment's template, ordered

questions(section)           = questions in that section where retired_at is null, ordered

visible(question, answers)   = branch_condition is null
                               or evaluate(branch_condition, answers in the same section)

answered(question, answers)  = a response row exists with status = 'answered'
                               or status = 'skipped' and not question.required_core

core_complete(assignment)    = every visible question with required_core = true is answered

progress_percent             = answered visible questions / visible questions
```

A skip counts as complete only for a question that was not core, which is guaranteed meaningful
by `high_sensitivity_is_never_core`: a high-sensitivity question is always skippable and never
blocks the gate.

`discovery_assignments.status`, `core_completed_at` and `completed_at` are a cache for cheap
listing. The authority is `progress.py`, pure functions over plain data, so a mismatch is a
one-line assertion in a test rather than a debugging session.

---

## 8. Queries the admin surface needs

Written here so the schema can be judged against them rather than against a description.

**Progress for all three:**

```sql
select p.first_name, coalesce(a.title_override, t.title) as title, a.status,
       count(*) filter (where q.required_core) as core_total,
       count(*) filter (where q.required_core and r.id is not null) as core_done,
       count(r.id) as answered_total
from public.discovery_assignments a
join public.profiles p on p.user_id = a.user_id
join public.discovery_templates t on t.id = a.template_id
join public.discovery_template_sections ts on ts.template_id = a.template_id
join public.discovery_questions q on q.section_id = ts.section_id and q.retired_at is null
left join public.discovery_responses r
       on r.assignment_id = a.id and r.question_id = q.id and r.status = 'answered'
group by p.first_name, title, a.status;
```

**Lead versus Partner, on the 70 shared questions.** This is the query the schema change bought:
grouping is on `question_id`, a primary key, not on a text label.

```sql
select q.question_key, q.question_text,
       jsonb_object_agg(p.first_name, r.answer) as answers
from public.discovery_responses r
join public.discovery_questions q on q.id = r.question_id
join public.profiles p on p.user_id = r.user_id
where r.status = 'answered'
group by q.id, q.question_key, q.question_text
having count(distinct r.user_id) > 1;
```

**Comparison across templates** (a Engineer question that parallels a founder one) still uses
`compare_key`, which is what it is now for:

```sql
... group by q.compare_key having count(distinct r.user_id) > 1;
```

**Scenario disagreements:**

```sql
select q.question_key, q.question_text,
       array_agg(p.first_name || ': ' || s.decision || ' (' || s.confidence || ')'
                 order by p.first_name) as positions
from public.discovery_scenario_responses s
join public.discovery_questions q on q.id = s.question_id
join public.profiles p on p.user_id = s.user_id
group by q.id, q.question_key, q.question_text
having count(distinct s.decision) > 1;
```

**Changed minds**, which is new in revision 2 and only possible because of 4.10:

```sql
select p.first_name, q.question_key,
       h.decision as was, h.confidence as was_confidence,
       s.decision as now, s.confidence as now_confidence, h.valid_to as changed_at
from public.discovery_scenario_response_history h
join public.discovery_scenario_responses s on s.response_id = h.response_id
join public.discovery_questions q on q.id = h.question_id
join public.profiles p on p.user_id = h.user_id
where h.decision <> s.decision
order by h.valid_to desc;
```

**Unanswered critical questions:**

```sql
select p.first_name, q.question_key, q.question_text, q.priority
from public.discovery_assignments a
join public.profiles p on p.user_id = a.user_id
join public.discovery_template_sections ts on ts.template_id = a.template_id
join public.discovery_questions q on q.section_id = ts.section_id and q.retired_at is null
left join public.discovery_responses r
       on r.assignment_id = a.id and r.question_id = q.id and r.status = 'answered'
where q.required_core and r.id is null
order by q.priority desc, p.first_name;
```

**Underwriting rule candidates**: the disagreement query with
`having count(distinct s.decision) = 1 and min(s.confidence) >= 4`.

**Engineering requirements from Engineer**: answers on his template where `data_key is not null`.

Every one is a bounded query over indexed columns. None scans a JSON blob, which is why 4.9 uses
typed columns.

---

## 9. Seed data shape

Two kinds of fixture, because sections are now shared:

```
fixtures/discovery/sections/founder-core-business-model.json   one section and its questions
fixtures/discovery/sections/founder-core-underwriting.json
fixtures/discovery/sections/lead-portfolio.json
fixtures/discovery/sections/partner-operations.json
fixtures/discovery/sections/eng-integrations.json
        ...
fixtures/discovery/templates.json                               ordered section slugs per template
```

A section file:

```json
{
  "slug": "founder-core-underwriting",
  "title": "How you decide",
  "description": "The shortest path to a shared picture of underwriting.",
  "is_core": true,
  "estimated_minutes": 15,
  "questions": [
    {
      "question_key": "founder.core.primary_customer",
      "question_text": "Who is the customer you underwrite for most often?",
      "helper_text": "A sentence is plenty.",
      "question_type": "long_text",
      "required_core": true,
      "priority": 90,
      "sensitivity": "low",
      "order": 0
    },
    {
      "question_key": "founder.core.decline_rate",
      "question_text": "Roughly what share of applications do you decline today?",
      "question_type": "percentage",
      "required_core": false,
      "priority": 70,
      "sensitivity": "high",
      "order": 1,
      "data_key": "underwriting.baseline_decline_rate"
    },
    {
      "question_key": "founder.core.scenario_thin_file",
      "question_text": "Would you approve this applicant?",
      "question_type": "scenario_decision",
      "required_core": true,
      "priority": 95,
      "sensitivity": "low",
      "order": 2,
      "options": [
        {"value": "thin_file", "label": "Limited credit history"},
        {"value": "income_unverified", "label": "Income not verified"},
        {"value": "prior_late", "label": "Prior late payments"}
      ],
      "scenario_brief": {
        "applicant": "Sole trader, two years in business, synthetic",
        "monthly_revenue_minor": 1840000,
        "requested_limit_minor": 500000,
        "months_of_history": 8,
        "notes": "Made-up case. No real applicant, no real financial data."
      }
    }
  ]
}
```

`templates.json`:

```json
[
  {
    "slug": "founder-lead",
    "title": "Business + Underwriting Discovery",
    "subtitle": "How the customer decides, in your words.",
    "estimated_minutes": 75,
    "sections": ["founder-core-business-model", "founder-core-underwriting", "lead-portfolio"]
  },
  {
    "slug": "founder-partner",
    "title": "Business + Underwriting Discovery",
    "subtitle": "How the customer decides, in your words.",
    "estimated_minutes": 75,
    "sections": ["founder-core-business-model", "founder-core-underwriting", "partner-operations"]
  },
  {
    "slug": "engineering-engineer",
    "title": "Engineering Discovery",
    "estimated_minutes": 80,
    "sections": ["eng-integrations", "eng-data-model", "eng-operations"]
  }
]
```

The shared founder sections appear in both founder templates by slug. They are inserted once.
`"order"` in `discovery_template_sections` comes from the array position, so reordering a
template is a diff in one file.

**Re-seed rules, enforced by the loader rather than by care:**

- A question whose `question_key` and text are unchanged is left alone.
- Changed text increments `question_version` in place. Existing responses keep the version they
  were written against.
- A new `question_key` is inserted.
- A question that has disappeared from the fixture is **not deleted** if any response references
  it: it gets `retired_at` set and stops being served. Deleting would cascade away an answer.
- A section that has disappeared from a template's list is removed from
  `discovery_template_sections` only if no response references its questions through that
  template.

**Answers never appear in a fixture, in any scenario.** Neither do real applicant details: every
`scenario_brief` is synthetic, which is what makes it committable and identical for everyone who
sees it.

---

## 10. Migration checklist

```
0025_discovery.sql
  1. types: app_role, discovery_question_type, discovery_sensitivity, underwriting_decision
  2. profiles + backfill from auth.users + security definer trigger for new users
  3. discovery_templates, discovery_sections, discovery_template_sections
  4. discovery_questions
  5. discovery_assignments          -- must follow 3: its current_section_id foreign key
                                    -- references discovery_template_sections
  6. discovery_responses
  7. discovery_response_history + record_discovery_response_history() + trigger
  8. discovery_scenario_responses
  9. discovery_scenario_response_history + record_discovery_scenario_history() + trigger
 10. discovery_sessions
 11. revokes, grants to service_role only, enable RLS on all eleven, NO policies
 12. notify pgrst, 'reload schema';
```

The final `notify` matters: PostgREST caches its schema. Every migration from `0011` onward that
adds a column or table ends with it; `0001` to `0010`, including `0002` and `0006`, predate the
convention and do not carry it.

**No existing table is altered. No existing function is replaced.** The migration is additive, so
rolling it back is dropping the new objects, and nothing already in production depends on them.

---

## Appendix A: policies if direct access is ever required

**Not applied in 0025.** Kept so the reasoning is not lost and the bar for enabling it is
explicit. Adding any of this means accepting a second authorization surface, and the leak in
section 6.1 point 2, so it wants its own decision record.

The zero-argument helper, the only form that should ever ship:

```sql
create function public.current_app_role() returns public.app_role
language sql stable security definer set search_path = public as $$
  select coalesce(
    (select p.role from public.profiles p where p.user_id = auth.uid()),
    'member'::public.app_role
  );
$$;
revoke all on function public.current_app_role() from public;
grant execute on function public.current_app_role() to authenticated, service_role;
```

No parameter, so there is no way to ask about anybody else. Defaults to `member` on a missing
row, because a helper returning null makes both `= 'admin'` and `<> 'admin'` null, which is how
a security helper fails open.

Then, and only for the tables a proven requirement names:

```sql
grant select on public.discovery_responses to authenticated;

create policy responses_own_read on public.discovery_responses
  for select to authenticated using (user_id = auth.uid());
create policy responses_admin_read on public.discovery_responses
  for select to authenticated using (public.current_app_role() = 'admin');
```

Three rules if this is ever done:

1. **Never grant on `discovery_questions`** without solving the hidden-question leak first
   (section 6.1 point 2).
2. **Never add an insert, update or delete policy.** Writes stay service-role only, which is how
   every other table in this database works.
3. **Add the matching assertions to `db/checks/discovery_rls_check.py` in the same change**, so
   the policy is proved rather than assumed.
