-- 0014: owner-requested private business deletion (milestone 8, task 2).
--
-- Authorization. `membership_role` has exactly one value, `owner`, so a membership row says
-- only that someone belongs to a business, never that they may destroy it. Rather than invent
-- roles, this records who provisioned a business in `businesses.created_by` and makes that the
-- deletion authority. Existing rows are backfilled only where the business has exactly one
-- membership, which is what provisioning creates; a business with none or several is left null
-- and cannot be deleted through this feature at all. Ownership is never inferred from the first
-- row a query happens to return.
--
-- Lifecycle. Deletion is a request with durable state, not a single destructive statement. The
-- business row survives as a tombstone until every uploaded file is actually gone, so the owner
-- can still see the status while every scoped read is already refused. The financial data goes
-- immediately. The inventory of files to remove is copied into `storage_cleanup`, which has no
-- foreign key on purpose: a file whose removal keeps failing must stay retryable long after the
-- import row that named it has been cascaded away.
--
-- Demo businesses are refused twice over: `data_mode` is checked here, and `guard_demo_rows`
-- already fires BEFORE INSERT OR DELETE OR UPDATE on `businesses`, so a demo row cannot be
-- deleted by any path.

alter table public.businesses add column created_by uuid references auth.users (id) on delete set null;
comment on column public.businesses.created_by is
  'Who provisioned this private business. The deletion authority: a membership alone never carries it, because membership_role has only one value.';

update public.businesses b set created_by = (
    select m.user_id from public.business_memberships m where m.business_id = b.id
  )
  where b.data_mode = 'private'
    and (select count(*) from public.business_memberships m where m.business_id = b.id) = 1;

alter table public.businesses
  add column deletion_requested_at timestamptz,
  add column deletion_requested_by uuid,
  add column data_removed_at timestamptz;
comment on column public.businesses.deletion_requested_at is
  'Set when the owner asked for deletion. Every scoped read and write refuses the business from this moment; the row remains only so the owner can watch the cleanup finish.';

create index businesses_pending_deletion on public.businesses (deletion_requested_at)
  where deletion_requested_at is not null;

-- The inventory that outlives the rows it came from.
create table public.storage_cleanup (
  id uuid primary key default gen_random_uuid(),
  bucket text not null check (bucket = 'statements'),
  object_path text not null unique,
  business_id uuid,
  reason text not null check (reason ~ '^[a-z_]{1,60}$'),
  attempts integer not null default 0 check (attempts >= 0),
  last_attempt_at timestamptz,
  last_error text check (length(last_error) <= 200),
  deleted_at timestamptz,
  created_at timestamptz not null default now()
);
comment on table public.storage_cleanup is
  'Files still to remove from Storage. Deliberately has no foreign key to businesses or imports: it must survive the cascade that removes them, so a failed removal stays retryable afterwards. Never reports a file as gone before Storage confirmed it.';
create index storage_cleanup_pending on public.storage_cleanup (last_attempt_at nulls first)
  where deleted_at is null;
create index storage_cleanup_by_business on public.storage_cleanup (business_id) where deleted_at is null;

alter table public.storage_cleanup enable row level security;
-- No client policies on purpose: only the API, through the service role, ever touches this.
revoke all on table public.storage_cleanup from anon, authenticated;
grant select, insert, update, delete on table public.storage_cleanup to service_role;

-- ------------------------------------------------------ requesting deletion --
-- Idempotent. The first call authorizes, freezes the business, copies the file inventory,
-- ends anything in flight under the lease rules M7 established, and removes the financial data.
-- A repeat call finds the work done and reports the same state.
create function public.request_business_deletion(p_business_id uuid, p_user_id uuid)
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
  -- Membership is not authority. Only the account that provisioned the business may delete it.
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

  -- The inventory is copied before anything cascades, so no path is lost.
  insert into public.storage_cleanup (bucket, object_path, business_id, reason)
    select i.object_bucket, i.object_path, i.business_id, 'business_deleted'
    from public.document_imports i
    where i.business_id = p_business_id and i.object_deleted_at is null
    on conflict (object_path) do nothing;
  get diagnostics v_queued = row_count;

  -- Anything in flight ends under the lease rules, so a worker that returns cannot write.
  for v_import in
    select i.id from public.document_imports i
    where i.business_id = p_business_id and i.status in ('awaiting_upload', 'staged', 'failed')
  loop
    perform public.end_import(v_import.id, 'business_deleted');
    v_ended := v_ended + 1;
  end loop;

  -- Transactions first: transaction_sources restricts deleting the import rows that back them.
  delete from public.transactions t where t.business_id = p_business_id;
  get diagnostics v_removed = row_count;
  delete from public.balance_snapshots s where s.business_id = p_business_id;
  delete from public.document_imports i where i.business_id = p_business_id;
  delete from public.accounts a where a.business_id = p_business_id;
  delete from public.connections c where c.business_id = p_business_id;

  update public.businesses b set data_removed_at = now() where b.id = p_business_id;
  return query select false, v_business.name, v_queued, v_ended, v_removed, now();
end $$;

-- --------------------------------------------------------------- finishing --
-- The tombstone goes only once every file it owned is confirmed gone. Until then the owner can
-- still read the status, and the business is refused everywhere else.
create function public.finalize_business_deletion(p_business_id uuid)
returns boolean
language plpgsql security definer set search_path = public, pg_temp as $$
declare
  v_pending integer;
  v_removed integer;
begin
  select count(*) into v_pending from public.storage_cleanup c
    where c.business_id = p_business_id and c.deleted_at is null;
  if v_pending > 0 then
    return false;
  end if;
  delete from public.businesses b
    where b.id = p_business_id and b.data_mode = 'private'
      and b.deletion_requested_at is not null and b.data_removed_at is not null;
  get diagnostics v_removed = row_count;
  return v_removed = 1;
end $$;

-- ------------------------------------------------------- draining the queue --
create function public.storage_cleanup_backlog(p_limit integer default 200, p_business_id uuid default null)
returns table (id uuid, bucket text, object_path text, business_id uuid, attempts integer)
language sql stable security definer set search_path = public, pg_temp as $$
  select c.id, c.bucket, c.object_path, c.business_id, c.attempts
  from public.storage_cleanup c
  where c.deleted_at is null
    and (p_business_id is null or c.business_id = p_business_id)
  order by c.last_attempt_at nulls first, c.created_at
  limit greatest(coalesce(p_limit, 0), 0);
$$;

create function public.storage_cleanup_mark_deleted(p_id uuid)
returns boolean language plpgsql security definer set search_path = public, pg_temp as $$
declare
  v_updated integer;
begin
  update public.storage_cleanup c
    set deleted_at = now(), attempts = c.attempts + 1, last_attempt_at = now(), last_error = null
    where c.id = p_id and c.deleted_at is null;
  get diagnostics v_updated = row_count;
  return v_updated = 1;
end $$;

create function public.storage_cleanup_mark_failed(p_id uuid, p_error text)
returns integer language plpgsql security definer set search_path = public, pg_temp as $$
declare
  v_attempts integer;
begin
  update public.storage_cleanup c
    set attempts = c.attempts + 1, last_attempt_at = now(), last_error = left(p_error, 200)
    where c.id = p_id and c.deleted_at is null
    returning c.attempts into v_attempts;
  return coalesce(v_attempts, 0);
end $$;

-- Businesses whose files are all gone and whose tombstone can now be removed.
create function public.pending_business_deletions(p_limit integer default 50)
returns table (business_id uuid, objects_pending integer)
language sql stable security definer set search_path = public, pg_temp as $$
  select b.id,
         (select count(*)::integer from public.storage_cleanup c
          where c.business_id = b.id and c.deleted_at is null)
  from public.businesses b
  where b.deletion_requested_at is not null
  order by b.deletion_requested_at
  limit greatest(coalesce(p_limit, 0), 0);
$$;

-- ------------------------------------------------------------ provisioning --
-- Only change from 0011: a business awaiting deletion is never resumed and never matched by a
-- key, so asking again creates a new one instead of reviving what the owner asked to remove.
create or replace function public.provision_private_business(
  p_user_id uuid, p_business_name text, p_idempotency_key text default null
) returns table (business_id uuid, workspace_id uuid, business_name text, created boolean)
language plpgsql security definer set search_path = public, pg_temp as $$
#variable_conflict use_column
declare
  v_business_id uuid;
  v_workspace_id uuid;
  v_name text;
  v_created boolean := false;
  v_key text := nullif(btrim(coalesce(p_idempotency_key, '')), '');
begin
  if p_user_id is null then
    raise exception 'user required' using errcode = 'invalid_parameter_value';
  end if;
  if nullif(btrim(coalesce(p_business_name, '')), '') is null then
    raise exception 'business name required' using errcode = 'invalid_parameter_value';
  end if;
  perform pg_advisory_xact_lock(hashtext('provision:' || p_user_id::text));
  if v_key is null then
    select m.business_id into v_business_id from public.business_memberships m
      join public.businesses b on b.id = m.business_id
      where m.user_id = p_user_id and b.deletion_requested_at is null
      order by m.created_at limit 1;
  else
    select m.business_id into v_business_id from public.business_memberships m
      join public.businesses b on b.id = m.business_id
      where m.user_id = p_user_id and m.provisioning_key = v_key and b.deletion_requested_at is null;
  end if;
  if v_business_id is null then
    insert into public.businesses (name, slug, industry, data_mode, created_by)
      values (btrim(p_business_name), 'private-' || replace(gen_random_uuid()::text, '-', ''), 'other', 'private', p_user_id)
      returning id into v_business_id;
    v_created := true;
    insert into public.business_memberships (business_id, business_data_mode, user_id, role, provisioning_key)
      values (v_business_id, 'private', p_user_id, 'owner', v_key);
  end if;
  insert into public.workspaces (business_id, business_data_mode, user_id)
    values (v_business_id, 'private', p_user_id)
    on conflict (user_id, business_id) do nothing;
  select w.id into v_workspace_id from public.workspaces w
    where w.user_id = p_user_id and w.business_id = v_business_id;
  select b.name into v_name from public.businesses b where b.id = v_business_id;
  return query select v_business_id, v_workspace_id, v_name, v_created;
end $$;

revoke all on function public.request_business_deletion(uuid, uuid) from public, anon, authenticated;
revoke all on function public.finalize_business_deletion(uuid) from public, anon, authenticated;
revoke all on function public.storage_cleanup_backlog(integer, uuid) from public, anon, authenticated;
revoke all on function public.storage_cleanup_mark_deleted(uuid) from public, anon, authenticated;
revoke all on function public.storage_cleanup_mark_failed(uuid, text) from public, anon, authenticated;
revoke all on function public.pending_business_deletions(integer) from public, anon, authenticated;
grant execute on function public.request_business_deletion(uuid, uuid) to service_role;
grant execute on function public.finalize_business_deletion(uuid) to service_role;
grant execute on function public.storage_cleanup_backlog(integer, uuid) to service_role;
grant execute on function public.storage_cleanup_mark_deleted(uuid) to service_role;
grant execute on function public.storage_cleanup_mark_failed(uuid, text) to service_role;
grant execute on function public.pending_business_deletions(integer) to service_role;

notify pgrst, 'reload schema';
