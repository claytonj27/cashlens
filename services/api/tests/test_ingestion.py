"""Synthetic adapter conversion and incremental sync orchestration (no database)."""

from typing import Any

import pytest

from app.ingestion.contract import PaginationMutationError, ProviderEvent, ProviderPage
from app.ingestion.sync import SyncError, sync_connection
from app.ingestion.synthetic import SyntheticProvider, load_fixture, to_canonical
from app.supabase import SupabaseError

CONNECTION = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"


def test_provider_sign_convention_is_converted_in_the_adapter() -> None:
    record = {
        "account_id": "acct-1",
        "transaction_id": "tx-1",
        "amount": "12.34",
        "date": "2026-03-01",
        "name": "RENT",
        "pending": False,
        "pending_transaction_id": None,
        "iso_currency_code": "USD",
    }
    canonical = to_canonical(record)
    assert canonical.amount_minor == -1234 and canonical.status == "posted"
    assert to_canonical({**record, "amount": "-5.00", "pending": True}).amount_minor == 500
    assert to_canonical({**record, "pending": True}).status == "pending"
    with pytest.raises(ValueError):
        to_canonical({**record, "iso_currency_code": "EUR"})


def test_fixture_provider_pages_by_cursor_and_limits_availability() -> None:
    fixture = load_fixture("willow-studio")
    provider = SyntheticProvider(fixture, available_pages=2)
    first = provider.fetch(None)
    assert first.has_more and first.next_cursor == fixture["pages"][0]["next_cursor"]
    second = provider.fetch(first.next_cursor)
    assert not second.has_more  # the provider has only two pages "so far"
    later = SyntheticProvider(fixture).fetch(second.next_cursor)
    assert later.events and later.cursor == second.next_cursor
    with pytest.raises(ValueError):
        provider.fetch("nope")
    assert all(e.canonical is not None for e in first.events if e.operation != "removed")


class FakeClient:
    """Records the staging calls and answers the apply RPC as instructed."""

    def __init__(self, cursor: str | None = None, apply_error: str | None = None) -> None:
        self.cursor = cursor
        self.apply_error = apply_error
        self.batches: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.patches: list[dict[str, Any]] = []

    def rest(self, method: str, path: str, *, params=None, json_body=None, prefer=None):
        if path == "connections":
            return [
                {
                    "id": CONNECTION,
                    "business_id": "b",
                    "sync_cursor": self.cursor,
                    "status": "active",
                }
            ]
        if path == "sync_batches" and method == "POST":
            self.batches.append(json_body)
            return [{"id": f"batch-{len(self.batches)}"}]
        if path == "sync_batches" and method == "PATCH":
            self.patches.append({**params, **json_body})
            return None
        if path == "raw_events":
            self.events.extend(json_body)
            return None
        raise AssertionError(f"unexpected {method} {path}")

    def rpc(self, name: str, params: dict[str, Any]):
        assert name == "apply_sync_batch"
        if self.apply_error:
            raise SupabaseError(self.apply_error)
        adds = sum(1 for e in self.events if e["operation"] == "added")
        return [
            {
                "added_count": adds,
                "modified_count": 0,
                "removed_count": 0,
                "unchanged_count": 0,
                "end_cursor": self.batches[-1]["end_cursor"],
            }
        ]


class ScriptedProvider:
    def __init__(self, pages: list[ProviderPage], mutate_first: int = 0) -> None:
        self.pages = pages
        self.mutate_first = mutate_first
        self.fetches = 0

    def fetch(self, cursor: str | None) -> ProviderPage:
        self.fetches += 1
        if self.mutate_first:
            self.mutate_first -= 1
            raise PaginationMutationError()
        for page in self.pages:
            if page.cursor == cursor:
                return page
        raise AssertionError(f"unexpected cursor {cursor}")


def page(cursor: str | None, nxt: str, more: bool, n: int) -> ProviderPage:
    events = [
        ProviderEvent(
            "added",
            f"tx-{cursor}-{i}",
            {"transaction_id": f"tx-{cursor}-{i}"},
            to_canonical(
                {
                    "account_id": "acct",
                    "transaction_id": f"tx-{cursor}-{i}",
                    "amount": "1.00",
                    "date": "2026-01-01",
                    "name": "x",
                    "pending": False,
                }
            ),
        )
        for i in range(n)
    ]
    return ProviderPage(cursor, nxt, more, events)


def test_sync_stages_every_page_then_applies_once() -> None:
    client = FakeClient()
    provider = ScriptedProvider([page(None, "c1", True, 2), page("c1", "c2", False, 3)])
    result = sync_connection(client, CONNECTION, provider)
    assert result.pages == 2 and result.events == 5 and result.added == 5
    assert result.end_cursor == "c2" and result.restarts == 0
    assert client.batches == [
        {"connection_id": CONNECTION, "start_cursor": None, "end_cursor": "c2", "status": "staged"}
    ]
    assert [e["sequence"] for e in client.events] == [0, 1, 2, 3, 4]
    assert client.events[0]["canonical"]["amount_minor"] == -100  # adapter conversion staged


def test_sync_is_a_no_op_when_the_provider_has_nothing_new() -> None:
    client = FakeClient(cursor="c2")
    provider = ScriptedProvider([ProviderPage("c2", "c2", False, [])])
    result = sync_connection(client, CONNECTION, provider)
    assert result.batch_id is None and result.events == 0 and client.batches == []


def test_pagination_mutation_restarts_from_the_committed_cursor() -> None:
    client = FakeClient()
    provider = ScriptedProvider([page(None, "c1", False, 1)], mutate_first=2)
    result = sync_connection(client, CONNECTION, provider)
    assert result.restarts == 2 and result.added == 1 and provider.fetches == 3
    with pytest.raises(SyncError):
        sync_connection(
            FakeClient(),
            CONNECTION,
            ScriptedProvider([page(None, "c1", False, 1)], mutate_first=99),
        )


def test_failed_apply_marks_the_batch_failed_and_keeps_the_cursor() -> None:
    client = FakeClient(cursor="c0", apply_error='{"message":"cursor_mismatch"}')
    provider = ScriptedProvider([page("c0", "c1", False, 1)])
    with pytest.raises(SyncError):
        sync_connection(client, CONNECTION, provider)
    assert client.patches == [{"id": "eq.batch-1", "status": "failed"}]
    assert client.cursor == "c0"  # nothing here moves the cursor; only apply_sync_batch does
