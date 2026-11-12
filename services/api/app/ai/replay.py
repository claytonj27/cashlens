"""Recorded responses, always labelled as such.

A recording is a real response captured from a real call, stored under the hash of the request
that produced it. Replaying one is useful for tests and for continuous integration, which must
never reach the network; it is never useful as evidence of how the model performs, so every run
and every card carries the mode and a replayed answer is shown as a recorded response.

A missing recording is a provider error, deliberately. The alternative — falling back to a live
call — would turn an offline test suite into one that quietly spends money.

There are no recordings in this repository yet: none can exist before the first live call is
authorised. Until then this adapter's only reachable behaviour is the missing-recording error,
which is itself one of the failure paths the interface has to handle.
"""

import hashlib
import json
import logging
from pathlib import Path

from app.ai.provider import ModelRequest, ModelResponse, ProviderError

logger = logging.getLogger(__name__)

# Beside the adapter for the same reason as the price table: only what lives under
# services/api is deployed, so a recordings directory above that root would be absent in a
# preview and every replay would report a missing recording.
DEFAULT_RECORDINGS_DIR = Path(__file__).resolve().parent / "recordings"


def request_key(request: ModelRequest) -> str:
    """A stable name for one exact request, so a changed prompt cannot replay an old answer."""
    material = json.dumps(
        {
            "capability": request.capability,
            "prompt_version": request.prompt_version,
            "model": request.model,
            "system": request.system,
            "user": request.user,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(material.encode()).hexdigest()


class Replay:
    name = "replay"
    live = False

    def __init__(self, directory: Path | None = None) -> None:
        self._dir = directory or DEFAULT_RECORDINGS_DIR

    def complete(self, request: ModelRequest) -> ModelResponse:
        key = request_key(request)
        path = self._dir / f"{key}.json"
        try:
            recorded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.info("ai.recording_missing", extra={"key": key[:16]})
            raise ProviderError(
                "no recorded response for this request", code="recording_missing"
            ) from exc
        return ModelResponse(
            text=recorded["text"],
            model_used=recorded.get("model_used"),
            provider_request_id=None,
            prompt_tokens=recorded.get("prompt_tokens"),
            completion_tokens=recorded.get("completion_tokens"),
            # A replay costs nothing now, whatever the original call cost.
            cost_micro=0,
            cost_source="price_table",
            latency_ms=0,
            finish_reason=recorded.get("finish_reason"),
            extra={"recorded_at": recorded.get("recorded_at")},
        )
