-- 0015: upload quotas, counted per user across every business they belong to (milestone 8, task 3).
--
-- Until now only one import in progress PER BUSINESS was enforced, so a user with several
-- businesses had no effective ceiling, and nothing limited how often uploads could be started
-- or how many files could sit in Storage at once.
--
-- Everything is evaluated inside one transaction holding an advisory lock on the caller, so two
-- requests cannot both pass the same last slot. Nothing is counted in a process-local variable:
-- this runs across many function instances, where such a counter would mean nothing.
--
-- The document_imports row is the reservation for active slots and outstanding bytes. Initiation
-- history needs its own table, because deleting a business cascades its document_imports away:
-- without import_starts, deleting a business would reset the caller's rate limit, which is a
-- concrete bypass. import_starts therefore has no foreign key to businesses, exactly as
-- storage_cleanup has none.

create table public.import_starts (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  import_id uuid not null,
  created_at timestamptz not null default now()
);
comment on table public.import_starts is
  'One row per upload a user starts, kept independently of the business so that deleting a business cannot reset a rate limit. Rows are pruned opportunistically once they are older than any window that counts them.';
create index import_starts_by_user on public.import_starts (user_id, created_at desc);
create unique index import_starts_by_import on public.import_starts (import_id);

alter table public.import_starts enable row level security;
-- No client policies: only the API, through the service role, touches this.
revoke all on table public.import_starts from anon, authenticated;
grant select, insert, update, delete on table public.import_starts to service_role;

-- Rolling counts read created_at over the caller's own rows; this index serves them.
create index document_imports_by_business_created on public.document_imports (business_id, created_at desc);

-- ------------------------------------------------------------- reservation --
-- Returns whether the caller may start another upload, and records the initiation when they may.
-- p_user_id comes from the caller's validated session at the route, never from a request body.
--
-- What consumes what:
--   * an active slot is held by an import in 'awaiting_upload' or 'staged'. Confirmed, cancelled
--     and failed imports hold none;
--   * outstanding bytes are held by any import whose object_deleted_at is null, whatever its
--     status, so a file whose removal failed keeps its reservation while the object still exists;
--   * an initiation is recorded for every upload started, and is only withdrawn when the upload
--     turns out to be an exact replay of a file the account already holds, which is not a new
--     upload at all. Ordinary cancellation leaves the initiation in place.
create function public.reserve_import_slot(
  p_user_id uuid, p_business_id uuid, p_import_id uuid, p_declared_bytes bigint,
  p_max_active integer, p_max_per_hour integer, p_max_per_day integer, p_max_outstanding_bytes bigint
) returns table (
  allowed boolean, reason text, active_count integer, started_hour integer,
  started_day integer, outstanding_bytes bigint, active_import_id uuid
)
language plpgsql security definer set search_path = public, pg_temp as $$
#variable_conflict use_column
declare
  v_businesses uuid[];
  v_active integer;
  v_hour integer;
  v_day integer;
  v_bytes bigint;
  v_first uuid;
begin
  perform pg_advisory_xact_lock(hashtext('import_quota:' || p_user_id::text));

  -- Old initiations can never affect any window that is still counted; clearing the caller's
  -- own stale rows here keeps the table bounded without a separate sweep.
  delete from public.import_starts s
    where s.user_id = p_user_id and s.created_at < now() - interval '2 days';

  select coalesce(array_agg(m.business_id), '{}') into v_businesses
    from public.business_memberships m
    join public.businesses b on b.id = m.business_id
    where m.user_id = p_user_id and b.deletion_requested_at is null;

  select count(*), min(i.id) into v_active, v_first
    from public.document_imports i
    where i.business_id = any(v_businesses) and i.status in ('awaiting_upload', 'staged');

  select coalesce(sum(coalesce(i.byte_size, i.declared_bytes)), 0) into v_bytes
    from public.document_imports i
    where i.business_id = any(v_businesses) and i.object_deleted_at is null;

  select count(*) into v_hour from public.import_starts s
    where s.user_id = p_user_id and s.created_at > now() - interval '1 hour';
  select count(*) into v_day from public.import_starts s
    where s.user_id = p_user_id and s.created_at > now() - interval '24 hours';

  if v_active >= p_max_active then
    return query select false, 'active_imports', v_active, v_hour, v_day, v_bytes, v_first;
    return;
  end if;
  if v_hour >= p_max_per_hour then
    return query select false, 'rate_hour', v_active, v_hour, v_day, v_bytes, null::uuid;
    return;
  end if;
  if v_day >= p_max_per_day then
    return query select false, 'rate_day', v_active, v_hour, v_day, v_bytes, null::uuid;
    return;
  end if;
  if v_bytes + greatest(coalesce(p_declared_bytes, 0), 0) > p_max_outstanding_bytes then
    return query select false, 'outstanding_bytes', v_active, v_hour, v_day, v_bytes, null::uuid;
    return;
  end if;

  insert into public.import_starts (user_id, import_id) values (p_user_id, p_import_id)
    on conflict (import_id) do nothing;
  return query select true, 'ok'::text, v_active, v_hour, v_day, v_bytes, null::uuid;
end $$;

-- An exact replay is not a new upload: the file is already in the account and the import that
-- was just created is cancelled immediately. Withdrawing its initiation keeps a replay free.
-- Ordinary cancellation deliberately does not call this, so cancelling cannot reset a rate limit.
create function public.release_import_start(p_import_id uuid)
returns boolean language plpgsql security definer set search_path = public, pg_temp as $$
declare
  v_removed integer;
begin
  delete from public.import_starts s where s.import_id = p_import_id;
  get diagnostics v_removed = row_count;
  return v_removed = 1;
end $$;

-- What the caller is currently using, for an error the owner can act on.
create function public.import_quota_usage(p_user_id uuid)
returns table (active_count integer, started_hour integer, started_day integer, outstanding_bytes bigint)
language sql stable security definer set search_path = public, pg_temp as $$
  with mine as (
    select m.business_id from public.business_memberships m
    join public.businesses b on b.id = m.business_id
    where m.user_id = p_user_id and b.deletion_requested_at is null
  )
  select
    (select count(*)::integer from public.document_imports i
      where i.business_id in (select business_id from mine)
        and i.status in ('awaiting_upload', 'staged')),
    (select count(*)::integer from public.import_starts s
      where s.user_id = p_user_id and s.created_at > now() - interval '1 hour'),
    (select count(*)::integer from public.import_starts s
      where s.user_id = p_user_id and s.created_at > now() - interval '24 hours'),
    (select coalesce(sum(coalesce(i.byte_size, i.declared_bytes)), 0)::bigint
      from public.document_imports i
      where i.business_id in (select business_id from mine) and i.object_deleted_at is null);
$$;

revoke all on function public.reserve_import_slot(uuid, uuid, uuid, bigint, integer, integer, integer, bigint) from public, anon, authenticated;
revoke all on function public.release_import_start(uuid) from public, anon, authenticated;
revoke all on function public.import_quota_usage(uuid) from public, anon, authenticated;
grant execute on function public.reserve_import_slot(uuid, uuid, uuid, bigint, integer, integer, integer, bigint) to service_role;
grant execute on function public.release_import_start(uuid) to service_role;
grant execute on function public.import_quota_usage(uuid) to service_role;

notify pgrst, 'reload schema';
