-- 0010: cast the import format to the source_type enum inside confirm_import (milestone 7 fix).
-- 0009 selected the source type with a CASE over string literals, whose result is text;
-- PostgreSQL has no assignment cast from text to an enum, so every confirmation of a
-- staged import failed on the development project while the function compiled cleanly.
-- Found by the development-project checks; the function is otherwise unchanged.

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
        where t.account_id = v_account_id and t.source_type = v_target.source_type and t.external_id = v_ref
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
