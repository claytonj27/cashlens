#!/usr/bin/env python3
"""Synthetic adapter and incremental sync against the development project, on isolated resources.

Creates a temporary private business with a synthetic connection and the
fixture's accounts, then runs the real sync path (stage raw events, apply
atomically through apply_sync_batch, commit the cursor): a first sync over
the pages available "so far", a replay that must change nothing, a later
sync that delivers the fixture's modification, removal and pending-to-posted
page, a pagination mutation that must restart cleanly, and a poisoned batch
that must fail without moving the cursor or touching rows. Shared demo
businesses are never read for writing or modified. Everything created here
is deleted afterwards.

    services/api/.venv/bin/python db/checks/sync_adapter_check.py
"""

import json
import os
import re
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "api"))

from app.ingestion.contract import CanonicalTransaction, ProviderEvent, ProviderPage  # noqa: E402
from app.ingestion.sync import SyncError, publish_balances, sync_connection  # noqa: E402
from app.ingestion.synthetic import SyntheticProvider, load_fixture  # noqa: E402
from app.supabase import SupabaseClient  # noqa: E402

ENV_FILE = ROOT / "services" / "api" / ".env"
SLUG = "juniper-kitchen"
results: list[tuple[str, bool, str]] = []


def env(key: str) -> str:
    value = os.environ.get(key, "").strip()
    if not value and ENV_FILE.exists():
        match = re.search(rf"^{key}=(.*)$", ENV_FILE.read_text(encoding="utf-8"), re.M)
        value = match.group(1).strip() if match else ""
    if not value:
        sys.exit(f"{key} is not set")
    return value


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name:<62} {detail}")


class PoisonedProvider:
    """One page whose canonical record names an account the connection does not have."""

    def fetch(self, cursor):
        event = ProviderEvent(
            "added",
            "tx-poison",
            {"transaction_id": "tx-poison"},
            CanonicalTransaction("acct-does-not-exist", "tx-poison", -100, "2026-08-01", "POISON", "posted"),
        )
        return ProviderPage(cursor, "cursor-poison", False, [event])


def counts(client: SupabaseClient, business_id: str) -> dict:
    posted = client.count("transactions", {"business_id": f"eq.{business_id}", "status": "eq.posted", "removed_at": "is.null"})
    pending = client.count("transactions", {"business_id": f"eq.{business_id}", "status": "eq.pending", "removed_at": "is.null"})
    removed = client.count("transactions", {"business_id": f"eq.{business_id}", "removed_at": "not.is.null"})
    return {"posted": posted, "pending": pending, "removed": removed}


def main() -> int:
    client = SupabaseClient(env("SUPABASE_URL"), env("SUPABASE_SERVICE_ROLE_KEY"))
    fixture = load_fixture(SLUG)
    evaluation = json.loads((ROOT / "fixtures" / "evaluation" / f"{SLUG}.json").read_text())
    business_id = str(uuid.uuid4())
    try:
        client.rest("POST", "businesses", json_body={"id": business_id, "slug": f"synccheck-{business_id[:8]}", "name": "Sync check", "industry": "test", "data_mode": "private"}, prefer="return=minimal")
        connection = client.rest("POST", "connections", params={"select": "id"}, json_body={"business_id": business_id, "provider": "synthetic", "external_item_id": f"item-{business_id[:8]}"}, prefer="return=representation")[0]
        for account in fixture["accounts"]:
            client.rest("POST", "accounts", json_body={"business_id": business_id, "connection_id": connection["id"], "source_type": "synthetic_provider", "external_account_id": account["external_account_id"], "name": account["name"], "account_type": account["type"], "mask": account["mask"]}, prefer="return=minimal")
        total_pages = len(fixture["pages"])

        first = sync_connection(client, connection["id"], SyntheticProvider(fixture, available_pages=total_pages - 1))
        c1 = counts(client, business_id)
        check("first_sync_stages_all_available_pages_in_one_batch", first.pages == total_pages - 1 and first.batch_id is not None and first.added + first.unchanged == first.events, f"pages={first.pages} added={first.added} unchanged={first.unchanged}")
        cursor = client.rest("GET", "connections", params={"id": f"eq.{connection['id']}", "select": "sync_cursor"})[0]["sync_cursor"]
        check("cursor_committed_with_the_batch", cursor == fixture["pages"][total_pages - 2]["next_cursor"], cursor)

        replay = sync_connection(client, connection["id"], SyntheticProvider(fixture, available_pages=total_pages - 1))
        check("replay_changes_nothing", replay.batch_id is None and counts(client, business_id) == c1, str(replay))

        second = sync_connection(client, connection["id"], SyntheticProvider(fixture))
        c2 = counts(client, business_id)
        last = fixture["pages"][-1]
        check("later_page_applies_modification_and_removals", second.modified == len(last["modified"]) and second.removed == len(last["removed"]) and second.added == len(last["added"]), f"{second}")
        check("posted_count_matches_evaluation_after_full_sync", c2["posted"] == evaluation["expected"]["posted_transaction_count"], f"{c2['posted']} vs {evaluation['expected']['posted_transaction_count']}")
        check("pending_and_removed_rows_tracked", c2["pending"] == 1 and c2["removed"] == len(last["removed"]), str(c2))
        modified_id = last["modified"][0]["transaction_id"]
        row = client.rest("GET", "transactions", params={"business_id": f"eq.{business_id}", "external_id": f"eq.{modified_id}", "select": "revision,description"})[0]
        check("modified_transaction_carries_revision_two", row["revision"] == 2 and row["description"] == last["modified"][0]["name"], str(row))
        posted_link = client.rest("GET", "transactions", params={"business_id": f"eq.{business_id}", "pending_transaction_id": "not.is.null", "select": "id,pending_transaction_id,status"})
        check("posted_row_links_its_pending_predecessor", len(posted_link) == 1 and posted_link[0]["status"] == "posted", str(posted_link)[:100])
        sources = client.count("transaction_sources", {"transaction_id": f"eq.{row['id'] if 'id' in row else client.rest('GET', 'transactions', params={'business_id': f'eq.{business_id}', 'external_id': f'eq.{modified_id}', 'select': 'id'})[0]['id']}"})
        check("every_event_leaves_provenance", sources == 2, f"{sources} sources for the modified transaction")
        written = publish_balances(client, business_id, connection["id"], SyntheticProvider(fixture).balances())
        snaps = client.count("balance_snapshots", {"business_id": f"eq.{business_id}"})
        check("balances_published_as_bank_observed", written == 3 and snaps == 3, f"{written}/{snaps}")
        chk_id = client.rest("GET", "accounts", params={"business_id": f"eq.{business_id}", "external_account_id": f"eq.{fixture['accounts'][0]['external_account_id']}", "select": "id"})[0]["id"]
        activity = sum(t["amount_minor"] for t in client.rest_all("transactions", {"business_id": f"eq.{business_id}", "account_id": f"eq.{chk_id}", "status": "eq.posted", "removed_at": "is.null", "select": "amount_minor", "order": "id"}))
        current = next(b["amount_minor"] for b in fixture["balances"] if b["account_id"] == fixture["accounts"][0]["external_account_id"] and b["kind"] == "current")
        check("synced_activity_reconciles_to_fixture_balance", fixture["accounts"][0]["opening_balance_minor"] + activity == current, f"{fixture['accounts'][0]['opening_balance_minor']} + {activity} vs {current}")

        cursor_before = client.rest("GET", "connections", params={"id": f"eq.{connection['id']}", "select": "sync_cursor"})[0]["sync_cursor"]
        try:
            sync_connection(client, connection["id"], PoisonedProvider())
            poisoned = False
        except SyncError:
            poisoned = True
        cursor_after = client.rest("GET", "connections", params={"id": f"eq.{connection['id']}", "select": "sync_cursor"})[0]["sync_cursor"]
        failed = client.count("sync_batches", {"connection_id": f"eq.{connection['id']}", "status": "eq.failed"})
        check("poisoned_batch_fails_without_moving_cursor_or_rows", poisoned and cursor_after == cursor_before and counts(client, business_id) == c2 and failed == 1, f"failed_batches={failed}")

        biz2 = str(uuid.uuid4())
        client.rest("POST", "businesses", json_body={"id": biz2, "slug": f"synccheck-{biz2[:8]}", "name": "Sync check restart", "industry": "test", "data_mode": "private"}, prefer="return=minimal")
        conn2 = client.rest("POST", "connections", params={"select": "id"}, json_body={"business_id": biz2, "provider": "synthetic", "external_item_id": f"item-{biz2[:8]}"}, prefer="return=representation")[0]
        for account in fixture["accounts"]:
            client.rest("POST", "accounts", json_body={"business_id": biz2, "connection_id": conn2["id"], "source_type": "synthetic_provider", "external_account_id": account["external_account_id"], "name": account["name"], "account_type": account["type"], "mask": account["mask"]}, prefer="return=minimal")
        restarted = sync_connection(client, conn2["id"], SyntheticProvider(fixture, mutate_once_at=2))
        check("pagination_mutation_restarts_from_committed_cursor", restarted.restarts == 1 and counts(client, biz2)["posted"] == evaluation["expected"]["posted_transaction_count"], f"restarts={restarted.restarts}")
        businesses = [business_id, biz2]
    except Exception as exc:  # noqa: BLE001
        check("aborted", False, f"{type(exc).__name__}: {str(exc)[:200]}")
        businesses = [business_id]
    finally:
        for biz in list({business_id, *locals().get("businesses", [])}):
            for tx in client.rest_all("transactions", {"business_id": f"eq.{biz}", "select": "id", "order": "id"}):
                client.rest("DELETE", "transaction_sources", params={"transaction_id": f"eq.{tx['id']}"})
            client.rest("DELETE", "transactions", params={"business_id": f"eq.{biz}"})
            client.rest("DELETE", "businesses", params={"id": f"eq.{biz}", "data_mode": "eq.private"})
        left = client.count("businesses", {"slug": "like.synccheck-*"})
        check("temporary_businesses_deleted", left == 0, f"{left} left")
    failures = sum(1 for _, ok, _ in results if not ok)
    print(f"{len(results) - failures}/{len(results)} sync adapter checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
