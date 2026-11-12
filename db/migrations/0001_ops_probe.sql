-- 0001: isolated probe table for the operator-only /api/ops/db-probe diagnostic.
-- Holds only synthetic rows that the probe inserts and deletes itself.
-- Row Level Security is enabled with no policies and the public roles have no
-- grants, so only privileged (service role) connections can touch it.

create table if not exists public.ops_probe (
  id bigint generated always as identity primary key,
  probe_id uuid not null unique,
  note text not null default 'synthetic probe',
  created_at timestamptz not null default now()
);

alter table public.ops_probe enable row level security;
revoke all on table public.ops_probe from anon, authenticated;

comment on table public.ops_probe is
  'Synthetic rows written and deleted by the operator-only /api/ops/db-probe diagnostic. No financial data.';

-- This project's default privileges give the API roles no data access on new
-- tables, so the privileged role is granted exactly what the probe needs.
grant select, insert, delete on table public.ops_probe to service_role;
grant usage, select on sequence public.ops_probe_id_seq to service_role;
