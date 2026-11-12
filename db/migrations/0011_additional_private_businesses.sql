-- 0011: explicitly requested additional private businesses (milestone 7 support).
-- Ordinary onboarding is unchanged: calling provision_private_business without a
-- key still resumes the caller's earliest business and is idempotent. Passing a
-- caller-scoped idempotency key instead asks for an additional business: the key
-- is stored on the membership with a unique index per user, so a retried or
-- concurrent duplicate request returns the business the first call created
-- rather than making another one. The signature gains a defaulted third
-- parameter, so the existing two-argument callers (the API's provision route and
-- the isolation check's denial probe) are unaffected.

alter table public.business_memberships
  add column provisioning_key text check (length(provisioning_key) between 8 and 120);
comment on column public.business_memberships.provisioning_key is
  'Caller-scoped idempotency key for an explicitly requested additional business; null for ordinary first-business onboarding.';
create unique index business_memberships_provisioning_key
  on public.business_memberships (user_id, provisioning_key) where provisioning_key is not null;

drop function public.provision_private_business(uuid, text);

create function public.provision_private_business(
  p_user_id uuid, p_business_name text, p_idempotency_key text default null
)
returns table (business_id uuid, workspace_id uuid, business_name text, created boolean)
language plpgsql security definer set search_path = public, pg_temp as $$
#variable_conflict use_column
declare
  v_business_id uuid;
  v_workspace_id uuid;
  v_name text;
  v_key text;
  v_created boolean := false;
begin
  if p_user_id is null then
    raise exception 'user id required' using errcode = 'invalid_parameter_value';
  end if;
  if not exists (select 1 from auth.users u where u.id = p_user_id) then
    raise exception 'unknown user' using errcode = 'no_data_found';
  end if;
  v_key := nullif(btrim(coalesce(p_idempotency_key, '')), '');
  if v_key is not null and length(v_key) not between 8 and 120 then
    raise exception 'idempotency key must be 8 to 120 characters' using errcode = 'invalid_parameter_value';
  end if;
  -- One lock per user covers both paths, so a first-business call and a keyed
  -- call cannot interleave and a duplicate keyed call waits for the first.
  perform pg_advisory_xact_lock(hashtext('provision:' || p_user_id::text));
  if v_key is null then
    select m.business_id into v_business_id from public.business_memberships m
      where m.user_id = p_user_id order by m.created_at limit 1;
  else
    select m.business_id into v_business_id from public.business_memberships m
      where m.user_id = p_user_id and m.provisioning_key = v_key;
  end if;
  if v_business_id is null then
    v_name := nullif(btrim(coalesce(p_business_name, '')), '');
    if v_name is null or length(v_name) > 120 then
      raise exception 'business name required (1 to 120 characters)' using errcode = 'invalid_parameter_value';
    end if;
    insert into public.businesses (slug, name, industry, data_mode)
      values ('private-' || replace(gen_random_uuid()::text, '-', ''), v_name, 'unspecified', 'private')
      returning id into v_business_id;
    insert into public.business_memberships (business_id, business_data_mode, user_id, role, provisioning_key)
      values (v_business_id, 'private', p_user_id, 'owner', v_key);
    v_created := true;
  end if;
  insert into public.workspaces (business_id, business_data_mode, user_id)
    values (v_business_id, 'private', p_user_id)
    on conflict (user_id, business_id) do nothing;
  select w.id into v_workspace_id from public.workspaces w
    where w.user_id = p_user_id and w.business_id = v_business_id;
  select b.name into v_name from public.businesses b where b.id = v_business_id;
  return query select v_business_id, v_workspace_id, v_name, v_created;
end $$;

comment on function public.provision_private_business(uuid, text, text) is
  'Without a key: create or resume the caller''s earliest private business. With a caller-scoped key: create or return the additional business that key identifies. Service role only; the caller''s identity is supplied by the API after token validation.';

revoke all on function public.provision_private_business(uuid, text, text) from public, anon, authenticated;
grant execute on function public.provision_private_business(uuid, text, text) to service_role;

notify pgrst, 'reload schema';
