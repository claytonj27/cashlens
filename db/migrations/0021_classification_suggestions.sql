-- 0021: suggestions a person reviews, and the provenance of what they decide (milestone 10).
--
-- The rules leave rows alone rather than guess at them, which is correct and leaves the owner
-- with a pile of uncategorised entries. This is the table that lets a model propose an answer
-- WITHOUT the proposal counting as one: a suggestion is stored, shown, and changes no figure.
-- Only a person's decision writes a `category_overrides` row, and only that row is read back
-- into the analysis.
--
-- `unresolved` is a first-class outcome, not a failure. A bare deposit names nobody, and the
-- honest answer is a question with alternatives rather than a category. The check constraints
-- below make that structural: a suggested row must name a category, an unresolved row must not,
-- and must carry a question or some alternatives instead.
--
-- One open suggestion per transaction per workspace, enforced by a partial unique index, so a
-- second run cannot stack a competing proposal on a row somebody is already looking at.

create table public.classification_suggestions (
  id uuid primary key default gen_random_uuid(),
  run_id uuid not null references public.ai_runs (id) on delete cascade,
  workspace_id uuid not null,
  business_id uuid not null,
  transaction_id uuid not null,
  -- The revision the proposal was made against. A decision on a suggestion whose row has since
  -- been revised is refused rather than applied to data nobody reviewed.
  transaction_revision integer not null check (transaction_revision >= 1),
  prompt_version text not null check (length(prompt_version) between 1 and 40),
  outcome text not null check (outcome in ('suggested', 'unresolved')),
  category text check (category is null or category ~ '^[a-z_]{1,40}$'),
  alternatives jsonb not null default '[]'::jsonb,
  rationale text check (length(rationale) <= 400),
  question text check (length(question) <= 280),
  evidence jsonb not null default '[]'::jsonb,
  status text not null default 'open' check (status in ('open', 'accepted', 'rejected', 'superseded')),
  decided_by_kind text check (decided_by_kind in ('user', 'visitor')),
  decided_by_id uuid,
  decided_at timestamptz,
  created_at timestamptz not null default now(),
  foreign key (workspace_id, business_id) references public.workspaces (id, business_id) on delete cascade,
  foreign key (transaction_id, business_id) references public.transactions (id, business_id) on delete cascade,
  -- A category is what "suggested" means; its absence is what "unresolved" means.
  constraint suggestion_outcome_matches_category check (
    (outcome = 'suggested' and category is not null)
    or (outcome = 'unresolved' and category is null
        and (question is not null or jsonb_array_length(alternatives) > 0))
  ),
  constraint suggestion_decided_together check (
    (status = 'open' and decided_at is null)
    or (status <> 'open' and (decided_at is not null or status = 'superseded'))
  )
);
comment on table public.classification_suggestions is
  'What a model proposed for a transaction the rules could not place. Stored and shown; never applied. Only a person''s decision writes a category_overrides row, and only that changes a figure.';
comment on column public.classification_suggestions.outcome is
  'suggested = a category with evidence. unresolved = the model declined to guess and asked instead, which is a first-class answer rather than a failure.';

create unique index classification_suggestions_one_open
  on public.classification_suggestions (workspace_id, transaction_id)
  where status = 'open';
create index classification_suggestions_for_review
  on public.classification_suggestions (workspace_id, status, created_at desc);
create index classification_suggestions_by_run on public.classification_suggestions (run_id);

alter table public.classification_suggestions enable row level security;
revoke all on table public.classification_suggestions from anon, authenticated;
grant select, insert, update, delete on table public.classification_suggestions to service_role;

-- ------------------------------------------------ provenance on the decision --
-- What the row was categorised as before this decision, so the history reads as a sequence of
-- changes rather than a list of end states, and so an interface can say what a correction
-- actually moved. Null previous_category means it was uncategorised, which is the usual case.
alter table public.category_overrides
  add column previous_category text check (previous_category is null or previous_category ~ '^[a-z_]{1,40}$'),
  add column suggestion_id uuid references public.classification_suggestions (id) on delete set null,
  add column run_id uuid references public.ai_runs (id) on delete set null;

comment on column public.category_overrides.previous_category is
  'The category in effect when this decision was made. Null means the rules had left it uncategorised.';
comment on column public.category_overrides.suggestion_id is
  'The proposal this decision answered, when there was one. Null for a correction made directly on a transaction.';

-- --------------------------------------------------------------- deletion --
-- Same function as 0018 with suggestions added to the AI rows that go first, before the
-- transactions they describe.
create or replace function public.request_business_deletion(p_business_id uuid, p_user_id uuid)
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

  insert into public.storage_cleanup (bucket, object_path, business_id, reason)
    select i.object_bucket, i.object_path, i.business_id, 'business_deleted'
    from public.document_imports i
    where i.business_id = p_business_id and i.object_deleted_at is null
    on conflict (object_path) do nothing;
  get diagnostics v_queued = row_count;

  for v_import in
    select i.id from public.document_imports i
    where i.business_id = p_business_id and i.status in ('awaiting_upload', 'staged', 'failed')
  loop
    perform public.end_import(v_import.id, 'business_deleted');
    v_ended := v_ended + 1;
  end loop;

  -- AI rows first, suggestions included: they hold the owner's figures and their decisions,
  -- so they must not outlive the transactions they were derived from.
  delete from public.classification_suggestions s where s.business_id = p_business_id;
  delete from public.ai_runs r where r.business_id = p_business_id;
  delete from public.category_overrides o where o.business_id = p_business_id;

  delete from public.transactions t where t.business_id = p_business_id;
  get diagnostics v_removed = row_count;
  delete from public.balance_snapshots s where s.business_id = p_business_id;
  delete from public.document_imports i where i.business_id = p_business_id;
  delete from public.accounts a where a.business_id = p_business_id;
  delete from public.connections c where c.business_id = p_business_id;

  update public.businesses b set data_removed_at = now() where b.id = p_business_id;
  return query select false, v_business.name, v_queued, v_ended, v_removed, now();
end $$;

notify pgrst, 'reload schema';
