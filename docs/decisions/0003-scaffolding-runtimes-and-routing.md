# ADR 0003: Scaffolding runtimes, tooling and local routing

- Status: accepted
- Date: 2026-09-12
- Deciders: project author
- Related: [ADR 0001](0001-project-scope.md) (stack), [ADR 0002](0002-private-repository-and-deployment.md) (repository and deployment)

## Context

Milestone 1 turns the empty `apps/web` and `services/api` folders into
runnable services with reproducible dependencies, basic verification and
minimal CI, without financial features or external integrations. The choices
below were checked against the Next.js and Vercel documentation current on
the decision date and against what is already installed on the development
machine, so that local development, CI and the planned Vercel deployment use
the same versions.

## Decisions

Runtimes
- Node.js 24 (Active LTS; Vercel's default and 24.x is installed via nvm).
  Pinned in `.nvmrc` (`24`) and `apps/web/package.json` `engines.node`
  (`24.x`). Node 22 was rejected only because 24 is the current default on
  both sides; either would work with Next.js 16 (minimum 20.9).
- Python 3.12 (Vercel's default; 3.13 and 3.14 are also offered). Pinned in
  `services/api/.python-version`. The local virtual environment uses the
  installed 3.12.2 interpreter.

Frontend
- Next.js 16.3.5 with the App Router, React 19.2.8, TypeScript strict,
  ESLint via eslint-config-next, and Tailwind CSS 4 with the CashLens colors
  declared as theme tokens in `globals.css`. React Compiler is off.
- npm is the single package manager (already installed with Node, Vercel's
  default); `apps/web/package-lock.json` is committed and CI runs `npm ci`.
- System font stack instead of `next/font/google` so builds need no network
  access for fonts.
- No generated agent instruction files in the app directory.

Backend
- FastAPI 0.141.1, uvicorn 0.52.4, pydantic-settings 2.15.0 for validated
  configuration, and stdlib JSON logging. Tests use pytest 9.1.1 with the
  `httpx2` transport that Starlette 1.6 recommends. Ruff 0.16.7 lints and
  formats.
- Dependencies are declared in `requirements.in` / `requirements-dev.in`
  and locked with pip-tools into `requirements.txt` / `requirements-dev.txt`.
  `pyproject.toml` carries only tool configuration and the Vercel entrypoint,
  deliberately without a `[project]` dependency table, so hosting installs
  from the locked `requirements.txt` rather than an unpinned list. uv and
  Poetry were rejected for now because neither is installed and pip-tools is
  fully project-local; uv can replace it later as one contained change.
- No migration tool yet. It is selected when the first migration is written
  (M3), so nothing is configured speculatively.

Routing between the services
- The API serves everything under `/api` (`/api/health` today). The web app
  rewrites `/api/:path*` to `${API_ORIGIN}/api/:path*` in `next.config.ts`,
  so the browser only talks to its own origin, no CORS configuration exists,
  and no API URL appears in client code. This matches the planned two-project
  Vercel layout with the frontend proxying to the Python project.
- `API_ORIGIN` defaults to the local uvicorn address, is validated as a URL,
  and is required when `VERCEL=1` so a Vercel build cannot silently proxy to
  localhost. Rewrites are computed at build time.
- Next.js route handlers must not be created under `/api`; that prefix is
  reserved for the Python service.

Verification and CI
- GitHub Actions runs two jobs on every push and on pull requests to main:
  web (`npm ci`, lint, typecheck, build) and API (locked pip install, Ruff
  check and format check, pytest). Workflow permissions are read-only and no
  paid keys or external services are needed.
- Docker, the Supabase CLI, the Vercel CLI and any orchestration framework
  were not added; no M1 requirement needs them.

## Consequences

- The health route reports process status only. Adding database, storage or
  model checks later must be a separate, explicitly labeled endpoint so
  health never triggers spend or reveals configuration.
- Because rewrites are fixed at build time, every Vercel deployment of the
  web app needs a known API origin at build time. Preview deployments in
  particular need a decision on how they reach an API (a stable API domain,
  a per-branch API deployment, or a runtime proxy). This is an M2 question.
- The `httpx2` test dependency follows Starlette's current guidance; if the
  FastAPI test client changes its transport again, only the dev lock moves.

## Open questions for the deployment spike (M2)

Update 2026-09-12: questions 1 and 3 are answered in [ADR 0004](0004-deployment-topology-and-protection.md); the config rewrite was replaced by a request-time proxy, and `pyproject.toml` was removed.

1. Confirm Vercel installs the API from `requirements.txt` when
   `pyproject.toml` has no `[project]` table, and that `tool.vercel.entrypoint`
   resolves `app.main:app`.
2. Confirm the `/api/*` rewrite works across two Vercel projects, including
   streaming responses and cookie handling through the rewrite.
3. Decide how preview deployments obtain `API_ORIGIN`.
4. Measure function duration limits against the planned CSV and PDF
   processing before any claim of support.
5. Confirm Node 24 and Python 3.12 are what the Vercel projects actually use.
