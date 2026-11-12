"""Retention cleanup: its own switch, its own token, and honest reporting of what it removed."""

import json
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.supabase import SupabaseClient, get_supabase

TOKEN = "test-cleanup-token"
AUTH = {"x-cleanup-token": TOKEN}
PATH = "/api/maintenance/retention"


class FakeRetention:
    """Just enough of the database and Storage to drive the route's own logic."""

    def __init__(self) -> None:
        self.expired: list[dict[str, Any]] = []
        self.backlog: list[dict[str, Any]] = []
        self.visitors_removed = 0
        self.deleted_paths: list[str] = []
        self.marked_deleted: list[str] = []
        self.marked_failed: list[tuple[str, str]] = []
        self.storage_fails: set[str] = set()
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.queue: list[dict[str, Any]] = []  # storage_cleanup, the durable inventory
        self.queue_deleted: list[str] = []
        self.queue_failed: list[tuple[str, str]] = []
        self.tombstones: list[dict[str, Any]] = []
        self.finalized: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.startswith("/rest/v1/rpc/"):
            name = path.rsplit("/", 1)[-1]
            body = json.loads(request.content or b"{}")
            self.calls.append((name, body))
            return self.rpc(name, body)
        if path == "/storage/v1/object/statements" and request.method == "DELETE":
            target = json.loads(request.content)["prefixes"][0]
            if target in self.storage_fails:
                return httpx.Response(503, json={"message": "Service Unavailable"})
            self.deleted_paths.append(target)
            return httpx.Response(200, json=[])
        return httpx.Response(404, json={"message": f"unhandled {path}"})

    def rpc(self, name: str, body: dict[str, Any]) -> httpx.Response:
        if name == "retention_sweep_imports":
            return httpx.Response(200, json=self.expired[: body["p_limit"]])
        if name == "retention_storage_backlog":
            pending = [b for b in self.backlog if b["object_path"] not in self.marked_deleted]
            return httpx.Response(200, json=pending[: body["p_limit"]])
        if name == "retention_mark_object_deleted":
            item = next(b for b in self.backlog if b["import_id"] == body["p_import_id"])
            self.marked_deleted.append(item["object_path"])
            return httpx.Response(
                200, content=b"true", headers={"content-type": "application/json"}
            )
        if name == "retention_mark_object_failed":
            self.marked_failed.append((body["p_import_id"], body["p_error"]))
            return httpx.Response(200, content=b"1", headers={"content-type": "application/json"})
        if name == "storage_cleanup_backlog":
            pending = [q for q in self.queue if q["id"] not in self.queue_deleted]
            return httpx.Response(200, json=pending[: body["p_limit"]])
        if name == "storage_cleanup_mark_deleted":
            self.queue_deleted.append(body["p_id"])
            return httpx.Response(
                200, content=b"true", headers={"content-type": "application/json"}
            )
        if name == "storage_cleanup_mark_failed":
            self.queue_failed.append((body["p_id"], body["p_error"]))
            return httpx.Response(200, content=b"1", headers={"content-type": "application/json"})
        if name == "pending_business_deletions":
            live = [
                {
                    "business_id": t["business_id"],
                    "objects_pending": len(
                        [
                            q
                            for q in self.queue
                            if q["business_id"] == t["business_id"]
                            and q["id"] not in self.queue_deleted
                        ]
                    ),
                }
                for t in self.tombstones
                if t["business_id"] not in self.finalized
            ]
            return httpx.Response(200, json=live)
        if name == "finalize_business_deletion":
            self.finalized.append(body["p_business_id"])
            return httpx.Response(
                200, content=b"true", headers={"content-type": "application/json"}
            )
        if name == "retention_sweep_visitors":
            return httpx.Response(
                200,
                content=str(self.visitors_removed).encode(),
                headers={"content-type": "application/json"},
            )
        return httpx.Response(404, json={"message": f"unhandled rpc {name}"})


@pytest.fixture
def backend(monkeypatch: pytest.MonkeyPatch) -> FakeRetention:
    fake = FakeRetention()
    app.dependency_overrides[get_supabase] = lambda: SupabaseClient(
        "https://fake.supabase.local", "sb_secret_test", transport=httpx.MockTransport(fake.handler)
    )
    yield fake
    app.dependency_overrides.clear()


@pytest.fixture
def enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RETENTION_CLEANUP_ENABLED", "true")
    monkeypatch.setenv("RETENTION_CLEANUP_TOKEN", TOKEN)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_cleanup_does_not_exist_when_switched_off() -> None:
    with TestClient(app) as client:
        assert client.post(PATH, headers=AUTH, json={}).status_code == 404


def test_cleanup_does_not_exist_when_enabled_without_a_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RETENTION_CLEANUP_ENABLED", "true")
    monkeypatch.delenv("RETENTION_CLEANUP_TOKEN", raising=False)
    get_settings.cache_clear()
    with TestClient(app) as client:
        assert client.post(PATH, headers=AUTH, json={}).status_code == 404
    get_settings.cache_clear()


def test_missing_or_wrong_token_is_refused(enabled: None, backend: FakeRetention) -> None:
    with TestClient(app) as client:
        assert client.post(PATH, json={}).status_code == 401
        assert client.post(PATH, headers={"x-cleanup-token": "wrong"}, json={}).status_code == 401
    assert backend.calls == []  # nothing was swept on the way to being refused


def test_cleanup_runs_while_the_diagnostics_router_stays_unavailable(
    enabled: None, backend: FakeRetention
) -> None:
    """The two switches are independent: cleanup must not turn on the database or model probes."""
    with TestClient(app) as client:
        assert client.post(PATH, headers=AUTH, json={}).status_code == 200
        assert client.get("/api/ops/config", headers={"x-ops-token": TOKEN}).status_code == 404
        assert client.post("/api/ops/db-probe", headers={"x-ops-token": TOKEN}).status_code == 404
        assert (
            client.post(
                "/api/ops/openai-probe", headers={"x-ops-token": TOKEN}, json={}
            ).status_code
            == 404
        )


def test_a_run_reports_what_it_actually_removed(enabled: None, backend: FakeRetention) -> None:
    backend.expired = [
        {
            "import_id": "11111111-1111-4111-8111-111111111111",
            "business_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb1",
            "reason": "expired_unstaged",
            "rows_removed": 0,
            "object_path": "b/1.csv",
            "file_removed": False,
        },
        {
            "import_id": "22222222-2222-4222-8222-222222222222",
            "business_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb1",
            "reason": "expired_staged",
            "rows_removed": 12,
            "object_path": "b/2.csv",
            "file_removed": False,
        },
    ]
    backend.backlog = [
        {
            "import_id": e["import_id"],
            "business_id": e["business_id"],
            "object_path": e["object_path"],
            "attempts": 0,
        }
        for e in backend.expired
    ]
    backend.visitors_removed = 3
    with TestClient(app) as client:
        result = client.post(PATH, headers=AUTH, json={}).json()
    assert result["imports_expired"] == 2 and result["rows_removed"] == 12
    assert result["objects_deleted"] == 2 and result["objects_failed"] == 0
    assert result["objects_pending"] == 0 and result["visitors_removed"] == 3
    assert sorted(backend.deleted_paths) == ["b/1.csv", "b/2.csv"]


def test_a_storage_failure_is_recorded_and_never_counted_as_deleted(
    enabled: None, backend: FakeRetention
) -> None:
    backend.backlog = [
        {
            "import_id": "11111111-1111-4111-8111-111111111111",
            "business_id": "b",
            "object_path": "b/1.csv",
            "attempts": 0,
        },
        {
            "import_id": "22222222-2222-4222-8222-222222222222",
            "business_id": "b",
            "object_path": "b/2.csv",
            "attempts": 0,
        },
    ]
    backend.storage_fails = {"b/1.csv"}
    with TestClient(app) as client:
        result = client.post(PATH, headers=AUTH, json={}).json()
    assert result["objects_deleted"] == 1  # only the one Storage confirmed
    assert result["objects_failed"] == 1
    assert result["objects_pending"] == 1  # the failure stays eligible for the next run
    assert [i for i, _ in backend.marked_failed] == ["11111111-1111-4111-8111-111111111111"]
    assert backend.marked_deleted == ["b/2.csv"]
    # the retry succeeds and the backlog empties
    backend.storage_fails = set()
    with TestClient(app) as client:
        again = client.post(PATH, headers=AUTH, json={}).json()
    assert again["objects_deleted"] == 1 and again["objects_pending"] == 0


def test_the_batch_limit_and_the_scope_are_passed_to_the_database(
    enabled: None, backend: FakeRetention
) -> None:
    """Bounding and narrowing happen in SQL, not by slicing a full result in Python."""
    business = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb1"
    with TestClient(app) as client:
        client.post(
            PATH, headers=AUTH, json={"limit": 5, "business_id": business, "visitor_limit": 0}
        )
    sweep = next(body for name, body in backend.calls if name == "retention_sweep_imports")
    assert sweep == {"p_limit": 5, "p_business_id": business}
    assert not any(name == "retention_sweep_visitors" for name, _ in backend.calls)


def test_the_run_log_carries_counts_and_no_content(
    enabled: None, backend: FakeRetention, capsys: pytest.CaptureFixture[str]
) -> None:
    backend.backlog = [
        {
            "import_id": "11111111-1111-4111-8111-111111111111",
            "business_id": "b",
            "object_path": "secret/name.csv",
            "attempts": 0,
        }
    ]
    with TestClient(app) as client:
        client.post(PATH, headers=AUTH, json={})
    printed = capsys.readouterr().out
    runs = [
        json.loads(line)
        for line in printed.splitlines()
        if line.startswith("{") and '"retention.run"' in line
    ]
    assert len(runs) == 1 and runs[0]["objects_deleted"] == 1
    assert "secret/name.csv" not in printed  # counts only, never a path or a file name


def test_a_deleted_business_is_finished_only_once_its_files_are_gone(
    enabled: None, backend: FakeRetention
) -> None:
    """The tombstone is what lets the owner watch the removal, so it goes last."""
    business = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb1"
    backend.queue = [
        {
            "id": "q1",
            "bucket": "statements",
            "object_path": f"{business}/a.csv",
            "business_id": business,
            "attempts": 0,
        },
        {
            "id": "q2",
            "bucket": "statements",
            "object_path": f"{business}/b.pdf",
            "business_id": business,
            "attempts": 0,
        },
    ]
    backend.tombstones = [{"business_id": business}]
    backend.storage_fails = {f"{business}/b.pdf"}
    with TestClient(app) as client:
        first = client.post(PATH, headers=AUTH, json={}).json()
    assert first["objects_deleted"] == 1 and first["objects_failed"] == 1
    assert first["objects_pending"] == 1 and first["businesses_finalized"] == 0
    assert backend.finalized == []  # a file is still there, so the business stays
    backend.storage_fails = set()
    with TestClient(app) as client:
        second = client.post(PATH, headers=AUTH, json={}).json()
    assert second["objects_deleted"] == 1 and second["objects_pending"] == 0
    assert second["businesses_finalized"] == 1 and backend.finalized == [business]


def test_the_queued_inventory_is_drained_even_with_no_imports_left(
    enabled: None, backend: FakeRetention
) -> None:
    """The queue has no foreign key, so it still works after the import rows are cascaded away."""
    backend.expired = []
    backend.backlog = []
    backend.queue = [
        {
            "id": "q1",
            "bucket": "statements",
            "object_path": "gone-business/x.csv",
            "business_id": None,
            "attempts": 3,
        }
    ]
    with TestClient(app) as client:
        result = client.post(PATH, headers=AUTH, json={}).json()
    assert result["objects_deleted"] == 1 and result["objects_pending"] == 0
    assert backend.deleted_paths == ["gone-business/x.csv"]
