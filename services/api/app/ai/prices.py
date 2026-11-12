"""What a call cost, and what to hold before making one.

Prices live in a dated fixture rather than in code, because they change on the provider's
schedule and every run records which table it used. The table ships empty on purpose: no model
has been chosen yet, and inventing a price for a model nobody has run would put a number in the
reviewer view that came from nowhere. Until the owner records real prices, a completed call is
reported with `cost_source = unknown` and the reservation keeps the conservative estimate it
was granted against.

Micro-dollars throughout ($1 = 1,000,000). A price quoted per million tokens converts with no
arithmetic at all: $2.50 per million tokens is 2.5 micro-dollars per token, so the cost in
micro-dollars is simply tokens x the per-million price.
"""

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Beside the adapter, not in the repository's fixtures directory. The API deploys from
# services/api alone, so anything above that root simply is not there at run time: the first
# live call refused itself because the price table it was reading had not been deployed. A
# table that only exists in development is not a spend control.
PRICES_PATH = Path(__file__).resolve().parent / "prices.json"

# A brief makes at most two billable calls: the first, and one repair when validation rejects
# the answer outright. Both are reserved for up front, because a repair that was not held for
# is a call nobody budgeted.
MAX_BILLABLE_ATTEMPTS = 2
# The repair request is the original fact sheet plus the validator's messages. The message list
# is bounded by the schema (at most eight claims and four questions), so a flat allowance
# covers it; it is counted per repair rather than ignored.
REPAIR_OVERHEAD_TOKENS = 500

SOURCE_TABLE = "price_table"
SOURCE_PROVIDER = "provider"
SOURCE_ESTIMATE = "reserved_estimate"
SOURCE_UNKNOWN = "unknown"


@lru_cache
def _table() -> dict[str, Any]:
    try:
        return json.loads(PRICES_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("ai.prices_unreadable")
        return {"recorded_on": None, "models": {}}


def recorded_on() -> str | None:
    """The day the prices in the table were read off the provider's page."""
    return _table().get("recorded_on")


def _rates(model: str) -> tuple[float, float] | None:
    entry = _table().get("models", {}).get(model)
    if not isinstance(entry, dict):
        return None
    try:
        return float(entry["input_usd_per_million"]), float(entry["output_usd_per_million"])
    except (KeyError, TypeError, ValueError):
        logger.warning("ai.price_entry_malformed", extra={"model_present": True})
        return None


def rates(model: str) -> tuple[float, float] | None:
    """The committed input and output price per million tokens, or None if not recorded.

    Public because a provider that can be told a price ceiling should be told this one: the
    number the reservation was granted against is the only number the call is allowed to cost.
    """
    return _rates(model)


def cost_micro(model: str, prompt_tokens: int | None, completion_tokens: int | None) -> int | None:
    """What the call cost by the committed table, or None when the model is not in it."""
    rates = _rates(model)
    if rates is None or prompt_tokens is None or completion_tokens is None:
        return None
    per_input, per_output = rates
    return round(prompt_tokens * per_input + completion_tokens * per_output)


def has_price(model: str) -> bool:
    """Whether this deployment knows what the model costs. No price, no live call."""
    return _rates(model) is not None


def estimate_micro(
    model: str,
    input_tokens: int,
    max_output_tokens: int,
    attempts: int = MAX_BILLABLE_ATTEMPTS,
) -> int | None:
    """What to hold for the whole bounded operation, or None when the model has no price.

    None is a refusal, not a default. Reserving against an assumed price would put a number in
    the budget that the provider never agreed to, and the cap would then be measured in units
    nobody can check — so a model with no recorded price is not dispatched at all.

    The hold covers every call the operation may make, each at its full output allowance. A
    reasoning model bills its hidden reasoning as output tokens, and `max_completion_tokens`
    bounds those too, so the allowance is the real ceiling rather than a guess at usage.
    """
    rates = _rates(model)
    if rates is None:
        return None
    per_input, per_output = rates
    repairs = max(attempts - 1, 0)
    total_input = input_tokens * attempts + REPAIR_OVERHEAD_TOKENS * repairs
    total_output = max_output_tokens * attempts
    return max(1, round(total_input * per_input + total_output * per_output))


# Characters per token, and the margin on top. Four is the usual rule of thumb for prose, but
# what is actually sent is JSON — full of braces, quotes, colons and digits, which tokenize far
# denser than words — so a prose ratio would under-reserve on the one input that matters. The
# ratio below is deliberately pessimistic and the margin is on top of that, because an estimate
# that runs low buys a call the budget never approved.
CHARS_PER_TOKEN = 3.2
ESTIMATE_MARGIN = 1.15


def estimate_tokens(text: str) -> int:
    """A deliberately high token count for reserving against, never an exact one."""
    return max(1, round(len(text) / CHARS_PER_TOKEN * ESTIMATE_MARGIN))
