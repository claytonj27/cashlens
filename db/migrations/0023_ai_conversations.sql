-- 0023: the conversation behind the AI CFO panel (milestone 10).
--
-- The first three capabilities each answer one question the code already knew to ask. This is
-- the one where the owner asks. Two tables, because a conversation and a turn have different
-- lifetimes: the agreed assumptions belong to the conversation and survive a turn that failed,
-- while a turn is a single bounded attempt with its own run row and its own tool trace.
--
-- What is deliberately NOT here:
--
--   * no raw model text. A turn stores the validated answer and nothing else, exactly as
--     `ai_runs` does, so a rejected answer leaves its reasons and its hash rather than itself.
--   * no free-form history the client can set. A turn's question is what the owner typed and
--     the answer is what survived validation; the server reads both back from here rather than
--     trusting anything a request carries.
--   * no memory. These rows are one conversation in one workspace. Nothing spans workspaces,
--     nothing describes the person, and nothing here adjusts a model.
--
-- `agreed_assumptions` is a validated ScenarioRequest fragment — the amount and date the owner
-- actually agreed to — carried so a follow-up can change only the delay. It is stamped with the
-- fingerprint it was agreed against: when the data moves underneath it, the panel asks again
-- rather than quietly reusing an assumption about figures that have changed.

create table public.ai_conversations (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references public.businesses (id) on delete cascade,
  workspace_id uuid not null,
  analysis_date date not null,
  started_fingerprint text not null check (length(started_fingerprint) = 64),
  -- The owner's agreed scenario, and the data it was agreed against.
  agreed_assumptions jsonb,
  assumptions_fingerprint text check (assumptions_fingerprint is null or length(assumptions_fingerprint) = 64),
  turn_count integer not null default 0 check (turn_count >= 0),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (workspace_id, business_id) references public.workspaces (id, business_id) on delete cascade,
  unique (id, business_id)
);
comment on table public.ai_conversations is
  'One conversation in one workspace. Holds the assumptions the owner agreed to and the data fingerprint they were agreed against. Not memory: nothing here spans workspaces or describes a person.';

create index ai_conversations_for_workspace
  on public.ai_conversations (workspace_id, business_id, updated_at desc);

create table public.ai_turns (
  id uuid primary key default gen_random_uuid(),
  conversation_id uuid not null,
  business_id uuid not null,
  workspace_id uuid not null,
  sequence integer not null check (sequence >= 1),
  question text not null check (length(question) between 1 and 500),
  -- The validated answer only. Never raw model text.
  answer jsonb,
  -- The options this turn offered, kept server-side so choosing one is a key into data the
  -- server wrote rather than a scenario the client can supply.
  clarify jsonb,
  scenario_applied jsonb,
  proposal_id uuid,
  -- Validated arguments and safe result summaries, one entry per executed tool call.
  tool_calls jsonb not null default '[]'::jsonb,
  run_id uuid references public.ai_runs (id) on delete set null,
  input_fingerprint text check (input_fingerprint is null or length(input_fingerprint) = 64),
  status text not null check (status in (
    'answered', 'partial', 'clarifying', 'invalid_output', 'provider_error', 'timeout',
    'deadline', 'refused'
  )),
  created_at timestamptz not null default now(),
  foreign key (conversation_id, business_id) references public.ai_conversations (id, business_id) on delete cascade,
  foreign key (workspace_id, business_id) references public.workspaces (id, business_id) on delete cascade,
  unique (conversation_id, sequence)
);
comment on table public.ai_turns is
  'One bounded attempt at answering one question: what was asked, what survived validation, which tools ran with which validated arguments, and the run row that paid for it.';

create index ai_turns_for_conversation on public.ai_turns (conversation_id, sequence);

alter table public.ai_conversations enable row level security;
alter table public.ai_turns enable row level security;
revoke all on table public.ai_conversations from anon, authenticated;
revoke all on table public.ai_turns from anon, authenticated;
grant select, insert, update, delete on table public.ai_conversations to service_role;
grant select, insert, update, delete on table public.ai_turns to service_role;

-- ------------------------------------------------------------ the capability --
alter table public.ai_runs drop constraint ai_runs_capability_check;
alter table public.ai_runs add constraint ai_runs_capability_check
  check (capability in ('brief', 'classify', 'scenario_interpret', 'scenario_explain', 'ask'));

-- ---------------------------------------------------------------- deletion --
-- Replaced only to remove the two new tables, in order, before the runs they point at.

create or replace function public.request_business_deletion(p_business_id uuid, p_user_id uuid)
returns table (
  already_requested boolean, business_name text, objects_queued integer,
  imports_ended integer, transactions_removed integer, requested_at timestamptz
)
language plpgsql security definer set search_path = public, pg_temp as $$
#variable_conflict use_column
declare
  v_business public.businesses%rowtype;
  v_members integer;
  v_queued integer := 0;
  v_ended integer := 0;
  v_removed integer := 0;
  v_import record;
begin
  select * into v_business from public.businesses b where b.id = p_business_id for update;
  if v_business.id is null or v_business.data_mode <> 'private' then
    raise exception 'not_found' using errcode = 'no_data_found';
  end if;
  if not exists (
    select 1 from public.business_memberships m
    where m.business_id = p_business_id and m.user_id = p_user_id
  ) then
    raise exception 'not_found' using errcode = 'no_data_found';
  end if;
  if v_business.created_by is distinct from p_user_id then
    raise exception 'not_the_owner' using errcode = 'insufficient_privilege';
  end if;
  select count(*) into v_members from public.business_memberships m where m.business_id = p_business_id;
  if v_members > 1 then
    raise exception 'shared_business' using errcode = 'insufficient_privilege';
  end if;

  if v_business.deletion_requested_at is not null then
    select count(*) into v_queued from public.storage_cleanup c
      where c.business_id = p_business_id and c.deleted_at is null;
    return query select true, v_business.name, v_queued, 0, 0, v_business.deletion_requested_at;
    return;
  end if;

  update public.businesses b
    set deletion_requested_at = now(), deletion_requested_by = p_user_id
    where b.id = p_business_id;

  insert into public.storage_cleanup (bucket, object_path, business_id, reason)
    select i.object_bucket, i.object_path, i.business_id, 'business_deleted'
    from public.document_imports i
    where i.business_id = p_business_id and i.object_deleted_at is null
    on conflict (object_path) do nothing;
  get diagnostics v_queued = row_count;

  for v_import in
    select i.id from public.document_imports i
    where i.business_id = p_business_id and i.status in ('awaiting_upload', 'staged', 'failed')
  loop
    perform public.end_import(v_import.id, 'business_deleted');
    v_ended := v_ended + 1;
  end loop;

  -- AI rows first, conversations and suggestions included: they hold the owner's figures,
  -- their questions and their decisions, so they must not outlive the transactions they
  -- were derived from. Turns before conversations, conversations before runs: each points
  -- at the next, and a run row is what a turn's trace hangs on.
  delete from public.ai_turns t where t.business_id = p_business_id;
  delete from public.ai_conversations c where c.business_id = p_business_id;
  delete from public.classification_suggestions s where s.business_id = p_business_id;
  delete from public.ai_runs r where r.business_id = p_business_id;
  delete from public.category_overrides o where o.business_id = p_business_id;

  delete from public.transactions t where t.business_id = p_business_id;
  get diagnostics v_removed = row_count;
  delete from public.balance_snapshots s where s.business_id = p_business_id;
  delete from public.document_imports i where i.business_id = p_business_id;
  delete from public.accounts a where a.business_id = p_business_id;
  delete from public.connections c where c.business_id = p_business_id;

  update public.businesses b set data_removed_at = now() where b.id = p_business_id;
  return query select false, v_business.name, v_queued, v_ended, v_removed, now();
end $$;

notify pgrst, 'reload schema';
