-- 0007: atomic application of staged provider sync batches (milestone 6).
-- A source adapter stages a provider's update pages as raw events (exactly
-- as received) plus the canonical form it derived, then calls
-- apply_sync_batch. The function serializes per connection, refuses batches
-- that do not start at the connection's committed cursor, applies added,
-- modified and removed events in order, links pending rows to the posted
-- rows that replace them, records provenance for every event, and commits
-- the next cursor only in the same transaction. Any error leaves the
-- connection, its transactions and its cursor untouched.

alter table public.raw_events add column canonical jsonb;
comment on column public.raw_events.canonical is
  'The adapter''s canonical reading of the payload: external_account_id, amount_minor (inflow positive), posted_date, description, status, pending_external_id. Null for removals.';

create function public.apply_sync_batch(p_batch_id uuid)
returns table (added_count integer, modified_count integer, removed_count integer, unchanged_count integer, end_cursor text)
language plpgsql security definer set search_path = public, pg_temp as $$
#variable_conflict use_column
declare
  v_batch public.sync_batches%rowtype;
  v_connection public.connections%rowtype;
  v_event record;
  v_account_id uuid;
  v_existing public.transactions%rowtype;
  v_pending_id uuid;
  v_tx_id uuid;
  v_added integer := 0;
  v_modified integer := 0;
  v_removed integer := 0;
  v_unchanged integer := 0;
  v_amount bigint;
  v_date date;
  v_desc text;
  v_status public.transaction_status;
  v_changed boolean;
begin
  select * into v_batch from public.sync_batches b where b.id = p_batch_id;
  if v_batch.id is null then
    raise exception 'batch_not_found' using errcode = 'no_data_found';
  end if;
  select * into v_connection from public.connections c where c.id = v_batch.connection_id for update;
  if v_batch.status = 'applied' then
    return query select 0, 0, 0, 0, v_batch.end_cursor; return;
  end if;
  if v_batch.status <> 'staged' then
    raise exception 'batch_not_staged' using errcode = 'invalid_parameter_value';
  end if;
  if v_connection.sync_cursor is distinct from v_batch.start_cursor then
    raise exception 'cursor_mismatch' using errcode = 'serialization_failure';
  end if;

  for v_event in
    select e.* from public.raw_events e where e.sync_batch_id = p_batch_id order by e.sequence
  loop
    if v_event.operation = 'removed' then
      update public.transactions t
        set removed_at = coalesce(t.removed_at, v_event.received_at), revision = t.revision + 1
        where t.business_id = v_connection.business_id and t.source_type = 'synthetic_provider'
          and t.external_id = v_event.external_transaction_id and t.removed_at is null
        returning t.id into v_tx_id;
      if v_tx_id is not null then
        insert into public.transaction_sources (transaction_id, raw_event_id) values (v_tx_id, v_event.id);
        v_removed := v_removed + 1;
      else
        v_unchanged := v_unchanged + 1;
      end if;
      continue;
    end if;
    if v_event.canonical is null then
      raise exception 'canonical_missing:%', v_event.sequence using errcode = 'invalid_parameter_value';
    end if;
    select a.id into v_account_id from public.accounts a
      where a.connection_id = v_connection.id and a.business_id = v_connection.business_id
        and a.external_account_id = v_event.canonical ->> 'external_account_id';
    if v_account_id is null then
      raise exception 'unknown_account:%', v_event.sequence using errcode = 'invalid_parameter_value';
    end if;
    v_amount := (v_event.canonical ->> 'amount_minor')::bigint;
    v_date := (v_event.canonical ->> 'posted_date')::date;
    v_desc := left(v_event.canonical ->> 'description', 500);
    v_status := (v_event.canonical ->> 'status')::public.transaction_status;
    v_pending_id := null;
    if (v_event.canonical ->> 'pending_external_id') is not null then
      select t.id into v_pending_id from public.transactions t
        where t.business_id = v_connection.business_id and t.source_type = 'synthetic_provider'
          and t.external_id = v_event.canonical ->> 'pending_external_id';
    end if;
    select * into v_existing from public.transactions t
      where t.account_id = v_account_id and t.source_type = 'synthetic_provider'
        and t.external_id = v_event.external_transaction_id;
    if v_existing.id is null then
      insert into public.transactions (business_id, account_id, source_type, external_id, amount_minor, posted_date, description, status, pending_transaction_id)
        values (v_connection.business_id, v_account_id, 'synthetic_provider', v_event.external_transaction_id, v_amount, v_date, v_desc, v_status, v_pending_id)
        returning id into v_tx_id;
      v_added := v_added + 1;
    else
      v_changed := v_existing.amount_minor <> v_amount or v_existing.posted_date <> v_date
        or v_existing.description <> v_desc or v_existing.status <> v_status
        or v_existing.pending_transaction_id is distinct from v_pending_id or v_existing.removed_at is not null;
      if v_changed then
        update public.transactions t
          set amount_minor = v_amount, posted_date = v_date, description = v_desc, status = v_status,
              pending_transaction_id = v_pending_id, removed_at = null, revision = t.revision + 1
          where t.id = v_existing.id;
        v_modified := v_modified + 1;
      else
        v_unchanged := v_unchanged + 1;
      end if;
      v_tx_id := v_existing.id;
    end if;
    insert into public.transaction_sources (transaction_id, raw_event_id) values (v_tx_id, v_event.id);
  end loop;

  update public.sync_batches b set status = 'applied', applied_at = now() where b.id = p_batch_id;
  update public.connections c set sync_cursor = v_batch.end_cursor where c.id = v_connection.id;
  return query select v_added, v_modified, v_removed, v_unchanged, v_batch.end_cursor;
end $$;

comment on function public.apply_sync_batch(uuid) is
  'Applies one staged sync batch atomically and commits its end cursor. Serialized per connection; a cursor mismatch means the caller must restart from the committed cursor.';

revoke all on function public.apply_sync_batch(uuid) from public, anon, authenticated;
grant execute on function public.apply_sync_batch(uuid) to service_role;
