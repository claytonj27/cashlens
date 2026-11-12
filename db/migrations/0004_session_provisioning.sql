-- 0004: server-side provisioning functions for demo visitors and private businesses.
-- Both run only for the service role, which the API uses after it has validated
-- the caller (a visitor cookie it issued, or a Supabase Auth access token). They
-- are idempotent and take an advisory lock per identity so concurrent calls
-- cannot create duplicates.

create function public.ensure_demo_workspace(p_visitor_id uuid, p_slug text)
returns table (workspace_id uuid, business_id uuid, slug text, name text, industry text)
language plpgsql as $$
declare
  v_business public.businesses%rowtype;
  v_workspace_id uuid;
begin
  if p_visitor_id is null then
    raise exception 'visitor id required' using errcode = 'invalid_parameter_value';
  end if;
  select * into v_business from public.businesses b where b.slug = p_slug and b.data_mode = 'demo';
  if v_business.id is null then
    raise exception 'unknown demo business' using errcode = 'no_data_found';
  end if;
  perform pg_advisory_xact_lock(hashtext('visitor:' || p_visitor_id::text));
  insert into public.visitors (id) values (p_visitor_id) on conflict (id) do update set last_seen_at = now();
  insert into public.workspaces (business_id, business_data_mode, visitor_id)
    values (v_business.id, 'demo', p_visitor_id)
    on conflict (visitor_id, business_id) do nothing;
  select w.id into v_workspace_id from public.workspaces w
    where w.visitor_id = p_visitor_id and w.business_id = v_business.id;
  return query select v_workspace_id, v_business.id, v_business.slug, v_business.name, v_business.industry;
end $$;

create function public.provision_private_business(p_user_id uuid, p_business_name text)
returns table (business_id uuid, workspace_id uuid, business_name text, created boolean)
language plpgsql as $$
declare
  v_business_id uuid;
  v_workspace_id uuid;
  v_name text;
  v_created boolean := false;
begin
  if p_user_id is null then
    raise exception 'user id required' using errcode = 'invalid_parameter_value';
  end if;
  if not exists (select 1 from auth.users u where u.id = p_user_id) then
    raise exception 'unknown user' using errcode = 'no_data_found';
  end if;
  perform pg_advisory_xact_lock(hashtext('provision:' || p_user_id::text));
  select m.business_id into v_business_id from public.business_memberships m
    where m.user_id = p_user_id order by m.created_at limit 1;
  if v_business_id is null then
    v_name := nullif(btrim(coalesce(p_business_name, '')), '');
    if v_name is null or length(v_name) > 120 then
      raise exception 'business name required (1 to 120 characters)' using errcode = 'invalid_parameter_value';
    end if;
    insert into public.businesses (slug, name, industry, data_mode)
      values ('private-' || replace(gen_random_uuid()::text, '-', ''), v_name, 'unspecified', 'private')
      returning id into v_business_id;
    insert into public.business_memberships (business_id, business_data_mode, user_id, role)
      values (v_business_id, 'private', p_user_id, 'owner');
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

revoke all on function public.ensure_demo_workspace(uuid, text) from public, anon, authenticated;
revoke all on function public.provision_private_business(uuid, text) from public, anon, authenticated;
grant execute on function public.ensure_demo_workspace(uuid, text) to service_role;
grant execute on function public.provision_private_business(uuid, text) to service_role;

comment on function public.provision_private_business(uuid, text) is
  'Create or resume the signed-in user''s private business, membership and workspace. Never attaches demo data.';
