-- 0018: the AI layer's storage (milestone 10, stage 1 foundation + the brief).
--
-- Three tables and three functions, all of them service-role only. What they exist for:
--
-- * `ai_runs` records one generation attempt. The fact sheet the model was given and the
--   validated output are both stored, because the reviewer view has to show what was sent as
--   well as what came back; raw model text never is. A run is business-scoped with a nullable
--   workspace, so a future extraction run can precede a workspace. The composite key
--   (workspace_id, business_id) references workspaces (id, business_id), so a run can never
--   point at a workspace belonging to another business even if an id is guessed.
--
-- * `category_overrides` is the append-only record of a person's category decisions. A new row
--   supersedes the previous one for that transaction by time; nothing is updated in place, so
--   the history of who decided what stays readable. Workspace-scoped through the same kind of
--   composite key, so one visitor's correction never moves another's figures.
--
-- * `usage_reservations` holds spend. `reserve_ai_usage` counts and inserts inside ONE
--   transaction under two advisory locks taken in a fixed order (global, then identity). This
--   is migration 0017's lesson applied before the first paid call rather than after a race:
--   0015 checked a quota and let the caller insert afterwards, and two concurrent requests both
--   took the last slot. The reservation IS the check here.
--
-- Reservations are keyed by the spending identity (a user or a visitor), not by workspace,
-- because the person spends and one person can hold several workspaces.

alter table public.workspaces add constraint workspaces_id_business_key unique (id, business_id);

-- ------------------------------------------------------------------ overrides --
create table public.category_overrides (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null,
  business_id uuid not null,
  transaction_id uuid not null,
  -- Null is a deliberate value: "put this row back under the standard rules".
  category text check (category is null or category ~ '^[a-z_]{1,40}$'),
  transaction_revision integer not null check (transaction_revision >= 1),
  source text not null check (source in ('owner_correction', 'accepted_suggestion')),
  decided_by_kind text not null check (decided_by_kind in ('user', 'visitor')),
  decided_by_id uuid not null,
  batch_id uuid,
  created_at timestamptz not null default now(),
  foreign key (workspace_id, business_id) references public.workspaces (id, business_id) on delete cascade,
  foreign key (transaction_id, business_id) references public.transactions (id, business_id) on delete cascade
);
comment on table public.category_overrides is
  'Append-only category decisions made by a person, scoped to one workspace. The newest row for a transaction wins; earlier rows are kept so the decision history survives. Stores data only: no prompt, model or rule changes when a row is written, which is why the interface says "set by you" and never "learned".';
comment on column public.category_overrides.transaction_revision is
  'The revision the decision was made against. If a later import ever changes the row, the decision is shown as needing another look rather than silently applied to different data.';
create index category_overrides_current on public.category_overrides (workspace_id, transaction_id, created_at desc);

alter table public.category_overrides enable row level security;
revoke all on table public.category_overrides from anon, authenticated;
grant select, insert, delete on table public.category_overrides to service_role;

-- ----------------------------------------------------------------------- runs --
create table public.ai_runs (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references public.businesses (id) on delete cascade,
  workspace_id uuid,
  import_id uuid,
  capability text not null check (capability in ('brief', 'classify', 'scenario_interpret', 'scenario_explain')),
  mode text not null check (mode in ('live', 'cached', 'replay')),
  -- `scripted` is the adapter a test drives. It is recorded like any other provider so a run
  -- produced by one is never mistaken for a live call; its mode is always `replay`.
  provider text not null check (provider in ('openai', 'openrouter', 'replay', 'scripted')),
  model_requested text not null check (length(model_requested) between 1 and 120),
  model_used text check (length(model_used) between 1 and 120),
  prompt_version text not null check (length(prompt_version) between 1 and 40),
  analysis_version text not null,
  rules_version text not null,
  analysis_date date not null,
  input_fingerprint text not null check (length(input_fingerprint) = 64),
  status text not null check (status in (
    'reserved', 'running', 'succeeded', 'invalid_output', 'provider_error',
    'timeout', 'insufficient_data', 'superseded'
  )),
  validation jsonb,
  attempts integer not null default 0 check (attempts >= 0),
  latency_ms integer check (latency_ms >= 0),
  prompt_tokens integer check (prompt_tokens >= 0),
  completion_tokens integer check (completion_tokens >= 0),
  cost_micro bigint check (cost_micro >= 0),
  cost_source text check (cost_source in ('price_table', 'provider', 'reserved_estimate', 'unknown')),
  provider_request_id text check (length(provider_request_id) <= 200),
  error_code text check (error_code ~ '^[a-z_]{1,60}$'),
  -- The fact sheet as sent, and the output as validated. The owner's own figures, readable
  -- only through their own scoped route, and deleted with the business.
  safe_input jsonb,
  output jsonb,
  input_summary jsonb,
  cached_from uuid references public.ai_runs (id) on delete set null,
  reservation_id uuid,
  created_at timestamptz not null default now(),
  completed_at timestamptz,
  foreign key (workspace_id, business_id) references public.workspaces (id, business_id) on delete cascade
);
comment on table public.ai_runs is
  'One row per generation attempt. Stores the fact sheet the model was given and the validated output, never raw model text: an invalid output keeps the validator messages and a hash instead. Business-scoped with a nullable workspace so an extraction run can precede one.';
create index ai_runs_for_workspace on public.ai_runs (workspace_id, capability, created_at desc);
create index ai_runs_by_business on public.ai_runs (business_id, created_at desc);

alter table public.ai_runs enable row level security;
revoke all on table public.ai_runs from anon, authenticated;
grant select, insert, update, delete on table public.ai_runs to service_role;

-- --------------------------------------------------------------- reservations --
create table public.usage_reservations (
  id uuid primary key,
  identity_kind text not null check (identity_kind in ('user', 'visitor')),
  identity_id uuid not null,
  capability text not null check (length(capability) between 1 and 40),
  estimated_cost_micro bigint not null check (estimated_cost_micro >= 0),
  cost_micro bigint check (cost_micro >= 0),
  -- True when the call may have been billed but the usage never came back (a timeout).
  -- The estimate stands in that case, which errs toward refusing rather than overspending.
  uncertain boolean not null default false,
  expires_at timestamptz not null,
  settled_at timestamptz,
  created_at timestamptz not null default now()
);
comment on table public.usage_reservations is
  'Model spend held before the call. Keyed by the spending identity, never by workspace: the person spends and one person can hold several workspaces. An unsettled reservation counts against the caps until it expires, so a process killed mid-call cannot quietly free budget.';
create index usage_reservations_identity_window on public.usage_reservations (identity_kind, identity_id, created_at desc);
create index usage_reservations_window on public.usage_reservations (created_at desc);

alter table public.usage_reservations enable row level security;
revoke all on table public.usage_reservations from anon, authenticated;
grant select, insert, update, delete on table public.usage_reservations to service_role;

-- ------------------------------------------------------ reserving before paying --
-- Counts settled cost and unexpired unsettled estimates in the rolling 24 hours, refuses if a
-- cap would be exceeded, and inserts the reservation in the SAME transaction under the same
-- locks. Two concurrent requests cannot both take the last of the budget.
create function public.reserve_ai_usage(
  p_reservation_id uuid,
  p_identity_kind text,
  p_identity_id uuid,
  p_capability text,
  p_estimated_cost_micro bigint,
  p_identity_daily_cap_micro bigint,
  p_global_daily_cap_micro bigint,
  p_max_in_flight integer,
  p_expires_seconds integer
) returns table (
  allowed boolean, reason text, identity_spend_micro bigint,
  global_spend_micro bigint, in_flight integer
)
language plpgsql security definer set search_path = public, pg_temp as $$
#variable_conflict use_column
declare
  v_identity bigint;
  v_global bigint;
  v_in_flight integer;
  v_estimate bigint := greatest(coalesce(p_estimated_cost_micro, 0), 0);
begin
  if p_identity_kind not in ('user', 'visitor') then
    raise exception 'invalid identity' using errcode = 'invalid_parameter_value';
  end if;

  -- Fixed order, always: the global lock first, then the identity. Taking them in one order
  -- everywhere is what stops two callers deadlocking against each other.
  perform pg_advisory_xact_lock(hashtext('ai_usage:global'));
  perform pg_advisory_xact_lock(hashtext('ai_usage:' || p_identity_kind || ':' || p_identity_id::text));

  -- An unsettled reservation counts at its estimate until it expires; a settled one at what it
  -- actually cost. Anything expired and unsettled has been released by time and counts nothing.
  select coalesce(sum(
    case when r.settled_at is not null then coalesce(r.cost_micro, 0)
         when r.expires_at > now() then r.estimated_cost_micro
         else 0 end), 0)
    into v_identity
    from public.usage_reservations r
    where r.identity_kind = p_identity_kind and r.identity_id = p_identity_id
      and r.created_at > now() - interval '24 hours';

  select coalesce(sum(
    case when r.settled_at is not null then coalesce(r.cost_micro, 0)
         when r.expires_at > now() then r.estimated_cost_micro
         else 0 end), 0)
    into v_global
    from public.usage_reservations r
    where r.created_at > now() - interval '24 hours';

  select count(*) into v_in_flight
    from public.usage_reservations r
    where r.identity_kind = p_identity_kind and r.identity_id = p_identity_id
      and r.settled_at is null and r.expires_at > now();

  if v_in_flight >= greatest(coalesce(p_max_in_flight, 1), 1) then
    return query select false, 'in_flight'::text, v_identity, v_global, v_in_flight; return;
  end if;
  if v_identity + v_estimate > p_identity_daily_cap_micro then
    return query select false, 'identity_cap'::text, v_identity, v_global, v_in_flight; return;
  end if;
  if v_global + v_estimate > p_global_daily_cap_micro then
    return query select false, 'global_cap'::text, v_identity, v_global, v_in_flight; return;
  end if;

  insert into public.usage_reservations (
    id, identity_kind, identity_id, capability, estimated_cost_micro, expires_at
  ) values (
    p_reservation_id, p_identity_kind, p_identity_id, p_capability, v_estimate,
    now() + make_interval(secs => greatest(coalesce(p_expires_seconds, 600), 1))
  );
  return query select true, 'ok'::text, v_identity, v_global, v_in_flight;
end $$;

-- Replaces the held estimate with what the call actually cost. A null cost means the usage
-- never came back (a timeout): the estimate stands and the row is marked uncertain, because
-- the provider may well have billed for work we cannot measure.
create function public.settle_ai_usage(p_reservation_id uuid, p_cost_micro bigint default null)
returns bigint
language plpgsql security definer set search_path = public, pg_temp as $$
declare
  v_cost bigint;
begin
  update public.usage_reservations r
    set settled_at = now(),
        cost_micro = coalesce(p_cost_micro, r.estimated_cost_micro),
        uncertain = p_cost_micro is null
    where r.id = p_reservation_id and r.settled_at is null
    returning r.cost_micro into v_cost;
  return v_cost;
end $$;

-- The request never left this process, so nothing can have been billed.
create function public.release_ai_usage(p_reservation_id uuid)
returns boolean
language plpgsql security definer set search_path = public, pg_temp as $$
declare
  v_rows integer;
begin
  update public.usage_reservations r
    set settled_at = now(), cost_micro = 0, uncertain = false
    where r.id = p_reservation_id and r.settled_at is null;
  get diagnostics v_rows = row_count;
  return v_rows = 1;
end $$;

-- --------------------------------------------------------------- deletion --
-- Same function as 0014 with one change: the AI rows go FIRST, before the transactions they
-- describe, so the tombstone phase never holds a fact sheet or an owner's category decision.
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

  -- AI rows first. They hold the owner's figures and their category decisions, so they must
  -- not survive the transactions they were derived from, not even for the tombstone phase.
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

revoke all on function public.reserve_ai_usage(uuid, text, uuid, text, bigint, bigint, bigint, integer, integer) from public, anon, authenticated;
revoke all on function public.settle_ai_usage(uuid, bigint) from public, anon, authenticated;
revoke all on function public.release_ai_usage(uuid) from public, anon, authenticated;
grant execute on function public.reserve_ai_usage(uuid, text, uuid, text, bigint, bigint, bigint, integer, integer) to service_role;
grant execute on function public.settle_ai_usage(uuid, bigint) to service_role;
grant execute on function public.release_ai_usage(uuid) to service_role;

comment on function public.reserve_ai_usage(uuid, text, uuid, text, bigint, bigint, bigint, integer, integer) is
  'Counts the rolling 24-hour spend and inserts the reservation in one transaction under a global then an identity advisory lock, so two concurrent requests cannot both take the last of the budget. Refuses before any paid work with the cap that would have been exceeded.';

notify pgrst, 'reload schema';
