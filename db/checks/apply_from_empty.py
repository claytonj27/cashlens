#!/usr/bin/env python3
"""Apply every migration to a brand-new local PostgreSQL cluster, then run the RLS checks.

Proves the migration history works from nothing, independently of the hosted
development project. Needs initdb, pg_ctl and postgres (Homebrew PostgreSQL).
The cluster lives in a temporary directory and is destroyed afterwards; no
hosted database is touched.

    python3 db/checks/apply_from_empty.py [--seed]
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PORT = "54329"

# Mirrors what a Supabase project provides before our migrations run: the API
# roles, an auth schema with users and uid(), and the default privileges that
# hand the API roles truncate/references/trigger on new tables.
SHIM = """
create role anon nologin;
create role authenticated nologin;
create role service_role nologin bypassrls;
create role authenticator noinherit login;
grant anon, authenticated, service_role to authenticator;
create schema auth;
create table auth.users (
  id uuid primary key, instance_id uuid, aud text, role text, email text,
  encrypted_password text, created_at timestamptz, updated_at timestamptz
);
create function auth.uid() returns uuid language sql stable as $$
  select nullif(coalesce(
    current_setting('request.jwt.claim.sub', true),
    nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub'
  ), '')::uuid
$$;
grant usage on schema auth to anon, authenticated, service_role;
grant execute on function auth.uid() to anon, authenticated, service_role;
grant usage on schema public to anon, authenticated, service_role;
alter default privileges in schema public grant truncate, references, trigger on tables to anon, authenticated, service_role;
"""


def binary(name: str) -> str:
    for candidate in (shutil.which(name), f"/opt/homebrew/bin/{name}", f"/usr/local/bin/{name}"):
        if candidate and Path(candidate).exists():
            return candidate
    sys.exit(f"{name} not found; install PostgreSQL to run this check")


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, capture_output=True, text=True, **kwargs)
    if result.returncode != 0:
        print(result.stdout[-1500:], result.stderr[-1500:])
        sys.exit(f"command failed: {' '.join(cmd[:2])}")
    return result


def main(argv: list[str]) -> int:
    seed = "--seed" in argv
    workdir = Path(tempfile.mkdtemp(prefix="cashlens-pg-"))
    data = workdir / "data"
    url = f"postgresql://postgres@127.0.0.1:{PORT}/postgres"
    env = {**os.environ, "SUPABASE_DB_URL": url, "PGCONNECT_TIMEOUT": "10"}
    started = False
    try:
        run([binary("initdb"), "-D", str(data), "-U", "postgres", "-A", "trust", "--no-locale", "-E", "UTF8"])
        run([binary("pg_ctl"), "-D", str(data), "-l", str(workdir / "pg.log"), "-w",
             "-o", f"-p {PORT} -k {workdir} -c listen_addresses=127.0.0.1", "start"])
        started = True
        time.sleep(1)
        run(["psql", url, "-X", "-q", "-v", "ON_ERROR_STOP=1"], input=SHIM)
        print("empty cluster ready with Supabase-like roles and auth shim")
        result = run([sys.executable, str(ROOT / "db" / "migrate.py"), "apply"], env=env)
        print(result.stdout.strip())
        result = run([sys.executable, str(ROOT / "db" / "migrate.py"), "status"], env=env)
        print(result.stdout.strip())
        probe = run(["psql", url, "-X", "-tA", "-c", "select to_regclass('public.ops_probe') is not null"]).stdout.strip()
        print(f"M2 probe objects present after applying from empty: {probe}")
        if probe != "t":
            return 1
        result = subprocess.run([sys.executable, str(ROOT / "db" / "checks" / "rls_check.py")], env=env, capture_output=True, text=True)
        print(result.stdout.strip())
        if result.returncode != 0:
            print(result.stderr[-1500:])
            return 1
        if seed:
            python = ROOT / "services" / "api" / ".venv" / "bin" / "python"
            api_dir = ROOT / "services" / "api"
            digest_sql = """
              select (select count(*) from public.businesses where data_mode='demo') || '|' ||
                     (select count(*) from public.transactions t join public.businesses b on b.id=t.business_id where b.data_mode='demo') || '|' ||
                     (select count(*) from public.raw_events) || '|' ||
                     (select md5(string_agg(t.external_id||'|'||t.amount_minor||'|'||t.posted_date||'|'||t.description||'|'||t.status||'|'||coalesce(t.removed_at::text,'')||'|'||t.revision||'|'||coalesce((select p.external_id from public.transactions p where p.id = t.pending_transaction_id),''), ',' order by t.external_id))
                        from public.transactions t join public.businesses b on b.id=t.business_id where b.data_mode='demo') || '|' ||
                     (select md5(string_agg(a.external_account_id||':'||x.kind||':'||x.amount_minor||':'||x.as_of, ',' order by a.external_account_id, x.kind))
                        from public.balance_snapshots x join public.accounts a on a.id=x.account_id)
            """
            def digest() -> str:
                return run(["psql", url, "-X", "-tA", "-c", digest_sql]).stdout.strip()
            result = run([str(python), "-m", "app.synthetic.seed"], env=env, cwd=api_dir)
            print(result.stdout.strip())
            first = digest()
            # unrelated private records must survive a reseed untouched
            private_sql = """
              insert into public.businesses (id, slug, name, industry, data_mode) values ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'private-keep', 'Keep me', 'test', 'private');
              insert into public.accounts (id, business_id, source_type, name, account_type) values ('bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'csv_import', 'Private checking', 'checking');
              insert into public.transactions (business_id, account_id, source_type, amount_minor, posted_date, description) values ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', 'csv_import', -4200, '2026-08-01', 'private rent');
            """
            run(["psql", url, "-X", "-q", "-v", "ON_ERROR_STOP=1"], input=private_sql)
            private_digest_sql = "select md5(string_agg(amount_minor||'|'||description, ',' order by id)) || '|' || count(*) from public.transactions where business_id='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'"
            private_before = run(["psql", url, "-X", "-tA", "-c", private_digest_sql]).stdout.strip()
            refused = subprocess.run([str(python), "-m", "app.synthetic.seed"], env=env, cwd=api_dir, capture_output=True, text=True)
            print(f"seed without --replace refuses when demo data exists: {refused.returncode != 0}")
            result = run([str(python), "-m", "app.synthetic.seed", "--replace"], env=env, cwd=api_dir)
            second = digest()
            private_after = run(["psql", url, "-X", "-tA", "-c", private_digest_sql]).stdout.strip()
            print(f"reseed produces identical counts, canonical rows and balances: {first == second}")
            print(f"unrelated private records preserved through reseed: {private_before == private_after}")
            # the API's routine credential path cannot lift demo immutability even with the flag
            auth_url = f"postgresql://authenticator@127.0.0.1:{PORT}/postgres"
            bypass = subprocess.run(["psql", auth_url, "-X", "-q", "-v", "ON_ERROR_STOP=1"], capture_output=True, text=True,
                                    input="set role service_role; set cashlens.allow_demo_mutation = 'on'; update public.businesses set name = 'x' where data_mode = 'demo';")
            print(f"authenticator/service_role with flag cannot modify demo rows: {bypass.returncode != 0 and 'immutable' in bypass.stderr}")
            if not (first == second and private_before == private_after and refused.returncode != 0 and bypass.returncode != 0):
                return 1
        print("apply-from-empty: OK")
        return 0
    finally:
        if started:
            subprocess.run([binary("pg_ctl"), "-D", str(data), "-m", "fast", "stop"], capture_output=True)
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
