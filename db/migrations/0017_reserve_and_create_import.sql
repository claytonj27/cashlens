-- 0017: hold the slot in the same transaction that checks it (milestone 8, task 3).
--
-- 0015 and 0016 checked the quotas and then let the route insert the import row afterwards, in
-- a separate statement. Two creations in different businesses could therefore both pass the
-- check before either row existed and both take the same last slot. db/checks/quota_check.py
-- caught it: two concurrent reservations were both allowed.
--
-- The import row is the reservation, so the check and the insert belong in one transaction
-- under the same advisory lock on the caller. reserve_import_slot stays for read-only callers
-- that only want the verdict; the route now uses this instead.

create function public.reserve_and_create_import(
  p_user_id uuid, p_business_id uuid, p_import_id uuid, p_account_id uuid,
  p_proposed_account jsonb, p_object_path text, p_file_label text, p_declared_bytes bigint,
  p_expires_at timestamptz,
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

  delete from public.import_starts s
    where s.user_id = p_user_id and s.created_at < now() - interval '2 days';

  select coalesce(array_agg(m.business_id), '{}') into v_businesses
    from public.business_memberships m
    join public.businesses b on b.id = m.business_id
    where m.user_id = p_user_id and b.deletion_requested_at is null;

  if not (p_business_id = any(v_businesses)) then
    raise exception 'not_found' using errcode = 'no_data_found';
  end if;

  select count(*) into v_active
    from public.document_imports i
    where i.business_id = any(v_businesses) and i.status in ('awaiting_upload', 'staged');
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
    return query select false, 'active_imports', v_active, v_hour, v_day, v_bytes, v_first; return;
  end if;
  if v_hour >= p_max_per_hour then
    return query select false, 'rate_hour', v_active, v_hour, v_day, v_bytes, null::uuid; return;
  end if;
  if v_day >= p_max_per_day then
    return query select false, 'rate_day', v_active, v_hour, v_day, v_bytes, null::uuid; return;
  end if;
  if v_bytes + greatest(coalesce(p_declared_bytes, 0), 0) > p_max_outstanding_bytes then
    return query select false, 'outstanding_bytes', v_active, v_hour, v_day, v_bytes, null::uuid; return;
  end if;

  -- The row that holds the slot is created here, inside the same lock as the count above.
  insert into public.document_imports (
    id, business_id, business_data_mode, uploaded_by, account_id, proposed_account,
    format, object_path, file_label, declared_bytes, expires_at
  ) values (
    p_import_id, p_business_id, 'private', p_user_id, p_account_id, p_proposed_account,
    'csv', p_object_path, p_file_label, p_declared_bytes, p_expires_at
  );
  insert into public.import_starts (user_id, import_id) values (p_user_id, p_import_id)
    on conflict (import_id) do nothing;
  return query select true, 'ok'::text, v_active, v_hour, v_day, v_bytes, null::uuid;
end $$;

revoke all on function public.reserve_and_create_import(uuid, uuid, uuid, uuid, jsonb, text, text, bigint, timestamptz, integer, integer, integer, bigint) from public, anon, authenticated;
grant execute on function public.reserve_and_create_import(uuid, uuid, uuid, uuid, jsonb, text, text, bigint, timestamptz, integer, integer, integer, bigint) to service_role;

comment on function public.reserve_and_create_import(uuid, uuid, uuid, uuid, jsonb, text, text, bigint, timestamptz, integer, integer, integer, bigint) is
  'Checks every upload quota and creates the import row in one transaction under an advisory lock on the caller, so two requests cannot both take the last slot. The import row is the reservation.';

notify pgrst, 'reload schema';
