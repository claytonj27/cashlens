-- 0009: text-PDF statement imports, decision provenance and lease-guarded staging (milestone 7).
-- Imports may now be PDFs; staged rows keep their page; every review decision
-- records whether it came from the system or the owner; staging and failure
-- state changes run inside the database under the job lease so a stale
-- worker can never overwrite a replacement's work; confirmation refuses rows
-- that still need an owner decision and lets a linked row lend its source
-- reference to the existing transaction. See docs/decisions/0009-pdf-statement-imports.md.

alter table public.document_imports drop constraint document_imports_format_check;
alter table public.document_imports add constraint document_imports_format_check check (format in ('csv', 'pdf'));
alter table public.document_imports drop constraint document_imports_object_path_check;
alter table public.document_imports add constraint document_imports_object_path_check
  check (object_path ~ '^[0-9a-f-]{36}/[0-9a-f-]{36}\.(csv|pdf)$');

alter table public.import_rows add column page_number integer check (page_number >= 1);
alter table public.import_rows add column decision_by text not null default 'system' check (decision_by in ('system', 'owner'));
comment on column public.import_rows.decision_by is 'Whether the current decision is the system default or was set by the owner. Rows flagged needs_decision block confirmation until the owner decides.';

do $$ begin
  if to_regclass('storage.buckets') is not null then
    update storage.buckets
      set allowed_mime_types = array['text/csv', 'text/plain', 'application/pdf'], file_size_limit = 10485760, public = false
      where id = 'statements';
  end if;
end $$;

-- ------------------------------------------------ lease-guarded mutations --
-- The caller names its job and lease token; the row lock on the job makes the
-- ownership check and the writes one atomic unit, so a worker whose lease was
-- re-acquired by another finds its writes refused rather than applied late.
create function public.assert_lease(p_job_id uuid, p_import_id uuid, p_lease_token uuid) returns void
language plpgsql as $$
declare
  v_job public.import_jobs%rowtype;
begin
  select * into v_job from public.import_jobs j where j.id = p_job_id for update;
  if v_job.id is null or v_job.import_id <> p_import_id or v_job.state <> 'leased'
     or v_job.lease_token is distinct from p_lease_token or v_job.lease_expires_at <= now() then
    raise exception 'lease_lost' using errcode = 'lock_not_available';
  end if;
end $$;

create function public.stage_import(
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

create function public.fail_import(
  p_import_id uuid, p_job_id uuid, p_lease_token uuid, p_error_code text, p_error_message text
) returns boolean
language plpgsql security definer set search_path = public, pg_temp as $$
#variable_conflict use_column
declare
  v_updated integer;
begin
  perform public.assert_lease(p_job_id, p_import_id, p_lease_token);
  update public.document_imports i
    set status = 'failed', error_code = p_error_code, error_message = left(p_error_message, 300)
    where i.id = p_import_id and i.status in ('awaiting_upload', 'staged');
  get diagnostics v_updated = row_count;
  delete from public.import_rows r where r.import_id = p_import_id;
  return v_updated = 1;
end $$;

-- ------------------------------------------------------- confirmation --
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
        v_import.business_id, case when v_import.format = 'pdf' then 'pdf_import' else 'csv_import' end,
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
      values (v_import.business_id, v_account_id, case when v_import.format = 'pdf' then 'pdf_import' else 'csv_import' end,
              v_ref, v_amount, v_date, v_desc, 'posted')
      returning id into v_tx_id;
    insert into public.transaction_sources (transaction_id, import_row_id) values (v_tx_id, v_row.id);
    v_created := v_created + 1;
  end loop;

  v_balance := v_import.summary -> 'balance';
  if v_balance is not null and (v_balance ->> 'closing_minor') is not null then
    insert into public.balance_snapshots (business_id, account_id, kind, provenance, source_type, amount_minor, as_of)
      values (v_import.business_id, v_account_id, 'statement_closing', 'bank_observed',
              case when v_import.format = 'pdf' then 'pdf_import' else 'csv_import' end,
              (v_balance ->> 'closing_minor')::bigint, ((v_balance ->> 'period_end')::date + time '23:59:59') at time zone 'UTC')
      on conflict (account_id, kind, provenance, as_of) do update set amount_minor = excluded.amount_minor;
    if (v_balance ->> 'opening_minor') is not null then
      insert into public.balance_snapshots (business_id, account_id, kind, provenance, source_type, amount_minor, as_of)
        values (v_import.business_id, v_account_id, 'statement_opening', 'bank_observed',
                case when v_import.format = 'pdf' then 'pdf_import' else 'csv_import' end,
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

revoke all on function public.assert_lease(uuid, uuid, uuid) from public, anon, authenticated, service_role;
revoke all on function public.stage_import(uuid, uuid, uuid, jsonb, jsonb) from public, anon, authenticated;
revoke all on function public.fail_import(uuid, uuid, uuid, text, text) from public, anon, authenticated;
grant execute on function public.stage_import(uuid, uuid, uuid, jsonb, jsonb) to service_role;
grant execute on function public.fail_import(uuid, uuid, uuid, text, text) to service_role;
