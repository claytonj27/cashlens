# CashLens API

Python 3.12 / FastAPI service. All authoritative financial calculations and AI
orchestration will live here; the Next.js frontend only presents results.

Status: scaffold only. The single route reports process health. No database,
storage, model provider or financial logic is connected.

## Layout

- `app/main.py`: ASGI entrypoint exposing `app` (`app.main:app`), the `/api/health` route
- `app/config.py`: validated settings loaded from environment variables and `.env`
- `app/logging_setup.py`: single-line JSON logging to stdout
- `app/supabase.py`: Data API and Auth client (service role; callers check membership)
- `app/auth.py`: visitor cookies, access-token validation, CSRF client header
- `app/demo.py`, `app/private.py`: demo sessions and workspaces; signed-in identity, provisioning and the private workspace
- `app/workspaces.py`: scoped reads for one workspace (accounts with balances, keyset-paginated transactions, allowlisted evidence)
- `app/ops.py`: operator-only diagnostics (see below)
- `app/synthetic/`: deterministic fixture generator (`generate`), and the demo seed loader (`seed`) for the development database
- `tests/`: pytest suite for configuration, the health route and the diagnostics gate (FastAPI test client over `httpx2`)
- `ruff.toml`, `pytest.ini`: lint and test configuration (no `pyproject.toml`, see deployment notes)
- `requirements.in` / `requirements.txt`: runtime dependencies and their lock
- `requirements-dev.in` / `requirements-dev.txt`: development dependencies and their lock
- `.python-version`: `3.12`, read by pyenv, uv and Vercel

## Statement imports and source adapters

- `app/imports/csvparse.py`: pure, versioned CSV parsing (type and encoding checks, delimiter, bounds, mapping proposal with ambiguity flags, money and date parsing, running-balance checks, summary).
- `app/imports/pdfparse.py`: deterministic text-PDF extraction with pypdf (positioned text, header-defined columns, wrapped lines, statement metadata for independent reconciliation, page and line provenance); refuses encrypted, scanned, damaged, oversized and non-statement PDFs with explained codes. No OCR, no model calls.
- `app/imports/matching.py`: identity versus shape evidence, one-to-one pairing that preserves repeated payments, decisions the owner must make before confirmation, and a statement-level overlap assessment; never merges on its own (ADR 0009).
- `app/imports/budget.py`: the per-step processing deadline (100 s) that fits inside the 180 s lease and the 300 s function limit.
- `app/synthetic/pdfwriter.py` and `app/synthetic/statements.py`: dependency-free synthetic PDF fixtures under `fixtures/statements/` (`python -m app.synthetic.statements --check` in CI).
- `app/imports/routes.py`: `/api/workspaces/{id}/imports` for signed-in owners of a private business: create (one-path signed upload token), uploaded (validate, detect the format from bytes, parse, stage under the lease through `stage_import`), detail, mapping, rows, row review, decisions (bulk owner decisions), confirm (atomic `confirm_import`, refused while rows need a decision), cancel (atomic `cancel_import`, which also invalidates any lease still open so a parse that returns afterwards cannot stage into it). A row whose source reference the account already holds cannot be kept as new, per row or in bulk; the database enforces one reference per account across both import formats. Leased jobs with tokens, deadlines, safe error codes, object deletion at confirmation, cancellation and failure, retried when Storage refused it.
- `app/ingestion/`: the canonical contract, the synthetic provider adapter and the incremental sync that stages raw events and applies them through `apply_sync_batch`.
- Checks on the development project: `db/checks/import_flow_check.py`, `db/checks/sync_adapter_check.py`, `db/checks/statement_pack_check.py` (see their docstrings; all need `API_BASE` for a running API, plus `API_BYPASS` for a protected preview).

## Private businesses and workspace selection

A signed-in owner may hold more than one private business. `POST
/api/private/provision` still creates or resumes the earliest one and is
idempotent. `POST /api/private/businesses` asks explicitly for an additional
business and takes the caller's idempotency key, so retries and concurrent
duplicates return the same business. `GET /api/me` lists every business with
its workspace id while keeping the earliest in the original fields. `GET
/api/private/workspace?workspace=<id>` resolves a named workspace through the
same resolver the scoped routes use and answers 404 rather than falling back.
`GET /api/private/imports/{id}` says which workspace an import belongs to, so
a link cannot move it. See `db/checks/workspace_selection_check.py` and ADR
0006. A link naming a workspace that is not usable is reported as unavailable by the
pages as well, never replaced by the account's default business.

## Configuration

`app/config.py` uses pydantic-settings. Environment variables take precedence
over `services/api/.env`; `.env.example` documents every setting and its
default. Unknown environment variables are ignored; invalid values (bad log
level, port out of range, unknown environment name) stop startup with a
validation error.

Product routes (demo sessions, accounts, provisioning, workspace reads) are
switched by `FEATURES_ENABLED`. Unset, they are on in development, test and
preview and off in production, where each of them answers 404 through a
router-level dependency before any Supabase call; only `/api/health` and the
separately gated diagnostics exist there. Production therefore stays a
health-only scaffold until an operator sets `FEATURES_ENABLED=true` on it
together with the credentials the features need. `FEATURES_ENABLED=false`
switches them off anywhere. With features on but Supabase unconfigured the
routes answer 503 so that misconfiguration stays visible.

## Commands

Run from `services/api/`. The virtual environment is project-local.

```bash
/opt/homebrew/bin/python3 -m venv .venv          # any Python 3.12 works
.venv/bin/pip install -r requirements-dev.txt    # locked install
.venv/bin/uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
.venv/bin/pytest                                 # tests
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```

Health check: `curl http://127.0.0.1:8000/api/health`

## Operator diagnostics

`app/ops.py` serves `/api/ops/*`: configuration presence, a database round
trip on the isolated `ops_probe` table, one bounded model call, and
streaming, cookie, header-echo and redirect probes. Every route answers 404
unless `OPS_DIAGNOSTICS_ENABLED=true`, and 401 unless the request carries the
`X-Ops-Token` header matching `OPS_DIAGNOSTICS_TOKEN`. Responses never contain
secrets or raw header values. Keep the routes disabled except while running a
deployment check, and never enable them on production without a recorded
decision. `/api/health` does not touch this module.

On Vercel, `APP_ENV` is set per environment in the project settings so the
health payload reports `preview` or `production`.

Re-lock after changing a `.in` file:

```bash
.venv/bin/pip-compile --strip-extras --output-file requirements.txt requirements.in
.venv/bin/pip-compile --strip-extras --output-file requirements-dev.txt requirements-dev.in
```

## Synthetic fixtures and seeding

```bash
.venv/bin/python -m app.synthetic.generate          # rewrite fixtures/provider and fixtures/evaluation
.venv/bin/python -m app.synthetic.generate --check  # fail if committed fixtures differ from a fresh build
.venv/bin/python -m app.synthetic.seed              # load the demo businesses (refuses if present)
.venv/bin/python -m app.synthetic.seed --replace    # delete and reload them
```

The seed loader reads `SUPABASE_DB_URL` from the environment or `.env` and
runs one transaction as the `postgres` operator under the demo-mutation flag;
the immutability trigger accepts that combination only, never the API's
service role. Reloading is deterministic (stable ids) and leaves private
rows untouched. Provider fixtures are the only thing the application ever
sees; `fixtures/evaluation` holds the answers and must never be loaded or
sent to a model.

## Deployment notes

Vercel detects the FastAPI app from `app/main.py` (the `app` object) and
installs the pinned `requirements.txt` with uv. There is deliberately no
`pyproject.toml`: when one exists Vercel treats it as the dependency source and
runs `uv lock`, which fails without a `[project]` table (observed in the M2
spike on 2026-09-12). Function duration and bundle size still need measuring
against real CSV and PDF processing before any claim of support.
