-- 0016: fix reserve_import_slot, which used min() on a uuid column (milestone 8, task 3).
-- 0015 named the caller's oldest active import with min(i.id). PostgreSQL has no min(uuid), so
-- every reservation failed with 42883 and the create route answered 502. Caught by
-- db/checks/quota_check.py against the development project before any preview existed.
-- The oldest active import is now taken by created_at, which is what "oldest" meant anyway.

create or replace function public.reserve_import_slot(
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

  select count(*) into v_active
    from public.document_imports i
    where i.business_id = any(v_businesses) and i.status in ('awaiting_upload', 'staged');
  -- uuid has no min(); the oldest active import is the one worth naming back to the owner.
  select i.id into v_first
    from public.document_imports i
    where i.business_id = any(v_businesses) and i.status in ('awaiting_upload', 'staged')
    order by i.created_at limit 1;

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

notify pgrst, 'reload schema';
