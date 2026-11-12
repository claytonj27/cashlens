"""The canonical ingestion contract every source adapter publishes through."""

from dataclasses import dataclass, field
from typing import Any, Literal

Operation = Literal["added", "modified", "removed"]


@dataclass(frozen=True)
class CanonicalTransaction:
    """One provider transaction in CashLens terms: signed minor units, inflows positive."""

    external_account_id: str
    external_id: str
    amount_minor: int
    posted_date: str
    description: str
    status: Literal["pending", "posted"]
    currency: str = "USD"
    pending_external_id: str | None = None

    def as_json(self) -> dict[str, Any]:
        return {
            "external_account_id": self.external_account_id,
            "amount_minor": self.amount_minor,
            "posted_date": self.posted_date,
            "description": self.description,
            "status": self.status,
            "currency": self.currency,
            "pending_external_id": self.pending_external_id,
        }


@dataclass(frozen=True)
class ProviderEvent:
    """One provider operation: the payload exactly as received plus its canonical reading."""

    operation: Operation
    external_id: str
    payload: dict[str, Any]
    canonical: CanonicalTransaction | None


@dataclass(frozen=True)
class ProviderPage:
    cursor: str | None
    next_cursor: str
    has_more: bool
    events: list[ProviderEvent] = field(default_factory=list)


@dataclass(frozen=True)
class BalanceObservation:
    external_account_id: str
    kind: Literal["current", "available", "statement_closing", "statement_opening"]
    amount_minor: int
    as_of: str


class PaginationMutationError(Exception):
    """The provider changed the update set mid-pagination; restart from the committed cursor."""
