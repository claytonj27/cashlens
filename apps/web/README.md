# CashLens web

Next.js 16 (App Router, React 19, TypeScript strict, Tailwind CSS 4) frontend.
It presents results and forwards API requests; all financial logic stays in
`services/api`.

Status: scaffold only. One page shows the product name, a short description
and live backend connectivity with loading, connected and unavailable states.

## Layout

- `src/app/layout.tsx`, `src/app/page.tsx`: root layout and landing page
- `src/components/backend-status.tsx`: client component that polls the health route
- `src/components/transaction-view.tsx`, `account-balances.tsx`, `transaction-detail.tsx`: the shared workspace data view (balances with kind and provenance, filterable keyset-paginated table, evidence panel) used by the demo and private workspace pages
- `src/lib/workspace-api.ts`: typed calls and formatting for the workspace routes
- `src/lib/health.ts`: typed health check with timeout and payload validation
- `src/app/globals.css`: Tailwind import and the CashLens color and shadow tokens
- `src/proxy.ts`: forwards `/api/*` to the API origin (see below)
- `.env.example`: documented local configuration

## Routing to the API

`src/proxy.ts` forwards `/api/:path*` to `${API_ORIGIN}/api/:path*` on the
server. The browser only talks to this origin, so no CORS setup is needed and
no API URL appears in client code. Do not add Next.js route handlers under
`/api`; that prefix belongs to the Python service.

Both settings are read at request time, not build time:

- `API_ORIGIN`: origin only, no `/api` suffix. Defaults to
  `http://127.0.0.1:8000` locally; required on Vercel, where a missing value
  makes `/api/*` answer 503 with a clear message instead of proxying to
  localhost.
- `API_PROTECTION_BYPASS`: the API project's Vercel deployment-protection
  bypass secret, sent as a request header by the proxy. Server-side only.
  Needed for protected preview deployments; the production API domain is
  public and ignores it.

Safeguards: the destination is always built from the configured origin plus
the matched path, incoming bypass headers from clients are dropped before the
server adds its own, and redirects from the API are returned to the client
rather than followed, so the secret is only ever sent to `API_ORIGIN`. Preview
deployments have no default origin: a branch gets one only when it is set
explicitly, otherwise `/api/*` answers 503 rather than reaching production.

## Accounts and sessions

Supabase Auth runs in the browser (`src/lib/supabase.ts`, publishable key,
implicit flow so email links work on any device). Pages: `/signup`, `/login`,
`/forgot-password`, `/auth/callback` (confirmation and recovery links land
here), `/auth/reset-password`, `/workspace` (private workspace, provisions
the business on first visit). `/demo` starts an anonymous visitor session
through the API and lists the synthetic businesses; `/demo/[slug]` shows a
minimal workspace shell. API calls go through `src/lib/api.ts`, which adds
the CSRF client header and the bearer token.

Required for these pages: `NEXT_PUBLIC_SUPABASE_URL` and
`NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` at build time, and the deployment's
exact `/auth/callback` URL in the Supabase project's redirect allowlist
(confirmation, resend and recovery links all use that one URL).

## Statement imports

`/workspace/import` starts an import: account choice (an existing imported account or a new one), the CSV or text-based PDF file and the upload notice (limits, no OCR, no model calls, deletion after confirmation). The page asks the API for a one-path signed upload token, uploads the file straight to the private `statements` bucket with `supabase-js` (`src/lib/imports-api.ts`), then asks the API to parse and stage it. `/workspace/imports/[id]` (`src/components/import-review.tsx`) shows what the statement itself says (printed period, balances, count), whether the rows reconcile with it, totals as read from the file, warnings, the column mapping with the file's first rows, the staged rows with page and line locators, flags, per-row edit and decision controls, bulk decisions for matched rows, and explicit confirm and cancel actions. Confirmation stays disabled while rows need a decision. The workspace page lists imports and resumes an unfinished review; evidence panels show the import row behind an imported transaction.

## Workspace selection

An owner with more than one private business reaches the others by link:
`/workspace?workspace=<id>` and `/workspace/import?workspace=<id>`. The id is
validated by the API's authorized resolver, an inaccessible or malformed one
shows an unavailable state rather than another business, and both pages name
the business so it is clear where transactions will go. The review page asks
the API which workspace an import belongs to, so a link never changes its
owner. Links without a selection behave exactly as before, and the header
keeps the selection inside the workspace pages. There is no business switcher.

## Feature availability

Product pages (demo exploration, accounts, private workspaces) render only
when the build carries `NEXT_PUBLIC_SUPABASE_URL` and
`NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` (`src/lib/features.ts`). Without them
the landing page, the header and the demo, account and workspace routes show
a health-only scaffold notice and make no API calls. The API applies the real
gate: its product routes answer 404 wherever `FEATURES_ENABLED` resolves to
off, which is production's default. Production carries neither the browser
values nor the API switch by design (ADR 0004 amendment).

## Commands

Run from `apps/web/` with Node 24 (`nvm use` reads the repository `.nvmrc`).

```bash
npm ci               # locked install
npm run dev          # http://localhost:3000
npm run lint         # ESLint (eslint-config-next)
npm run typecheck    # route type generation + tsc --noEmit
npm run build        # production build
npm run start        # serve the production build
```
