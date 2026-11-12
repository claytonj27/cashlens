"""The OpenRouter adapter: a second model family, on synthetic demo data only.

Why this exists: comparing another family on the demo businesses needs a route to one, and
OpenRouter reaches many behind a single credential. Why it is deliberately narrow: OpenRouter
is a router, so the upstream provider that actually serves a request can vary between calls,
and a private business's bank statements are not something to hand to a set of upstreams that
is decided per request. The data restriction is NOT enforced here — `routing.py` enforces it
against the business's own `data_mode` before this adapter is ever constructed. This file
assumes it has already been allowed to run.

Three controls are sent on every request, and each one is a refusal rather than a preference:

  * `allow_fallbacks: false` — if the chosen upstream cannot serve it, the call fails instead
    of quietly moving to another one. A fallback is a different model's answer wearing the
    requested model's name.
  * `require_parameters: true` — only upstreams that support everything sent, which for this
    application means strict JSON schema. An upstream that silently ignored `response_format`
    would return prose that fails validation after being paid for.
  * `max_price` — the per-million ceiling from this deployment's own dated price table. When
    no upstream is at or below it, OpenRouter answers 404 and nothing is billed. This is what
    makes the reservation a real bound: the routing cannot cost more than was held for it.

`data_collection: "deny"` keeps the request away from upstreams that may retain it. That is a
routing preference about retention, not the reason the demo restriction exists, and it is not
treated as one.

What comes back is recorded as it arrives. OpenRouter reports the model it served and, when it
chooses to, the upstream that served it; neither is invented when absent, because a run that
named a backend it never confirmed would read as pinned when the routing is variable.
"""

import logging
import time
from typing import Any

import httpx

from app.ai import prices
from app.ai.provider import (
    ModelNotAllowedError,
    ModelRequest,
    ModelResponse,
    ProviderError,
    ProviderTimeoutError,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://openrouter.ai/api/v1"
COMPLETIONS_URL = f"{BASE_URL}/chat/completions"

# OpenRouter bills in credits, bought at one credit to the United States dollar, so a reported
# cost converts to micro-dollars by this factor alone. Kept as a named constant because it is
# an assumption about the provider's units, not arithmetic: if credits ever stop being dollars,
# this is the one line that is wrong, rather than a silent factor spread through the file.
MICRO_PER_CREDIT = 1_000_000


class OpenRouter:
    name = "openrouter"
    live = True

    def __init__(
        self,
        api_key: str,
        allowed_models: tuple[str, ...],
        *,
        model: str | None = None,
        site_url: str | None = None,
        app_title: str = "CashLens",
        reasoning_effort: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._allowed = allowed_models
        # The model this route calls when the caller names none. Its own default, separate
        # from the direct route's, because the two allowlists are separate.
        self.model = model
        self._site_url = site_url
        self._app_title = app_title
        self._reasoning_effort = reasoning_effort
        # Only a test supplies this. Production builds its own client per call.
        self._transport = transport

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            # Attribution. Both are public identifiers for this application; neither carries
            # anything belonging to a business.
            "X-OpenRouter-Title": self._app_title,
        }
        if self._site_url:
            headers["HTTP-Referer"] = self._site_url
        return headers

    def _routing(self, model: str) -> dict[str, Any]:
        """The provider preferences sent with every call, including the price ceiling."""
        routing: dict[str, Any] = {
            "allow_fallbacks": False,
            "require_parameters": True,
            "data_collection": "deny",
        }
        rates = prices.rates(model)
        if rates is not None:
            per_input, per_output = rates
            # Dollars per million tokens, which is the unit this table already uses.
            routing["max_price"] = {"prompt": per_input, "completion": per_output}
        return routing

    def complete(self, request: ModelRequest) -> ModelResponse:
        if request.model not in self._allowed:
            raise ModelNotAllowedError()
        # Defence in depth. The capability already refuses an unpriced model before reserving,
        # so reaching here without a price would mean that check was bypassed; without rates
        # there is no ceiling to send, and an uncapped call is not one this route makes.
        if prices.rates(request.model) is None:
            raise ModelNotAllowedError("no recorded price for this model, so no ceiling was set")

        body: dict[str, Any] = {
            "model": request.model,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.user},
            ],
            # `max_tokens` rather than `max_completion_tokens`: with `require_parameters` on,
            # naming a parameter an upstream does not accept excludes it from routing, and
            # `max_tokens` is the form OpenRouter normalises for every upstream.
            "max_tokens": request.max_output_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": request.capability,
                    "strict": True,
                    "schema": request.json_schema,
                },
            },
            "provider": self._routing(request.model),
        }
        if self._reasoning_effort:
            body["reasoning"] = {"effort": self._reasoning_effort}

        started = time.perf_counter()
        try:
            with httpx.Client(
                timeout=request.timeout_seconds,
                transport=self._transport,
            ) as client:
                # No retry, here or anywhere else in this boundary: a retry the transport
                # hides is a paid call nobody can see.
                response = client.post(COMPLETIONS_URL, headers=self._headers(), json=body)
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError() from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"{type(exc).__name__}") from exc
        latency_ms = int((time.perf_counter() - started) * 1000)

        if response.status_code == 404:
            # The documented answer when no upstream satisfies the constraints, which for this
            # adapter most often means none was at or below the price ceiling. Nothing was
            # served and nothing was billed, so this is a refusal, not a failed call.
            raise ProviderError(
                "no upstream met the routing constraints", code="no_provider_available"
            )
        if response.status_code == 402:
            raise ProviderError("the OpenRouter account has no credit", code="insufficient_credit")
        if response.status_code != 200:
            # The status only. A body can carry an upstream's own error text, which is not
            # this application's to log.
            raise ProviderError(f"http_{response.status_code}", code="provider_error")

        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderError("the answer was not JSON", code="malformed_response") from exc
        if not isinstance(payload, dict):
            raise ProviderError("the answer was not an object", code="malformed_response")
        # OpenRouter reports some upstream failures as a 200 carrying an error object.
        error = payload.get("error")
        if isinstance(error, dict):
            raise ProviderError(f"upstream_{error.get('code', 'error')}", code="upstream_error")

        choices = payload.get("choices") or []
        choice = choices[0] if isinstance(choices, list) and choices else {}
        message = choice.get("message") if isinstance(choice, dict) else None
        text = (message or {}).get("content") or ""

        usage = payload.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        prompt_tokens = _as_int(usage.get("prompt_tokens"))
        completion_tokens = _as_int(usage.get("completion_tokens"))

        cost_micro, cost_source = _cost(request.model, usage, prompt_tokens, completion_tokens)

        return ModelResponse(
            text=text if isinstance(text, str) else "",
            # What was actually served. OpenRouter echoes the model id it used, which for a
            # slug with several upstreams is the same string; the upstream identity, when it
            # is reported at all, is carried in `extra` rather than folded in here.
            model_used=payload.get("model") if isinstance(payload.get("model"), str) else None,
            provider_request_id=payload.get("id") if isinstance(payload.get("id"), str) else None,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_micro=cost_micro,
            cost_source=cost_source,
            latency_ms=latency_ms,
            finish_reason=choice.get("finish_reason") if isinstance(choice, dict) else None,
            extra=_routing_record(payload, body["provider"]),
        )


def _as_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _cost(
    model: str, usage: dict[str, Any], prompt_tokens: int | None, completion_tokens: int | None
) -> tuple[int | None, str]:
    """What the call cost, and whether that is the provider's figure or this deployment's.

    The provider's own number is preferred when it is there: it is what was actually charged,
    including anything the price table does not model. The table is the fallback, and is
    labelled as the table so a reviewer can tell a reported charge from a computed one.
    """
    reported = usage.get("cost")
    if isinstance(reported, int | float) and not isinstance(reported, bool) and reported >= 0:
        return round(float(reported) * MICRO_PER_CREDIT), prices.SOURCE_PROVIDER
    computed = prices.cost_micro(model, prompt_tokens, completion_tokens)
    return (
        (computed, prices.SOURCE_TABLE) if computed is not None else (None, prices.SOURCE_UNKNOWN)
    )


def _routing_record(payload: dict[str, Any], sent: dict[str, Any]) -> dict[str, Any]:
    """What was asked of the routing, and whatever it disclosed about serving the request.

    The upstream is recorded only when the response names one. OpenRouter's documented
    response shape does not promise that field, so its absence is recorded as an absence: a
    run that filled it in from the model slug would claim a pinned backend for routing that is
    variable by design.
    """
    record: dict[str, Any] = {"routing": dict(sent)}
    upstream = payload.get("provider")
    if isinstance(upstream, str) and upstream:
        record["upstream_provider"] = upstream
    else:
        record["upstream_provider"] = None
        record["upstream_provider_note"] = (
            "OpenRouter did not name the upstream that served this request."
        )
    return record
