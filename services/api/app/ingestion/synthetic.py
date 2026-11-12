"""The synthetic provider adapter: Plaid-like fixture pages to the canonical contract.

Sign convention: a positive provider amount is money leaving the account, so
canonical = -(amount * 100). Nothing here touches the database.
"""

import json
from pathlib import Path
from typing import Any

from app.ingestion.contract import (
    BalanceObservation,
    CanonicalTransaction,
    PaginationMutationError,
    ProviderEvent,
    ProviderPage,
)
from app.synthetic.generator import provider_amount_to_minor

FIXTURES = Path(__file__).resolve().parents[4] / "fixtures" / "provider"


def load_fixture(slug: str) -> dict[str, Any]:
    return json.loads((FIXTURES / f"{slug}.json").read_text(encoding="utf-8"))


def to_canonical(record: dict[str, Any]) -> CanonicalTransaction:
    """Convert one provider record; raises ValueError for anything malformed."""
    if record.get("iso_currency_code", "USD") != "USD":
        raise ValueError("unsupported currency")
    return CanonicalTransaction(
        external_account_id=str(record["account_id"]),
        external_id=str(record["transaction_id"]),
        amount_minor=provider_amount_to_minor(str(record["amount"])),
        posted_date=str(record["date"]),
        description=str(record["name"])[:500],
        status="pending" if record.get("pending") else "posted",
        pending_external_id=record.get("pending_transaction_id") or None,
    )


class SyntheticProvider:
    """Serves fixture pages by cursor, like a provider's transactions/sync endpoint.

    `available_pages` limits how many pages the provider has "so far", which
    lets tests deliver later updates on a second sync. `mutate_once_at`
    makes the provider report a pagination mutation the first time that page
    is requested, so the restart path can be exercised.
    """

    def __init__(
        self,
        fixture: dict[str, Any],
        available_pages: int | None = None,
        mutate_once_at: int | None = None,
    ) -> None:
        self.fixture = fixture
        self.pages: list[dict[str, Any]] = fixture["pages"]
        self.available = len(self.pages) if available_pages is None else available_pages
        self._mutate_once_at = mutate_once_at
        self.calls = 0

    @property
    def item_id(self) -> str:
        return str(self.fixture["item"]["external_item_id"])

    def accounts(self) -> list[dict[str, Any]]:
        return list(self.fixture["accounts"])

    def balances(self) -> list[BalanceObservation]:
        return [
            BalanceObservation(b["account_id"], b["kind"], int(b["amount_minor"]), b["as_of"])
            for b in self.fixture["balances"]
        ]

    def fetch(self, cursor: str | None) -> ProviderPage:
        self.calls += 1
        index = 0 if cursor is None else self._index_of(cursor)
        if index >= self.available:
            return ProviderPage(
                cursor=cursor, next_cursor=cursor or self.pages[0]["cursor"], has_more=False
            )
        if self._mutate_once_at == index:
            self._mutate_once_at = None
            raise PaginationMutationError()
        page = self.pages[index]
        events: list[ProviderEvent] = []
        for record in page["added"]:
            events.append(
                ProviderEvent("added", record["transaction_id"], record, to_canonical(record))
            )
        for record in page["modified"]:
            events.append(
                ProviderEvent("modified", record["transaction_id"], record, to_canonical(record))
            )
        for record in page["removed"]:
            events.append(ProviderEvent("removed", record["transaction_id"], record, None))
        has_more = bool(page["has_more"]) and index + 1 < self.available
        return ProviderPage(
            cursor=cursor, next_cursor=page["next_cursor"], has_more=has_more, events=events
        )

    def _index_of(self, cursor: str) -> int:
        for i, page in enumerate(self.pages):
            if page["cursor"] == cursor:
                return i
        if cursor == self.pages[-1]["next_cursor"]:
            return len(self.pages)
        raise ValueError("unknown cursor")
