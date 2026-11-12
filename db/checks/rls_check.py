#!/usr/bin/env python3
"""Row Level Security and grant checks against the development database.

Runs one transaction as the postgres role, creates throwaway private and demo
rows plus a throwaway auth user, then switches to the anon and authenticated
roles to assert what each can see and do. Everything is rolled back. Reports
each check as PASS or FAIL and exits non-zero on any failure.

    python3 db/checks/rls_check.py
"""

import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parents[2] / "services" / "api" / ".env"
MEMBER = str(uuid.uuid4())
STRANGER = str(uuid.uuid4())
PRIVATE_BUSINESS = str(uuid.uuid4())
DEMO_BUSINESS = str(uuid.uuid4())


def database_url() -> str:
    url = os.environ.get("SUPABASE_DB_URL", "").strip()
    if not url and ENV_FILE.exists():
        match = re.search(r"^SUPABASE_DB_URL=(.*)$", ENV_FILE.read_text(encoding="utf-8"), re.M)
        url = match.group(1).strip() if match else ""
    if not url:
        sys.exit("SUPABASE_DB_URL is not set")
    return url


def attempt(name: str, statement: str, expect: str) -> str:
    """A DO block that reports ALLOWED or DENIED for a statement and records the expectation."""
    return f"""
do $$ begin
  {statement};
  raise notice 'CHECK {name} expect={expect} got=ALLOWED';
exception when insufficient_privilege then
  raise notice 'CHECK {name} expect={expect} got=DENIED';
end $$;
"""


def count(name: str, query: str, expect: int) -> str:
    return f"do $$ declare n int; begin select ({query}) into n; raise notice 'CHECK {name} expect={expect} got=%', n; end $$;\n"


SCRIPT = f"""
begin;
set local cashlens.allow_demo_mutation = 'on';
insert into auth.users (id, instance_id, aud, role, email, created_at, updated_at)
  values ('{MEMBER}', '00000000-0000-0000-0000-000000000000', 'authenticated', 'authenticated', 'rls-check-member@example.invalid', now(), now()),
         ('{STRANGER}', '00000000-0000-0000-0000-000000000000', 'authenticated', 'authenticated', 'rls-check-stranger@example.invalid', now(), now());
insert into public.businesses (id, slug, name, industry, data_mode) values
  ('{PRIVATE_BUSINESS}', 'rlscheck-private', 'RLS check private', 'test', 'private'),
  ('{DEMO_BUSINESS}', 'rlscheck-demo', 'RLS check demo', 'test', 'demo');
insert into public.business_memberships (business_id, business_data_mode, user_id) values ('{PRIVATE_BUSINESS}', 'private', '{MEMBER}');
insert into public.accounts (id, business_id, source_type, name, account_type) values
  ('11111111-1111-4111-8111-111111111111', '{PRIVATE_BUSINESS}', 'csv_import', 'Private checking', 'checking');
insert into public.connections (id, business_id, provider, external_item_id) values ('22222222-2222-4222-8222-222222222222', '{DEMO_BUSINESS}', 'synthetic', 'item-rlscheck');
insert into public.accounts (id, business_id, connection_id, source_type, external_account_id, name, account_type) values
  ('33333333-3333-4333-8333-333333333333', '{DEMO_BUSINESS}', '22222222-2222-4222-8222-222222222222', 'synthetic_provider', 'acct-rlscheck', 'Demo checking', 'checking');
insert into public.transactions (business_id, account_id, source_type, amount_minor, posted_date, description) values
  ('{PRIVATE_BUSINESS}', '11111111-1111-4111-8111-111111111111', 'csv_import', -1000, '2026-08-01', 'private row'),
  ('{DEMO_BUSINESS}', '33333333-3333-4333-8333-333333333333', 'synthetic_provider', 2000, '2026-08-01', 'demo row');
insert into public.visitors (id) values ('44444444-4444-4444-8444-444444444444');
insert into public.workspaces (business_id, business_data_mode, visitor_id) values ('{DEMO_BUSINESS}', 'demo', '44444444-4444-4444-8444-444444444444');
reset cashlens.allow_demo_mutation;

-- structural guards (as postgres, without the flag)
{attempt("demo_rows_are_immutable", f"update public.businesses set name = 'x' where id = '{DEMO_BUSINESS}'", "DENIED")}
{attempt("demo_transaction_delete_blocked", f"delete from public.transactions where business_id = '{DEMO_BUSINESS}'", "DENIED")}
do $$ begin
  insert into public.workspaces (business_id, business_data_mode, visitor_id) values ('{PRIVATE_BUSINESS}', 'private', '44444444-4444-4444-8444-444444444444');
  raise notice 'CHECK visitor_cannot_own_private_workspace expect=DENIED got=ALLOWED';
exception when check_violation or foreign_key_violation then
  raise notice 'CHECK visitor_cannot_own_private_workspace expect=DENIED got=DENIED';
end $$;
do $$ begin
  insert into public.workspaces (business_id, business_data_mode, user_id) values ('{PRIVATE_BUSINESS}', 'private', '{STRANGER}');
  raise notice 'CHECK non_member_cannot_own_private_workspace expect=DENIED got=ALLOWED';
exception when check_violation or foreign_key_violation then
  raise notice 'CHECK non_member_cannot_own_private_workspace expect=DENIED got=DENIED';
end $$;
do $$ begin
  insert into public.business_memberships (business_id, business_data_mode, user_id) values ('{DEMO_BUSINESS}', 'demo', '{STRANGER}');
  raise notice 'CHECK demo_business_cannot_have_members expect=DENIED got=ALLOWED';
exception when check_violation or foreign_key_violation then
  raise notice 'CHECK demo_business_cannot_have_members expect=DENIED got=DENIED';
end $$;

-- cross-scope references are rejected structurally (as postgres; private rows so the demo guard is not what fails)
do $$ begin
  insert into public.transactions (business_id, account_id, source_type, amount_minor, posted_date, description)
    values ('{PRIVATE_BUSINESS}', '33333333-3333-4333-8333-333333333333', 'csv_import', 5, '2026-08-02', 'wrong business for this account');
  raise notice 'CHECK transaction_cannot_reference_other_business_account expect=DENIED got=ALLOWED';
exception when foreign_key_violation then
  raise notice 'CHECK transaction_cannot_reference_other_business_account expect=DENIED got=DENIED';
end $$;
do $$ begin
  insert into public.balance_snapshots (business_id, account_id, kind, provenance, source_type, amount_minor, as_of)
    values ('{PRIVATE_BUSINESS}', '33333333-3333-4333-8333-333333333333', 'current', 'bank_observed', 'csv_import', 1, now());
  raise notice 'CHECK snapshot_cannot_reference_other_business_account expect=DENIED got=ALLOWED';
exception when foreign_key_violation then
  raise notice 'CHECK snapshot_cannot_reference_other_business_account expect=DENIED got=DENIED';
end $$;
do $$ begin
  insert into public.workspaces (business_id, business_data_mode, user_id) values ('{DEMO_BUSINESS}', 'private', '{MEMBER}');
  raise notice 'CHECK workspace_mode_must_match_business expect=DENIED got=ALLOWED';
exception when check_violation or foreign_key_violation then
  raise notice 'CHECK workspace_mode_must_match_business expect=DENIED got=DENIED';
end $$;
do $$ begin
  insert into public.workspaces (business_id, business_data_mode, visitor_id, user_id) values ('{DEMO_BUSINESS}', 'demo', '44444444-4444-4444-8444-444444444444', '{MEMBER}');
  raise notice 'CHECK workspace_cannot_have_two_identities expect=DENIED got=ALLOWED';
exception when check_violation or foreign_key_violation or unique_violation then
  raise notice 'CHECK workspace_cannot_have_two_identities expect=DENIED got=DENIED';
end $$;

-- the demo-mutation bypass needs an operator session, not just the flag
do $$ begin
  set local role service_role;
  set local cashlens.allow_demo_mutation = 'on';
  update public.businesses set name = 'x' where id = '{DEMO_BUSINESS}';
  raise notice 'CHECK service_role_cannot_lift_demo_immutability expect=DENIED got=ALLOWED';
exception when insufficient_privilege then
  raise notice 'CHECK service_role_cannot_lift_demo_immutability expect=DENIED got=DENIED';
end $$;
reset role;
reset cashlens.allow_demo_mutation;

-- anon: demo rows only, no writes, no service-only tables
set local role anon;
{count("anon_sees_demo_business", f"select count(*) from public.businesses where id = '{DEMO_BUSINESS}'", 1)}
{count("anon_cannot_see_private_business", f"select count(*) from public.businesses where id = '{PRIVATE_BUSINESS}'", 0)}
{count("anon_cannot_see_private_transactions", f"select count(*) from public.transactions where business_id = '{PRIVATE_BUSINESS}'", 0)}
{count("anon_sees_demo_transactions", f"select count(*) from public.transactions where business_id = '{DEMO_BUSINESS}'", 1)}
{attempt("anon_cannot_read_workspaces", "perform 1 from public.workspaces", "DENIED")}
{attempt("anon_cannot_read_visitors", "perform 1 from public.visitors", "DENIED")}
{attempt("anon_cannot_read_raw_events", "perform 1 from public.raw_events", "DENIED")}
{attempt("anon_cannot_insert_transactions", f"insert into public.transactions (business_id, account_id, source_type, amount_minor, posted_date, description) values ('{DEMO_BUSINESS}', '33333333-3333-4333-8333-333333333333', 'synthetic_provider', 5, '2026-08-02', 'x')", "DENIED")}
{attempt("anon_cannot_truncate", "truncate public.transactions", "DENIED")}
{attempt("anon_cannot_update_demo_business", f"update public.businesses set name = 'x' where id = '{DEMO_BUSINESS}'", "DENIED")}
{attempt("anon_cannot_delete_demo_transactions", f"delete from public.transactions where business_id = '{DEMO_BUSINESS}'", "DENIED")}
{attempt("anon_cannot_insert_workspaces", f"insert into public.workspaces (business_id, business_data_mode, visitor_id) values ('{DEMO_BUSINESS}', 'demo', '44444444-4444-4444-8444-444444444444')", "DENIED")}
{attempt("anon_cannot_set_bypass_and_write", f"set local cashlens.allow_demo_mutation = 'on'; update public.businesses set name = 'x' where id = '{DEMO_BUSINESS}'", "DENIED")}
reset role;

-- authenticated member: sees own private business and demo data
set local role authenticated;
select set_config('request.jwt.claims', '{{"sub": "{MEMBER}", "role": "authenticated"}}', true);
{count("member_sees_own_private_business", f"select count(*) from public.businesses where id = '{PRIVATE_BUSINESS}'", 1)}
{count("member_sees_own_private_transactions", f"select count(*) from public.transactions where business_id = '{PRIVATE_BUSINESS}'", 1)}
{count("member_sees_own_membership", f"select count(*) from public.business_memberships where business_id = '{PRIVATE_BUSINESS}'", 1)}
{count("member_sees_demo_business", f"select count(*) from public.businesses where id = '{DEMO_BUSINESS}'", 1)}
{attempt("member_cannot_update_private_transactions", f"update public.transactions set description = 'y' where business_id = '{PRIVATE_BUSINESS}'", "DENIED")}
{attempt("member_cannot_read_workspaces", "perform 1 from public.workspaces", "DENIED")}
reset role;

-- authenticated stranger: sees nothing private
set local role authenticated;
select set_config('request.jwt.claims', '{{"sub": "{STRANGER}", "role": "authenticated"}}', true);
{count("stranger_cannot_see_private_business", f"select count(*) from public.businesses where id = '{PRIVATE_BUSINESS}'", 0)}
{count("stranger_cannot_see_private_transactions", f"select count(*) from public.transactions where business_id = '{PRIVATE_BUSINESS}'", 0)}
{count("stranger_cannot_see_others_membership", f"select count(*) from public.business_memberships where business_id = '{PRIVATE_BUSINESS}'", 0)}
{count("stranger_sees_demo_transactions", f"select count(*) from public.transactions where business_id = '{DEMO_BUSINESS}'", 1)}
reset role;
rollback;
"""


def main() -> int:
    result = subprocess.run(
        ["psql", database_url(), "-X", "-q", "-v", "ON_ERROR_STOP=1"],
        input=SCRIPT, capture_output=True, text=True, env={**os.environ, "PGCONNECT_TIMEOUT": "15"},
    )
    checks = re.findall(r"CHECK (\S+) expect=(\S+) got=(\S+)", result.stdout + result.stderr)
    failures = 0
    for name, expect, got in checks:
        ok = expect == got
        failures += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {name:<48} expected {expect}, got {got}")
    if result.returncode != 0:
        print("psql error:", result.stderr.strip()[-1500:])
        return 1
    print(f"{len(checks) - failures}/{len(checks)} checks passed")
    return 1 if failures or not checks else 0


if __name__ == "__main__":
    sys.exit(main())
