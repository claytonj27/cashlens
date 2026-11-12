-- 0006: private statement imports (milestone 6).
-- Owner-uploaded CSV statements live in a private Storage bucket; the API
-- stages parsed rows for review and publishes them into the canonical
-- transactions atomically through confirm_import. Import records, jobs and
-- rows are service-role only: every client request is mediated by the API,
-- which checks membership itself. See docs/decisions/0008-statement-imports.md.

-- ---------------------------------------------------------------- types --
create type public.import_status as enum ('awaiting_upload', 'staged', 'confirmed', 'cancelled', 'failed');
create type public.import_job_kind as enum ('parse', 'confirm');
create type public.import_job_state as enum ('leased', 'succeeded', 'failed');
-- A statement's opening balance is a dated observation like its closing balance.
alter type public.balance_kind add value if not exists 'statement_opening';

-- --------------------------------------------------------------- tables --
create table public.document_imports (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null,
  business_data_mode public.data_mode not null check (business_data_mode = 'private'),
  uploaded_by uuid not null references auth.users (id) on delete cascade,
  account_id uuid,
  proposed_account jsonb,
  format text not null check (format in ('csv')),
  status public.import_status not null default 'awaiting_upload',
  object_bucket text not null default 'statements' check (object_bucket = 'statements'),
  object_path text not null unique check (object_path ~ '^[0-9a-f-]{36}/[0-9a-f-]{36}\.csv$'),
  object_deleted_at timestamptz,
  file_label text not null check (file_label ~ '^[A-Za-z0-9][A-Za-z0-9 ._()-]{0,79}$'),
  declared_bytes bigint not null check (declared_bytes between 1 and 10485760),
  byte_size bigint check (byte_size between 1 and 10485760),
  content_hash text check (content_hash ~ '^[0-9a-f]{64}$'),
  parser_version text check (length(parser_version) between 1 and 20),
  mapping jsonb,
  summary jsonb,
  draft_revision integer not null default 0 check (draft_revision >= 0),
  confirmed_revision integer,
  confirmed_at timestamptz,
  cancelled_at timestamptz,
  error_code text check (error_code ~ '^[a-z_]{1,60}$'),
  error_message text check (length(error_message) <= 300),
  expires_at timestamptz not null default now() + interval '24 hours',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (business_id, business_data_mode) references public.businesses (id, data_mode) on delete cascade,
  foreign key (account_id, business_id) references public.accounts (id, business_id) on delete cascade,
  unique (id, business_id),
  constraint document_imports_account_choice check (num_nonnulls(account_id, proposed_account) >= 1),
  constraint document_imports_confirmed_shape check (
    (status = 'confirmed') = (confirmed_at is not null and confirmed_revision is not null and account_id is not null)
  ),
  constraint document_imports_cancelled_shape check ((status = 'cancelled') = (cancelled_at is not null))
);
comment on table public.document_imports is 'One uploaded statement file and its review state. Only private businesses; the original object is deleted at confirmation, cancellation or failure.';
comment on column public.document_imports.proposed_account is 'name, account_type and optional mask for an account created at confirmation; null when an existing account was chosen.';
comment on column public.document_imports.expires_at is 'When an unconfirmed upload is considered abandoned. No scheduled sweep exists yet (M8); the API sweeps opportunistically.';
create index document_imports_by_business on public.document_imports (business_id, created_at desc);
create unique index document_imports_confirmed_hash
  on public.document_imports (business_id, account_id, content_hash) where status = 'confirmed';

create table public.import_jobs (
  id uuid primary key default gen_random_uuid(),
  import_id uuid not null references public.document_imports (id) on delete cascade,
  kind public.import_job_kind not null,
  state public.import_job_state not null default 'leased',
  idempotency_key text not null unique check (length(idempotency_key) between 1 and 120),
  attempts integer not null default 1 check (attempts >= 1),
  max_attempts integer not null default 3 check (max_attempts >= 1),
  lease_expires_at timestamptz not null,
  safe_error text check (length(safe_error) <= 300),
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  check ((state = 'leased') = (finished_at is null))
);
comment on table public.import_jobs is 'Leased, bounded processing steps (parse, confirm). One live lease per idempotency key; attempts are capped.';
create index import_jobs_by_import on public.import_jobs (import_id, started_at desc);

create table public.import_rows (
  id uuid primary key default gen_random_uuid(),
  import_id uuid not null references public.document_imports (id) on delete cascade,
  row_index integer not null check (row_index >= 0),
  source_locator text not null check (length(source_locator) between 1 and 40),
  raw jsonb not null,
  extracted jsonb not null,
  reviewed jsonb,
  flags text[] not null default '{}',
  decision text not null default 'keep' check (decision in ('keep', 'exclude', 'link_existing', 'replace_existing')),
  linked_transaction_id uuid references public.transactions (id) on delete set null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (import_id, row_index),
  constraint import_rows_link_requires_target check (decision not in ('link_existing', 'replace_existing') or linked_transaction_id is not null)
);
comment on table public.import_rows is 'Staged rows with the raw cells, the deterministic extraction, owner corrections and validation flags. Retained after confirmation as provenance.';

create trigger document_imports_set_updated_at before update on public.document_imports
  for each row execute function public.set_updated_at();
create trigger import_rows_set_updated_at before update on public.import_rows
  for each row execute function public.set_updated_at();

-- Import rows become a second origin for canonical transactions.
alter table public.transaction_sources
  add column import_row_id uuid references public.import_rows (id) on delete restrict;
alter table public.transaction_sources drop constraint transaction_sources_exactly_one_origin;
alter table public.transaction_sources
  add constraint transaction_sources_exactly_one_origin check (num_nonnulls(raw_event_id, import_row_id) = 1);
create index transaction_sources_by_transaction on public.transaction_sources (transaction_id);
create index transaction_sources_by_import_row on public.transaction_sources (import_row_id) where import_row_id is not null;

-- ------------------------------------------------------------ job leases --
create function public.acquire_import_job(
  p_import_id uuid, p_kind public.import_job_kind, p_idempotency_key text, p_lease_seconds integer default 120
) returns table (job_id uuid, state public.import_job_state, attempts integer)
language plpgsql security definer set search_path = public, pg_temp as $$
#variable_conflict use_column
declare
  v_job public.import_jobs%rowtype;
begin
  perform pg_advisory_xact_lock(hashtext('import_job:' || p_idempotency_key));
  select * into v_job from public.import_jobs j where j.idempotency_key = p_idempotency_key;
  if v_job.id is null then
    insert into public.import_jobs (import_id, kind, idempotency_key, lease_expires_at)
      values (p_import_id, p_kind, p_idempotency_key, now() + make_interval(secs => p_lease_seconds))
      returning * into v_job;
  elsif v_job.state = 'succeeded' then
    null; -- caller returns the recorded outcome; nothing runs again
  elsif v_job.state = 'leased' and v_job.lease_expires_at > now() then
    raise exception 'job_in_progress' using errcode = 'lock_not_available';
  elsif v_job.attempts >= v_job.max_attempts then
    raise exception 'attempts_exhausted' using errcode = 'program_limit_exceeded';
  else
    update public.import_jobs j
      set state = 'leased', attempts = j.attempts + 1, finished_at = null, safe_error = null,
          lease_expires_at = now() + make_interval(secs => p_lease_seconds)
      where j.id = v_job.id returning * into v_job;
  end if;
  return query select v_job.id, v_job.state, v_job.attempts;
end $$;

create function public.finish_import_job(p_job_id uuid, p_state public.import_job_state, p_safe_error text default null)
returns void language sql security definer set search_path = public, pg_temp as $$
  update public.import_jobs set state = p_state, finished_at = now(), safe_error = left(p_safe_error, 300)
  where id = p_job_id and state = 'leased';
$$;

-- ------------------------------------------------------- confirmation --
-- Publishes the staged rows of one import into canonical transactions in a
-- single transaction. Repeating a confirmed revision is a no-op that returns
-- the recorded result; any error leaves nothing behind.
create function public.confirm_import(p_import_id uuid, p_user_id uuid, p_expected_revision integer)
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

  v_account_id := v_import.account_id;
  if v_account_id is null then
    insert into public.accounts (business_id, source_type, name, account_type, mask)
      values (
        v_import.business_id, 'csv_import',
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
      insert into public.transaction_sources (transaction_id, import_row_id) values (v_target.id, v_row.id);
      continue;
    end if;
    if v_ref is not null and exists (
      select 1 from public.transactions t
      where t.account_id = v_account_id and t.source_type = 'csv_import' and t.external_id = v_ref
    ) then
      raise exception 'duplicate_source_reference:%', v_row.row_index using errcode = 'unique_violation';
    end if;
    insert into public.transactions (business_id, account_id, source_type, external_id, amount_minor, posted_date, description, status)
      values (v_import.business_id, v_account_id, 'csv_import', v_ref, v_amount, v_date, v_desc, 'posted')
      returning id into v_tx_id;
    insert into public.transaction_sources (transaction_id, import_row_id) values (v_tx_id, v_row.id);
    v_created := v_created + 1;
  end loop;

  v_balance := v_import.summary -> 'balance';
  if v_balance is not null and (v_balance ->> 'closing_minor') is not null then
    insert into public.balance_snapshots (business_id, account_id, kind, provenance, source_type, amount_minor, as_of)
      values (v_import.business_id, v_account_id, 'statement_closing', 'bank_observed', 'csv_import',
              (v_balance ->> 'closing_minor')::bigint, ((v_balance ->> 'period_end')::date + time '23:59:59') at time zone 'UTC')
      on conflict (account_id, kind, provenance, as_of) do update set amount_minor = excluded.amount_minor;
    if (v_balance ->> 'opening_minor') is not null then
      insert into public.balance_snapshots (business_id, account_id, kind, provenance, source_type, amount_minor, as_of)
        values (v_import.business_id, v_account_id, 'statement_opening', 'bank_observed', 'csv_import',
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

revoke all on function public.acquire_import_job(uuid, public.import_job_kind, text, integer) from public, anon, authenticated;
revoke all on function public.finish_import_job(uuid, public.import_job_state, text) from public, anon, authenticated;
revoke all on function public.confirm_import(uuid, uuid, integer) from public, anon, authenticated;
grant execute on function public.acquire_import_job(uuid, public.import_job_kind, text, integer) to service_role;
grant execute on function public.finish_import_job(uuid, public.import_job_state, text) to service_role;
grant execute on function public.confirm_import(uuid, uuid, integer) to service_role;

-- ------------------------------------------------ privileges and RLS --
revoke all on table public.document_imports, public.import_jobs, public.import_rows from anon, authenticated;
grant select, insert, update, delete on table public.document_imports, public.import_jobs, public.import_rows to service_role;
alter table public.document_imports enable row level security;
alter table public.import_jobs enable row level security;
alter table public.import_rows enable row level security;
-- No client policies on purpose: the API mediates every import request and
-- checks membership itself; statement contents never reach client roles.

-- ---------------------------------------------------------- storage --
-- The private bucket for uploaded statements. Uploads use signed upload URLs
-- issued by the API, downloads happen only server-side with the service role,
-- so client roles get no storage policies at all. Guarded so the migration
-- history also applies to a plain PostgreSQL cluster without Supabase Storage.
do $$ begin
  if to_regclass('storage.buckets') is not null then
    insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
      values ('statements', 'statements', false, 10485760, array['text/csv', 'text/plain'])
      on conflict (id) do update
        set public = false, file_size_limit = 10485760, allowed_mime_types = array['text/csv', 'text/plain'];
  end if;
end $$;
