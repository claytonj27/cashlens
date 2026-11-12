"""Statement import routes against an in-memory Data API, Auth and Storage stand-in.

The stand-in mirrors the database function confirm_import closely enough to
exercise the API's contract (revisions, blocking rows, idempotent repeats,
atomic failure), while the real function is verified against the development
database by db/checks/import_flow_check.py.
"""

import copy
import json
import pathlib
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest import mock

import httpx
import pytest
from fastapi.testclient import TestClient

from app.imports import csvparse, extract
from app.main import app
from app.supabase import SupabaseClient, SupabaseError, get_supabase

USER_A, USER_B = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2"
BIZ_A, BIZ_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb1", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb2"
WS_A, WS_B = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeee1", "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeee2"
ACC_A = "dddddddd-dddd-4ddd-8ddd-ddddddddddd1"
ACC_B = "dddddddd-dddd-4ddd-8ddd-ddddddddddd2"
TX_EXISTING = "ffffffff-ffff-4fff-8fff-fffffffffff1"
TOKEN_A, TOKEN_B = "token-a", "token-b"
VISITOR = "99999999-9999-4999-8999-999999999999"
CLIENT = {"x-cashlens-client": "web"}
AUTH_A = {"authorization": f"Bearer {TOKEN_A}", **CLIENT}
AUTH_B = {"authorization": f"Bearer {TOKEN_B}", **CLIENT}

CSV_MAY = (
    b"date,description,amount,balance,currency,transaction_id\n"
    b"2026-05-01,LEASE PAYMENT,-3500.00,51828.00,USD,r-may-1\n"
    b"2026-05-03,CARD SETTLEMENT,3706.76,55534.76,USD,r-may-2\n"
    b"2026-05-05,SUPPLIER,-2625.95,52908.81,USD,r-may-3\n"
)
CSV_REPLAY = (
    b"date,description,amount\n"
    b"2026-04-30,EXISTING ROW,-1000.00\n"  # same shape as the existing transaction
    b"2026-04-30,EXISTING ROW,-1000.00\n"
)
CSV_REF_CONFLICT = (
    b"date,description,amount,transaction_id\n"
    b"2026-04-30,EXISTING ROW,-1000.00,r-existing\n"  # identical replay of the existing row
    b"2026-04-30,NEW ROW,-5.00,r-new\n"
)
CSV_BAD_ROW = b"date,description,amount\nnot-a-date,BROKEN,-1.00\n2026-06-01,GOOD,2.00\n"


def now() -> str:
    return datetime.now(UTC).isoformat()


class FakeBackend:
    """A small PostgREST, Auth and Storage: enough of each for the import routes."""

    def __init__(self) -> None:
        self.tables: dict[str, list[dict[str, Any]]] = {
            "businesses": [
                {
                    "id": BIZ_A,
                    "slug": "private-a",
                    "name": "Owner A",
                    "industry": "unspecified",
                    "data_mode": "private",
                },
                {
                    "id": BIZ_B,
                    "slug": "private-b",
                    "name": "Owner B",
                    "industry": "unspecified",
                    "data_mode": "private",
                },
            ],
            "business_memberships": [
                {"business_id": BIZ_A, "user_id": USER_A, "role": "owner", "created_at": now()},
                {"business_id": BIZ_B, "user_id": USER_B, "role": "owner", "created_at": now()},
            ],
            "workspaces": [
                {
                    "id": WS_A,
                    "business_id": BIZ_A,
                    "business_data_mode": "private",
                    "visitor_id": None,
                    "user_id": USER_A,
                },
                {
                    "id": WS_B,
                    "business_id": BIZ_B,
                    "business_data_mode": "private",
                    "visitor_id": None,
                    "user_id": USER_B,
                },
            ],
            "visitors": [{"id": VISITOR}],
            "accounts": [
                {
                    "id": ACC_A,
                    "business_id": BIZ_A,
                    "name": "Operating Checking",
                    "account_type": "checking",
                    "mask": "6812",
                    "source_type": "csv_import",
                },
                {
                    "id": ACC_B,
                    "business_id": BIZ_B,
                    "name": "B Checking",
                    "account_type": "checking",
                    "mask": None,
                    "source_type": "csv_import",
                },
            ],
            "transactions": [
                {
                    "id": TX_EXISTING,
                    "business_id": BIZ_A,
                    "account_id": ACC_A,
                    "source_type": "csv_import",
                    "external_id": "r-existing",
                    "amount_minor": -100000,
                    "currency": "USD",
                    "posted_date": "2026-04-30",
                    "description": "EXISTING ROW",
                    "status": "posted",
                    "pending_transaction_id": None,
                    "removed_at": None,
                    "revision": 1,
                }
            ],
            "transaction_sources": [],
            "balance_snapshots": [],
            "document_imports": [],
            "import_jobs": [],
            "import_rows": [],
        }
        self.objects: dict[str, bytes] = {}
        self.tokens: dict[str, str] = {}
        # (method, table) -> statuses to answer before behaving normally; simulates gateway trouble
        self.failures: dict[tuple[str, str], list[int]] = {}

    # ---- storage used by tests to simulate the browser upload ----
    def upload(self, path: str, data: bytes) -> None:
        assert path in self.tokens, "upload needs a signed token for that path"
        self.objects[path] = data

    # ---- query evaluation ----
    @staticmethod
    def _match(row: dict[str, Any], key: str, spec: str) -> bool:
        op, _, value = spec.partition(".")
        current = row.get(key)
        if op == "eq":
            return str(current) == value
        if op == "neq":
            if value == "{}":
                return bool(current)
            return str(current) != value
        if op == "is":
            return current is None if value == "null" else current is not None
        if op == "in":
            return str(current) in value.strip("()").split(",")
        if op in ("lt", "gt", "gte", "lte"):
            if current is None:
                return False
            if isinstance(current, int):
                value = int(value)  # type: ignore[assignment]
            return {
                "lt": current < value,
                "gt": current > value,
                "gte": current >= value,
                "lte": current <= value,
            }[op]
        if op == "ilike":
            return value.strip("*").lower() in str(current).lower()
        if op == "cs":
            wanted = [v for v in value.strip("{}").split(",") if v]
            return all(v in (current or []) for v in wanted)
        raise AssertionError(f"unsupported filter {key}={spec}")

    def _select(self, table: str, params: dict[str, str]) -> list[dict[str, Any]]:
        rows = self.tables[table]
        for key, spec in params.items():
            if key in ("select", "order", "limit", "offset", "on_conflict"):
                continue
            rows = [r for r in rows if self._match(r, key, spec)]
        if "order" in params:
            for part in reversed(params["order"].split(",")):
                col, _, direction = part.partition(".")
                rows = sorted(
                    rows,
                    key=lambda r: (r.get(col) is None, str(r.get(col))),
                    reverse=direction == "desc",
                )
        if "offset" in params:
            rows = rows[int(params["offset"]) :]
        if "limit" in params:
            rows = rows[: int(params["limit"])]
        return rows

    def _defaults(self, table: str, row: dict[str, Any]) -> dict[str, Any]:
        base: dict[str, Any] = {"id": str(uuid.uuid4()), "created_at": now()}
        if table == "document_imports":
            base.update(
                {
                    "format": "csv",
                    "status": "awaiting_upload",
                    "draft_revision": 0,
                    "expires_at": "2999-01-01T00:00:00+00:00",
                    "object_deleted_at": None,
                    "byte_size": None,
                    "content_hash": None,
                    "parser_version": None,
                    "mapping": None,
                    "summary": None,
                    "confirmed_revision": None,
                    "confirmed_at": None,
                    "cancelled_at": None,
                    "error_code": None,
                    "error_message": None,
                }
            )
        if table == "import_rows":
            base.update(
                {
                    "reviewed": None,
                    "flags": [],
                    "decision": "keep",
                    "decision_by": "system",
                    "linked_transaction_id": None,
                    "page_number": None,
                }
            )
        if table == "transactions":
            base.update(
                {
                    "currency": "USD",
                    "status": "posted",
                    "pending_transaction_id": None,
                    "removed_at": None,
                    "revision": 1,
                    "external_id": None,
                }
            )
        return {**base, **row}

    # ---- rpc: mirrors of the database functions ----
    def _lease_ok(self, body: dict[str, Any]) -> bool:
        job = next((j for j in self.tables["import_jobs"] if j["id"] == body["p_job_id"]), None)
        return bool(
            job
            and job["import_id"] == body["p_import_id"]
            and job["state"] == "leased"
            and not job.get("expired")
            and job.get("lease_token") == body["p_lease_token"]
        )

    def rpc(self, name: str, body: dict[str, Any]) -> httpx.Response:
        queued = self.failures.get(("RPC", name))
        if queued:
            return httpx.Response(queued.pop(0), json={"message": "Gateway Timeout"})
        if name == "stage_import":
            if not self._lease_ok(body):
                return httpx.Response(400, json={"message": "lease_lost"})
            record = next(
                i for i in self.tables["document_imports"] if i["id"] == body["p_import_id"]
            )
            if record["status"] not in ("awaiting_upload", "staged"):
                return httpx.Response(400, json={"message": "invalid_state"})
            self.tables["import_rows"] = [
                r for r in self.tables["import_rows"] if r["import_id"] != body["p_import_id"]
            ]
            for r in body["p_rows"]:
                self.tables["import_rows"].append(
                    self._defaults("import_rows", {**r, "import_id": body["p_import_id"]})
                )
            fields = body["p_fields"]
            record.update(
                {
                    "status": "staged",
                    "format": fields.get("format", record.get("format")),
                    "byte_size": fields["byte_size"],
                    "content_hash": fields["content_hash"],
                    "parser_version": fields["parser_version"],
                    "mapping": fields["mapping"],
                    "summary": fields["summary"],
                    "draft_revision": fields["draft_revision"],
                    "error_code": None,
                    "error_message": None,
                }
            )
            return httpx.Response(
                200,
                content=str(len(body["p_rows"])).encode(),
                headers={"content-type": "application/json"},
            )
        if name == "fail_import":
            if not self._lease_ok(body):
                return httpx.Response(400, json={"message": "lease_lost"})
            record = next(
                i for i in self.tables["document_imports"] if i["id"] == body["p_import_id"]
            )
            changed = record["status"] in ("awaiting_upload", "staged")
            if changed:
                record.update(
                    {
                        "status": "failed",
                        "error_code": body["p_error_code"],
                        "error_message": body["p_error_message"][:300],
                    }
                )
            self.tables["import_rows"] = [
                r for r in self.tables["import_rows"] if r["import_id"] != body["p_import_id"]
            ]
            return httpx.Response(
                200,
                content=b"true" if changed else b"false",
                headers={"content-type": "application/json"},
            )
        if name == "reserve_and_create_import":
            response = self.rpc(
                "reserve_import_slot",
                {
                    "p_user_id": body["p_user_id"],
                    "p_business_id": body["p_business_id"],
                    "p_import_id": body["p_import_id"],
                    "p_declared_bytes": body["p_declared_bytes"],
                    "p_max_active": body["p_max_active"],
                    "p_max_per_hour": body["p_max_per_hour"],
                    "p_max_per_day": body["p_max_per_day"],
                    "p_max_outstanding_bytes": body["p_max_outstanding_bytes"],
                },
            )
            verdict = json.loads(response.content)[0]
            if verdict["allowed"]:
                # The row is the reservation, written in the same call as the check.
                self.tables["document_imports"].append(
                    self._defaults(
                        "document_imports",
                        {
                            "id": body["p_import_id"],
                            "business_id": body["p_business_id"],
                            "business_data_mode": "private",
                            "uploaded_by": body["p_user_id"],
                            "account_id": body["p_account_id"],
                            "proposed_account": body["p_proposed_account"],
                            "format": "csv",
                            "object_path": body["p_object_path"],
                            "file_label": body["p_file_label"],
                            "declared_bytes": body["p_declared_bytes"],
                            "expires_at": body["p_expires_at"],
                        },
                    )
                )
            return response
        if name == "reserve_import_slot":
            mine = {
                m["business_id"]
                for m in self.tables["business_memberships"]
                if m["user_id"] == body["p_user_id"]
            } or {b["id"] for b in self.tables.get("businesses", [])}
            imports = [i for i in self.tables["document_imports"] if i["business_id"] in mine]
            active = [i for i in imports if i["status"] in ("awaiting_upload", "staged")]
            outstanding = sum(
                (i.get("byte_size") or i.get("declared_bytes") or 0)
                for i in imports
                if i.get("object_deleted_at") is None
            )
            starts = self.tables.setdefault("import_starts", [])
            mine_starts = [x for x in starts if x["user_id"] == body["p_user_id"]]
            verdict = None
            if len(active) >= body["p_max_active"]:
                verdict = "active_imports"
            elif len(mine_starts) >= body["p_max_per_hour"]:
                verdict = "rate_hour"
            elif len(mine_starts) >= body["p_max_per_day"]:
                verdict = "rate_day"
            else:
                wanted = outstanding + (body.get("p_declared_bytes") or 0)
                if wanted > body["p_max_outstanding_bytes"]:
                    verdict = "outstanding_bytes"
            if verdict:
                return httpx.Response(
                    200,
                    json=[
                        {
                            "allowed": False,
                            "reason": verdict,
                            "active_count": len(active),
                            "started_hour": len(mine_starts),
                            "started_day": len(mine_starts),
                            "outstanding_bytes": outstanding,
                            "active_import_id": active[0]["id"] if active else None,
                        }
                    ],
                )
            starts.append({"user_id": body["p_user_id"], "import_id": body["p_import_id"]})
            return httpx.Response(
                200,
                json=[
                    {
                        "allowed": True,
                        "reason": "ok",
                        "active_count": len(active),
                        "started_hour": len(mine_starts),
                        "started_day": len(mine_starts),
                        "outstanding_bytes": outstanding,
                        "active_import_id": None,
                    }
                ],
            )
        if name == "release_import_start":
            starts = self.tables.setdefault("import_starts", [])
            before = len(starts)
            self.tables["import_starts"] = [
                x for x in starts if x["import_id"] != body["p_import_id"]
            ]
            return httpx.Response(
                200,
                content=b"true" if len(self.tables["import_starts"]) < before else b"false",
                headers={"content-type": "application/json"},
            )
        if name == "cancel_import":
            record = next(
                (i for i in self.tables["document_imports"] if i["id"] == body["p_import_id"]),
                None,
            )
            if record is None:
                return httpx.Response(400, json={"message": "not_found"})
            if record["status"] == "cancelled":
                return httpx.Response(
                    200,
                    json=[
                        {
                            "already_cancelled": True,
                            "rows_removed": 0,
                            "object_path": record["object_path"],
                            "object_deleted_at": record.get("object_deleted_at"),
                        }
                    ],
                )
            if record["status"] not in ("awaiting_upload", "staged", "failed"):
                return httpx.Response(400, json={"message": f"invalid_state:{record['status']}"})
            for job in self.tables["import_jobs"]:
                if job["import_id"] == body["p_import_id"] and job["state"] == "leased":
                    job.update({"state": "failed", "lease_token": None, "safe_error": "cancelled"})
            removed = [
                r for r in self.tables["import_rows"] if r["import_id"] == body["p_import_id"]
            ]
            self.tables["import_rows"] = [
                r for r in self.tables["import_rows"] if r["import_id"] != body["p_import_id"]
            ]
            record.update({"status": "cancelled", "cancelled_at": now()})
            return httpx.Response(
                200,
                json=[
                    {
                        "already_cancelled": False,
                        "rows_removed": len(removed),
                        "object_path": record["object_path"],
                        "object_deleted_at": record.get("object_deleted_at"),
                    }
                ],
            )
        if name == "acquire_import_job":
            jobs = [
                j
                for j in self.tables["import_jobs"]
                if j["idempotency_key"] == body["p_idempotency_key"]
            ]
            if jobs and jobs[0]["state"] == "succeeded":
                job = jobs[0]
                payload = {"job_id": job["id"], "state": "succeeded", "attempts": job["attempts"]}
                return httpx.Response(200, json=[{**payload, "lease_token": None}])
            if jobs and jobs[0]["state"] == "leased" and not jobs[0].get("expired"):
                return httpx.Response(400, json={"message": "job_in_progress"})
            if jobs and jobs[0]["attempts"] >= 3:
                return httpx.Response(400, json={"message": "attempts_exhausted"})
            token = str(uuid.uuid4())
            if jobs:
                job = jobs[0]
                job.update(
                    {
                        "state": "leased",
                        "attempts": job["attempts"] + 1,
                        "lease_token": token,
                        "expired": False,
                    }
                )
            else:
                job = {
                    "id": str(uuid.uuid4()),
                    "import_id": body["p_import_id"],
                    "kind": body["p_kind"],
                    "state": "leased",
                    "idempotency_key": body["p_idempotency_key"],
                    "attempts": 1,
                    "lease_token": token,
                }
                self.tables["import_jobs"].append(job)
            payload = {"job_id": job["id"], "state": job["state"], "attempts": job["attempts"]}
            return httpx.Response(200, json=[{**payload, "lease_token": token}])
        if name == "finish_import_job":
            for j in self.tables["import_jobs"]:
                if (
                    j["id"] == body["p_job_id"]
                    and j["state"] == "leased"
                    and j.get("lease_token") == body["p_lease_token"]
                ):
                    j.update({"state": body["p_state"], "lease_token": None})
                    return httpx.Response(
                        200, content=b"true", headers={"content-type": "application/json"}
                    )
            return httpx.Response(
                200, content=b"false", headers={"content-type": "application/json"}
            )
        if name == "confirm_import":
            snapshot = copy.deepcopy(self.tables)
            try:
                return httpx.Response(200, json=[self._confirm(body)])
            except _RefusedError as exc:
                self.tables = (
                    snapshot  # the real function is one transaction: nothing survives an error
                )
                return httpx.Response(400, json={"message": str(exc)})
        return httpx.Response(404, json={"message": f"unknown rpc {name}"})

    def _confirm(self, body: dict[str, Any]) -> dict[str, Any]:
        imports = [i for i in self.tables["document_imports"] if i["id"] == body["p_import_id"]]
        member = (
            any(
                m["business_id"] == imports[0]["business_id"] and m["user_id"] == body["p_user_id"]
                for m in self.tables["business_memberships"]
            )
            if imports
            else False
        )
        if not imports or not member:
            raise _RefusedError("not_found")
        record = imports[0]
        if record["status"] == "confirmed":
            if record["confirmed_revision"] == body["p_expected_revision"]:
                r = record["summary"]["result"]
                return {
                    "created_count": r["created"],
                    "linked_count": r["linked"],
                    "replaced_count": r["replaced"],
                    "excluded_count": r["excluded"],
                    "account_id": record["account_id"],
                    "already_confirmed": True,
                }
            raise _RefusedError("revision_conflict")
        if record["status"] != "staged":
            raise _RefusedError("invalid_state")
        if record["draft_revision"] != body["p_expected_revision"]:
            raise _RefusedError("revision_conflict")
        if any(
            r["import_id"] == record["id"]
            and "needs_decision" in r["flags"]
            and r.get("decision_by", "system") == "system"
            and r["decision"] != "exclude"
            for r in self.tables["import_rows"]
        ):
            raise _RefusedError("undecided_rows")
        source_type = "pdf_import" if record.get("format") == "pdf" else "csv_import"
        account_id = record["account_id"]
        if account_id is None:
            account = self._defaults(
                "accounts",
                {
                    "business_id": record["business_id"],
                    "source_type": source_type,
                    **record["proposed_account"],
                },
            )
            self.tables["accounts"].append(account)
            account_id = account["id"]
        counts = {"created": 0, "linked": 0, "replaced": 0, "excluded": 0}
        rows = sorted(
            (r for r in self.tables["import_rows"] if r["import_id"] == record["id"]),
            key=lambda r: r["row_index"],
        )
        for row in rows:
            if row["decision"] == "exclude":
                counts["excluded"] += 1
                continue
            eff = {
                **{k: v for k, v in row["extracted"].items() if k != "conflict"},
                **{k: v for k, v in (row["reviewed"] or {}).items() if v is not None},
            }
            if (
                not eff.get("posted_date")
                or not eff.get("amount_minor")
                or not (eff.get("description") or "").strip()
            ):
                raise _RefusedError(f"blocking_rows:{row['row_index']}")
            if row["decision"] in ("link_existing", "replace_existing"):
                targets = [
                    t
                    for t in self.tables["transactions"]
                    if t["id"] == row["linked_transaction_id"]
                    and t["business_id"] == record["business_id"]
                    and t["account_id"] == account_id
                    and t["removed_at"] is None
                ]
                if not targets:
                    raise _RefusedError(f"link_target_missing:{row['row_index']}")
                if row["decision"] == "replace_existing":
                    targets[0].update(
                        {
                            "amount_minor": eff["amount_minor"],
                            "posted_date": eff["posted_date"],
                            "description": eff["description"],
                            "revision": targets[0]["revision"] + 1,
                        }
                    )
                    counts["replaced"] += 1
                else:
                    counts["linked"] += 1
                ref_lend = eff.get("external_ref")
                if (
                    ref_lend
                    and targets[0].get("external_id") is None
                    and not any(
                        t["account_id"] == account_id and t.get("external_id") == ref_lend
                        for t in self.tables["transactions"]
                    )
                ):
                    targets[0]["external_id"] = ref_lend
                self.tables["transaction_sources"].append(
                    {
                        "id": str(uuid.uuid4()),
                        "transaction_id": targets[0]["id"],
                        "raw_event_id": None,
                        "import_row_id": row["id"],
                    }
                )
                continue
            ref = eff.get("external_ref")
            if ref and any(
                t["account_id"] == account_id and t["external_id"] == ref
                for t in self.tables["transactions"]
            ):
                raise _RefusedError(f"duplicate_source_reference:{row['row_index']}")
            tx = self._defaults(
                "transactions",
                {
                    "business_id": record["business_id"],
                    "account_id": account_id,
                    "source_type": "csv_import",
                    "external_id": ref,
                    "amount_minor": eff["amount_minor"],
                    "posted_date": eff["posted_date"],
                    "description": eff["description"][:500],
                },
            )
            self.tables["transactions"].append(tx)
            self.tables["transaction_sources"].append(
                {
                    "id": str(uuid.uuid4()),
                    "transaction_id": tx["id"],
                    "raw_event_id": None,
                    "import_row_id": row["id"],
                }
            )
            counts["created"] += 1
        balance = (record.get("summary") or {}).get("balance")
        if balance and balance.get("closing_minor") is not None:
            self.tables["balance_snapshots"].append(
                {
                    "id": str(uuid.uuid4()),
                    "business_id": record["business_id"],
                    "account_id": account_id,
                    "kind": "statement_closing",
                    "provenance": "bank_observed",
                    "source_type": "csv_import",
                    "amount_minor": balance["closing_minor"],
                    "as_of": f"{balance['period_end']}T23:59:59+00:00",
                }
            )
            if balance.get("opening_minor") is not None:
                self.tables["balance_snapshots"].append(
                    {
                        "id": str(uuid.uuid4()),
                        "business_id": record["business_id"],
                        "account_id": account_id,
                        "kind": "statement_opening",
                        "provenance": "bank_observed",
                        "source_type": "csv_import",
                        "amount_minor": balance["opening_minor"],
                        "as_of": f"{balance['period_start']}T00:00:00+00:00",
                    }
                )
        record.update(
            {
                "status": "confirmed",
                "confirmed_at": now(),
                "confirmed_revision": body["p_expected_revision"],
                "account_id": account_id,
                "summary": {**(record.get("summary") or {}), "result": counts},
            }
        )
        return {
            "created_count": counts["created"],
            "linked_count": counts["linked"],
            "replaced_count": counts["replaced"],
            "excluded_count": counts["excluded"],
            "account_id": account_id,
            "already_confirmed": False,
        }

    # ---- http dispatch ----
    def handler(self, request: httpx.Request) -> httpx.Response:
        path, params = request.url.path, dict(request.url.params)
        if path == "/auth/v1/user":
            token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
            users = {TOKEN_A: USER_A, TOKEN_B: USER_B}
            if token in users:
                return httpx.Response(
                    200,
                    json={
                        "id": users[token],
                        "aud": "authenticated",
                        "email": f"{token}@example.org",
                    },
                )
            return httpx.Response(401, json={})
        if path.startswith("/rest/v1/rpc/"):
            return self.rpc(path.removeprefix("/rest/v1/rpc/"), json.loads(request.content))
        if path.startswith("/rest/v1/"):
            return self.rest(request, path.removeprefix("/rest/v1/"), params)
        if path.startswith("/storage/v1/"):
            return self.storage(request, path.removeprefix("/storage/v1/"))
        return httpx.Response(404, json={"message": f"unhandled {path}"})

    def rest(self, request: httpx.Request, table: str, params: dict[str, str]) -> httpx.Response:
        assert table in self.tables, table
        queued = self.failures.get((request.method, table))
        if queued:
            return httpx.Response(queued.pop(0), json={"message": "Gateway Timeout"})
        prefer = request.headers.get("prefer", "")
        if request.method == "GET":
            rows = self._select(table, params)
            if "count=exact" in prefer:
                return httpx.Response(200, json=[], headers={"content-range": f"0-0/{len(rows)}"})
            select = params.get("select")
            if select and select != "*":
                wanted = [c.strip() for c in select.split(",")]
                rows = [
                    {k: r.get(k) for k in wanted} for r in rows
                ]  # the Data API returns only selected columns
            return httpx.Response(200, json=rows)
        if request.method == "POST":
            body = json.loads(request.content)
            items = body if isinstance(body, list) else [body]
            inserted = [self._defaults(table, item) for item in items]
            self.tables[table].extend(inserted)
            return httpx.Response(201, json=inserted if "return=representation" in prefer else [])
        if request.method == "PATCH":
            body = json.loads(request.content)
            rows = self._select(table, params)
            for row in rows:
                row.update(body)
            return httpx.Response(200, json=rows if "return=representation" in prefer else [])
        if request.method == "DELETE":
            rows = self._select(table, params)
            self.tables[table] = [r for r in self.tables[table] if r not in rows]
            return httpx.Response(200, json=[])
        return httpx.Response(405, json={})

    def storage(self, request: httpx.Request, rest: str) -> httpx.Response:
        m = re.fullmatch(r"object/upload/sign/statements/(.+)", rest)
        if m and request.method == "POST":
            token = f"tok-{uuid.uuid4()}"
            self.tokens[m.group(1)] = token
            return httpx.Response(
                200,
                json={
                    "url": f"/object/upload/sign/statements/{m.group(1)}?token={token}",
                    "token": token,
                },
            )
        m = re.fullmatch(r"object/info/statements/(.+)", rest)
        if m:
            data = self.objects.get(m.group(1))
            return (
                httpx.Response(404, json={"message": "not found"})
                if data is None
                else httpx.Response(200, json={"name": m.group(1), "size": len(data)})
            )
        m = re.fullmatch(r"object/statements/(.+)", rest)
        if m and request.method == "GET":
            data = self.objects.get(m.group(1))
            return (
                httpx.Response(404, json={}) if data is None else httpx.Response(200, content=data)
            )
        if rest == "object/statements" and request.method == "DELETE":
            queued = self.failures.get(("STORAGE", "delete"))
            if queued:
                return httpx.Response(queued.pop(0), json={"message": "Service Unavailable"})
            for p in json.loads(request.content)["prefixes"]:
                self.objects.pop(p, None)
            return httpx.Response(200, json=[])
        return httpx.Response(404, json={"message": f"unhandled storage {rest}"})


class _RefusedError(Exception):
    pass


@pytest.fixture
def backend(monkeypatch: pytest.MonkeyPatch) -> FakeBackend:
    import app.supabase as module

    monkeypatch.setattr(module, "RETRY_DELAYS", (0.0, 0.0))
    fake = FakeBackend()
    app.dependency_overrides[get_supabase] = lambda: SupabaseClient(
        "https://fake.supabase.local", "sb_secret_test", transport=httpx.MockTransport(fake.handler)
    )
    yield fake
    app.dependency_overrides.clear()


def base(ws: str = WS_A) -> str:
    return f"/api/workspaces/{ws}/imports"


def start_import(
    client: TestClient,
    backend: FakeBackend,
    data: bytes,
    account_id: str | None = ACC_A,
    new_account: dict | None = None,
    label: str = "may.csv",
) -> dict[str, Any]:
    body: dict[str, Any] = {"file_label": label, "declared_bytes": len(data)}
    if new_account:
        body["new_account"] = new_account
    else:
        body["account_id"] = account_id
    created = client.post(base(), headers=AUTH_A, json=body)
    assert created.status_code == 201, created.text
    upload = created.json()["upload"]
    assert (
        upload["bucket"] == "statements"
        and upload["path"].startswith(f"{BIZ_A}/")
        and upload["token"]
    )
    backend.upload(upload["path"], data)
    staged = client.post(f"{base()}/{created.json()['import']['id']}/uploaded", headers=AUTH_A)
    assert staged.status_code == 200, staged.text
    return staged.json()


def test_only_the_signed_in_owner_can_start_an_import(backend: FakeBackend) -> None:
    body = {"account_id": ACC_A, "file_label": "x.csv", "declared_bytes": 10}
    with TestClient(app) as client:
        assert client.post(base(), headers=CLIENT, json=body).status_code == 401
        client.cookies.set("cl_visitor", VISITOR)
        assert (
            client.post(base(), headers=CLIENT, json=body).status_code == 404
        )  # a visitor never owns this
        assert (
            client.post(base(), headers=AUTH_B, json=body).status_code == 404
        )  # B does not own WS_A
        assert (
            client.post(
                base(), headers={"authorization": f"Bearer {TOKEN_A}"}, json=body
            ).status_code
            == 403
        )
        assert (
            client.post(
                base(), headers=AUTH_A, json={"file_label": "x", "declared_bytes": 1}
            ).status_code
            == 422
        )
        assert (
            client.post(base(), headers=AUTH_A, json={**body, "account_id": ACC_B}).status_code
            == 404
        )
        assert (
            client.post(
                base(), headers=AUTH_A, json={**body, "declared_bytes": 99_000_000}
            ).status_code
            == 422
        )


def test_upload_parse_stage_review_and_confirm(backend: FakeBackend) -> None:
    with TestClient(app) as client:
        staged = start_import(client, backend, CSV_MAY)
        assert staged["status"] == "staged" and staged["draft_revision"] == 1
        summary = staged["summary"]
        assert summary["row_count"] == 3 and summary["blocking_rows"] == 0
        assert summary["credits_minor"] == 370676 and summary["debits_minor"] == 612595
        assert (
            summary["balance"]["opening_minor"] == 5532800
            and summary["balance"]["closing_minor"] == 5290881
        )
        assert summary["balance"]["reconciles"] is True and summary["existing_in_period"] == 0
        iid = staged["id"]
        assert (
            client.post(
                base(),
                headers=AUTH_A,
                json={"account_id": ACC_A, "file_label": "again", "declared_bytes": 5},
            ).status_code
            == 409
        )
        detail = client.get(f"{base()}/{iid}", headers=AUTH_A).json()
        assert detail["columns"] == [
            "date",
            "description",
            "amount",
            "balance",
            "currency",
            "transaction_id",
        ]
        assert (
            detail["mapping"]["columns"]["reference"] == "transaction_id"
            and len(detail["sample"]) == 3
        )
        rows = client.get(f"{base()}/{iid}/rows?limit=2", headers=AUTH_A).json()
        assert (
            [r["row_index"] for r in rows["items"]] == [0, 1]
            and rows["next_after"] == 1
            and rows["total"] == 3
        )
        more = client.get(f"{base()}/{iid}/rows?limit=2&after=1", headers=AUTH_A).json()
        assert [r["row_index"] for r in more["items"]] == [2] and more["next_after"] is None
        assert (
            rows["items"][0]["effective"]["amount_minor"] == -350000
            and rows["items"][0]["blocking"] is False
        )
        stale = client.post(f"{base()}/{iid}/confirm", headers=AUTH_A, json={"revision": 7})
        assert stale.status_code == 409
        confirmed = client.post(f"{base()}/{iid}/confirm", headers=AUTH_A, json={"revision": 1})
        assert confirmed.status_code == 200, confirmed.text
        outcome = confirmed.json()
        assert outcome["created"] == 3 and outcome["already_confirmed"] is False
        assert (
            outcome["import"]["status"] == "confirmed"
            and outcome["import"]["confirmed_revision"] == 1
        )
        assert backend.objects == {}  # the original file is removed once the rows are canonical
        created = [t for t in backend.tables["transactions"] if t["external_id"] == "r-may-2"]
        assert (
            created
            and created[0]["amount_minor"] == 370676
            and created[0]["source_type"] == "csv_import"
        )
        kinds = sorted((b["kind"], b["amount_minor"]) for b in backend.tables["balance_snapshots"])
        assert kinds == [("statement_closing", 5290881), ("statement_opening", 5532800)]
        again = client.post(f"{base()}/{iid}/confirm", headers=AUTH_A, json={"revision": 1}).json()
        assert again["already_confirmed"] is True and again["created"] == 3
        assert len(backend.tables["transactions"]) == 4  # repeat confirmation inserted nothing
        assert client.post(f"{base()}/{iid}/cancel", headers=AUTH_A).status_code == 409
        workspace = client.get("/api/private/workspace", headers=AUTH_A).json()
        assert (
            workspace["statements"]["count"] == 1
            and workspace["statements"]["uploads_available"] is True
        )
        listing = client.get(base(), headers=AUTH_A).json()
        assert [i["status"] for i in listing] == ["confirmed"]


def test_imported_rows_carry_evidence_and_stay_invisible_to_others(backend: FakeBackend) -> None:
    with TestClient(app) as client:
        staged = start_import(client, backend, CSV_MAY)
        client.post(f"{base()}/{staged['id']}/confirm", headers=AUTH_A, json={"revision": 1})
        tx = next(t for t in backend.tables["transactions"] if t["external_id"] == "r-may-1")
        detail = client.get(f"/api/workspaces/{WS_A}/transactions/{tx['id']}", headers=AUTH_A)
        assert detail.status_code == 200
        source = detail.json()["sources"][0]
        assert source["kind"] == "import_row" and source["file_label"] == "may.csv"
        assert source["row_index"] == 0 and source["source_locator"] == "line 2"
        assert source["extracted"]["amount_minor"] == -350000 and "raw" not in source
        assert (
            client.get(
                f"/api/workspaces/{WS_B}/transactions/{tx['id']}", headers=AUTH_B
            ).status_code
            == 404
        )
        for path in (f"{base()}/{staged['id']}", f"{base()}/{staged['id']}/rows"):
            assert client.get(path, headers=AUTH_B).status_code == 404
            assert client.get(path).status_code == 401
        assert client.post(f"{base()}/{staged['id']}/cancel", headers=AUTH_B).status_code == 404
        assert (
            client.post(
                f"{base()}/{staged['id']}/confirm", headers=AUTH_B, json={"revision": 1}
            ).status_code
            == 404
        )
        assert (
            client.patch(
                f"{base()}/{staged['id']}/rows/0", headers=AUTH_B, json={"description": "x"}
            ).status_code
            == 404
        )
        assert (
            client.get(f"{base(WS_B)}/{staged['id']}", headers=AUTH_B).status_code == 404
        )  # right user, wrong workspace


def test_unsupported_upload_fails_honestly_and_can_be_cancelled(backend: FakeBackend) -> None:
    with TestClient(app) as client:
        created = client.post(
            base(),
            headers=AUTH_A,
            json={"account_id": ACC_A, "file_label": "scan.pdf", "declared_bytes": 12},
        ).json()
        iid, path = created["import"]["id"], created["upload"]["path"]
        missing = client.post(f"{base()}/{iid}/uploaded", headers=AUTH_A)
        assert missing.status_code == 422  # nothing uploaded yet; lease released as failed
        backend.upload(path, b"%PDF-1.7 not a csv")
        failed = client.post(f"{base()}/{iid}/uploaded", headers=AUTH_A).json()
        assert failed["status"] == "failed" and failed["error_code"] == "pdf_malformed"
        assert path not in backend.objects  # rejected files are deleted immediately
        assert client.post(f"{base()}/{iid}/cancel", headers=AUTH_A).json()["status"] == "cancelled"
        # a failed or cancelled import no longer blocks a new one
        staged = start_import(client, backend, CSV_BAD_ROW, label="june.csv")
        assert (
            staged["summary"]["blocking_rows"] == 1
            and staged["summary"]["flag_counts"]["invalid_date"] == 1
        )


def test_blocking_rows_are_corrected_or_excluded_before_confirmation(backend: FakeBackend) -> None:
    with TestClient(app) as client:
        staged = start_import(client, backend, CSV_BAD_ROW, label="june.csv")
        iid = staged["id"]
        blocked = client.post(f"{base()}/{iid}/confirm", headers=AUTH_A, json={"revision": 1})
        assert blocked.status_code == 422 and blocked.json()["detail"]["rows"] == [0]
        bad = client.patch(
            f"{base()}/{iid}/rows/0", headers=AUTH_A, json={"posted_date": "06/01/2026"}
        )
        assert bad.status_code == 422
        fixed = client.patch(
            f"{base()}/{iid}/rows/0", headers=AUTH_A, json={"posted_date": "2026-06-01"}
        ).json()
        assert fixed["blocking"] is False and fixed["effective"]["posted_date"] == "2026-06-01"
        assert fixed["extracted"]["posted_date"] is None and "invalid_date" in fixed["flags"]
        assert client.get(f"{base()}/{iid}", headers=AUTH_A).json()["draft_revision"] == 2
        assert (
            client.post(f"{base()}/{iid}/confirm", headers=AUTH_A, json={"revision": 1}).status_code
            == 409
        )
        assert (
            client.patch(
                f"{base()}/{iid}/rows/0", headers=AUTH_A, json={"amount_minor": 0}
            ).status_code
            == 422
        )
        excluded = client.patch(
            f"{base()}/{iid}/rows/1", headers=AUTH_A, json={"decision": "exclude"}
        ).json()
        assert excluded["decision"] == "exclude"
        result = client.post(f"{base()}/{iid}/confirm", headers=AUTH_A, json={"revision": 3}).json()
        assert result["created"] == 1 and result["excluded"] == 1


def test_mapping_change_restages_rows_under_a_new_revision(backend: FakeBackend) -> None:
    data = b"Date,Details,Amount\n03/04/2026,A,-1.00\n05/06/2026,B,2.00\n"
    with TestClient(app) as client:
        staged = start_import(client, backend, data, label="ambiguous.csv")
        assert "ambiguous_date_format" in staged["summary"]["mapping_flags"]
        iid = staged["id"]
        bad = client.put(
            f"{base()}/{iid}/mapping",
            headers=AUTH_A,
            json={
                "mapping": {
                    "columns": {"date": "Nope", "description": "Details", "amount": "Amount"}
                }
            },
        )
        assert bad.status_code == 422
        good = client.put(
            f"{base()}/{iid}/mapping",
            headers=AUTH_A,
            json={
                "mapping": {
                    "columns": {"date": "Date", "description": "Details", "amount": "Amount"},
                    "date_format": "dmy",
                    "sign_convention": "signed_inflow_positive",
                }
            },
        )
        assert good.status_code == 200, good.text
        assert good.json()["draft_revision"] == 2 and good.json()["mapping"]["date_format"] == "dmy"
        rows = client.get(f"{base()}/{iid}/rows", headers=AUTH_A).json()["items"]
        assert [r["effective"]["posted_date"] for r in rows] == ["2026-04-03", "2026-06-05"]


def test_exact_file_replay_returns_the_existing_import(backend: FakeBackend) -> None:
    with TestClient(app) as client:
        first = start_import(client, backend, CSV_MAY)
        client.post(f"{base()}/{first['id']}/confirm", headers=AUTH_A, json={"revision": 1})
        second = start_import(client, backend, CSV_MAY, label="may-again.csv")
        assert second["status"] == "cancelled" and second["duplicate_of"] == first["id"]
        assert second["error_code"] == "duplicate_file" and backend.objects == {}
        assert len(backend.tables["transactions"]) == 4  # nothing double-counted


def test_overlap_and_reference_conflicts_are_reviewed_and_confirmation_is_atomic(
    backend: FakeBackend,
) -> None:
    with TestClient(app) as client:
        staged = start_import(client, backend, CSV_REF_CONFLICT, label="overlap.csv")
        iid = staged["id"]
        assert (
            staged["summary"]["flag_counts"]["already_imported"] == 1
            and staged["summary"]["existing_in_period"] == 1
        )
        rows = client.get(f"{base()}/{iid}/rows?only=flagged", headers=AUTH_A).json()
        assert rows["flagged_total"] == 1 and rows["items"][0]["decision"] == "link_existing"
        assert rows["items"][0]["linked_transaction_id"] == TX_EXISTING
        # keeping the row as new is refused where the owner can see which row is wrong,
        # because the account already holds a transaction with that reference
        kept = client.patch(f"{base()}/{iid}/rows/0", headers=AUTH_A, json={"decision": "keep"})
        assert kept.status_code == 422
        assert kept.json()["detail"]["rows"] == [0]
        assert kept.json()["detail"]["kind"] == "conflicting_reference"
        assert (
            client.get(f"{base()}/{iid}/rows", headers=AUTH_A).json()["items"][0]["decision"]
            == "link_existing"
        )  # the refused decision changed nothing
        # a foreign target is refused; the right one succeeds and the retry commits once
        foreign = client.patch(
            f"{base()}/{iid}/rows/0",
            headers=AUTH_A,
            json={"decision": "link_existing", "linked_transaction_id": str(uuid.uuid4())},
        )
        assert foreign.status_code == 404
        relinked = client.patch(
            f"{base()}/{iid}/rows/0",
            headers=AUTH_A,
            json={"decision": "link_existing", "linked_transaction_id": TX_EXISTING},
        )
        assert relinked.status_code == 200
        detail = client.get(f"{base()}/{iid}", headers=AUTH_A).json()
        result = client.post(
            f"{base()}/{iid}/confirm", headers=AUTH_A, json={"revision": detail["draft_revision"]}
        ).json()
        assert result["created"] == 1 and result["linked"] == 1
        sources = [
            s for s in backend.tables["transaction_sources"] if s["transaction_id"] == TX_EXISTING
        ]
        assert len(sources) == 1  # provenance preserved on the existing transaction


def test_shape_duplicates_without_references_are_kept_unless_the_owner_links(
    backend: FakeBackend,
) -> None:
    """Two identical-looking rows against one existing transaction: one candidate, one new."""
    with TestClient(app) as client:
        staged = start_import(client, backend, CSV_REPLAY, label="replay.csv")
        assert staged["summary"]["flag_counts"]["duplicate_candidate"] == 1
        assert staged["summary"]["flag_counts"]["repeated_in_file"] == 2
        rows = client.get(f"{base()}/{staged['id']}/rows", headers=AUTH_A).json()["items"]
        assert [r["needs_decision"] for r in rows] == [True, False]
        assert (
            rows[0]["conflict"]["transaction_id"] == TX_EXISTING and rows[0]["decision"] == "keep"
        )
        refused = client.post(
            f"{base()}/{staged['id']}/confirm", headers=AUTH_A, json={"revision": 1}
        )
        assert refused.status_code == 422 and refused.json()["detail"]["kind"] == "undecided"
        decided = client.post(
            f"{base()}/{staged['id']}/decisions", headers=AUTH_A, json={"decision": "keep"}
        ).json()
        assert decided["updated"] == 1 and decided["revision"] == 2
        result = client.post(
            f"{base()}/{staged['id']}/confirm", headers=AUTH_A, json={"revision": 2}
        ).json()
        assert result["created"] == 2  # the owner kept both; nothing was dropped on their behalf


def test_new_account_is_created_only_at_confirmation(backend: FakeBackend) -> None:
    with TestClient(app) as client:
        staged = start_import(
            client,
            backend,
            CSV_MAY,
            account_id=None,
            new_account={"name": "Reserve Savings", "account_type": "savings", "mask": "1234"},
        )
        assert staged["account"] is None and staged["proposed_account"]["name"] == "Reserve Savings"
        assert len(backend.tables["accounts"]) == 2
        link = client.patch(
            f"{base()}/{staged['id']}/rows/0",
            headers=AUTH_A,
            json={"decision": "link_existing", "linked_transaction_id": TX_EXISTING},
        )
        assert link.status_code == 422  # a new account has no history to link to
        result = client.post(
            f"{base()}/{staged['id']}/confirm", headers=AUTH_A, json={"revision": 1}
        ).json()
        assert result["created"] == 3 and len(backend.tables["accounts"]) == 3
        assert (
            result["import"]["account"]["name"] == "Reserve Savings"
            and result["import"]["account"]["mask"] == "1234"
        )


def test_cancel_removes_staging_rows_and_the_file(backend: FakeBackend) -> None:
    with TestClient(app) as client:
        staged = start_import(client, backend, CSV_MAY)
        assert len(backend.tables["import_rows"]) == 3 and len(backend.objects) == 1
        cancelled = client.post(f"{base()}/{staged['id']}/cancel", headers=AUTH_A).json()
        assert (
            cancelled["status"] == "cancelled"
            and backend.objects == {}
            and backend.tables["import_rows"] == []
        )
        assert (
            client.post(f"{base()}/{staged['id']}/cancel", headers=AUTH_A).json()["status"]
            == "cancelled"
        )
        assert client.get(f"{base()}/{staged['id']}/rows", headers=AUTH_A).json()["total"] == 0


def test_transient_failure_while_staging_keeps_the_upload_retryable(backend: FakeBackend) -> None:
    """A gateway timeout while staging must not lose the file, keep a lease or need a re-upload."""
    with TestClient(app) as client:
        created = client.post(
            base(),
            headers=AUTH_A,
            json={"account_id": ACC_A, "file_label": "may.csv", "declared_bytes": len(CSV_MAY)},
        ).json()
        iid, path = created["import"]["id"], created["upload"]["path"]
        backend.upload(path, CSV_MAY)
        backend.failures[("RPC", "stage_import")] = [504]  # the atomic staging call; never retried
        failed = client.post(f"{base()}/{iid}/uploaded", headers=AUTH_A)
        assert failed.status_code == 502
        body = failed.json()
        assert body["retryable"] is True and body["reference"]
        assert failed.headers["x-request-id"] == body["reference"]
        record = client.get(f"{base()}/{iid}", headers=AUTH_A).json()
        assert record["status"] == "awaiting_upload" and path in backend.objects
        job = backend.tables["import_jobs"][0]
        assert job["state"] == "failed" and job["attempts"] == 1  # the lease was released
        assert [r for r in backend.tables["import_rows"] if r["import_id"] == iid] == []
        # the same import is read again without a new upload, and the second attempt succeeds
        retried = client.post(f"{base()}/{iid}/uploaded", headers=AUTH_A)
        assert retried.status_code == 200 and retried.json()["status"] == "staged"
        assert backend.tables["import_jobs"][0]["attempts"] == 2
        assert len([r for r in backend.tables["import_rows"] if r["import_id"] == iid]) == 3
        # a new import cannot start while this one is in progress, and the client is told which one
        blocked = client.post(
            base(),
            headers=AUTH_A,
            json={"account_id": ACC_A, "file_label": "x.csv", "declared_bytes": 5},
        )
        assert blocked.status_code == 409 and blocked.json()["detail"]["active_import_id"] == iid


def test_read_gateway_errors_are_retried_transparently(backend: FakeBackend) -> None:
    backend.failures[("GET", "accounts")] = [504]
    with TestClient(app) as client:
        listing = client.get(base(), headers=AUTH_A)
    assert listing.status_code == 200


def test_a_stale_lease_holder_cannot_close_a_newer_lease(backend: FakeBackend) -> None:
    """Finishing is bound to the lease token, so an expired worker's late finish is a no-op."""
    client = SupabaseClient(
        "https://fake.supabase.local",
        "sb_secret_test",
        transport=httpx.MockTransport(backend.handler),
    )
    params = {"p_import_id": "i", "p_kind": "parse", "p_idempotency_key": "k", "p_lease_seconds": 1}
    first = client.rpc("acquire_import_job", params)[0]
    backend.tables["import_jobs"][0]["expired"] = True  # the first worker's lease ran out
    second = client.rpc("acquire_import_job", params)[0]
    assert second["attempts"] == 2 and second["lease_token"] != first["lease_token"]
    stale = client.rpc(
        "finish_import_job",
        {
            "p_job_id": first["job_id"],
            "p_lease_token": first["lease_token"],
            "p_state": "succeeded",
        },
    )
    assert stale is False and backend.tables["import_jobs"][0]["state"] == "leased"
    current = client.rpc(
        "finish_import_job",
        {"p_job_id": second["job_id"], "p_lease_token": second["lease_token"], "p_state": "failed"},
    )
    assert current is True and backend.tables["import_jobs"][0]["state"] == "failed"


FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "statements"
LEDGER_CSV_ROWS = [
    ("2026-02-02", "SHOP LEASE PAYMENT", -180000, "m-1"),
    ("2026-02-03", "CARD PROCESSOR SETTLEMENT", 214550, "m-2"),
    ("2026-02-10", "EQUIPMENT LOAN PAYMENT", -8500, "m-3"),
    ("2026-02-10", "EQUIPMENT LOAN PAYMENT", -8500, "m-4"),
]


def ledger_csv(rows: list[tuple[str, str, int, str]], with_refs: bool = True) -> bytes:
    header = "date,description,amount" + (",transaction_id" if with_refs else "") + "\n"
    body = "".join(
        f"{d},{desc},{amount / 100:.2f}" + (f",{ref}" if with_refs else "") + "\n"
        for d, desc, amount, ref in rows
    )
    return (header + body).encode()


def stage_file(
    client: TestClient,
    backend: FakeBackend,
    data: bytes,
    label: str,
    account_id: str | None = ACC_A,
) -> dict[str, Any]:
    return start_import(
        client,
        backend,
        data,
        account_id=account_id,
        new_account=None
        if account_id
        else {"name": "Operating Checking", "account_type": "checking", "mask": "4410"},
        label=label,
    )


def confirm_now(client: TestClient, iid: str) -> httpx.Response:
    detail = client.get(f"{base()}/{iid}", headers=AUTH_A).json()
    return client.post(
        f"{base()}/{iid}/confirm", headers=AUTH_A, json={"revision": detail["draft_revision"]}
    )


def test_pdf_statement_goes_through_the_same_flow_with_page_provenance(
    backend: FakeBackend,
) -> None:
    data = (FIXTURES / "layout-a-running-balance.pdf").read_bytes()
    with TestClient(app) as client:
        staged = stage_file(client, backend, data, "statement.pdf", account_id=None)
        assert staged["status"] == "staged" and staged["format"] == "pdf"
        s = staged["summary"]
        assert s["row_count"] == 24 and s["blocking_rows"] == 0
        assert (
            s["reconciliation"]["status"] == "matched" and s["statement"]["account_last4"] == "4410"
        )
        rows = client.get(f"{base()}/{staged['id']}/rows?limit=200", headers=AUTH_A).json()["items"]
        assert rows[0]["page_number"] == 1 and rows[0]["source_locator"] == "page 1 line 9"
        assert any(r["page_number"] == 2 for r in rows)
        loans = [r for r in rows if r["effective"]["description"] == "EQUIPMENT LOAN PAYMENT"]
        assert len(loans) == 3 and not any(r["needs_decision"] for r in loans)
        result = confirm_now(client, staged["id"]).json()
        assert result["created"] == 24 and result["import"]["account"]["mask"] == "4410"
        account = next(
            a for a in backend.tables["accounts"] if a["id"] == result["import"]["account"]["id"]
        )
        assert account["source_type"] == "pdf_import"
        kinds = sorted((b["kind"], b["amount_minor"]) for b in backend.tables["balance_snapshots"])
        expected = json.loads((FIXTURES / "expected.json").read_text())[
            "layout-a-running-balance.pdf"
        ]
        assert kinds == [
            ("statement_closing", expected["closing_minor"]),
            ("statement_opening", expected["opening_minor"]),
        ]
        tx = next(
            t for t in backend.tables["transactions"] if t["description"] == "SHOP LEASE PAYMENT"
        )
        detail = client.get(
            f"/api/workspaces/{WS_A}/transactions/{tx['id']}", headers=AUTH_A
        ).json()
        source = detail["sources"][0]
        assert (
            source["kind"] == "import_row"
            and source["format"] == "pdf"
            and source["page_number"] == 1
        )
        assert backend.objects == {}


def test_unsupported_pdfs_fail_honestly_through_the_route(backend: FakeBackend) -> None:
    with TestClient(app) as client:
        for name, code in (
            ("encrypted.pdf", "pdf_encrypted"),
            ("image-only.pdf", "pdf_no_text_layer"),
        ):
            created = client.post(
                base(),
                headers=AUTH_A,
                json={"account_id": ACC_A, "file_label": name, "declared_bytes": 10},
            ).json()
            backend.upload(created["upload"]["path"], (FIXTURES / name).read_bytes())
            failed = client.post(
                f"{base()}/{created['import']['id']}/uploaded", headers=AUTH_A
            ).json()
            assert failed["status"] == "failed" and failed["error_code"] == code
            assert created["upload"]["path"] not in backend.objects
            client.post(f"{base()}/{created['import']['id']}/cancel", headers=AUTH_A)


def test_csv_then_pdf_of_the_same_statement_needs_a_decision_and_never_double_counts(
    backend: FakeBackend,
) -> None:
    from app.synthetic.statements import LEDGER

    csv_rows = [(r.date, r.description, r.amount_minor, f"ref-{i}") for i, r in enumerate(LEDGER)]
    pdf = (FIXTURES / "layout-a-running-balance.pdf").read_bytes()
    with TestClient(app) as client:
        first = stage_file(client, backend, ledger_csv(csv_rows), "ledger.csv", account_id=None)
        confirmed = confirm_now(client, first["id"]).json()
        account_id = confirmed["import"]["account"]["id"]
        assert confirmed["created"] == 24
        second = stage_file(client, backend, pdf, "statement.pdf", account_id=account_id)
        s = second["summary"]
        assert (
            s["flag_counts"]["duplicate_candidate"] == 24
            and s["overlap"]["likely_duplicate_statement"] is True
        )
        assert s["overlap"]["undecided_rows"] == 24 and s["blocking_rows"] == 24
        refused = confirm_now(client, second["id"])
        assert refused.status_code == 422 and refused.json()["detail"]["kind"] == "undecided"
        before = len(backend.tables["transactions"])
        decided = client.post(
            f"{base()}/{second['id']}/decisions", headers=AUTH_A, json={"decision": "link_existing"}
        ).json()
        assert decided["updated"] == 24 and decided["skipped_without_target"] == 0
        result = confirm_now(client, second["id"]).json()
        assert result["created"] == 0 and result["linked"] == 24
        assert len(backend.tables["transactions"]) == before
        linked_sources = [s for s in backend.tables["transaction_sources"]]
        assert len(linked_sources) == 48  # every existing transaction now carries the PDF row too


def test_pdf_then_csv_lends_references_so_a_later_replay_matches_by_identity(
    backend: FakeBackend,
) -> None:
    from app.synthetic.statements import LEDGER

    csv_rows = [(r.date, r.description, r.amount_minor, f"ref-{i}") for i, r in enumerate(LEDGER)]
    pdf = (FIXTURES / "layout-a-running-balance.pdf").read_bytes()
    with TestClient(app) as client:
        first = stage_file(client, backend, pdf, "statement.pdf", account_id=None)
        account_id = confirm_now(client, first["id"]).json()["import"]["account"]["id"]
        second = stage_file(
            client, backend, ledger_csv(csv_rows), "ledger.csv", account_id=account_id
        )
        assert second["summary"]["flag_counts"]["duplicate_candidate"] == 24
        client.post(
            f"{base()}/{second['id']}/decisions", headers=AUTH_A, json={"decision": "link_existing"}
        )
        result = confirm_now(client, second["id"]).json()
        assert result["created"] == 0 and result["linked"] == 24
        lent = [
            t
            for t in backend.tables["transactions"]
            if t["account_id"] == account_id and t.get("external_id")
        ]
        assert len(lent) == 24
        third = stage_file(
            client, backend, ledger_csv(csv_rows), "ledger-again.csv", account_id=account_id
        )
        assert (
            third["status"] == "cancelled" and third["duplicate_of"] == second["id"]
        )  # exact bytes
        altered = stage_file(
            client, backend, ledger_csv(csv_rows) + b"\n", "ledger-alt.csv", account_id=account_id
        )
        assert altered["summary"]["flag_counts"].get("already_imported") == 24
        assert altered["summary"]["blocking_rows"] == 0  # identity matches need no decision
        result = confirm_now(client, altered["id"]).json()
        assert result["created"] == 0 and result["linked"] == 24


def test_legitimate_repeated_payments_survive_in_both_directions(backend: FakeBackend) -> None:
    with TestClient(app) as client:
        first = stage_file(
            client, backend, ledger_csv(LEDGER_CSV_ROWS, with_refs=False), "a.csv", account_id=None
        )
        account_id = confirm_now(client, first["id"]).json()["import"]["account"]["id"]
        three = LEDGER_CSV_ROWS + [("2026-02-10", "EQUIPMENT LOAN PAYMENT", -8500, "m-5")]
        second = stage_file(
            client, backend, ledger_csv(three, with_refs=False), "b.csv", account_id=account_id
        )
        rows = client.get(f"{base()}/{second['id']}/rows?limit=200", headers=AUTH_A).json()["items"]
        loans = [r for r in rows if r["effective"]["description"] == "EQUIPMENT LOAN PAYMENT"]
        assert [r["needs_decision"] for r in loans] == [
            True,
            True,
            False,
        ]  # two candidates, one new
        client.post(f"{base()}/{second['id']}/decisions", headers=AUTH_A, json={"decision": "keep"})
        result = confirm_now(client, second["id"]).json()
        assert result["created"] == 5 and result["linked"] == 0  # the owner kept everything
        loans_now = [
            t
            for t in backend.tables["transactions"]
            if t["description"] == "EQUIPMENT LOAN PAYMENT"
        ]
        assert len(loans_now) == 5


def test_stale_worker_cannot_stage_or_fail_after_a_takeover(backend: FakeBackend) -> None:
    client = SupabaseClient(
        "https://fake.supabase.local",
        "sb_secret_test",
        transport=httpx.MockTransport(backend.handler),
    )
    with TestClient(app) as api:
        created = api.post(
            base(),
            headers=AUTH_A,
            json={"account_id": ACC_A, "file_label": "x.csv", "declared_bytes": 5},
        ).json()
    iid = created["import"]["id"]
    params = {
        "p_import_id": iid,
        "p_kind": "parse",
        "p_idempotency_key": f"parse:{iid}:1",
        "p_lease_seconds": 1,
    }
    first = client.rpc("acquire_import_job", params)[0]
    backend.tables["import_jobs"][0]["expired"] = True
    second = client.rpc("acquire_import_job", params)[0]
    fields = {
        "format": "csv",
        "byte_size": 5,
        "content_hash": "0" * 64,
        "parser_version": "csv-1",
        "mapping": {},
        "summary": {},
        "draft_revision": 1,
    }
    with pytest.raises(SupabaseError, match="lease_lost"):
        client.rpc(
            "stage_import",
            {
                "p_import_id": iid,
                "p_job_id": first["job_id"],
                "p_lease_token": first["lease_token"],
                "p_rows": [
                    {"row_index": 0, "source_locator": "line 2", "raw": {}, "extracted": {}}
                ],
                "p_fields": fields,
            },
        )
    with pytest.raises(SupabaseError, match="lease_lost"):
        client.rpc(
            "fail_import",
            {
                "p_import_id": iid,
                "p_job_id": first["job_id"],
                "p_lease_token": first["lease_token"],
                "p_error_code": "x",
                "p_error_message": "x",
            },
        )
    assert [r for r in backend.tables["import_rows"] if r["import_id"] == iid] == []
    record = next(i for i in backend.tables["document_imports"] if i["id"] == iid)
    assert record["status"] == "awaiting_upload"
    written = client.rpc(
        "stage_import",
        {
            "p_import_id": iid,
            "p_job_id": second["job_id"],
            "p_lease_token": second["lease_token"],
            "p_rows": [{"row_index": 0, "source_locator": "line 2", "raw": {}, "extracted": {}}],
            "p_fields": fields,
        },
    )
    assert written == 1 and record["status"] == "staged"


def test_processing_deadline_fails_the_step_retryably(
    backend: FakeBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.imports.budget as budget_module

    monkeypatch.setattr(budget_module, "PROCESSING_BUDGET_SECONDS", 0.0)
    with TestClient(app) as client:
        created = client.post(
            base(),
            headers=AUTH_A,
            json={"account_id": ACC_A, "file_label": "s.pdf", "declared_bytes": 10},
        ).json()
        backend.upload(
            created["upload"]["path"], (FIXTURES / "layout-a-running-balance.pdf").read_bytes()
        )
        response = client.post(f"{base()}/{created['import']['id']}/uploaded", headers=AUTH_A)
        assert response.status_code == 503 and response.json()["detail"]["retryable"] is True
        record = client.get(f"{base()}/{created['import']['id']}", headers=AUTH_A).json()
        assert (
            record["status"] == "awaiting_upload"
            and backend.tables["import_jobs"][0]["state"] == "failed"
        )


def test_the_database_still_refuses_a_reference_the_review_did_not_flag(
    backend: FakeBackend,
) -> None:
    """The API's refusal is a courtesy; atomicity belongs to confirm_import.

    A reference can be claimed by another confirmation after this file was staged, so the
    flag is missing and the decision looks legitimate. Nothing may be written.
    """
    with TestClient(app) as client:
        staged = start_import(client, backend, CSV_REF_CONFLICT, label="overlap.csv")
        iid = staged["id"]
        row = next(r for r in backend.tables["import_rows"] if r["import_id"] == iid)
        row.update({"flags": [], "decision": "keep", "linked_transaction_id": None})
        before = copy.deepcopy(
            (
                backend.tables["transactions"],
                backend.tables["transaction_sources"],
                backend.tables["balance_snapshots"],
            )
        )
        refused = confirm_now(client, iid)
        assert refused.status_code == 409 and refused.json()["detail"]["rows"] == [0]
        assert (
            backend.tables["transactions"],
            backend.tables["transaction_sources"],
            backend.tables["balance_snapshots"],
        ) == before  # the file's other row was not inserted either
        assert client.get(f"{base()}/{iid}", headers=AUTH_A).json()["status"] == "staged"


def test_keeping_every_row_is_refused_when_one_of_them_conflicts(backend: FakeBackend) -> None:
    """Bulk decisions obey the same rule as a single row, and change nothing when refused."""
    with TestClient(app) as client:
        staged = start_import(client, backend, CSV_REF_CONFLICT, label="overlap.csv")
        iid = staged["id"]
        row = next(r for r in backend.tables["import_rows"] if r["import_id"] == iid)
        row["flags"] = ["conflicting_revision", "needs_decision"]
        before = client.get(f"{base()}/{iid}", headers=AUTH_A).json()["draft_revision"]
        refused = client.post(
            f"{base()}/{iid}/decisions", headers=AUTH_A, json={"decision": "keep"}
        )
        assert refused.status_code == 422 and refused.json()["detail"]["rows"] == [0]
        assert row["decision"] != "keep" or row["decision_by"] != "owner"
        assert client.get(f"{base()}/{iid}", headers=AUTH_A).json()["draft_revision"] == before
        # excluding the same row is still allowed, and so is linking it
        allowed = client.post(
            f"{base()}/{iid}/decisions", headers=AUTH_A, json={"decision": "exclude"}
        )
        assert allowed.status_code == 200 and allowed.json()["updated"] == 1
        assert row["decision"] == "exclude" and row["decision_by"] == "owner"


def test_a_linked_reference_is_not_lent_to_a_transaction_of_another_import_format(
    backend: FakeBackend,
) -> None:
    """One reference per account, whatever format it arrived in, so a replay stays unambiguous."""
    rows = [("2026-05-01", "LEASE PAYMENT", -350000, "ref-1")]
    with TestClient(app) as client:
        first = stage_file(client, backend, ledger_csv(rows), "csv-one.csv")
        assert confirm_now(client, first["id"]).json()["created"] == 1
        existing = [t for t in backend.tables["transactions"] if t["external_id"] == "ref-1"]
        assert len(existing) == 1 and existing[0]["source_type"] == "csv_import"
        # the same reference arrives again in a second, different file and is linked by the owner
        second = stage_file(
            client,
            backend,
            ledger_csv(rows + [("2026-05-04", "CARD SETTLEMENT", 120000, "ref-2")]),
            "csv-two.csv",
        )
        detail = client.get(f"{base()}/{second['id']}", headers=AUTH_A).json()
        assert detail["status"] == "staged"
        refused = client.patch(
            f"{base()}/{second['id']}/rows/0", headers=AUTH_A, json={"decision": "keep"}
        )
        assert refused.status_code == 422  # a second transaction would share the reference
        linked = confirm_now(client, second["id"]).json()
        assert linked["created"] == 1 and linked["linked"] == 1  # only the new reference is added
        holders = [t for t in backend.tables["transactions"] if t["external_id"] == "ref-1"]
        assert len(holders) == 1  # still exactly one transaction owns the reference


def test_cancelling_invalidates_the_lease_so_a_running_parse_cannot_stage_rows(
    backend: FakeBackend,
) -> None:
    with TestClient(app) as client:
        created = client.post(
            base(),
            headers=AUTH_A,
            json={"account_id": ACC_A, "file_label": "may.csv", "declared_bytes": len(CSV_MAY)},
        ).json()
        iid, path = created["import"]["id"], created["upload"]["path"]
        backend.upload(path, CSV_MAY)
        # a parse takes the lease and is still reading the file
        lease = backend.rpc(
            "acquire_import_job",
            {
                "p_import_id": iid,
                "p_kind": "parse",
                "p_idempotency_key": f"parse:{iid}:1",
                "p_lease_seconds": 180,
            },
        ).json()[0]
        assert lease["lease_token"]
        cancelled = client.post(f"{base()}/{iid}/cancel", headers=AUTH_A)
        assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
        # the worker comes back and tries to write what it read
        staged = backend.rpc(
            "stage_import",
            {
                "p_import_id": iid,
                "p_job_id": lease["job_id"],
                "p_lease_token": lease["lease_token"],
                "p_rows": [{"row_index": 0, "raw": {}, "extracted": {}}],
                "p_fields": {},
            },
        )
        assert staged.status_code == 400 and "lease_lost" in staged.json()["message"]
        assert backend.tables["import_rows"] == [] and backend.objects == {}


def test_cancelling_twice_is_safe_and_retries_a_file_removal_that_failed(
    backend: FakeBackend,
) -> None:
    with TestClient(app) as client:
        staged = start_import(client, backend, CSV_MAY)
        iid = staged["id"]
        backend.failures[("STORAGE", "delete")] = [503, 503]  # the retry fails too
        first = client.post(f"{base()}/{iid}/cancel", headers=AUTH_A).json()
        assert first["status"] == "cancelled"
        assert backend.tables["import_rows"] == []  # staging is gone whatever Storage did
        assert len(backend.objects) == 1  # the file survived the failed removal
        record = next(i for i in backend.tables["document_imports"] if i["id"] == iid)
        assert record["object_deleted_at"] is None  # and that is recorded, not assumed
        second = client.post(f"{base()}/{iid}/cancel", headers=AUTH_A).json()
        assert second["status"] == "cancelled" and backend.objects == {}
        assert record["object_deleted_at"] is not None


def test_a_confirmation_and_a_cancellation_cannot_both_succeed(backend: FakeBackend) -> None:
    with TestClient(app) as client:
        staged = start_import(client, backend, CSV_MAY)
        iid = staged["id"]
        assert confirm_now(client, iid).status_code == 200
        refused = client.post(f"{base()}/{iid}/cancel", headers=AUTH_A)
        assert refused.status_code == 409 and "cannot be cancelled" in refused.json()["detail"]
        assert client.get(f"{base()}/{iid}", headers=AUTH_A).json()["status"] == "confirmed"
        assert len([t for t in backend.tables["transactions"] if t["account_id"] == ACC_A]) >= 3


def test_a_slow_extraction_keeps_the_upload_and_stays_retryable(backend: FakeBackend) -> None:
    """A resource refusal must not delete the owner's file: it is the run that failed."""
    with TestClient(app) as client:
        created = client.post(
            base(),
            headers=AUTH_A,
            json={"account_id": ACC_A, "file_label": "may.csv", "declared_bytes": len(CSV_MAY)},
        ).json()
        iid, path = created["import"]["id"], created["upload"]["path"]
        pdf = (
            pathlib.Path(__file__).resolve().parents[3]
            / "fixtures"
            / "statements"
            / "layout-a-running-balance.pdf"
        ).read_bytes()
        backend.upload(path, pdf)
        original = dict(backend.objects)

        def slow(_data: bytes, _timeout: float | None = None):
            raise csvparse.UnsupportedFileError(
                "extraction_timeout", "Reading this PDF took longer than the importer allows."
            )

        with mock.patch.object(extract, "extract_pdf", slow):
            answer = client.post(f"{base()}/{iid}/uploaded", headers=AUTH_A)
        assert answer.status_code == 503 and answer.json()["detail"]["retryable"] is True
        assert backend.objects == original  # the upload is still there
        record = next(i for i in backend.tables["document_imports"] if i["id"] == iid)
        assert record["status"] == "awaiting_upload"  # and the import can simply be read again


def test_an_exact_replay_does_not_refund_its_rate_limit_initiation(backend: FakeBackend) -> None:
    """Refunding would let a known duplicate be re-uploaded in a loop to farm the limit."""
    with TestClient(app) as client:
        first = start_import(client, backend, CSV_MAY)
        confirm_now(client, first["id"])
        starts_before = len(backend.tables.get("import_starts", []))
        created = client.post(
            base(),
            headers=AUTH_A,
            json={"account_id": ACC_A, "file_label": "again.csv", "declared_bytes": len(CSV_MAY)},
        ).json()
        backend.upload(created["upload"]["path"], CSV_MAY)
        replay = client.post(f"{base()}/{created['import']['id']}/uploaded", headers=AUTH_A).json()
        assert replay["status"] == "cancelled" and replay["duplicate_of"] == first["id"]
        assert len(backend.tables["import_starts"]) == starts_before + 1  # kept, not refunded
