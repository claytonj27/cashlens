#!/usr/bin/env python3
"""Ordered, tracked SQL migrations for the CashLens database.

Usage (from the repository root):

    python3 db/migrate.py status
    python3 db/migrate.py apply
    python3 db/migrate.py baseline 0001

`status` reports applied, pending and drifted files. `apply` runs every
pending file in order and is the only step a fresh database needs. `baseline`
records a file as applied without running it; it exists solely to adopt a
migration verified to be present by other means (used once for 0001 on the
development project) and is never part of routine setup.

The target database is SUPABASE_DB_URL from the environment or, failing that,
from services/api/.env. Each migration file runs in one transaction together
with the row that records it in public.schema_migrations, so a failed file
leaves nothing behind. Applied files are checksummed; a changed applied file
is reported as drift and the runner refuses to continue. The runner never
drops, resets or re-runs anything.
"""

import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MIGRATIONS = ROOT / "migrations"
ENV_FILE = ROOT.parent / "services" / "api" / ".env"
FILE_RE = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")
# Files must not manage their own transactions: the runner wraps each file and its
# history row in one transaction, and an advisory lock serializes concurrent runners.
TX_CONTROL_RE = re.compile(r"^\s*(begin|commit|rollback|start\s+transaction|end)\s*;", re.I | re.M)
LOCK = "select pg_advisory_xact_lock(726451853);"

BOOTSTRAP = """
create table if not exists public.schema_migrations (
  version text primary key,
  name text not null,
  checksum text not null,
  applied_at timestamptz not null default now()
);
alter table public.schema_migrations enable row level security;
revoke all on table public.schema_migrations from anon, authenticated;
"""


def database_url() -> str:
    url = os.environ.get("SUPABASE_DB_URL", "").strip()
    if not url and ENV_FILE.exists():
        match = re.search(r"^SUPABASE_DB_URL=(.*)$", ENV_FILE.read_text(encoding="utf-8"), re.M)
        url = match.group(1).strip() if match else ""
    if not url:
        sys.exit("SUPABASE_DB_URL is not set (environment or services/api/.env)")
    return url


def psql(url: str, sql: str, *, single_transaction: bool = False) -> str:
    args = ["psql", url, "-X", "-v", "ON_ERROR_STOP=1", "-q", "-tA"]
    if single_transaction:
        args.append("--single-transaction")
    result = subprocess.run(
        args, input=sql, capture_output=True, text=True,
        env={**os.environ, "PGCONNECT_TIMEOUT": "15"},
    )
    if result.returncode != 0:
        sys.exit(f"psql failed:\n{result.stderr.strip()[:2000]}")
    return result.stdout


def local_migrations() -> list[tuple[str, str, Path, str]]:
    files = []
    for path in sorted(MIGRATIONS.glob("*.sql")):
        match = FILE_RE.match(path.name)
        if not match:
            sys.exit(f"migration file name not allowed: {path.name} (expected NNNN_name.sql)")
        if TX_CONTROL_RE.search(path.read_text(encoding="utf-8")):
            sys.exit(f"{path.name} contains transaction control statements; the runner owns the transaction")
        checksum = hashlib.sha256(path.read_bytes()).hexdigest()
        files.append((match.group(1), match.group(2), path, checksum))
    versions = [f[0] for f in files]
    if len(versions) != len(set(versions)):
        sys.exit("duplicate migration versions")
    return files


def applied(url: str) -> dict[str, str]:
    psql(url, LOCK + BOOTSTRAP, single_transaction=True)
    rows = psql(url, "select version || '|' || checksum from public.schema_migrations order by version;")
    return dict(line.split("|", 1) for line in rows.splitlines() if line.strip())


def cmd_status(url: str) -> int:
    done = applied(url)
    drift = 0
    for version, name, _, checksum in local_migrations():
        if version in done:
            state = "applied" if done[version] == checksum else "APPLIED BUT CHANGED (drift)"
            drift += done[version] != checksum
        else:
            state = "pending"
        print(f"{version} {name:<32} {state}")
    for version in sorted(set(done) - {f[0] for f in local_migrations()}):
        print(f"{version} {'(no local file)':<32} applied remotely only")
    return 1 if drift else 0


def cmd_apply(url: str) -> int:
    done = applied(url)
    for version, name, path, checksum in local_migrations():
        if version in done:
            if done[version] != checksum:
                sys.exit(f"{path.name} was applied with a different checksum; refusing to continue")
            continue
        sql = path.read_text(encoding="utf-8")
        record = (
            "insert into public.schema_migrations (version, name, checksum) "
            f"values ('{version}', '{name}', '{checksum}');"
        )
        psql(url, LOCK + "\n" + sql + "\n" + record, single_transaction=True)
        print(f"applied {path.name}")
    print("up to date")
    return 0


def cmd_baseline(url: str, version: str) -> int:
    done = applied(url)
    for v, name, path, checksum in local_migrations():
        if v == version:
            if version in done:
                print(f"{path.name} already recorded")
                return 0
            psql(url, LOCK + " insert into public.schema_migrations (version, name, checksum) "
                      f"values ('{version}', '{name}', '{checksum}');", single_transaction=True)
            print(f"recorded {path.name} as applied without running it")
            return 0
    sys.exit(f"no migration file with version {version}")


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in ("status", "apply", "baseline"):
        print(__doc__)
        return 2
    url = database_url()
    if argv[0] == "status":
        return cmd_status(url)
    if argv[0] == "apply":
        return cmd_apply(url)
    if len(argv) != 2:
        sys.exit("usage: migrate.py baseline VERSION")
    return cmd_baseline(url, argv[1])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
