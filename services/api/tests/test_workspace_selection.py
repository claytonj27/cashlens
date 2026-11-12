"""A caller with several owned businesses: provisioning, listing, selection and import ownership."""

import json
import uuid
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.supabase import SupabaseClient, get_supabase

USER_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1"
USER_B = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2"
TOKEN_A, TOKEN_B = "token-a", "token-b"
CLIENT = {"x-cashlens-client": "web"}
AUTH_A = {"authorization": f"Bearer {TOKEN_A}", **CLIENT}
AUTH_B = {"authorization": f"Bearer {TOKEN_B}", **CLIENT}


class RefusedError(Exception):
    pass


class FakeDatabase:
    """The Data API shapes these routes use, plus a mirror of provision_private_business."""

    def __init__(self) -> None:
        self.clock = 0
        self.tables: dict[str, list[dict[str, Any]]] = {
            "businesses": [],
            "business_memberships": [],
            "workspaces": [],
            "accounts": [],
            "transactions": [],
            "document_imports": [],
            "import_rows": [],
        }

    # ---- provisioning mirror ----
    def provision(self, body: dict[str, Any]) -> list[dict[str, Any]]:
        user_id = body["p_user_id"]
        key = (body.get("p_idempotency_key") or "").strip() or None
        if key is not None and not 8 <= len(key) <= 120:
            raise RefusedError("idempotency key must be 8 to 120 characters")
        mine = sorted(
            (m for m in self.tables["business_memberships"] if m["user_id"] == user_id),
            key=lambda m: m["created_at"],
        )
        match = mine[:1] if key is None else [m for m in mine if m.get("provisioning_key") == key]
        if match:
            business_id, created = match[0]["business_id"], False
        else:
            name = (body.get("p_business_name") or "").strip()
            if not name or len(name) > 120:
                raise RefusedError("business name required (1 to 120 characters)")
            business_id = str(uuid.uuid4())
            self.clock += 1
            self.tables["businesses"].append(
                {
                    "id": business_id,
                    "slug": f"private-{business_id[:8]}",
                    "name": name,
                    "industry": "unspecified",
                    "data_mode": "private",
                }
            )
            self.tables["business_memberships"].append(
                {
                    "business_id": business_id,
                    "business_data_mode": "private",
                    "user_id": user_id,
                    "role": "owner",
                    "provisioning_key": key,
                    "created_at": f"2026-09-13T00:00:{self.clock:02d}+00:00",
                }
            )
            created = True
        workspace = next(
            (
                w
                for w in self.tables["workspaces"]
                if w["user_id"] == user_id and w["business_id"] == business_id
            ),
            None,
        )
        if workspace is None:
            workspace = {
                "id": str(uuid.uuid4()),
                "business_id": business_id,
                "business_data_mode": "private",
                "user_id": user_id,
                "visitor_id": None,
            }
            self.tables["workspaces"].append(workspace)
        name = next(b["name"] for b in self.tables["businesses"] if b["id"] == business_id)
        return [
            {
                "business_id": business_id,
                "workspace_id": workspace["id"],
                "business_name": name,
                "created": created,
            }
        ]

    # ---- query evaluation ----
    @staticmethod
    def _match(row: dict[str, Any], key: str, spec: str) -> bool:
        op, _, value = spec.partition(".")
        current = row.get(key)
        if op == "eq":
            return str(current) == value
        if op == "neq":
            return str(current) != value
        if op == "is":
            return current is None if value == "null" else current is not None
        if op == "in":
            return str(current) in value.strip("()").split(",")
        raise AssertionError(f"unsupported filter {key}={spec}")

    def select(self, table: str, params: dict[str, str]) -> list[dict[str, Any]]:
        rows = self.tables[table]
        for key, spec in params.items():
            if key in ("select", "order", "limit", "offset"):
                continue
            rows = [r for r in rows if self._match(r, key, spec)]
        if "order" in params:
            column = params["order"].split(".")[0]
            rows = sorted(rows, key=lambda r: str(r.get(column)))
        if "limit" in params:
            rows = rows[: int(params["limit"])]
        wanted = params.get("select")
        if wanted and wanted != "*":
            keys = [c.strip() for c in wanted.split(",")]
            rows = [{k: r.get(k) for k in keys} for r in rows]
        return rows

    def handler(self, request: httpx.Request) -> httpx.Response:
        path, params = request.url.path, dict(request.url.params)
        if path == "/auth/v1/user":
            token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
            users = {TOKEN_A: USER_A, TOKEN_B: USER_B}
            if token in users:
                return httpx.Response(
                    200, json={"id": users[token], "aud": "authenticated", "email": "o@example.org"}
                )
            return httpx.Response(401, json={})
        if path == "/rest/v1/rpc/provision_private_business":
            try:
                return httpx.Response(200, json=self.provision(json.loads(request.content)))
            except RefusedError as exc:
                return httpx.Response(400, json={"message": str(exc)})
        if path.startswith("/rest/v1/"):
            table = path.removeprefix("/rest/v1/")
            assert table in self.tables, table
            rows = self.select(table, params)
            if request.headers.get("prefer") == "count=exact":
                return httpx.Response(200, json=[], headers={"content-range": f"0-0/{len(rows)}"})
            return httpx.Response(200, json=rows)
        return httpx.Response(404, json={"message": f"unhandled {path}"})


@pytest.fixture
def database() -> FakeDatabase:
    fake = FakeDatabase()
    app.dependency_overrides[get_supabase] = lambda: SupabaseClient(
        "https://fake.supabase.local", "sb_secret_test", transport=httpx.MockTransport(fake.handler)
    )
    yield fake
    app.dependency_overrides.clear()


def onboard(
    client: TestClient, name: str = "First Co", headers: dict[str, str] = AUTH_A
) -> dict[str, Any]:
    response = client.post("/api/private/provision", headers=headers, json={"business_name": name})
    assert response.status_code == 200, response.text
    return response.json()


def add_business(
    client: TestClient, name: str, key: str, headers: dict[str, str] = AUTH_A
) -> httpx.Response:
    return client.post(
        "/api/private/businesses", headers=headers, json={"name": name, "idempotency_key": key}
    )


def test_ordinary_onboarding_still_resumes_the_first_business(database: FakeDatabase) -> None:
    with TestClient(app) as client:
        first = onboard(client, "Cash Lens")
        assert first["created"] is True
        again = onboard(client, "A Different Name")
        assert again["created"] is False and again["business"] == first["business"]
        me = client.get("/api/me", headers=AUTH_A).json()
        assert me["business"] == first["business"] and me["workspace_id"] == first["workspace_id"]
        assert len(me["businesses"]) == 1
        assert len(database.tables["businesses"]) == 1


def test_an_additional_business_is_created_once_however_often_it_is_retried(
    database: FakeDatabase,
) -> None:
    with TestClient(app) as client:
        first = onboard(client, "Cash Lens")
        created = add_business(client, "Cedar Corner PDF Test", "cedar-pdf-test-1")
        assert created.status_code == 200 and created.json()["created"] is True
        second = created.json()
        assert second["business"]["id"] != first["business"]["id"]
        for _ in range(3):  # retries and concurrent duplicates resolve to the same business
            repeat = add_business(client, "Cedar Corner PDF Test", "cedar-pdf-test-1").json()
            assert repeat["created"] is False and repeat["business"] == second["business"]
            assert repeat["workspace_id"] == second["workspace_id"]
        assert len(database.tables["businesses"]) == 2
        third = add_business(client, "Another One", "cedar-pdf-test-2").json()
        assert third["created"] is True and len(database.tables["businesses"]) == 3
        me = client.get("/api/me", headers=AUTH_A).json()
        assert [b["business"]["name"] for b in me["businesses"]] == [
            "Cash Lens",
            "Cedar Corner PDF Test",
            "Another One",
        ]
        assert me["business"]["name"] == "Cash Lens"  # the default stays the earliest


def test_additional_business_requires_a_usable_key_and_the_caller_owns_the_result(
    database: FakeDatabase,
) -> None:
    with TestClient(app) as client:
        onboard(client, "Cash Lens")
        assert add_business(client, "X", "short").status_code == 422
        assert add_business(client, "", "long-enough-key").status_code == 422
        assert add_business(client, "X", "spaces are not allowed").status_code == 422
        assert (
            client.post(
                "/api/private/businesses",
                headers={"authorization": f"Bearer {TOKEN_A}"},
                json={"name": "X", "idempotency_key": "no-client-header"},
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/api/private/businesses",
                headers=CLIENT,
                json={"name": "X", "idempotency_key": "no-token-at-all"},
            ).status_code
            == 401
        )
        # user B asking with the same key gets their own business, never A's
        onboard(client, "B Co", headers=AUTH_B)
        mine = add_business(client, "Mine", "shared-key-value", headers=AUTH_A).json()
        theirs = add_business(client, "Theirs", "shared-key-value", headers=AUTH_B).json()
        assert mine["business"]["id"] != theirs["business"]["id"]


def test_both_owned_workspaces_are_reachable_and_selection_never_falls_back(
    database: FakeDatabase,
) -> None:
    with TestClient(app) as client:
        first = onboard(client, "Cash Lens")
        second = add_business(client, "Cedar Corner PDF Test", "cedar-pdf-test-1").json()
        default = client.get("/api/private/workspace", headers=AUTH_A).json()
        assert default["business"]["name"] == "Cash Lens"
        selected = client.get(
            f"/api/private/workspace?workspace={second['workspace_id']}", headers=AUTH_A
        ).json()
        assert selected["business"] == second["business"]
        assert selected["workspace_id"] == second["workspace_id"]
        assert selected["account_count"] == 0 and selected["transaction_count"] == 0
        same = client.get(
            f"/api/private/workspace?workspace={first['workspace_id']}", headers=AUTH_A
        ).json()
        assert same["business"] == first["business"]
        for bad in ("not-a-uuid", str(uuid.uuid4()), ""):
            response = client.get(f"/api/private/workspace?workspace={bad}", headers=AUTH_A)
            assert response.status_code == 404, bad  # never the first business instead


def test_another_users_workspace_is_denied(database: FakeDatabase) -> None:
    with TestClient(app) as client:
        onboard(client, "Cash Lens")
        mine = add_business(client, "Cedar Corner PDF Test", "cedar-pdf-test-1").json()
        theirs = onboard(client, "B Co", headers=AUTH_B)
        assert (
            client.get(
                f"/api/private/workspace?workspace={mine['workspace_id']}", headers=AUTH_B
            ).status_code
            == 404
        )
        assert (
            client.get(
                f"/api/private/workspace?workspace={theirs['workspace_id']}", headers=AUTH_A
            ).status_code
            == 404
        )
        assert (
            client.get(f"/api/workspaces/{mine['workspace_id']}", headers=AUTH_B).status_code == 404
        )
        assert (
            client.get(f"/api/private/workspace?workspace={mine['workspace_id']}").status_code
            == 401
        )


def test_an_import_resolves_to_its_own_workspace_whatever_the_link_says(
    database: FakeDatabase,
) -> None:
    with TestClient(app) as client:
        first = onboard(client, "Cash Lens")
        second = add_business(client, "Cedar Corner PDF Test", "cedar-pdf-test-1").json()
        theirs = onboard(client, "B Co", headers=AUTH_B)
        import_id = str(uuid.uuid4())
        database.tables["document_imports"].append(
            {
                "id": import_id,
                "business_id": second["business"]["id"],
                "status": "staged",
                "file_label": "01_history.pdf",
            }
        )
        located = client.get(f"/api/private/imports/{import_id}", headers=AUTH_A).json()
        assert located["workspace_id"] == second["workspace_id"]
        assert located["business"] == second["business"] and located["status"] == "staged"
        assert located["workspace_id"] != first["workspace_id"]
        # the other owner and a stranger see nothing
        assert client.get(f"/api/private/imports/{import_id}", headers=AUTH_B).status_code == 404
        assert client.get(f"/api/private/imports/{uuid.uuid4()}", headers=AUTH_A).status_code == 404
        assert client.get("/api/private/imports/not-a-uuid", headers=AUTH_A).status_code == 404
        assert client.get(f"/api/private/imports/{import_id}").status_code == 401
        assert theirs["workspace_id"] not in json.dumps(located)


def test_a_mismatched_workspace_in_the_path_cannot_retarget_an_import(
    database: FakeDatabase,
) -> None:
    with TestClient(app) as client:
        first = onboard(client, "Cash Lens")
        second = add_business(client, "Cedar Corner PDF Test", "cedar-pdf-test-1").json()
        import_id = str(uuid.uuid4())
        database.tables["document_imports"].append(
            {
                "id": import_id,
                "business_id": second["business"]["id"],
                "status": "staged",
                "format": "pdf",
                "file_label": "01_history.pdf",
                "draft_revision": 1,
                "created_at": "2026-09-13T00:00:00+00:00",
                "expires_at": "2026-09-14T00:00:00+00:00",
            }
        )
        wrong = f"/api/workspaces/{first['workspace_id']}/imports/{import_id}"
        assert client.get(wrong, headers=AUTH_A).status_code == 404
        assert client.get(f"{wrong}/rows", headers=AUTH_A).status_code == 404
        assert (
            client.post(f"{wrong}/confirm", headers=AUTH_A, json={"revision": 1}).status_code == 404
        )
        assert client.post(f"{wrong}/cancel", headers=AUTH_A).status_code == 404
        assert (
            client.post(f"{wrong}/decisions", headers=AUTH_A, json={"decision": "keep"}).status_code
            == 404
        )
        right = f"/api/workspaces/{second['workspace_id']}/imports/{import_id}"
        assert client.get(right, headers=AUTH_A).status_code == 200
        assert database.tables["document_imports"][0]["business_id"] == second["business"]["id"]
