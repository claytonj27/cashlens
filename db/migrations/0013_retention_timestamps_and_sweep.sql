-- 0013: retention timestamps, storage-deletion tracking and the bounded cleanup sweep (milestone 8, task 1).
-- Retention rules implemented here, one per class, as recorded in the M8 plan and ADR 0010:
--   * an upload that never reached review expires 24 hours after it was created (expires_at);
--   * an import awaiting review expires seven days after it FIRST reached review (staged_at);
--   * confirmed transactions and the provenance transaction_sources requires never expire;
--   * the source file of a confirmed, cancelled or failed import follows the existing lifecycle,
--     and a removal that failed is retried with its attempts and last error recorded.
-- Nothing here schedules anything. The sweep runs only when a caller invokes it.
--
-- Existing rows: staged_at is backfilled from created_at for imports that are staged today.
-- created_at is the only immutable evidence available, and it is a lower bound on the true
-- staging time, so a backfilled import becomes eligible no later than it would have. Using
-- updated_at was rejected: it moves whenever a row is reviewed or re-staged, so it would let
-- looking at an import extend its retention, which is exactly what must not happen. No row's
-- age is reset to deployment time.

alter table public.document_imports add column staged_at timestamptz;
comment on column public.document_imports.staged_at is
  'When this import FIRST reached review. Set once by stage_import and never advanced by a re-stage, a decision or a retry, so viewing or retrying an import cannot extend its retention.';

update public.document_imports set staged_at = created_at where status = 'staged' and staged_at is null;

alter table public.document_imports
  add column object_delete_attempts integer not null default 0 check (object_delete_attempts >= 0),
  add column object_delete_attempted_at timestamptz,
  add column object_delete_error text check (length(object_delete_error) <= 200);
comment on column public.document_imports.object_delete_attempts is
  'How many times removing the source file has been tried. object_deleted_at stays null until a removal actually succeeds, so a failure remains visible and eligible for retry.';

-- Bounded selection needs indexes, or every sweep scans the table.
create index document_imports_retention_unstaged on public.document_imports (expires_at)
  where status = 'awaiting_upload';
create index document_imports_retention_staged on public.document_imports (staged_at)
  where status = 'staged';
create index document_imports_storage_backlog on public.document_imports (object_delete_attempted_at nulls first)
  where object_deleted_at is null;
create index visitors_last_seen on public.visitors (last_seen_at);

-- ------------------------------------------------------------ staging --
-- Only change from 0009: staged_at is stamped the first time and left alone afterwards.
create or replace function public.stage_import(
  p_import_id uuid, p_job_id uuid, p_lease_token uuid, p_rows jsonb, p_fields jsonb
) returns integer
language plpgsql security definer set search_path = public, pg_temp as $$
#variable_conflict use_column
declare
  v_import public.document_imports%rowtype;
  v_count integer;
begin
  perform public.assert_lease(p_job_id, p_import_id, p_lease_token);
  select * into v_import from public.document_imports i where i.id = p_import_id for update;
  if v_import.id is null or v_import.status not in ('awaiting_upload', 'staged') then
    raise exception 'invalid_state' using errcode = 'invalid_parameter_value';
  end if;
  delete from public.import_rows r where r.import_id = p_import_id;
  insert into public.import_rows (import_id, row_index, page_number, source_locator, raw, extracted, flags, decision, linked_transaction_id)
    select p_import_id,
           (r ->> 'row_index')::integer,
           nullif(r ->> 'page_number', '')::integer,
           r ->> 'source_locator',
           coalesce(r -> 'raw', '{}'::jsonb),
           coalesce(r -> 'extracted', '{}'::jsonb),
           coalesce(array(select jsonb_array_elements_text(coalesce(r -> 'flags', '[]'::jsonb))), '{}'::text[]),
           coalesce(r ->> 'decision', 'keep'),
           nullif(r ->> 'linked_transaction_id', '')::uuid
    from jsonb_array_elements(p_rows) as r;
  get diagnostics v_count = row_count;
  update public.document_imports i
    set status = 'staged',
        staged_at = coalesce(i.staged_at, now()),
        format = coalesce(p_fields ->> 'format', i.format),
        byte_size = (p_fields ->> 'byte_size')::bigint,
        content_hash = p_fields ->> 'content_hash',
        parser_version = p_fields ->> 'parser_version',
        mapping = p_fields -> 'mapping',
        summary = p_fields -> 'summary',
        draft_revision = (p_fields ->> 'draft_revision')::integer,
        error_code = null,
        error_message = null
    where i.id = p_import_id;
  return v_count;
end $$;

-- -------------------------------------------------------- ending an import --
-- The transitions M7 established, factored out so the owner's cancellation and the retention
-- sweep share one implementation: job rows are locked before the import row, any open lease is
-- invalidated in the same transaction, and the staged rows go with it. A worker that returns
-- afterwards finds its token rejected and the import in a state staging refuses.
create function public.end_import(p_import_id uuid, p_error_code text default null)
returns table (already_cancelled boolean, rows_removed integer, object_path text, object_deleted_at timestamptz)
language plpgsql security definer set search_path = public, pg_temp as $$
#variable_conflict use_column
declare
  v_import public.document_imports%rowtype;
  v_rows integer := 0;
begin
  perform 1 from public.import_jobs j where j.import_id = p_import_id order by j.id for update;
  select * into v_import from public.document_imports i where i.id = p_import_id for update;
  if v_import.id is null then
    raise exception 'not_found' using errcode = 'no_data_found';
  end if;
  if v_import.status = 'cancelled' then
    return query select true, 0, v_import.object_path, v_import.object_deleted_at;
    return;
  end if;
  if v_import.status not in ('awaiting_upload', 'staged', 'failed') then
    raise exception 'invalid_state:%', v_import.status using errcode = 'invalid_parameter_value';
  end if;
  update public.import_jobs j
    set state = 'failed', finished_at = now(), lease_token = null,
        lease_expires_at = now(), safe_error = coalesce(p_error_code, 'import cancelled')
    where j.import_id = p_import_id and j.state = 'leased';
  delete from public.import_rows r where r.import_id = p_import_id;
  get diagnostics v_rows = row_count;
  update public.document_imports i
    set status = 'cancelled', cancelled_at = now(), error_code = coalesce(p_error_code, i.error_code)
    where i.id = p_import_id;
  return query select false, v_rows, v_import.object_path, v_import.object_deleted_at;
end $$;

create or replace function public.cancel_import(p_import_id uuid, p_user_id uuid)
returns table (already_cancelled boolean, rows_removed integer, object_path text, object_deleted_at timestamptz)
language plpgsql security definer set search_path = public, pg_temp as $$
#variable_conflict use_column
declare
  v_import public.document_imports%rowtype;
begin
  select * into v_import from public.document_imports i where i.id = p_import_id;
  if v_import.id is null or not exists (
    select 1 from public.business_memberships m where m.business_id = v_import.business_id and m.user_id = p_user_id
  ) then
    raise exception 'not_found' using errcode = 'no_data_found';
  end if;
  return query select * from public.end_import(p_import_id, null);
end $$;

-- ------------------------------------------------------------ the sweep --
-- Work is bounded by the database, not by fetching everything and slicing afterwards. Two runs
-- cannot interleave: the second finds the advisory lock held and returns having done nothing.
-- p_business_id narrows the run to one business. It is a filter on top of the retention
-- conditions, never instead of them, so a caller cannot reach a row that is not yet eligible.
create function public.retention_sweep_imports(
  p_limit integer default 200, p_business_id uuid default null
) returns table (import_id uuid, business_id uuid, reason text, rows_removed integer, object_path text, file_removed boolean)
language plpgsql security definer set search_path = public, pg_temp as $$
#variable_conflict use_column
declare
  v_candidate record;
  v_result record;
begin
  if not pg_try_advisory_xact_lock(hashtext('retention_sweep_imports')) then
    return;
  end if;
  for v_candidate in
    select i.id,
           i.business_id as bid,
           case when i.status = 'awaiting_upload' then 'expired_unstaged' else 'expired_staged' end as reason
    from public.document_imports i
    where (p_business_id is null or i.business_id = p_business_id)
      and (
        (i.status = 'awaiting_upload' and i.expires_at < now())
        or (i.status = 'staged' and i.staged_at is not null and i.staged_at < now() - interval '7 days')
      )
    order by i.created_at
    limit greatest(coalesce(p_limit, 0), 0)
  loop
    select * into v_result from public.end_import(v_candidate.id, v_candidate.reason);
    if not v_result.already_cancelled then
      return query select v_candidate.id, v_candidate.bid, v_candidate.reason,
                          v_result.rows_removed, v_result.object_path,
                          v_result.object_deleted_at is not null;
    end if;
  end loop;
end $$;

-- Which source files still have to be removed. Terminal imports only: a confirmed import keeps
-- its rows because transaction_sources restricts their deletion, but its file still goes.
-- Oldest attempt first, nulls first, so a file that has never been tried is never starved.
create function public.retention_storage_backlog(
  p_limit integer default 200, p_business_id uuid default null
) returns table (import_id uuid, business_id uuid, object_path text, attempts integer)
language sql stable security definer set search_path = public, pg_temp as $$
  select i.id, i.business_id, i.object_path, i.object_delete_attempts
  from public.document_imports i
  where i.object_deleted_at is null
    and i.status in ('confirmed', 'cancelled', 'failed')
    and (p_business_id is null or i.business_id = p_business_id)
  order by i.object_delete_attempted_at nulls first, i.created_at
  limit greatest(coalesce(p_limit, 0), 0);
$$;

-- Recorded only after Storage actually confirmed the removal.
create function public.retention_mark_object_deleted(p_import_id uuid)
returns boolean language plpgsql security definer set search_path = public, pg_temp as $$
declare
  v_updated integer;
begin
  update public.document_imports i
    set object_deleted_at = now(),
        object_delete_attempts = i.object_delete_attempts + 1,
        object_delete_attempted_at = now(),
        object_delete_error = null
    where i.id = p_import_id and i.object_deleted_at is null;
  get diagnostics v_updated = row_count;
  return v_updated = 1;
end $$;

-- A failure leaves object_deleted_at null, which is the eligibility condition, so the next run
-- retries it. The attempt count and the last error make a stuck object visible instead of silent.
create function public.retention_mark_object_failed(p_import_id uuid, p_error text)
returns integer language plpgsql security definer set search_path = public, pg_temp as $$
declare
  v_attempts integer;
begin
  update public.document_imports i
    set object_delete_attempts = i.object_delete_attempts + 1,
        object_delete_attempted_at = now(),
        object_delete_error = left(p_error, 200)
    where i.id = p_import_id and i.object_deleted_at is null
    returning i.object_delete_attempts into v_attempts;
  return coalesce(v_attempts, 0);
end $$;

-- Anonymous demo identities idle for 30 days. Only the visitor row is deleted; its demo
-- workspaces follow by cascade. The shared demo businesses have no foreign key to a visitor
-- and are never touched. p_visitor_ids narrows a run to identities the caller already knows,
-- on top of the idle condition, never instead of it.
create function public.retention_sweep_visitors(
  p_limit integer default 500, p_visitor_ids uuid[] default null
) returns integer
language plpgsql security definer set search_path = public, pg_temp as $$
declare
  v_deleted integer;
begin
  if not pg_try_advisory_xact_lock(hashtext('retention_sweep_visitors')) then
    return 0;
  end if;
  with due as (
    select v.id from public.visitors v
    where v.last_seen_at < now() - interval '30 days'
      and (p_visitor_ids is null or v.id = any(p_visitor_ids))
    order by v.last_seen_at
    limit greatest(coalesce(p_limit, 0), 0)
    for update skip locked
  )
  delete from public.visitors v using due where v.id = due.id;
  get diagnostics v_deleted = row_count;
  return v_deleted;
end $$;

-- Demo activity, throttled so an ordinary page view does not write on every request.
create function public.touch_visitor(p_visitor_id uuid) returns void
language sql security definer set search_path = public, pg_temp as $$
  update public.visitors v set last_seen_at = now()
  where v.id = p_visitor_id and v.last_seen_at < now() - interval '1 hour';
$$;

revoke all on function public.end_import(uuid, text) from public, anon, authenticated;
revoke all on function public.retention_sweep_imports(integer, uuid) from public, anon, authenticated;
revoke all on function public.retention_storage_backlog(integer, uuid) from public, anon, authenticated;
revoke all on function public.retention_mark_object_deleted(uuid) from public, anon, authenticated;
revoke all on function public.retention_mark_object_failed(uuid, text) from public, anon, authenticated;
revoke all on function public.retention_sweep_visitors(integer, uuid[]) from public, anon, authenticated;
revoke all on function public.touch_visitor(uuid) from public, anon, authenticated;
grant execute on function public.end_import(uuid, text) to service_role;
grant execute on function public.retention_sweep_imports(integer, uuid) to service_role;
grant execute on function public.retention_storage_backlog(integer, uuid) to service_role;
grant execute on function public.retention_mark_object_deleted(uuid) to service_role;
grant execute on function public.retention_mark_object_failed(uuid, text) to service_role;
grant execute on function public.retention_sweep_visitors(integer, uuid[]) to service_role;
grant execute on function public.touch_visitor(uuid) to service_role;

comment on function public.retention_sweep_imports(integer, uuid) is
  'Ends imports that passed their retention window, in batches bounded by the database. Confirmed imports are never selected. Two concurrent runs do not interleave.';

notify pgrst, 'reload schema';
