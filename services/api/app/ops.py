"""Operator-only diagnostics.

Every route here answers 404 unless OPS_DIAGNOSTICS_ENABLED is true and the
caller presents OPS_DIAGNOSTICS_TOKEN in the X-Ops-Token header. The routes
exist to verify hosting behavior (a database round trip on an isolated probe
table, one bounded model call, streaming, cookies and header handling) and are
switched off after use. Nothing here touches financial data, responses never
contain secrets or raw header values, and /api/health never calls this module.
"""

import asyncio
import hashlib
import secrets
import time
import uuid
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse, StreamingResponse
from openai import OpenAI
from pydantic import BaseModel, Field

from app.config import Settings, get_settings

PROBE_COOKIE = "cl_probe"
PROBE_TABLE = "ops_probe"


def require_operator(request: Request) -> Settings:
    settings = get_settings()
    token = settings.ops_diagnostics_token
    if not settings.ops_diagnostics_enabled or token is None:
        raise HTTPException(status_code=404, detail="Not Found")
    presented = request.headers.get("x-ops-token", "")
    expected = token.get_secret_value()
    if not presented or not secrets.compare_digest(presented.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="operator token required")
    return settings


router = APIRouter(
    prefix="/api/ops",
    tags=["operator diagnostics"],
    dependencies=[Depends(require_operator)],
    include_in_schema=False,
)


def _digest(value: str | None) -> str | None:
    return hashlib.sha256(value.encode()).hexdigest()[:16] if value else None


@router.get("/config")
def config_presence() -> dict[str, Any]:
    """Report which integrations are configured. Values are never returned."""
    settings = get_settings()
    return {"environment": settings.app_env, "configured": settings.integration_presence()}


class ProbeStep(BaseModel):
    name: str
    ok: bool
    status: int | None = None
    ms: float
    detail: str | None = None


class DbProbeResult(BaseModel):
    ok: bool
    probe_id: str
    steps: list[ProbeStep]


def _supabase_headers(key: str) -> dict[str, str]:
    headers = {"apikey": key, "Content-Type": "application/json"}
    if key.startswith("eyJ"):  # legacy JWT-shaped keys are also sent as a bearer token
        headers["Authorization"] = f"Bearer {key}"
    return headers


@router.post("/db-probe", response_model=DbProbeResult)
def db_probe() -> DbProbeResult:
    """Insert, read back and delete one synthetic row in the isolated probe table."""
    settings = get_settings()
    if settings.supabase_url is None or settings.supabase_service_role_key is None:
        raise HTTPException(
            status_code=503, detail="Supabase is not configured for this deployment."
        )
    base = str(settings.supabase_url).rstrip("/") + f"/rest/v1/{PROBE_TABLE}"
    headers = _supabase_headers(settings.supabase_service_role_key.get_secret_value())
    represent = {**headers, "Prefer": "return=representation"}
    probe_id = str(uuid.uuid4())
    match = {"probe_id": f"eq.{probe_id}"}
    steps: list[ProbeStep] = []

    def step(name: str, call: Callable[[], httpx.Response]) -> httpx.Response | None:
        started = time.perf_counter()
        try:
            response = call()
        except httpx.HTTPError as exc:
            steps.append(
                ProbeStep(
                    name=name,
                    ok=False,
                    ms=round((time.perf_counter() - started) * 1000, 1),
                    detail=type(exc).__name__,
                )
            )
            return None
        ok = response.is_success
        steps.append(
            ProbeStep(
                name=name,
                ok=ok,
                status=response.status_code,
                ms=round((time.perf_counter() - started) * 1000, 1),
                detail=None if ok else response.text[:200],
            )
        )
        return response if ok else None

    with httpx.Client(timeout=10.0) as client:
        inserted = step(
            "insert",
            lambda: client.post(
                base, headers=represent, json={"probe_id": probe_id, "note": "synthetic probe"}
            ),
        )
        if inserted is not None:
            selected = step(
                "select",
                lambda: client.get(
                    base, headers=headers, params={**match, "select": "probe_id,note"}
                ),
            )
            found = bool(selected is not None and selected.json())
            matched = found and selected.json()[0].get("probe_id") == probe_id  # type: ignore[union-attr]
            steps.append(
                ProbeStep(
                    name="verify_read",
                    ok=matched,
                    ms=0.0,
                    detail=None if matched else "row not found",
                )
            )
            deleted = step("delete", lambda: client.delete(base, headers=represent, params=match))
            if deleted is not None:
                after = step(
                    "select_after_delete",
                    lambda: client.get(
                        base, headers=headers, params={**match, "select": "probe_id"}
                    ),
                )
                gone = after is not None and after.json() == []
                steps.append(
                    ProbeStep(
                        name="verify_deleted",
                        ok=gone,
                        ms=0.0,
                        detail=None if gone else "row still present",
                    )
                )
    return DbProbeResult(ok=all(s.ok for s in steps), probe_id=probe_id, steps=steps)


class OpenAIProbeRequest(BaseModel):
    max_output_tokens: int = Field(default=32, ge=1, le=64)
    timeout_seconds: float = Field(default=20.0, ge=1.0, le=30.0)


class OpenAIProbeResult(BaseModel):
    ok: bool
    requested_model: str
    model: str | None = None
    latency_ms: float
    usage: dict[str, int] | None = None
    finish_reason: str | None = None
    output_preview: str | None = None
    request_id: str | None = None
    error: str | None = None


@router.post("/openai-probe", response_model=OpenAIProbeResult)
def openai_probe(body: OpenAIProbeRequest) -> OpenAIProbeResult:
    """Make exactly one bounded, synthetic model call. No retries, no tools, no follow-ups."""
    settings = get_settings()
    if settings.openai_api_key is None or settings.openai_model is None:
        raise HTTPException(status_code=503, detail="OpenAI is not configured for this deployment.")
    model = settings.openai_model
    client = OpenAI(
        api_key=settings.openai_api_key.get_secret_value(),
        timeout=body.timeout_seconds,
        max_retries=0,
    )
    extra: dict[str, Any] = {}
    if model.startswith("gpt-5"):
        extra["reasoning_effort"] = "minimal"
    started = time.perf_counter()
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Reply with exactly the word OK."}],
            max_completion_tokens=body.max_output_tokens,
            **extra,
        )
    except Exception as exc:  # any SDK or transport failure is reported, never retried
        return OpenAIProbeResult(
            ok=False,
            requested_model=model,
            latency_ms=round((time.perf_counter() - started) * 1000, 1),
            request_id=getattr(exc, "request_id", None),
            error=f"{type(exc).__name__}: {str(exc)[:200]}",
        )
    latency_ms = round((time.perf_counter() - started) * 1000, 1)
    usage: dict[str, int] | None = None
    if response.usage is not None:
        usage = {
            "prompt_tokens": response.usage.prompt_tokens,
            "completion_tokens": response.usage.completion_tokens,
            "total_tokens": response.usage.total_tokens,
        }
        details = getattr(response.usage, "completion_tokens_details", None)
        reasoning = getattr(details, "reasoning_tokens", None)
        if reasoning is not None:
            usage["reasoning_tokens"] = reasoning
        prompt_details = getattr(response.usage, "prompt_tokens_details", None)
        cached = getattr(prompt_details, "cached_tokens", None)
        if cached is not None:
            usage["cached_prompt_tokens"] = cached
    choice = response.choices[0] if response.choices else None
    text = (choice.message.content or "") if choice is not None else ""
    return OpenAIProbeResult(
        ok=True,
        requested_model=model,
        model=response.model,
        latency_ms=latency_ms,
        usage=usage,
        finish_reason=choice.finish_reason if choice is not None else None,
        output_preview=text[:40],
        request_id=getattr(response, "_request_id", None),
    )


@router.get("/stream")
async def stream_probe() -> StreamingResponse:
    """Five small text chunks with pauses, to observe incremental delivery."""

    async def chunks() -> AsyncIterator[bytes]:
        for index in range(1, 6):
            stamp = datetime.now(UTC).isoformat(timespec="milliseconds")
            yield f"chunk {index} at {stamp}\n".encode()
            await asyncio.sleep(0.25)

    return StreamingResponse(
        chunks(),
        media_type="text/plain",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.get("/cookie")
def cookie_probe(request: Request, response: Response) -> dict[str, Any]:
    """Set an HttpOnly cookie on the first call; report its presence on the next."""
    settings = get_settings()
    existing = request.cookies.get(PROBE_COOKIE)
    if existing:
        return {"round_trip": True, "value_sha256_prefix": _digest(existing)}
    value = secrets.token_urlsafe(16)
    response.set_cookie(
        PROBE_COOKIE,
        value,
        httponly=True,
        samesite="lax",
        secure=settings.app_env != "development",
        max_age=300,
        path="/api/ops",
    )
    return {"round_trip": False, "set": True, "value_sha256_prefix": _digest(value)}


@router.get("/echo")
def echo_probe(request: Request) -> dict[str, Any]:
    """Report how selected headers arrived. Values are hashed, never returned."""
    return {
        "host": request.headers.get("host"),
        "forwarded_host": request.headers.get("x-forwarded-host"),
        "bypass_header_sha256_prefix": _digest(request.headers.get("x-vercel-protection-bypass")),
        "set_bypass_cookie_header_present": "x-vercel-set-bypass-cookie" in request.headers,
        "ops_token_present": "x-ops-token" in request.headers,
        "cookie_names": sorted(request.cookies.keys()),
    }


@router.get("/redirect")
def redirect_probe() -> RedirectResponse:
    """A redirect to a foreign host, to confirm the proxy passes it through unfollowed."""
    return RedirectResponse("https://example.com/", status_code=302)
