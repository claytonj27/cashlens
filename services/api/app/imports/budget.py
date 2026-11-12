"""A processing deadline that fits inside the job lease and the deployment's function limit."""

import time

PROCESSING_BUDGET_SECONDS = (
    100.0  # lease 180 s, function limit 300 s: writes after parsing still fit
)


class ProcessingTimeoutError(Exception):
    """The step ran out of its time budget; the job is retryable within its attempts."""

    def __init__(self, stage: str) -> None:
        super().__init__(f"processing budget exhausted during {stage}")
        self.stage = stage


class Deadline:
    def __init__(self, seconds: float = PROCESSING_BUDGET_SECONDS) -> None:
        self.expires_at = time.monotonic() + seconds

    def check(self, stage: str) -> None:
        if time.monotonic() > self.expires_at:
            raise ProcessingTimeoutError(stage)

    @property
    def remaining(self) -> float:
        return max(0.0, self.expires_at - time.monotonic())
