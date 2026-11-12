"""Choosing the provider for this deployment, and refusing when there is nothing to choose.

A dependency rather than a module-level object so a test can substitute a scripted provider
without patching anything, and so a deployment holding no credential fails here — before a
request is built — rather than somewhere inside the capability.
"""

from pathlib import Path
from typing import Annotated

from fastapi import Depends, HTTPException

from app.ai.openai_direct import OpenAIDirect
from app.ai.provider import ModelProvider
from app.ai.replay import Replay
from app.ai.simulated import Simulated
from app.config import Settings, get_settings


def require_ai_enabled() -> Settings:
    """The master switch. Off means every AI route answers 503 before touching anything."""
    settings = get_settings()
    if not settings.ai_enabled:
        raise HTTPException(status_code=503, detail="AI explanations are off in this environment.")
    return settings


def get_model_provider(
    settings: Annotated[Settings, Depends(require_ai_enabled)],
) -> ModelProvider:
    """The provider for this deployment, once the master switch has allowed one at all.

    The switch is a dependency of this rather than a sibling of it, so a deployment with AI
    switched off always answers with the switch. Ordering it by where the arguments happen to
    sit in a route signature put a configuration detail in front of the real reason.
    """
    if settings.ai_provider == "simulated":
        # Refused in production outright rather than trusted to configuration: a simulated
        # explanation on real data would look exactly like a real one to whoever read it.
        if settings.app_env == "production":
            raise HTTPException(
                status_code=503,
                detail="The simulated provider is not available in production.",
            )
        return Simulated(settings.ai_simulated_outcome)
    if settings.ai_provider == "replay":
        directory = Path(settings.ai_recordings_dir) if settings.ai_recordings_dir else None
        return Replay(directory)
    if settings.openai_api_key is None or settings.ai_model is None:
        raise HTTPException(
            status_code=503,
            detail="AI explanations are not configured in this deployment.",
        )
    return OpenAIDirect(
        settings.openai_api_key.get_secret_value(),
        settings.ai_model_allowlist,
        settings.ai_reasoning_effort,
    )


Provider = Annotated[ModelProvider, Depends(get_model_provider)]
AiSettings = Annotated[Settings, Depends(require_ai_enabled)]
