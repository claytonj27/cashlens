-- 0002: CashLens data foundation (milestone 3).
-- Businesses and memberships, demo visitors and workspaces, provider
-- connections with ingestion provenance, accounts, transactions and dated
-- balance snapshots. Every table gets Row Level Security, explicit grants and
-- a demo-immutability guard. See docs/data-contract.md.

-- ---------------------------------------------------------------- types --
create type public.data_mode as enum ('demo', 'private');
create type public.membership_role as enum ('owner');
create type public.account_type as enum ('checking', 'savings');
create type public.source_type as enum ('synthetic_provider', 'csv_import', 'pdf_import', 'bank_connector');
create type public.transaction_status as enum ('pending', 'posted');
create type public.sync_operation as enum ('added', 'modified', 'removed');
create type public.sync_batch_status as enum ('staged', 'applied', 'failed');
create type public.balance_kind as enum ('current', 'available', 'statement_closing');
create type public.provenance as enum ('bank_observed', 'inferred', 'owner_confirmed', 'projected');

-- --------------------------------------------------------------- tables --
create table public.businesses (
  id uuid primary key default gen_random_uuid(),
  slug text not null unique check (slug ~ '^[a-z0-9][a-z0-9-]{2,39}$'),
  name text not null check (length(name) between 1 and 120),
  industry text not null check (length(industry) between 1 and 80),
  currency char(3) not null default 'USD' check (currency = 'USD'),
  data_mode public.data_mode not null,
  created_at timestamptz not null default now(),
  unique (id, data_mode)
);
comment on table public.businesses is 'Demo businesses are immutable shared seeds; private businesses are reachable only through memberships.';

create table public.business_memberships (
  business_id uuid not null,
  business_data_mode public.data_mode not null check (business_data_mode = 'private'),
  user_id uuid not null references auth.users (id) on delete cascade,
  role public.membership_role not null default 'owner',
  created_at timestamptz not null default now(),
  primary key (business_id, user_id),
  foreign key (business_id, business_data_mode) references public.businesses (id, data_mode) on delete cascade
);
comment on table public.business_memberships is 'Only private businesses can have members; the composite key forces the mode.';

create table public.visitors (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now()
);
comment on table public.visitors is 'Opaque demo identities. Never an authorization for private data.';

create table public.workspaces (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null,
  business_data_mode public.data_mode not null,
  visitor_id uuid references public.visitors (id) on delete cascade,
  user_id uuid references auth.users (id) on delete cascade,
  revision integer not null default 1 check (revision >= 1),
  created_at timestamptz not null default now(),
  foreign key (business_id, business_data_mode) references public.businesses (id, data_mode) on delete cascade,
  foreign key (business_id, user_id) references public.business_memberships (business_id, user_id) on delete cascade,
  constraint workspaces_exactly_one_identity check (num_nonnulls(visitor_id, user_id) = 1),
  constraint workspaces_identity_matches_mode check (
    (visitor_id is not null and business_data_mode = 'demo')
    or (user_id is not null and business_data_mode = 'private')
  ),
  unique (visitor_id, business_id),
  unique (user_id, business_id)
);
comment on table public.workspaces is 'One mutable view per identity per business. Visitor workspaces are demo-only; user workspaces must match a membership.';

create table public.connections (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references public.businesses (id) on delete cascade,
  provider text not null check (provider in ('synthetic')),
  external_item_id text not null check (length(external_item_id) between 1 and 120),
  sync_cursor text,
  status text not null default 'active' check (status in ('active', 'paused', 'revoked')),
  created_at timestamptz not null default now(),
  unique (business_id, provider, external_item_id),
  unique (id, business_id)
);

create table public.accounts (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references public.businesses (id) on delete cascade,
  connection_id uuid,
  source_type public.source_type not null,
  external_account_id text check (length(external_account_id) between 1 and 120),
  name text not null check (length(name) between 1 and 120),
  account_type public.account_type not null,
  currency char(3) not null default 'USD' check (currency = 'USD'),
  mask text check (mask ~ '^[0-9]{4}$'),
  created_at timestamptz not null default now(),
  foreign key (connection_id, business_id) references public.connections (id, business_id) on delete set null (connection_id),
  unique (id, business_id),
  constraint accounts_identity_matches_source check (
    (source_type in ('synthetic_provider', 'bank_connector') and connection_id is not null and external_account_id is not null)
    or (source_type in ('csv_import', 'pdf_import') and connection_id is null)
  )
);
create unique index accounts_external_identity
  on public.accounts (connection_id, external_account_id) where connection_id is not null;

create table public.sync_batches (
  id uuid primary key default gen_random_uuid(),
  connection_id uuid not null references public.connections (id) on delete cascade,
  start_cursor text,
  end_cursor text,
  status public.sync_batch_status not null default 'staged',
  started_at timestamptz not null default now(),
  applied_at timestamptz,
  check (status <> 'applied' or applied_at is not null)
);

create table public.raw_events (
  id uuid primary key default gen_random_uuid(),
  sync_batch_id uuid not null references public.sync_batches (id) on delete cascade,
  sequence integer not null check (sequence >= 0),
  operation public.sync_operation not null,
  external_transaction_id text not null check (length(external_transaction_id) between 1 and 120),
  payload jsonb not null,
  received_at timestamptz not null default now(),
  unique (sync_batch_id, sequence)
);
comment on table public.raw_events is 'Provider operations exactly as received. Never edited; corrections arrive as new events.';

create table public.transactions (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null,
  account_id uuid not null,
  source_type public.source_type not null,
  external_id text check (length(external_id) between 1 and 120),
  amount_minor bigint not null check (amount_minor <> 0),
  currency char(3) not null default 'USD' check (currency = 'USD'),
  posted_date date not null,
  description text not null check (length(description) between 1 and 500),
  status public.transaction_status not null default 'posted',
  pending_transaction_id uuid references public.transactions (id) on delete set null,
  removed_at timestamptz,
  revision integer not null default 1 check (revision >= 1),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (account_id, business_id) references public.accounts (id, business_id) on delete cascade,
  unique (id, business_id)
);
create unique index transactions_external_identity
  on public.transactions (account_id, source_type, external_id) where external_id is not null;
create index transactions_by_account_date on public.transactions (account_id, posted_date);
create index transactions_by_business_date on public.transactions (business_id, posted_date);
comment on column public.transactions.amount_minor is 'Signed integer minor units; inflows positive. Each adapter converts its own sign convention.';

create table public.transaction_sources (
  id uuid primary key default gen_random_uuid(),
  transaction_id uuid not null references public.transactions (id) on delete cascade,
  raw_event_id uuid references public.raw_events (id) on delete restrict,
  created_at timestamptz not null default now(),
  -- M6 adds import_row_id and widens this to num_nonnulls(raw_event_id, import_row_id) = 1
  constraint transaction_sources_exactly_one_origin check (num_nonnulls(raw_event_id) = 1)
);

create table public.balance_snapshots (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null,
  account_id uuid not null,
  kind public.balance_kind not null,
  provenance public.provenance not null check (provenance in ('bank_observed', 'owner_confirmed')),
  source_type public.source_type not null,
  amount_minor bigint not null,
  as_of timestamptz not null,
  created_at timestamptz not null default now(),
  foreign key (account_id, business_id) references public.accounts (id, business_id) on delete cascade,
  unique (account_id, kind, provenance, as_of)
);
comment on column public.balance_snapshots.as_of is 'When the balance was true. A statement closing balance is historical evidence, never current cash.';

-- ------------------------------------------------------------- triggers --
create function public.set_updated_at() returns trigger
language plpgsql as $$
begin
  new.updated_at := now();
  return new;
end $$;

create trigger transactions_set_updated_at
  before update on public.transactions
  for each row execute function public.set_updated_at();

create function public.guard_demo_rows() returns trigger
language plpgsql as $$
declare
  v_mode public.data_mode;
begin
  if current_setting('cashlens.allow_demo_mutation', true) = 'on' then
    if tg_op = 'DELETE' then return old; else return new; end if;
  end if;
  if tg_table_name = 'businesses' then
    if tg_op = 'INSERT' then v_mode := new.data_mode;
    elsif tg_op = 'UPDATE' then v_mode := case when old.data_mode = 'demo' or new.data_mode = 'demo' then 'demo' else 'private' end;
    else v_mode := old.data_mode; end if;
  else
    if tg_op = 'DELETE' then
      select b.data_mode into v_mode from public.businesses b where b.id = old.business_id;
    else
      select b.data_mode into v_mode from public.businesses b where b.id = new.business_id;
    end if;
  end if;
  if v_mode = 'demo' then
    raise exception 'demo data is immutable (% on %)', tg_op, tg_table_name using errcode = 'insufficient_privilege';
  end if;
  if tg_op = 'DELETE' then return old; else return new; end if;
end $$;

create trigger businesses_guard_demo before insert or update or delete on public.businesses
  for each row execute function public.guard_demo_rows();
create trigger connections_guard_demo before insert or update or delete on public.connections
  for each row execute function public.guard_demo_rows();
create trigger accounts_guard_demo before insert or update or delete on public.accounts
  for each row execute function public.guard_demo_rows();
create trigger transactions_guard_demo before insert or update or delete on public.transactions
  for each row execute function public.guard_demo_rows();
create trigger balance_snapshots_guard_demo before insert or update or delete on public.balance_snapshots
  for each row execute function public.guard_demo_rows();

-- ------------------------------------------------ authorization helpers --
create function public.is_business_member(p_business_id uuid) returns boolean
language sql stable security definer set search_path = public as $$
  select exists (
    select 1 from public.business_memberships m
    where m.business_id = p_business_id and m.user_id = auth.uid()
  );
$$;
revoke all on function public.is_business_member(uuid) from public;
grant execute on function public.is_business_member(uuid) to anon, authenticated, service_role;

create function public.is_demo_business(p_business_id uuid) returns boolean
language sql stable security definer set search_path = public as $$
  select exists (select 1 from public.businesses b where b.id = p_business_id and b.data_mode = 'demo');
$$;
revoke all on function public.is_demo_business(uuid) from public;
grant execute on function public.is_demo_business(uuid) to anon, authenticated, service_role;

-- ------------------------------------------------ privileges and RLS --
-- This project's default privileges give anon/authenticated TRUNCATE,
-- REFERENCES and TRIGGER on new tables, which RLS does not restrict. Revoke
-- everything first, then grant exactly what each role needs.
revoke all on table
  public.businesses, public.business_memberships, public.visitors, public.workspaces,
  public.connections, public.accounts, public.sync_batches, public.raw_events,
  public.transactions, public.transaction_sources, public.balance_snapshots
  from anon, authenticated;

grant select on public.businesses, public.accounts, public.transactions, public.balance_snapshots to anon, authenticated;
grant select on public.business_memberships to authenticated;

grant select, insert, update, delete on table
  public.businesses, public.business_memberships, public.visitors, public.workspaces,
  public.connections, public.accounts, public.sync_batches, public.raw_events,
  public.transactions, public.transaction_sources, public.balance_snapshots
  to service_role;

alter table public.businesses enable row level security;
alter table public.business_memberships enable row level security;
alter table public.visitors enable row level security;
alter table public.workspaces enable row level security;
alter table public.connections enable row level security;
alter table public.accounts enable row level security;
alter table public.sync_batches enable row level security;
alter table public.raw_events enable row level security;
alter table public.transactions enable row level security;
alter table public.transaction_sources enable row level security;
alter table public.balance_snapshots enable row level security;

-- Demo rows are readable by anyone; private rows only by members. No client writes.
create policy businesses_demo_read on public.businesses
  for select to anon, authenticated using (data_mode = 'demo');
create policy businesses_member_read on public.businesses
  for select to authenticated using (data_mode = 'private' and public.is_business_member(id));

create policy memberships_own_read on public.business_memberships
  for select to authenticated using (user_id = auth.uid());

create policy accounts_demo_read on public.accounts
  for select to anon, authenticated using (public.is_demo_business(business_id));
create policy accounts_member_read on public.accounts
  for select to authenticated using (public.is_business_member(business_id));

create policy transactions_demo_read on public.transactions
  for select to anon, authenticated using (public.is_demo_business(business_id));
create policy transactions_member_read on public.transactions
  for select to authenticated using (public.is_business_member(business_id));

create policy balance_snapshots_demo_read on public.balance_snapshots
  for select to anon, authenticated using (public.is_demo_business(business_id));
create policy balance_snapshots_member_read on public.balance_snapshots
  for select to authenticated using (public.is_business_member(business_id));

-- visitors, workspaces, connections, sync_batches, raw_events and
-- transaction_sources intentionally have no client policies: service role only.
