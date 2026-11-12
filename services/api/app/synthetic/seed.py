"""Load the synthetic provider fixtures into the development database as demo businesses.

    .venv/bin/python -m app.synthetic.seed            # refuses if a demo business already exists
    .venv/bin/python -m app.synthetic.seed --replace  # deletes and reloads the demo businesses

The loader inserts each business, its connection and accounts, then stages
every provider page as one sync batch of raw events (payload as received plus
the adapter's canonical reading) and applies it through the same database
function the incremental sync uses, `apply_sync_batch`, which commits the
cursor page by page. Balances come from the fixture. Business, connection,
account, batch and event ids derive from stable keys; transaction ids are
assigned by the database, so a reload is deterministic in content, not in
ids. The whole load runs in one transaction under the demo-mutation flag that
the immutability trigger requires.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from app.ingestion.synthetic import to_canonical
from app.synthetic.generator import DEMO_DATE, stable_id

FIXTURES = Path(__file__).resolve().parents[4] / "fixtures" / "provider"
ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


def q(value: object) -> str:
    """SQL literal with dollar quoting for text; never interpolates untrusted input paths."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    text = str(value)
    tag = "$cl$"
    if tag in text:
        raise ValueError("literal contains the quoting tag")
    return f"{tag}{text}{tag}"


def build_sql(fixture: dict, replace: bool) -> str:
    slug = fixture["business"]["slug"]
    business_id = stable_id(slug, "business")
    connection_id = stable_id(slug, "connection", fixture["item"]["external_item_id"])
    account_ids = {
        a["external_account_id"]: stable_id(slug, "account", a["external_account_id"])
        for a in fixture["accounts"]
    }
    lines = ["set local cashlens.allow_demo_mutation = 'on';"]
    if replace:
        lines.append(
            f"delete from public.businesses where slug = {q(slug)} and data_mode = 'demo';"
        )
    else:
        lines.append(
            f"do $$ begin if exists (select 1 from public.businesses where slug = {q(slug)}) then "
            f"raise exception 'demo business % already exists; use --replace', {q(slug)}; end if; end $$;"
        )
    b = fixture["business"]
    lines.append(
        "insert into public.businesses (id, slug, name, industry, data_mode) values "
        f"({q(business_id)}, {q(slug)}, {q(b['name'])}, {q(b['industry'])}, 'demo');"
    )
    lines.append(
        "insert into public.connections (id, business_id, provider, external_item_id, sync_cursor, status) values "
        f"({q(connection_id)}, {q(business_id)}, 'synthetic', {q(fixture['item']['external_item_id'])}, null, 'active');"
    )
    for a in fixture["accounts"]:
        lines.append(
            "insert into public.accounts (id, business_id, connection_id, source_type, external_account_id, name, account_type, mask) values "
            f"({q(account_ids[a['external_account_id']])}, {q(business_id)}, {q(connection_id)}, 'synthetic_provider', "
            f"{q(a['external_account_id'])}, {q(a['name'])}, {q(a['type'])}, {q(a['mask'])});"
        )
    # Provenance page by page: stage the batch, then apply it through apply_sync_batch.
    applied_at = datetime.combine(DEMO_DATE, datetime.min.time(), tzinfo=UTC)
    for index, page in enumerate(fixture["pages"]):
        batch_id = stable_id(slug, "batch", page["cursor"])
        stamp = (applied_at.replace(hour=1) if index else applied_at).isoformat()
        start_cursor = "null" if index == 0 else q(page["cursor"])
        lines.append(
            "insert into public.sync_batches (id, connection_id, start_cursor, end_cursor, status, started_at) values "
            f"({q(batch_id)}, {q(connection_id)}, {start_cursor}, {q(page['next_cursor'])}, 'staged', {q(stamp)});"
        )
        sequence = 0
        for operation in ("added", "modified", "removed"):
            for record in page[operation]:
                event_id = stable_id(slug, "event", page["cursor"], str(sequence))
                canonical = (
                    "null"
                    if operation == "removed"
                    else q(json.dumps(to_canonical(record).as_json(), sort_keys=True)) + "::jsonb"
                )
                lines.append(
                    "insert into public.raw_events (id, sync_batch_id, sequence, operation, external_transaction_id, payload, canonical, received_at) values "
                    f"({q(event_id)}, {q(batch_id)}, {sequence}, {q(operation)}, {q(record['transaction_id'])}, {q(json.dumps(record, sort_keys=True))}::jsonb, {canonical}, {q(stamp)});"
                )
                sequence += 1
        lines.append(f"select public.apply_sync_batch({q(batch_id)});")
        lines.append(
            f"update public.sync_batches set applied_at = {q(stamp)} where id = {q(batch_id)};"
        )
    for snapshot in fixture["balances"]:
        lines.append(
            "insert into public.balance_snapshots (id, business_id, account_id, kind, provenance, source_type, amount_minor, as_of) values "
            f"({q(stable_id(slug, 'snapshot', snapshot['account_id'], snapshot['kind'], snapshot['as_of']))}, {q(business_id)}, "
            f"{q(account_ids[snapshot['account_id']])}, {q(snapshot['kind'])}, 'bank_observed', 'synthetic_provider', {snapshot['amount_minor']}, {q(snapshot['as_of'])});"
        )
    return "\n".join(lines) + "\n"


def database_url() -> str:
    url = os.environ.get("SUPABASE_DB_URL", "").strip()
    if not url and ENV_FILE.exists():
        match = re.search(r"^SUPABASE_DB_URL=(.*)$", ENV_FILE.read_text(encoding="utf-8"), re.M)
        url = match.group(1).strip() if match else ""
    if not url:
        sys.exit("SUPABASE_DB_URL is not set")
    return url


def main(argv: list[str]) -> int:
    replace = "--replace" in argv
    url = database_url()
    script = ""
    for path in sorted(FIXTURES.glob("*.json")):
        script += build_sql(json.loads(path.read_text(encoding="utf-8")), replace)
    with tempfile.NamedTemporaryFile("w", suffix=".sql", delete=False) as handle:
        handle.write(script)
        temp_path = handle.name
    try:
        result = subprocess.run(
            [
                "psql",
                url,
                "-X",
                "-q",
                "-v",
                "ON_ERROR_STOP=1",
                "--single-transaction",
                "-f",
                temp_path,
            ],
            capture_output=True,
            text=True,
            env={**os.environ, "PGCONNECT_TIMEOUT": "15"},
        )
    finally:
        os.unlink(temp_path)
    if result.returncode != 0:
        print(result.stderr.strip()[:2000])
        return 1
    print(
        f"seeded {len(list(FIXTURES.glob('*.json')))} demo businesses ({script.count(chr(10))} statements, one transaction)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
