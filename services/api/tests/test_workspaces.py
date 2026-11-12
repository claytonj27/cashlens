"""Scoped workspace reads against an in-memory PostgREST stand-in (no network)."""

import base64
import json
import re
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.supabase import SupabaseClient, get_supabase

CLIENT = {"x-cashlens-client": "web"}
GOOD_TOKEN, USER_ID = "good-token", "11111111-1111-4111-8111-111111111111"
BIZ, PRIVATE_BIZ = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2"
ACC1, ACC2, OTHER_ACC = (
    "cccccccc-cccc-4ccc-8ccc-ccccccccccc1",
    "cccccccc-cccc-4ccc-8ccc-ccccccccccc2",
    "cccccccc-cccc-4ccc-8ccc-ccccccccccc9",
)
WS_VISITOR, WS_USER = "dddddddd-dddd-4ddd-8ddd-ddddddddddd1", "dddddddd-dddd-4ddd-8ddd-ddddddddddd2"
VISITOR = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeee1"


def tx(
    n: int,
    posted: str,
    amount: int,
    account: str = ACC1,
    status: str = "posted",
    removed: bool = False,
    pending_link: str | None = None,
) -> dict[str, Any]:
    return {
        "id": f"ffffffff-ffff-4fff-8fff-{n:012d}",
        "business_id": BIZ,
        "account_id": account,
        "posted_date": posted,
        "description": f"Item {n}",
        "amount_minor": amount,
        "currency": "USD",
        "status": status,
        "source_type": "synthetic_provider",
        "revision": 1,
        "pending_transaction_id": pending_link,
        "external_id": f"tx-{n:04d}",
        "removed_at": "2026-08-30T00:00:00+00:00" if removed else None,
    }


# Three rows share 2026-08-10 so the tie-break on id is exercised across a page boundary.
ROWS = [
    tx(1, "2026-08-12", -500),
    tx(2, "2026-08-10", 1000),
    tx(3, "2026-08-10", -250, account=ACC2),
    tx(4, "2026-08-10", 300),
    tx(5, "2026-08-09", -75, status="pending"),
    tx(6, "2026-08-08", 4200, account=ACC2),
    tx(7, "2026-08-07", -60, removed=True),
    tx(8, "2026-08-06", 900, pending_link="ffffffff-ffff-4fff-8fff-000000000005"),
]


class FakePostgrest:
    """Evaluates the exact query shapes app.workspaces emits."""

    def __init__(self) -> None:
        self.rows = [dict(r) for r in ROWS]
        self.workspaces = [
            {"id": WS_VISITOR, "business_id": BIZ, "visitor_id": VISITOR, "user_id": None},
            {"id": WS_USER, "business_id": PRIVATE_BIZ, "visitor_id": None, "user_id": USER_ID},
        ]

    @staticmethod
    def _matches(row: dict[str, Any], key: str, spec: str) -> bool:
        op, _, value = spec.partition(".")
        current = row.get(key)
        if op == "eq":
            return str(current) == value
        if op == "is" and value == "null":
            return current is None
        if op == "gte":
            return current is not None and current >= value
        if op == "lte":
            return current is not None and current <= value
        if op == "lt":
            return current is not None and current < value
        if op == "ilike":
            needle = value.strip("*").lower()
            return needle in str(current).lower()
        raise AssertionError(f"unsupported filter {key}={spec}")

    def _filter_transactions(self, params: dict[str, str]) -> list[dict[str, Any]]:
        rows = self.rows
        for key, spec in params.items():
            if key in ("select", "order", "limit"):
                continue
            if key == "and":
                inner = re.fullmatch(r"\((posted_date)\.gte\.(\S+?),\1\.lte\.(\S+?)\)", spec)
                assert inner, spec
                rows = [r for r in rows if inner.group(2) <= r["posted_date"] <= inner.group(3)]
            elif key == "or":
                inner = re.fullmatch(
                    r"\(posted_date\.lt\.(\S+?),and\(posted_date\.eq\.\1,id\.lt\.(\S+?)\)\)", spec
                )
                assert inner, spec
                d, i = inner.group(1), inner.group(2)
                rows = [
                    r
                    for r in rows
                    if r["posted_date"] < d or (r["posted_date"] == d and r["id"] < i)
                ]
            else:
                rows = [r for r in rows if self._matches(r, key, spec)]
        order = params.get("order", "")
        if order:
            assert order == "posted_date.desc,id.desc", order
            rows = sorted(rows, key=lambda r: (r["posted_date"], r["id"]), reverse=True)
        if "limit" in params:
            rows = rows[: int(params["limit"])]
        return rows

    def handler(self, request: httpx.Request) -> httpx.Response:
        path, params = request.url.path, dict(request.url.params)
        if path == "/auth/v1/user":
            token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
            return (
                httpx.Response(200, json={"id": USER_ID, "aud": "authenticated"})
                if token == GOOD_TOKEN
                else httpx.Response(401, json={})
            )
        if path == "/rest/v1/visitors":
            return httpx.Response(
                200, json=[{"id": VISITOR}] if params.get("id") == f"eq.{VISITOR}" else []
            )
        if path == "/rest/v1/workspaces":
            rows = [
                w
                for w in self.workspaces
                if all(
                    str(w.get(k)) == v.removeprefix("eq.")
                    for k, v in params.items()
                    if k != "select"
                )
            ]
            return httpx.Response(200, json=rows)
        if path == "/rest/v1/business_memberships":
            ok = (
                params.get("business_id") == f"eq.{PRIVATE_BIZ}"
                and params.get("user_id") == f"eq.{USER_ID}"
            )
            return httpx.Response(200, json=[{"business_id": PRIVATE_BIZ}] if ok else [])
        if path == "/rest/v1/businesses":
            wanted = params.get("id", "").removeprefix("eq.")
            data = {
                BIZ: {
                    "id": BIZ,
                    "slug": "juniper-kitchen",
                    "name": "Juniper Kitchen",
                    "industry": "restaurant",
                    "data_mode": "demo",
                },
                PRIVATE_BIZ: {
                    "id": PRIVATE_BIZ,
                    "slug": "private-x",
                    "name": "Owner Co",
                    "industry": "unspecified",
                    "data_mode": "private",
                },
            }
            return httpx.Response(200, json=[data[wanted]] if wanted in data else [])
        if path == "/rest/v1/accounts":
            biz = params.get("business_id", "").removeprefix("eq.")
            accounts = (
                [
                    {
                        "id": ACC1,
                        "name": "Checking",
                        "account_type": "checking",
                        "mask": "4021",
                        "source_type": "synthetic_provider",
                    },
                    {
                        "id": ACC2,
                        "name": "Savings",
                        "account_type": "savings",
                        "mask": "4477",
                        "source_type": "synthetic_provider",
                    },
                ]
                if biz == BIZ
                else []
            )
            if request.headers.get("prefer") == "count=exact":
                return httpx.Response(
                    200, json=[], headers={"content-range": f"0-0/{len(accounts)}"}
                )
            return httpx.Response(200, json=accounts)
        if path == "/rest/v1/balance_snapshots":
            biz = params.get("business_id", "").removeprefix("eq.")
            snaps = (
                [
                    {
                        "account_id": ACC1,
                        "kind": "current",
                        "provenance": "bank_observed",
                        "amount_minor": 123456,
                        "as_of": "2026-08-31T23:59:59+00:00",
                        "source_type": "synthetic_provider",
                    },
                    {
                        "account_id": ACC1,
                        "kind": "current",
                        "provenance": "bank_observed",
                        "amount_minor": 100000,
                        "as_of": "2026-07-31T23:59:59+00:00",
                        "source_type": "synthetic_provider",
                    },
                    {
                        "account_id": ACC1,
                        "kind": "available",
                        "provenance": "bank_observed",
                        "amount_minor": 120000,
                        "as_of": "2026-08-31T23:59:59+00:00",
                        "source_type": "synthetic_provider",
                    },
                ]
                if biz == BIZ
                else []
            )
            return httpx.Response(200, json=snaps)
        if path == "/rest/v1/transactions":
            rows = self._filter_transactions(params)
            if request.headers.get("prefer") == "count=exact":
                return httpx.Response(200, json=[], headers={"content-range": f"0-0/{len(rows)}"})
            return httpx.Response(200, json=rows)
        if path == "/rest/v1/transaction_sources":
            tid = params.get("transaction_id", "").removeprefix("eq.")
            return httpx.Response(
                200, json=[{"raw_event_id": "99999999-9999-4999-8999-999999999999"}] if tid else []
            )
        if path == "/rest/v1/raw_events":
            return httpx.Response(
                200,
                json=[
                    {
                        "operation": "added",
                        "received_at": "2026-08-31T00:00:00+00:00",
                        "external_transaction_id": "tx-0001",
                        "sync_batch_id": "b1",
                        "payload": {"secret": "must not leak"},
                    }
                ],
            )
        if path == "/rest/v1/sync_batches":
            return httpx.Response(
                200,
                json=[
                    {
                        "start_cursor": "c0",
                        "end_cursor": "c1",
                        "applied_at": "2026-08-31T00:00:00+00:00",
                    }
                ],
            )
        return httpx.Response(404, json={"message": f"unhandled {path}"})


@pytest.fixture
def fake() -> FakePostgrest:
    fake = FakePostgrest()
    app.dependency_overrides[get_supabase] = lambda: SupabaseClient(
        "https://fake.supabase.local", "sb_secret_test", transport=httpx.MockTransport(fake.handler)
    )
    yield fake
    app.dependency_overrides.clear()


def visitor_client(client: TestClient) -> TestClient:
    client.cookies.set("cl_visitor", VISITOR)
    return client


def test_identity_precedence_and_not_found(fake: FakePostgrest) -> None:
    with TestClient(app) as client:
        assert client.get(f"/api/workspaces/{WS_VISITOR}").status_code == 401  # no identity
        assert (
            client.get(
                f"/api/workspaces/{WS_VISITOR}", headers={"authorization": "Bearer bad"}
            ).status_code
            == 401
        )
        client.cookies.set("cl_visitor", VISITOR)
        assert (
            client.get(
                f"/api/workspaces/{WS_VISITOR}", headers={"authorization": "Bearer bad"}
            ).status_code
            == 401
        )  # bad token wins over cookie
        assert client.get(f"/api/workspaces/{WS_VISITOR}").status_code == 200
        assert (
            client.get(f"/api/workspaces/{WS_USER}").status_code == 404
        )  # another identity's workspace: indistinguishable from missing
        assert client.get("/api/workspaces/not-a-uuid").status_code == 404
        assert client.get("/api/workspaces/dddddddd-dddd-4ddd-8ddd-ddddddddd999").status_code == 404
        client.cookies.clear()
        user = {"authorization": f"Bearer {GOOD_TOKEN}"}
        assert client.get(f"/api/workspaces/{WS_USER}", headers=user).status_code == 200
        assert client.get(f"/api/workspaces/{WS_VISITOR}", headers=user).status_code == 404


def test_accounts_report_latest_balance_per_kind_with_provenance(fake: FakePostgrest) -> None:
    with TestClient(app) as client:
        visitor_client(client)
        accounts = client.get(f"/api/workspaces/{WS_VISITOR}/accounts").json()
    checking = next(a for a in accounts if a["id"] == ACC1)
    kinds = {(b["kind"], b["provenance"]): b for b in checking["balances"]}
    assert (
        kinds[("current", "bank_observed")]["amount_minor"] == 123456
    )  # newest snapshot wins, older one ignored
    assert kinds[("current", "bank_observed")]["as_of"].startswith("2026-08-31")
    assert kinds[("available", "bank_observed")]["amount_minor"] == 120000
    savings = next(a for a in accounts if a["id"] == ACC2)
    assert savings["balances"] == []  # unknown balance is empty, never zero


def test_pagination_orders_by_date_then_id_across_duplicate_dates_without_gaps(
    fake: FakePostgrest,
) -> None:
    with TestClient(app) as client:
        visitor_client(client)
        seen: list[str] = []
        cursor = None
        pages = 0
        while True:
            params = {"limit": 2}
            if cursor:
                params["cursor"] = cursor
            page = client.get(f"/api/workspaces/{WS_VISITOR}/transactions", params=params)
            assert page.status_code == 200, page.text
            body = page.json()
            seen += [i["id"] for i in body["items"]]
            pages += 1
            assert body["total"] == 6  # posted, not removed
            cursor = body["next_cursor"]
            if cursor is None:
                break
    expected = [
        r["id"]
        for r in sorted(
            (r for r in ROWS if r["status"] == "posted" and not r["removed_at"]),
            key=lambda r: (r["posted_date"], r["id"]),
            reverse=True,
        )
    ]
    assert seen == expected and len(seen) == len(set(seen)) and pages == 3
    assert [r for r in ROWS if r["removed_at"]][0]["id"] not in seen


def test_filters_status_dates_account_and_search(fake: FakePostgrest) -> None:
    with TestClient(app) as client:
        visitor_client(client)
        base = f"/api/workspaces/{WS_VISITOR}/transactions"
        assert client.get(base, params={"status": "pending"}).json()["total"] == 1
        assert client.get(base, params={"status": "all"}).json()["total"] == 7
        assert (
            client.get(base, params={"from": "2026-08-10", "to": "2026-08-10"}).json()["total"] == 3
        )
        assert client.get(base, params={"account_id": ACC2}).json()["total"] == 2
        assert client.get(base, params={"q": "Item 4"}).json()["total"] == 1
        assert (
            client.get(base, params={"q": "(); drop *"}).json()["total"]
            == 0  # sanitized to "drop": no match
        )  # sanitized to "drop": matches nothing -> total counts rows containing 'drop'? none => 0
        assert (
            client.get(base, params={"from": "2026-08-12", "to": "2026-08-01"}).status_code == 422
        )
        assert client.get(base, params={"from": "not-a-date"}).status_code == 422
        assert client.get(base, params={"status": "weird"}).status_code == 422
        assert client.get(base, params={"limit": 0}).status_code == 422
        assert client.get(base, params={"limit": 101}).status_code == 422
        assert client.get(base, params={"account_id": "nope"}).status_code == 422
        assert (
            client.get(base, params={"account_id": OTHER_ACC}).status_code == 404
        )  # not this workspace's account
        empty = client.get(base, params={"from": "2030-01-01"}).json()
        assert empty["items"] == [] and empty["total"] == 0 and empty["next_cursor"] is None


def test_cursor_is_bound_to_workspace_and_filters(fake: FakePostgrest) -> None:
    with TestClient(app) as client:
        visitor_client(client)
        base = f"/api/workspaces/{WS_VISITOR}/transactions"
        first = client.get(base, params={"limit": 2}).json()
        cursor = first["next_cursor"]
        assert client.get(base, params={"limit": 2, "cursor": cursor}).status_code == 200
        assert (
            client.get(base, params={"limit": 2, "cursor": cursor, "status": "all"}).status_code
            == 400
        )
        assert (
            client.get(base, params={"limit": 2, "cursor": cursor, "account_id": ACC2}).status_code
            == 400
        )
        assert client.get(base, params={"limit": 2, "cursor": "garbage"}).status_code == 400
        forged = (
            base64.urlsafe_b64encode(
                json.dumps(
                    {"v": 1, "d": "2026-08-10", "i": ROWS[1]["id"], "h": "0000000000000000"}
                ).encode()
            )
            .decode()
            .rstrip("=")
        )
        assert client.get(base, params={"limit": 2, "cursor": forged}).status_code == 400
        # a cursor for another workspace is rejected on the user's own workspace too
        client.cookies.clear()
        assert (
            client.get(
                f"/api/workspaces/{WS_USER}/transactions",
                params={"cursor": cursor},
                headers={"authorization": f"Bearer {GOOD_TOKEN}"},
            ).status_code
            == 400
        )


def test_pages_read_live_data_and_continue_after_a_removal(fake: FakePostgrest) -> None:
    with TestClient(app) as client:
        visitor_client(client)
        base = f"/api/workspaces/{WS_VISITOR}/transactions"
        first = client.get(base, params={"limit": 2}).json()
        # a row on the next page is removed and a newer row appears between requests
        fake.rows[2]["removed_at"] = (
            "2026-09-01T00:00:00+00:00"  # tx 3 (2026-08-10), still ahead of the cursor
        )
        fake.rows.append(tx(9, "2026-08-13", 10))
        second = client.get(base, params={"limit": 2, "cursor": first["next_cursor"]}).json()
        ids = [i["id"] for i in first["items"]] + [i["id"] for i in second["items"]]
        assert fake.rows[2]["id"] not in ids  # removed row is skipped, no error
        assert (
            fake.rows[-1]["id"] not in ids
        )  # newer row is not surfaced on a later page (documented)
        assert second["total"] == 6  # live count: one removed, one added


def test_detail_returns_allowlisted_evidence_only(fake: FakePostgrest) -> None:
    with TestClient(app) as client:
        visitor_client(client)
        detail = client.get(f"/api/workspaces/{WS_VISITOR}/transactions/{ROWS[7]['id']}")
        assert detail.status_code == 200
        body = detail.json()
        assert (
            body["transaction"]["replaces_pending"] is True
            and body["pending_transaction"]["status"] == "pending"
        )
        assert (
            body["sources"][0]["operation"] == "added"
            and body["sources"][0]["batch_end_cursor"] == "c1"
        )
        assert "must not leak" not in detail.text and "payload" not in detail.text
        assert (
            client.get(f"/api/workspaces/{WS_VISITOR}/transactions/{ROWS[6]['id']}").status_code
            == 404
        )  # removed
        assert (
            client.get(f"/api/workspaces/{WS_VISITOR}/transactions/not-a-uuid").status_code == 404
        )
        client.cookies.clear()
        assert (
            client.get(
                f"/api/workspaces/{WS_USER}/transactions/{ROWS[0]['id']}",
                headers={"authorization": f"Bearer {GOOD_TOKEN}"},
            ).status_code
            == 404
        )  # other business's transaction id


def test_workspace_routes_are_404_where_features_are_off(
    monkeypatch: pytest.MonkeyPatch, fake: FakePostgrest
) -> None:
    """Production default: the gate answers before identity, so even a bare request is 404."""
    wid = "00000000-0000-4000-8000-000000000000"
    with TestClient(app) as client:
        assert client.get(f"/api/workspaces/{wid}").status_code == 401  # on: identity first
    monkeypatch.setenv("APP_ENV", "production")
    get_settings.cache_clear()
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
        assert client.get(f"/api/workspaces/{wid}").status_code == 404
        assert client.get(f"/api/workspaces/{wid}/accounts").status_code == 404
        assert client.get(f"/api/workspaces/{wid}/transactions").status_code == 404
        assert client.get(f"/api/workspaces/{wid}/transactions/{wid}").status_code == 404
