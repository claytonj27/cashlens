"""Thin client for the Supabase Data API and Auth server.

The API talks to Supabase with the service role, which bypasses row security.
Every caller in this service is therefore responsible for checking who is
asking (a visitor cookie it issued, or a validated Supabase Auth access token)
before touching private rows. Access tokens are validated by asking the Auth
server for the user they belong to; results are cached briefly, keyed by a
hash of the token and never beyond the token's own expiry.
"""

import base64
import hashlib
import json
import time
from typing import Any

import httpx
from fastapi import HTTPException

from app.config import get_settings

USER_CACHE_TTL_SECONDS = 60.0


class SupabaseError(Exception):
    """A Data API, Auth or Storage call failed; the message never contains credentials."""

    def __init__(self, message: str, *, status: int | None = None, transient: bool = False) -> None:
        super().__init__(message)
        self.status = status
        self.transient = transient


TRANSIENT_STATUSES = frozenset({502, 503, 504})
READ_RETRIES = 1  # one extra attempt: with the 10 s client timeout a read costs at most ~20.5 s
RETRY_DELAYS = (0.5,)


def _jwt_expiry(token: str) -> float | None:
    """Best-effort expiry from an unverified JWT payload, only to bound the cache."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return float(json.loads(base64.urlsafe_b64decode(payload)).get("exp"))
    except Exception:
        return None


class SupabaseClient:
    def __init__(
        self, url: str, service_key: str, transport: httpx.BaseTransport | None = None
    ) -> None:
        headers = {"apikey": service_key, "Content-Type": "application/json"}
        if service_key.startswith("eyJ"):  # legacy JWT-shaped keys also go in the bearer slot
            headers["Authorization"] = f"Bearer {service_key}"
        self._client = httpx.Client(
            base_url=url.rstrip("/"), timeout=10.0, transport=transport, headers=headers
        )
        self._service_key = service_key
        self._user_cache: dict[str, tuple[float, dict[str, Any] | None]] = {}

    def _send(self, method: str, url: str, *, idempotent: bool, **kwargs: Any) -> httpx.Response:
        """One upstream call. Idempotent reads retry a bounded number of times on gateway
        errors and transport failures; writes never do, so nothing is applied twice."""
        attempts = READ_RETRIES + 1 if idempotent else 1
        last_error: SupabaseError | None = None
        for attempt in range(attempts):
            try:
                response = self._client.request(method, url, **kwargs)
            except httpx.HTTPError as exc:
                last_error = SupabaseError(
                    f"{method} {url.split('?')[0]} -> {type(exc).__name__}", transient=True
                )
            else:
                if response.status_code not in TRANSIENT_STATUSES:
                    return response
                last_error = SupabaseError(
                    f"{method} {url.split('?')[0]} -> {response.status_code}",
                    status=response.status_code,
                    transient=True,
                )
            if attempt < attempts - 1:
                time.sleep(RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)])
        assert last_error is not None
        raise last_error

    def rest(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json_body: Any = None,
        prefer: str | None = None,
    ) -> Any:
        headers = {"Prefer": prefer} if prefer else {}
        response = self._send(
            method,
            f"/rest/v1/{path}",
            idempotent=method == "GET",
            params=params,
            json=json_body,
            headers=headers,
        )
        if response.status_code >= 400:
            raise SupabaseError(
                f"{method} {path} -> {response.status_code}: {response.text[:300]}",
                status=response.status_code,
            )
        return response.json() if response.content else None

    def rpc(self, name: str, params: dict[str, Any]) -> Any:
        return self.rest("POST", f"rpc/{name}", json_body=params)

    def count(self, path: str, params: dict[str, str]) -> int:
        response = self._send(
            "GET",
            f"/rest/v1/{path}",
            idempotent=True,
            params={**params, "select": "id"},
            headers={"Prefer": "count=exact", "Range": "0-0", "Range-Unit": "items"},
        )
        if response.status_code >= 400:
            raise SupabaseError(
                f"count {path} -> {response.status_code}", status=response.status_code
            )
        content_range = response.headers.get("content-range", "*/0")
        return int(content_range.rsplit("/", 1)[-1])

    def rest_all(
        self, path: str, params: dict[str, str], *, page_size: int = 1000, max_rows: int = 50000
    ) -> list[dict[str, Any]]:
        """Every matching row in ordered pages; the Data API row cap never truncates silently."""
        if "order" not in params:
            raise ValueError("rest_all needs a deterministic order")
        rows: list[dict[str, Any]] = []
        offset = 0
        while True:
            page = self.rest(
                "GET", path, params={**params, "limit": str(page_size), "offset": str(offset)}
            )
            rows.extend(page)
            if len(page) < page_size:
                return rows
            offset += page_size
            if offset >= max_rows:
                raise SupabaseError(f"{path}: more than {max_rows} rows")

    # ---- Storage (service role; the API never hands these credentials on) ----
    def _storage_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._service_key}"}

    def storage_signed_upload(self, bucket: str, path: str) -> str:
        """A one-path upload token for a browser to PUT the file straight to Storage."""
        response = self._send(
            "POST",
            f"/storage/v1/object/upload/sign/{bucket}/{path}",
            idempotent=True,  # issuing a token has no side effect beyond the token itself
            json={},
            headers=self._storage_headers(),
        )
        if response.status_code >= 400:
            raise SupabaseError(
                f"signed upload {bucket} -> {response.status_code}", status=response.status_code
            )
        token = response.json().get("token")
        if not token:
            raise SupabaseError("signed upload: no token in response")
        return str(token)

    def storage_object_info(self, bucket: str, path: str) -> dict[str, Any] | None:
        response = self._send(
            "GET",
            f"/storage/v1/object/info/{bucket}/{path}",
            idempotent=True,
            headers=self._storage_headers(),
        )
        if response.status_code in (400, 404):
            return None
        if response.status_code >= 400:
            raise SupabaseError(
                f"object info {bucket} -> {response.status_code}", status=response.status_code
            )
        return response.json()

    def storage_download(self, bucket: str, path: str, max_bytes: int) -> bytes:
        """The object's bytes, refusing anything beyond max_bytes without reading it all."""
        try:
            with self._client.stream(
                "GET", f"/storage/v1/object/{bucket}/{path}", headers=self._storage_headers()
            ) as response:
                if response.status_code in TRANSIENT_STATUSES:
                    raise SupabaseError(
                        f"download {bucket} -> {response.status_code}",
                        status=response.status_code,
                        transient=True,
                    )
                if response.status_code >= 400:
                    raise SupabaseError(
                        f"download {bucket} -> {response.status_code}", status=response.status_code
                    )
                chunks: list[bytes] = []
                received = 0
                for chunk in response.iter_bytes():
                    received += len(chunk)
                    if received > max_bytes:
                        raise SupabaseError("download: object exceeds the size limit", status=413)
                    chunks.append(chunk)
        except httpx.HTTPError as exc:
            raise SupabaseError(
                f"download {bucket} -> {type(exc).__name__}", transient=True
            ) from exc
        return b"".join(chunks)

    def storage_delete(self, bucket: str, paths: list[str]) -> None:
        response = self._send(
            "DELETE",
            f"/storage/v1/object/{bucket}",
            idempotent=True,  # deleting the same paths twice is harmless
            json={"prefixes": paths},
            headers=self._storage_headers(),
        )
        if response.status_code >= 400:
            raise SupabaseError(
                f"delete {bucket} -> {response.status_code}", status=response.status_code
            )

    def auth_user(self, access_token: str) -> dict[str, Any] | None:
        """The user an access token belongs to, or None when the token is not valid."""
        key = hashlib.sha256(access_token.encode()).hexdigest()
        now = time.monotonic()
        cached = self._user_cache.get(key)
        if cached is not None and cached[0] > now:
            return cached[1]
        response = self._send(
            "GET",
            "/auth/v1/user",
            idempotent=True,
            headers={"Authorization": f"Bearer {access_token}", "apikey": self._service_key},
        )
        user = response.json() if response.status_code == 200 else None
        ttl = USER_CACHE_TTL_SECONDS
        expiry = _jwt_expiry(access_token)
        if expiry is not None:
            ttl = max(0.0, min(ttl, expiry - time.time()))
        if len(self._user_cache) > 1000:
            self._user_cache.clear()
        self._user_cache[key] = (now + ttl, user)
        return user


_client: SupabaseClient | None = None


def get_supabase() -> SupabaseClient:
    """Process-wide client; answers 503 when Supabase is not configured."""
    global _client
    if _client is None:
        settings = get_settings()
        if settings.supabase_url is None or settings.supabase_service_role_key is None:
            raise HTTPException(
                status_code=503, detail="Supabase is not configured for this deployment."
            )
        _client = SupabaseClient(
            str(settings.supabase_url), settings.supabase_service_role_key.get_secret_value()
        )
    return _client
