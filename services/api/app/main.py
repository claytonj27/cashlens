"""CashLens API entrypoint.

The ASGI application object is `app` in this module (`app.main:app`). Vercel
detects it automatically from app/main.py; locally run:

    .venv/bin/uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

Routes are served under `/api` so the same paths work whether the frontend
proxies to this service or a client calls it directly.
"""

import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.admin import router as admin_router
from app.ai.routes import router as ai_router
from app.analysis.routes import router as analysis_router
from app.config import get_settings
from app.demo import router as demo_router
from app.discovery.admin import router as discovery_admin_router
from app.discovery.routes import router as discovery_router
from app.imports.cashflow_routes import router as cashflow_router
from app.imports.routes import router as imports_router
from app.logging_setup import configure_logging
from app.ops import router as ops_router
from app.private import router as private_router
from app.retention import router as retention_router
from app.supabase import SupabaseError
from app.workspaces import router as workspaces_router

SERVICE_NAME = "cashlens-api"
SERVICE_VERSION = "0.1.0"

logger = logging.getLogger(__name__)


class HealthResponse(BaseModel):
    """Process health only. No database, storage or model connectivity is implied."""

    status: Literal["ok"]
    service: str
    version: str
    environment: str
    time: datetime


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    logger.info(
        "api.start",
        extra={
            "environment": settings.app_env,
            "version": SERVICE_VERSION,
            "configured": settings.integration_presence(),
        },
    )
    yield
    logger.info("api.stop")


app = FastAPI(title="CashLens API", version=SERVICE_VERSION, lifespan=lifespan)
app.include_router(admin_router)
app.include_router(ops_router)
app.include_router(retention_router)
app.include_router(demo_router)
app.include_router(private_router)
app.include_router(workspaces_router)
app.include_router(analysis_router)
app.include_router(ai_router)
app.include_router(imports_router)
app.include_router(cashflow_router)
app.include_router(discovery_admin_router)
app.include_router(discovery_router)


# The interactive docs and the schema are development conveniences. Production answers 404 for
# all three on every request, the way the other production switches are read: the schema lists
# every route, and a public deployment has no reason to advertise them.
DOCS_PATHS = frozenset({"/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"})


@app.middleware("http")
async def hide_docs_in_production(request: Request, call_next):  # type: ignore[no-untyped-def]
    if request.url.path in DOCS_PATHS and get_settings().app_env == "production":
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    return await call_next(request)


def request_reference(request: Request) -> str:
    """A safe correlation id: the platform's request id when present, else one minted here."""
    existing = getattr(request.state, "reference", None)
    if existing:
        return str(existing)
    platform = request.headers.get("x-vercel-id", "").split("::")[-1].strip()
    reference = platform or uuid.uuid4().hex[:16]
    request.state.reference = reference
    return reference


@app.middleware("http")
async def no_store(request, call_next):  # type: ignore[no-untyped-def]
    """Session and account responses must never be cached by browsers or proxies."""
    reference = request_reference(request)
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
        response.headers.setdefault("X-Request-Id", reference)
    return response


@app.exception_handler(RequestValidationError)
async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
    """A body or parameter that does not fit: 422 naming the field and the problem, never
    echoing the input. The default handler echoes it, and an input the JSON parser admits but
    the encoder refuses (NaN, Infinity) then turns a 422 into a 500."""
    errors = [
        {key: value for key, value in error.items() if key in ("type", "loc", "msg")}
        for error in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content={"detail": errors},
        headers={"Cache-Control": "no-store", "X-Request-Id": request_reference(request)},
    )


@app.exception_handler(SupabaseError)
async def upstream_failure(request: Request, exc: SupabaseError) -> JSONResponse:
    """The data service failed: answer 502 with a reference, log metadata only, never a body."""
    reference = request_reference(request)
    logger.warning(
        "upstream.failed",
        extra={
            "reference": reference,
            "path": request.url.path,
            "method": request.method,
            "error_class": type(exc).__name__,
            "upstream_status": exc.status,
            "transient": exc.transient,
        },
    )
    detail = (
        "the data service did not respond in time; nothing was changed, try again"
        if exc.transient
        else "the data service refused the request; nothing was changed"
    )
    return JSONResponse(
        status_code=502,
        content={"detail": detail, "reference": reference, "retryable": exc.transient},
        headers={"Cache-Control": "no-store", "X-Request-Id": reference},
    )


@app.exception_handler(Exception)
async def unexpected_failure(request: Request, exc: Exception) -> JSONResponse:
    """Anything unhandled: a JSON 500 with a reference instead of a bare page."""
    reference = request_reference(request)
    logger.error(
        "request.failed",
        extra={
            "reference": reference,
            "path": request.url.path,
            "method": request.method,
            "error_class": type(exc).__name__,
        },
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "unexpected error; nothing was changed", "reference": reference},
        headers={"Cache-Control": "no-store", "X-Request-Id": reference},
    )


@app.get("/api/health", response_model=HealthResponse, tags=["operations"])
def health() -> HealthResponse:
    """Report that the process is up. Deliberately checks nothing external."""
    settings = get_settings()
    return HealthResponse(
        status="ok",
        service=SERVICE_NAME,
        version=SERVICE_VERSION,
        environment=settings.app_env,
        time=datetime.now(UTC),
    )
