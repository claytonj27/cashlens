"""The direct OpenAI adapter: the runtime provider for every business.

One bounded chat completion with a strict JSON schema, no retries, no tools and no streaming.
Streaming is deliberately absent: this milestone shows nothing until it has been checked
against the figures, and a token already on screen cannot be withheld.

The model is checked against the deployment's allowlist before a request is built, so a
mistyped or unexpectedly expensive id fails here rather than at the provider.
"""

import time
from typing import Any

from openai import APITimeoutError, OpenAI

from app.ai import prices
from app.ai.provider import (
    ModelNotAllowedError,
    ModelRequest,
    ModelResponse,
    ProviderError,
    ProviderTimeoutError,
)


class OpenAIDirect:
    name = "openai"
    live = True

    def __init__(
        self,
        api_key: str,
        allowed_models: tuple[str, ...],
        reasoning_effort: str | None = None,
    ) -> None:
        self._api_key = api_key
        self._allowed = allowed_models
        self._reasoning_effort = reasoning_effort

    def complete(self, request: ModelRequest) -> ModelResponse:
        if request.model not in self._allowed:
            raise ModelNotAllowedError()
        client = OpenAI(api_key=self._api_key, timeout=request.timeout_seconds, max_retries=0)
        extra: dict[str, Any] = {}
        # The reasoning family takes an effort setting, and the accepted values differ by
        # model — gpt-5 takes `minimal`, gpt-5.1 and later take `none` instead — so it is
        # configuration rather than a guess made from the model name. Reasoning tokens are
        # billed as output and counted inside `max_completion_tokens`, so the output
        # allowance has to leave room for them.
        if self._reasoning_effort and request.model.startswith(("gpt-5", "o1", "o3", "o4")):
            extra["reasoning_effort"] = self._reasoning_effort
        started = time.perf_counter()
        try:
            response = client.chat.completions.create(
                model=request.model,
                messages=[
                    {"role": "system", "content": request.system},
                    {"role": "user", "content": request.user},
                ],
                max_completion_tokens=request.max_output_tokens,
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": request.capability,
                        "schema": request.json_schema,
                        "strict": True,
                    },
                },
                **extra,
            )
        except APITimeoutError as exc:
            raise ProviderTimeoutError() from exc
        except Exception as exc:  # any SDK or transport failure; never retried here
            raise ProviderError(f"{type(exc).__name__}") from exc
        latency_ms = int((time.perf_counter() - started) * 1000)

        usage = response.usage
        prompt_tokens = getattr(usage, "prompt_tokens", None)
        completion_tokens = getattr(usage, "completion_tokens", None)
        choice = response.choices[0] if response.choices else None
        text = (choice.message.content or "") if choice is not None else ""
        cost = prices.cost_micro(request.model, prompt_tokens, completion_tokens)
        return ModelResponse(
            text=text,
            model_used=response.model,
            provider_request_id=getattr(response, "_request_id", None),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_micro=cost,
            cost_source=prices.SOURCE_TABLE if cost is not None else prices.SOURCE_UNKNOWN,
            latency_ms=latency_ms,
            finish_reason=choice.finish_reason if choice is not None else None,
        )
