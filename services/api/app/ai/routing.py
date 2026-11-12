"""Which provider and model may answer for this workspace, decided on the server.

There are two routes. The direct one is the deployment's configured provider and is what every
business uses. The OpenRouter one exists so another model family can be compared, and it is
available on synthetic demo workspaces only.

That restriction is the whole reason this module is separate from `factory.py`. The factory
answers "what can this deployment call at all", which is a question about configuration. This
answers "what may this caller have", which is a question about whose data is involved — and
the answer comes from the business's own `data_mode`, read from the session-derived scope, not
from anything the caller sent. A request naming a workspace that is not a demo cannot obtain
the OpenRouter route by asking for it.

Two things are deliberately absent:

  * no fallback. If the OpenRouter route is asked for and cannot be given, the request is
    refused. It is never quietly served by the direct provider, because a comparison whose
    result silently came from the other model is worse than no result.
  * no rerouting. A private business is never moved onto OpenRouter for any reason, including
    the direct provider being unavailable.
"""

from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

from app.ai.openrouter import OpenRouter
from app.ai.provider import ModelProvider
from app.config import Settings
from app.workspaces import WorkspaceScope

ROUTE_DIRECT = "direct"
ROUTE_OPENROUTER = "openrouter"

# The data mode that may reach a router. `demo` businesses hold generated transactions that
# ship with the application; nothing in one belongs to a person.
SYNTHETIC_MODE = "demo"


@dataclass(frozen=True)
class Route:
    """A provider and the model to ask it for. The two travel together or not at all."""

    key: str
    provider: ModelProvider
    model: str
    label: str

    @property
    def configuration(self) -> str:
        """How this pairing is written where a person or a query has to tell two apart."""
        return f"{self.provider.name}:{self.model}"


def _refuse(message: str, reason: str) -> HTTPException:
    return HTTPException(status_code=503, detail={"message": message, "reason": reason})


def resolve(
    scope: WorkspaceScope,
    settings: Settings,
    direct: ModelProvider,
    requested: str | None,
) -> Route:
    """The route this request may use, or a refusal. Never a substitution."""
    if requested in (None, "", ROUTE_DIRECT):
        return Route(
            key=ROUTE_DIRECT,
            provider=direct,
            model=settings.ai_model or direct.name,
            label=f"{direct.name} (direct)",
        )

    if requested != ROUTE_OPENROUTER:
        raise HTTPException(
            status_code=422,
            detail={"message": f"Unknown route {requested!r}.", "reason": "unknown_route"},
        )

    # Order matters below. The data restriction is checked before configuration, so a private
    # business is told it cannot use this route rather than being told how it is configured.
    if scope.data_mode != SYNTHETIC_MODE:
        raise _refuse(
            "OpenRouter is available on the synthetic demo businesses only. This business's "
            "own data stays on the direct provider, and nothing was sent.",
            "private_data_not_routable",
        )
    if not settings.ai_openrouter_enabled:
        raise _refuse("OpenRouter is not switched on in this deployment.", "route_disabled")
    if settings.openrouter_api_key is None:
        raise _refuse("OpenRouter is not configured in this deployment.", "route_unconfigured")

    model = settings.openrouter_model
    allowed = settings.openrouter_model_allowlist
    if not model or not allowed:
        raise _refuse("No OpenRouter model is configured in this deployment.", "no_model")

    provider = OpenRouter(
        settings.openrouter_api_key.get_secret_value(),
        allowed,
        model=model,
        site_url=settings.openrouter_site_url,
        app_title=settings.openrouter_app_title,
    )
    return Route(
        key=ROUTE_OPENROUTER,
        provider=provider,
        model=model,
        label=f"{model} (via OpenRouter)",
    )


# Whether the OpenRouter route is *offered as a choice* in the interface.
#
# Off for the interview demo release. Every recorded attempt on this route returned
# `invalid_output` — three of three — so a control offering it leads a visitor to a dead end
# that looks like a product fault rather than an unfinished comparison.
#
# This hides the control only. `resolve()` is deliberately untouched, so the route is still
# reachable by an explicit request and `db/checks/model_comparison.py` can still drive it; the
# adapter, its pricing table, the synthetic-only restriction and the recorded results are all
# unchanged. Restore the control by setting this back to True — there is no new setting to
# configure, because this is a decision about one release rather than about a deployment.
OFFER_OPENROUTER_IN_CONTROLS = False


def available(scope: WorkspaceScope, settings: Settings) -> list[dict[str, Any]]:
    """The routes this caller may choose between, for a reviewer control to render.

    A private workspace is told only about the direct route. The alternative is not listed as
    present-but-forbidden, because a control that shows a disabled option invites the question
    of how to enable it, and the answer for private data is that there is no way to.
    """
    options: list[dict[str, Any]] = [
        {
            "key": ROUTE_DIRECT,
            "label": f"{settings.ai_provider} (direct)",
            "model": settings.ai_model,
            "default": True,
        }
    ]
    if (
        OFFER_OPENROUTER_IN_CONTROLS
        and scope.data_mode == SYNTHETIC_MODE
        and settings.ai_openrouter_enabled
        and settings.openrouter_api_key is not None
        and settings.openrouter_model
    ):
        options.append(
            {
                "key": ROUTE_OPENROUTER,
                "label": f"{settings.openrouter_model} (via OpenRouter · experimental)",
                "model": settings.openrouter_model,
                "default": False,
                "note": (
                    "Experimental and not validated. The comparison against the direct "
                    "provider is unfinished, so answers from this route may fail validation "
                    "and be withheld. Routed: the upstream serving it can vary between calls, "
                    "and it is offered on the synthetic demos only."
                ),
            }
        )
    return options
