"""The OpenRouter route: what is sent, what is read back, and who is allowed to use it.

Two separate things are tested here and they fail for different reasons, so they are kept
apart:

  * the adapter — does it send the routing controls and the strict schema, and does it read a
    reported cost, a served model and an undisclosed upstream honestly;
  * the routing rule — may this workspace use the route at all. That one is about whose data is
    involved, and the only answer that matters is the one for a private business.

No network anywhere. The provider is an `httpx.MockTransport` the test writes, so a request
that should never be made can be proven never to have been made rather than assumed.
"""

import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.ai import prices, routing
from app.ai.openrouter import OpenRouter
from app.ai.provider import (
    ModelNotAllowedError,
    ModelRequest,
    ProviderError,
    ProviderTimeoutError,
)
from app.config import get_settings
from app.main import app
from tests.test_ai_brief import (
    BIZ,
    CLIENT,
    GOOD_TOKEN,
    WS_USER,
    WS_VISITOR,
    BillableScripted,
    FakeDb,
    good_answer,
    price_the_model,
    use,
    visitor,
)

MODEL = "qwen/qwen3.8-max-0902"
OR_PRICES = {
    "recorded_on": "2026-09-14",
    "models": {
        "test-model": {"input_usd_per_million": 2.5, "output_usd_per_million": 10.0},
        MODEL: {"input_usd_per_million": 2.0, "output_usd_per_million": 6.0},
    },
}


def a_request(model: str = MODEL) -> ModelRequest:
    return ModelRequest(
        capability="brief",
        prompt_version="brief-1",
        model=model,
        system="You explain figures.",
        user="the fact sheet",
        json_schema={"type": "object", "properties": {"headline": {"type": "string"}}},
        max_output_tokens=1200,
        timeout_seconds=30.0,
    )


def answered(payload: dict[str, Any], status: int = 200) -> tuple[Any, list[httpx.Request]]:
    """A transport that answers with `payload`, and the list of requests it received."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=payload)

    return httpx.MockTransport(handler), seen


def completion(**over: Any) -> dict[str, Any]:
    body = {
        "id": "gen-abc123",
        "model": MODEL,
        "choices": [{"message": {"content": '{"headline":"ok"}'}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1000, "completion_tokens": 200, "total_tokens": 1200},
    }
    body.update(over)
    return body


def adapter(transport: Any, **over: Any) -> OpenRouter:
    kwargs: dict[str, Any] = {
        "model": MODEL,
        "site_url": "https://cashlens-web.vercel.app",
        "app_title": "CashLens",
        "transport": transport,
    }
    kwargs.update(over)
    return OpenRouter("sk-or-v1-test", (MODEL,), **kwargs)


# ------------------------------------------------------------ what gets sent --
def test_every_call_carries_the_routing_refusals_and_a_price_ceiling_from_the_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The three controls are refusals, and the ceiling is this deployment's own number."""
    price_the_model(monkeypatch, OR_PRICES)
    transport, seen = answered(completion())
    adapter(transport).complete(a_request())

    assert len(seen) == 1
    sent = json.loads(seen[0].content)
    routing = sent["provider"]
    assert routing["allow_fallbacks"] is False
    assert routing["require_parameters"] is True
    assert routing["data_collection"] == "deny"
    # The ceiling is the recorded price, so routing cannot cost more than was reserved.
    assert routing["max_price"] == {"prompt": 2.0, "completion": 6.0}

    assert sent["model"] == MODEL
    assert sent["max_tokens"] == 1200
    assert sent["response_format"]["type"] == "json_schema"
    assert sent["response_format"]["json_schema"]["strict"] is True
    assert seen[0].url.path == "/api/v1/chat/completions"
    assert seen[0].headers["authorization"] == "Bearer sk-or-v1-test"
    assert seen[0].headers["x-openrouter-title"] == "CashLens"
    assert seen[0].headers["http-referer"] == "https://cashlens-web.vercel.app"


def test_a_model_off_the_allowlist_is_refused_before_anything_is_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    price_the_model(monkeypatch, OR_PRICES)
    transport, seen = answered(completion())
    with pytest.raises(ModelNotAllowedError):
        adapter(transport).complete(a_request("some/other-model"))
    assert seen == []


def test_an_unpriced_model_is_refused_because_no_ceiling_could_be_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Defence in depth: without a price there is no max_price, and an uncapped call is not made."""
    price_the_model(monkeypatch, {"recorded_on": None, "models": {}})
    transport, seen = answered(completion())
    with pytest.raises(ModelNotAllowedError):
        adapter(transport).complete(a_request())
    assert seen == []


# --------------------------------------------------------- what gets read back --
def test_a_reported_cost_is_recorded_as_the_providers_own_figure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    price_the_model(monkeypatch, OR_PRICES)
    # Credits, which are dollars. 0.0034 credits is 3400 micro-dollars.
    transport, _ = answered(
        completion(usage={"prompt_tokens": 1000, "completion_tokens": 200, "cost": 0.0034})
    )
    answer = adapter(transport).complete(a_request())

    assert answer.cost_micro == 3400
    assert answer.cost_source == prices.SOURCE_PROVIDER
    assert answer.prompt_tokens == 1000 and answer.completion_tokens == 200
    assert answer.model_used == MODEL
    assert answer.provider_request_id == "gen-abc123"
    assert answer.finish_reason == "stop"
    assert answer.text == '{"headline":"ok"}'


def test_without_a_reported_cost_the_table_is_used_and_labelled_as_the_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A computed figure and a charged one are never reported as the same kind of number."""
    price_the_model(monkeypatch, OR_PRICES)
    transport, _ = answered(completion())  # usage carries no `cost`
    answer = adapter(transport).complete(a_request())

    # 1000 x 2.0 + 200 x 6.0 micro-dollars.
    assert answer.cost_micro == 3200
    assert answer.cost_source == prices.SOURCE_TABLE


def test_the_upstream_is_recorded_when_named_and_recorded_as_absent_when_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A backend the answer never confirmed is never filled in from the model slug."""
    price_the_model(monkeypatch, OR_PRICES)

    transport, _ = answered(completion(provider="Alibaba"))
    named = adapter(transport).complete(a_request())
    assert named.extra["upstream_provider"] == "Alibaba"

    transport, _ = answered(completion())
    quiet = adapter(transport).complete(a_request())
    assert quiet.extra["upstream_provider"] is None
    assert "did not name the upstream" in quiet.extra["upstream_provider_note"]
    # What was asked of the routing is recorded either way.
    assert quiet.extra["routing"]["allow_fallbacks"] is False
    assert quiet.extra["routing"]["max_price"] == {"prompt": 2.0, "completion": 6.0}


# ------------------------------------------------------------------- refusals --
def test_no_upstream_within_the_price_ceiling_is_a_refusal_not_a_failed_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OpenRouter answers 404 when nothing satisfies the constraints. Nothing was billed."""
    price_the_model(monkeypatch, OR_PRICES)
    transport, _ = answered({"error": {"message": "no allowed providers"}}, status=404)
    with pytest.raises(ProviderError) as raised:
        adapter(transport).complete(a_request())
    assert raised.value.code == "no_provider_available"


def test_an_account_with_no_credit_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    price_the_model(monkeypatch, OR_PRICES)
    transport, _ = answered({"error": {"message": "insufficient credits"}}, status=402)
    with pytest.raises(ProviderError) as raised:
        adapter(transport).complete(a_request())
    assert raised.value.code == "insufficient_credit"


def test_an_error_returned_inside_a_200_is_still_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    price_the_model(monkeypatch, OR_PRICES)
    transport, _ = answered({"error": {"code": "upstream_timeout"}, "choices": []})
    with pytest.raises(ProviderError) as raised:
        adapter(transport).complete(a_request())
    assert raised.value.code == "upstream_error"


def test_a_timeout_is_a_timeout_and_never_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    price_the_model(monkeypatch, OR_PRICES)
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        raise httpx.ReadTimeout("too slow", request=request)

    with pytest.raises(ProviderTimeoutError):
        adapter(httpx.MockTransport(handler)).complete(a_request())
    assert len(calls) == 1


def test_an_upstream_error_body_is_never_put_in_the_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A body can carry an upstream's own text, which is not this application's to log."""
    price_the_model(monkeypatch, OR_PRICES)
    leak = "ACME-INTERNAL-TRACE customer ledger row 4471"
    transport, _ = answered({"error": {"message": leak}}, status=500)
    with pytest.raises(ProviderError) as raised:
        adapter(transport).complete(a_request())
    assert leak not in str(raised.value)
    assert str(raised.value) == "http_500"


# --------------------------------------------------- who may use the route --
def configure_openrouter(monkeypatch: pytest.MonkeyPatch, enabled: bool = True) -> None:
    monkeypatch.setenv("AI_OPENROUTER_ENABLED", "true" if enabled else "false")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    monkeypatch.setenv("OPENROUTER_MODEL", MODEL)
    monkeypatch.setenv("OPENROUTER_ALLOWED_MODELS", MODEL)
    get_settings.cache_clear()


def as_owner(client: TestClient) -> dict[str, str]:
    return {**CLIENT, "authorization": f"Bearer {GOOD_TOKEN}"}


def test_a_private_business_cannot_reach_openrouter_by_asking_for_it(
    db: FakeDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The restriction that matters. Nothing is reserved, recorded or sent."""
    configure_openrouter(monkeypatch)
    price_the_model(monkeypatch, OR_PRICES)
    use(BillableScripted(good_answer))
    with TestClient(app) as client:
        response = client.post(
            f"/api/workspaces/{WS_USER}/ai/brief?route=openrouter", headers=as_owner(client)
        )
    assert response.status_code == 503
    assert response.json()["detail"]["reason"] == "private_data_not_routable"
    # Refused before any of the machinery ran.
    assert db.ai_runs == [] and db.reservations == []


def test_a_private_business_is_not_quietly_rerouted_and_keeps_the_direct_provider(
    db: FakeDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refusing the route must not become a silent fallback to the other one."""
    configure_openrouter(monkeypatch)
    price_the_model(monkeypatch, OR_PRICES)
    use(BillableScripted(good_answer))
    with TestClient(app) as client:
        refused = client.post(
            f"/api/workspaces/{WS_USER}/ai/brief?route=openrouter", headers=as_owner(client)
        )
        direct = client.post(f"/api/workspaces/{WS_USER}/ai/brief", headers=as_owner(client))
    assert refused.status_code == 503
    assert direct.status_code == 200
    # Exactly one run happened, and it was the direct one.
    assert [r["provider"] for r in db.ai_runs] == ["openai"]


def test_a_private_workspace_is_never_offered_the_route_even_when_it_is_configured(
    db: FakeDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Data mode decides, not configuration — shown with the control switched on."""
    configure_openrouter(monkeypatch)
    monkeypatch.setattr(routing, "OFFER_OPENROUTER_IN_CONTROLS", True)
    with TestClient(app) as client:
        listed = client.get(f"/api/workspaces/{WS_USER}/ai/routes", headers=as_owner(client))
        demo = visitor(TestClient(app)).get(f"/api/workspaces/{WS_VISITOR}/ai/routes")
    assert [o["key"] for o in listed.json()["options"]] == ["direct"]
    assert [o["key"] for o in demo.json()["options"]] == ["direct", "openrouter"]


def test_the_control_offers_nothing_but_the_direct_route_while_the_switch_is_off(
    db: FakeDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The interview release hides the choice; the route itself still answers when asked.

    The switch is about what an interface offers, so a demo workspace sees one option even
    though the deployment is configured for both. `resolve()` is untouched, which is what
    keeps the unfinished comparison runnable.
    """
    configure_openrouter(monkeypatch)
    assert routing.OFFER_OPENROUTER_IN_CONTROLS is False
    with TestClient(app) as client:
        demo = visitor(client).get(f"/api/workspaces/{WS_VISITOR}/ai/routes")
    assert [o["key"] for o in demo.json()["options"]] == ["direct"]


def test_a_demo_workspace_is_refused_when_the_deployment_has_not_switched_it_on(
    db: FakeDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure_openrouter(monkeypatch, enabled=False)
    use(BillableScripted(good_answer))
    with TestClient(app) as client:
        visitor(client)
        response = client.post(
            f"/api/workspaces/{WS_VISITOR}/ai/brief?route=openrouter", headers=CLIENT
        )
    assert response.status_code == 503
    assert response.json()["detail"]["reason"] == "route_disabled"
    assert db.ai_runs == [] and db.reservations == []


def test_an_unknown_route_is_rejected_rather_than_falling_back_to_the_default(
    db: FakeDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    use(BillableScripted(good_answer))
    with TestClient(app) as client:
        visitor(client)
        response = client.post(
            f"/api/workspaces/{WS_VISITOR}/ai/brief?route=nonsense", headers=CLIENT
        )
    assert response.status_code == 422
    assert db.ai_runs == []


def test_another_visitors_workspace_stays_out_of_reach_whatever_route_is_named(
    db: FakeDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The route is chosen after the scope, so naming one cannot widen what is reachable."""
    configure_openrouter(monkeypatch)
    use(BillableScripted(good_answer))
    with TestClient(app) as client:
        visitor(client)
        response = client.post(
            f"/api/workspaces/{WS_USER}/ai/brief?route=openrouter", headers=CLIENT
        )
    # WS_USER belongs to the authenticated owner, not to this visitor.
    assert response.status_code == 404
    assert db.ai_runs == [] and db.reservations == []


# ------------------------------------------------- reuse cannot cross a configuration --
def test_a_stored_explanation_is_not_reused_for_a_different_configuration(
    db: FakeDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reuse key includes the provider and model, so one model's answer is never another's.

    Without this the second route would be handed the first route's explanation and report it
    as current, and a comparison between two models would be a comparison of one with itself.
    """
    from app.ai import scenario as scenario_module
    from app.supabase import SupabaseClient, get_supabase

    supabase = app.dependency_overrides[get_supabase]()
    assert isinstance(supabase, SupabaseClient)

    scope = SimpleNamespace(workspace_id=WS_VISITOR, business_id=BIZ)
    db.ai_runs.append(
        {
            "id": "99999999-9999-4999-8999-999999999999",
            "workspace_id": WS_VISITOR,
            "business_id": BIZ,
            "capability": scenario_module.CAPABILITY,
            "status": "succeeded",
            "analysis_date": "2026-08-28",
            "input_fingerprint": "fp-1",
            "provider": "openai",
            "model_requested": "test-model",
            "output": {"headline": "the direct model's answer"},
            "created_at": "2026-09-14T00:00:00+00:00",
        }
    )

    same = scenario_module.latest(supabase, scope, "2026-08-28", "fp-1", "openai", "test-model")
    other = scenario_module.latest(supabase, scope, "2026-08-28", "fp-1", "openrouter", MODEL)
    assert same is not None and same["output"]["headline"] == "the direct model's answer"
    assert other is None


# ------------------------------------------- the conversation panel's own round shape --
def test_the_ask_round_schema_survives_serialisation_to_the_router(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The panel's schema is the most nested one this application sends.

    It carries nullable objects for each tool's arguments and for the answer, which is exactly
    the shape a strict JSON-schema implementation is most likely to mangle. What is asserted
    here is that the adapter sends it intact and reads a round back; whether a given upstream
    HONOURS it is a live question, and is still pending a key.
    """
    from app.ai import prompts

    price_the_model(monkeypatch, OR_PRICES)
    round_text = json.dumps(
        {
            "action": "use_tools",
            "tools": [
                {
                    "name": "transactions",
                    "transactions": {
                        "category": "suppliers",
                        "description_contains": None,
                        "date_from": "2026-07-01",
                        "date_to": "2026-08-24",
                        "min_abs_minor": None,
                        "limit": 5,
                    },
                    "scenario": None,
                    "propose_category": None,
                }
            ],
            "answer": None,
        }
    )
    transport, seen = answered(
        completion(choices=[{"message": {"content": round_text}, "finish_reason": "stop"}])
    )
    request = ModelRequest(
        capability="ask",
        prompt_version=prompts.ASK_PROMPT_VERSION,
        model=MODEL,
        system=prompts.ASK_SYSTEM,
        user="the turn",
        json_schema=prompts.ASK_ROUND_SCHEMA,
        max_output_tokens=1600,
        timeout_seconds=30.0,
    )
    response = adapter(transport).complete(request)

    sent = json.loads(seen[0].content)
    schema = sent["response_format"]["json_schema"]
    assert sent["response_format"]["json_schema"]["strict"] is True
    assert schema["name"] == "ask"
    # The nested nullable halves both arrive as written.
    tools = schema["schema"]["properties"]["tools"]["items"]["properties"]
    assert tools["scenario"]["type"] == ["object", "null"]
    assert "expected_payment_minor" in tools["scenario"]["properties"]
    assert schema["schema"]["properties"]["answer"]["type"] == ["object", "null"]
    # A round comes back parseable, with the routing refusals still in place.
    assert json.loads(response.text)["tools"][0]["name"] == "transactions"
    assert sent["provider"]["allow_fallbacks"] is False
    assert sent["provider"]["data_collection"] == "deny"


def test_a_repair_keeps_the_routing_record_of_the_attempt_that_answered(
    db: FakeDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second attempt must not erase how the call was routed.

    Found on the first live OpenRouter brief: it needed a repair, and the run stored a routed
    provider with `routing` null — a run that says it went through a router and cannot say
    through what. The merge rebuilt the response from the second attempt's fields and simply
    did not copy `extra`.
    """
    from tests.test_ai_brief import BillableScripted, good_answer, post_brief

    price_the_model(monkeypatch, OR_PRICES)
    routing = {
        "routing": {"allow_fallbacks": False, "data_collection": "deny"},
        "upstream_provider": "Alibaba",
    }

    class Routed(BillableScripted):
        name = "openrouter"

        def complete(self, request: ModelRequest):
            answer = super().complete(request)
            return type(answer)(**{**answer.__dict__, "extra": routing})

    # The first answer is unusable, so a repair runs; the second is fine.
    use(Routed("not json at all", good_answer))
    with TestClient(app) as client:
        visitor(client)
        body = post_brief(client).json()

    assert body["run"]["attempts"] == 2
    assert body["run"]["routing"] == routing
    assert body["run"]["routing"]["upstream_provider"] == "Alibaba"
