"""The model provider boundary: one synchronous call, no streaming, no tools, no state.

Everything the application asks of a model goes through `ModelProvider.complete`. Adapters
never retry: `max_retries=0` in every transport, because a retry that the transport hides is a
paid call nobody can see. Retrying is the capability's decision, so it always shows up in the
run's `attempts` and in usage.

The interface is deliberately narrow. A provider receives the text to send and the schema the
answer must satisfy, and returns what came back plus what it cost. It decides nothing about
whether the answer is usable; that is the validator's job, and keeping the two apart is what
lets a second provider be added without touching a single grounding rule.
"""

from dataclasses import dataclass, field
from typing import Any, Protocol


class ProviderError(Exception):
    """The call failed. The message is safe to log: no prompt, no output, no credential."""

    def __init__(self, message: str, *, code: str = "provider_error") -> None:
        super().__init__(message)
        self.code = code


class ProviderTimeoutError(ProviderError):
    """No answer within the deadline. The provider may still have billed for the work."""

    def __init__(self, message: str = "the model did not answer in time") -> None:
        super().__init__(message, code="timeout")


class ModelNotAllowedError(ProviderError):
    """The requested model is not on this deployment's allowlist, so nothing was sent."""

    def __init__(self, message: str = "model is not allowed in this deployment") -> None:
        super().__init__(message, code="model_not_allowed")


@dataclass(frozen=True)
class ModelRequest:
    capability: str
    prompt_version: str
    model: str
    system: str
    user: str
    json_schema: dict[str, Any]
    max_output_tokens: int
    timeout_seconds: float


@dataclass(frozen=True)
class ModelResponse:
    text: str
    model_used: str | None
    provider_request_id: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    # Micro-dollars ($1 = 1,000,000), and where the figure came from. `unknown` is a real
    # answer: a provider that reports no cost for a model absent from the price table is
    # recorded as unknown rather than guessed at.
    cost_micro: int | None
    cost_source: str
    latency_ms: int
    finish_reason: str | None
    extra: dict[str, Any] = field(default_factory=dict)


class ModelProvider(Protocol):
    name: str
    # Whether an answer from this adapter came from a model just now. False for anything
    # canned, so a recorded or scripted answer can never be displayed or measured as live
    # model performance. The run's mode is derived from this, not from the adapter's name.
    live: bool

    def complete(self, request: ModelRequest) -> ModelResponse: ...
