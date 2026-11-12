-- 0012: one source reference per account across import formats, and atomic cancellation (milestone 7 review).
-- Three defects found in the pre-merge review of the M7 branch:
--   * confirm_import checked reference uniqueness across both import formats before inserting a
--     transaction but only within the target's own format before lending a reference to one, so a
--     manual link could leave a CSV and a PDF transaction sharing one reference in an account.
--     Nothing enforced the rule either, so two confirmations running at once could both pass their
--     checks; the index below makes it an invariant and the account lock makes the checks decide.
--   * cancellation changed the database in three separate statements outside any transaction, and
--     left an active lease alone, so a parse that was still running could stage rows into a
--     cancelled import.
-- Inspected on the development project before this ran: no account held a reference shared by more
-- than one imported transaction, so no existing row is rewritten by this migration.

create unique index transactions_import_reference_identity
  on public.transactions (account_id, external_id)
  where external_id is not null and source_type in ('csv_import', 'pdf_import');
comment on index public.transactions_import_reference_identity is
  'A source reference identifies at most one imported transaction per account, whatever the file format it arrived in. Provider and manual sources keep their own identity index.';

-- ------------------------------------------------------------ confirmation --
create or replace function public.confirm_import(p_import_id uuid, p_user_id uuid, p_expected_revision integer)
returns table (
  created_count integer, linked_count integer, replaced_count integer, excluded_count integer,
  account_id uuid, already_confirmed boolean
)
language plpgsql security definer set search_path = public, pg_temp as $$
#variable_conflict use_column
declare
  v_import public.document_imports%rowtype;
  v_account_id uuid;
  v_row record;
  v_created integer := 0;
  v_linked integer := 0;
  v_replaced integer := 0;
  v_excluded integer := 0;
  v_date date;
  v_amount bigint;
  v_desc text;
  v_ref text;
  v_tx_id uuid;
  v_target public.transactions%rowtype;
  v_balance jsonb;
  v_result jsonb;
begin
  select * into v_import from public.document_imports i where i.id = p_import_id for update;
  if v_import.id is null or not exists (
    select 1 from public.business_memberships m where m.business_id = v_import.business_id and m.user_id = p_user_id
  ) then
    raise exception 'not_found' using errcode = 'no_data_found';
  end if;
  if v_import.status = 'confirmed' then
    if v_import.confirmed_revision = p_expected_revision then
      v_result := v_import.summary -> 'result';
      return query select (v_result ->> 'created')::integer, (v_result ->> 'linked')::integer,
        (v_result ->> 'replaced')::integer, (v_result ->> 'excluded')::integer, v_import.account_id, true;
      return;
    end if;
    raise exception 'revision_conflict' using errcode = 'serialization_failure';
  end if;
  if v_import.status <> 'staged' then
    raise exception 'invalid_state' using errcode = 'invalid_parameter_value';
  end if;
  if v_import.draft_revision <> p_expected_revision then
    raise exception 'revision_conflict' using errcode = 'serialization_failure';
  end if;
  if exists (
    select 1 from public.import_rows r
    where r.import_id = p_import_id and 'needs_decision' = any(r.flags) and r.decision_by = 'system' and r.decision <> 'exclude'
  ) then
    raise exception 'undecided_rows' using errcode = 'invalid_parameter_value';
  end if;

  v_account_id := v_import.account_id;
  if v_account_id is null then
    insert into public.accounts (business_id, source_type, name, account_type, mask)
      values (
        v_import.business_id,
        (case when v_import.format = 'pdf' then 'pdf_import' else 'csv_import' end)::public.source_type,
        v_import.proposed_account ->> 'name',
        (v_import.proposed_account ->> 'account_type')::public.account_type,
        nullif(v_import.proposed_account ->> 'mask', '')
      ) returning id into v_account_id;
  end if;

  -- Confirmations into the same account are serialized, so the reference checks below
  -- decide the outcome instead of racing each other into the unique index.
  perform pg_advisory_xact_lock(hashtext('confirm_account:' || v_account_id::text));

  for v_row in
    select r.* from public.import_rows r where r.import_id = p_import_id order by r.row_index
  loop
    if v_row.decision = 'exclude' then
      v_excluded := v_excluded + 1;
      continue;
    end if;
    v_date := coalesce(v_row.reviewed ->> 'posted_date', v_row.extracted ->> 'posted_date')::date;
    v_amount := coalesce(v_row.reviewed ->> 'amount_minor', v_row.extracted ->> 'amount_minor')::bigint;
    v_desc := left(btrim(coalesce(v_row.reviewed ->> 'description', v_row.extracted ->> 'description')), 500);
    v_ref := nullif(left(coalesce(v_row.extracted ->> 'external_ref', ''), 120), '');
    if v_date is null or v_amount is null or v_amount = 0 or v_desc is null or v_desc = '' then
      raise exception 'blocking_rows:%', v_row.row_index using errcode = 'invalid_parameter_value';
    end if;
    if v_row.decision in ('link_existing', 'replace_existing') then
      select * into v_target from public.transactions t
        where t.id = v_row.linked_transaction_id and t.business_id = v_import.business_id
          and t.account_id = v_account_id and t.removed_at is null;
      if v_target.id is null then
        raise exception 'link_target_missing:%', v_row.row_index using errcode = 'invalid_parameter_value';
      end if;
      if v_row.decision = 'replace_existing' then
        update public.transactions t
          set amount_minor = v_amount, posted_date = v_date, description = v_desc, revision = t.revision + 1
          where t.id = v_target.id;
        v_replaced := v_replaced + 1;
      else
        v_linked := v_linked + 1;
      end if;
      -- A linked row that carries a trusted reference lends it to the existing transaction,
      -- so a later replay of the referenced file matches by identity instead of by shape.
      if v_ref is not null and v_target.external_id is null and not exists (
        select 1 from public.transactions t
        where t.account_id = v_account_id and t.source_type in ('csv_import', 'pdf_import') and t.external_id = v_ref
      ) then
        update public.transactions t set external_id = v_ref where t.id = v_target.id;
      end if;
      insert into public.transaction_sources (transaction_id, import_row_id) values (v_target.id, v_row.id);
      continue;
    end if;
    if v_ref is not null and exists (
      select 1 from public.transactions t
      where t.account_id = v_account_id and t.source_type in ('csv_import', 'pdf_import') and t.external_id = v_ref
    ) then
      raise exception 'duplicate_source_reference:%', v_row.row_index using errcode = 'unique_violation';
    end if;
    insert into public.transactions (business_id, account_id, source_type, external_id, amount_minor, posted_date, description, status)
      values (v_import.business_id, v_account_id,
              (case when v_import.format = 'pdf' then 'pdf_import' else 'csv_import' end)::public.source_type,
              v_ref, v_amount, v_date, v_desc, 'posted')
      returning id into v_tx_id;
    insert into public.transaction_sources (transaction_id, import_row_id) values (v_tx_id, v_row.id);
    v_created := v_created + 1;
  end loop;

  v_balance := v_import.summary -> 'balance';
  if v_balance is not null and (v_balance ->> 'closing_minor') is not null then
    insert into public.balance_snapshots (business_id, account_id, kind, provenance, source_type, amount_minor, as_of)
      values (v_import.business_id, v_account_id, 'statement_closing', 'bank_observed',
              (case when v_import.format = 'pdf' then 'pdf_import' else 'csv_import' end)::public.source_type,
              (v_balance ->> 'closing_minor')::bigint, ((v_balance ->> 'period_end')::date + time '23:59:59') at time zone 'UTC')
      on conflict (account_id, kind, provenance, as_of) do update set amount_minor = excluded.amount_minor;
    if (v_balance ->> 'opening_minor') is not null then
      insert into public.balance_snapshots (business_id, account_id, kind, provenance, source_type, amount_minor, as_of)
        values (v_import.business_id, v_account_id, 'statement_opening', 'bank_observed',
                (case when v_import.format = 'pdf' then 'pdf_import' else 'csv_import' end)::public.source_type,
                (v_balance ->> 'opening_minor')::bigint, ((v_balance ->> 'period_start')::date + time '00:00:00') at time zone 'UTC')
        on conflict (account_id, kind, provenance, as_of) do update set amount_minor = excluded.amount_minor;
    end if;
  end if;

  v_result := jsonb_build_object('created', v_created, 'linked', v_linked, 'replaced', v_replaced, 'excluded', v_excluded);
  update public.document_imports i
    set status = 'confirmed', confirmed_at = now(), confirmed_revision = p_expected_revision,
        account_id = v_account_id, summary = coalesce(i.summary, '{}'::jsonb) || jsonb_build_object('result', v_result)
    where i.id = p_import_id;
  return query select v_created, v_linked, v_replaced, v_excluded, v_account_id, false;
end $$;

-- ------------------------------------------------------------ cancellation --
-- One transaction ends the import, invalidates any lease still open and removes the staged
-- rows. A worker that returns from parsing afterwards finds its token rejected by assert_lease
-- and the import in a state stage_import refuses, so it cannot write rows into a cancelled
-- import. The uploaded object is removed by the caller afterwards and recorded separately,
-- because Storage is a different system that can fail on its own; calling this again retries
-- that removal without changing anything else.
create function public.cancel_import(p_import_id uuid, p_user_id uuid)
returns table (already_cancelled boolean, rows_removed integer, object_path text, object_deleted_at timestamptz)
language plpgsql security definer set search_path = public, pg_temp as $$
#variable_conflict use_column
declare
  v_import public.document_imports%rowtype;
  v_rows integer := 0;
begin
  -- Job rows first, then the import row: the same order stage_import takes them, so the two
  -- can never hold one lock each and wait for the other.
  perform 1 from public.import_jobs j where j.import_id = p_import_id order by j.id for update;
  select * into v_import from public.document_imports i where i.id = p_import_id for update;
  if v_import.id is null or not exists (
    select 1 from public.business_memberships m where m.business_id = v_import.business_id and m.user_id = p_user_id
  ) then
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
        lease_expires_at = now(), safe_error = 'import cancelled'
    where j.import_id = p_import_id and j.state = 'leased';
  delete from public.import_rows r where r.import_id = p_import_id;
  get diagnostics v_rows = row_count;
  update public.document_imports i
    set status = 'cancelled', cancelled_at = now()
    where i.id = p_import_id;
  return query select false, v_rows, v_import.object_path, v_import.object_deleted_at;
end $$;

revoke all on function public.cancel_import(uuid, uuid) from public, anon, authenticated;
grant execute on function public.cancel_import(uuid, uuid) to service_role;
comment on function public.cancel_import(uuid, uuid) is
  'Ends an import, invalidates any lease still open and removes its staged rows in one transaction. Returns the object to delete so the caller can record the deletion separately; safe to call again.';

notify pgrst, 'reload schema';
