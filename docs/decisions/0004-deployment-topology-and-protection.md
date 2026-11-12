# ADR 0004: Deployment topology, dependency install and deployment protection

- Status: accepted
- Date: 2026-09-12
- Deciders: project author
- Related: [ADR 0002](0002-private-repository-and-deployment.md) (private repository), [ADR 0003](0003-scaffolding-runtimes-and-routing.md) (runtimes and routing; open questions 1 and 3 are answered here)

## Context

The M2 deployment feasibility spike created the first Vercel deployments from
the private repository and recorded what the platform actually does. Two
assumptions from ADR 0003 did not survive contact: Vercel does not fall back
to `requirements.txt` when a `pyproject.toml` is present, and a build-time
rewrite cannot carry the header needed to reach a protected API preview
deployment.

## Decisions

Projects
- Two Vercel projects on the author's Hobby team, both Git-linked to
  `claytonj27/cashlens` with production branch `main`:
  `cashlens-api` (root `services/api`, framework preset FastAPI) and
  `cashlens-web` (root `apps/web`, framework preset Next.js). Build logs
  confirm Python 3.12 from `.python-version` and Node 24.x.
- Git-triggered preview builds work for the private repository. Every push
  to a non-production branch builds both projects.

API dependency install
- `services/api` has no `pyproject.toml`. When one exists, Vercel treats it
  as the dependency source and runs `uv lock`, which fails without a
  `[project]` table. The service keeps its pip-tools lock in
  `requirements.txt`, which Vercel installs with uv. Ruff and pytest settings
  moved to `ruff.toml` and `pytest.ini`. The FastAPI entrypoint is detected
  automatically from `app/main.py`.

Routing
- `apps/web/src/proxy.ts` forwards `/api/:path*` to `API_ORIGIN` on the
  server and adds the `x-vercel-protection-bypass` header from
  `API_PROTECTION_BYPASS` when set. Both values are read at request time, so
  changing them needs no rebuild and previews no longer depend on a
  build-time origin. A missing `API_ORIGIN` on Vercel yields a 503 JSON
  error instead of a silent proxy to localhost.
- `API_ORIGIN` on `cashlens-web`: production and default preview point at
  `https://cashlens-api.vercel.app`; the `feat/m2-deployment-spike` branch
  overrides it with the API's branch alias. Future feature branches therefore
  exercise the production API unless given their own override.

Deployment protection
- Vercel Authentication stays on for both projects at its default
  ("standard") scope. Measured on 2026-09-12 after the first production
  deployment: unique deployment URLs, Git-branch aliases and the
  `<project>-<team>.vercel.app` aliases require a Vercel sign-in or a bypass
  secret, while the production domains `cashlens-api.vercel.app` and
  `cashlens-web.vercel.app` are publicly reachable. Preview API deployments
  are therefore reachable only by callers that present the API's bypass
  secret, whose only holder is the web app's server-side environment;
  the production API domain is public and currently serves only the health
  route. Automated checks use the web project's own bypass secret as a
  request header, never in browser code or URLs.
- Disabling preview protection on the API was considered and rejected;
  previews stay behind Vercel sign-in. Vercel protection is not an
  authorization mechanism for the product: the production API domain is
  public under the default scope, so the application's own authentication
  and authorization (M4) is the only guard for private data, and no route
  beyond health ships to production until it exists.

Access model (decided 2026-09-12)
- The GitHub repository remains private (ADR 0002).
- The production website (`cashlens-web.vercel.app`) is publicly reachable
  so interviewers can open it without a Vercel account.
- The synthetic demo businesses will be accessible without signing in.
- Private statements and financial data require application authentication
  and authorization: Supabase Auth sign-in, business memberships, row and
  storage policies, and a membership check on every request. Vercel
  protection plays no part in this.
- Paid AI operations require usage controls before they are exposed:
  server-enforced per-session and global limits, atomic budget reservation
  and bounded retries, as the blueprint specifies.
- Vercel preview protection remains enabled: preview deployments, unique
  deployment URLs and non-production aliases require a sign-in or a bypass
  secret held server-side.
- A custom domain is optional and does not provide authorization.
- Production remains a health-only scaffold until feature-specific access
  controls are implemented and verified.

Environment labels
- `APP_ENV` is set to `preview` and `production` on `cashlens-api` so the
  health payload reports the real environment.

## Verified on 2026-09-12

- API preview from the M2 branch builds and serves `GET /api/health` with the
  expected JSON and HTTP 200 (with bypass; HTTP 401 without).
- Web preview from the M2 branch builds; `GET /api/health` through the web
  origin returns the API's JSON and HTTP 200 (with the web bypass; HTTP 401
  without). A WebKit render of the deployed page shows the Connected state
  with the API's service, version and environment.
- The web client bundle contains no API origin or secret.
- First-deployment quirk: Vercel targeted the very first (failed) API build
  as production. Later Git-sourced deployments were previews. No successful
  production deployment exists yet for either project.

## Verified on 2026-09-12, continued (operator diagnostics)

Operator-only routes under `/api/ops` (404 unless enabled, 401 without the
token; see the API README) were enabled on the M2 branch preview, exercised,
and switched off again. Measured on deployment `a571368`:

- Configuration: the deployed API reports presence (never values) of the
  Supabase URL and secret key, the model id and the diagnostics token.
- Supabase round trip from the deployed API through the REST interface on
  the isolated `public.ops_probe` table: insert 201, read back, delete, read
  back empty. Step latencies ranged roughly 50 to 460 ms. The publishable key
  is denied on that table; only the service role has grants.
- Streaming: five chunks emitted 250 ms apart arrived incrementally both
  directly and through the web proxy (first to last chunk about 1.0 s).
- Cookies: an HttpOnly, Secure, SameSite=Lax cookie set by the API was
  returned through the proxy and sent back on the next request.
- Header handling: a client-sent bypass header is dropped by the proxy and
  replaced by the server's own; the client-sent set-bypass-cookie header does
  not reach the API; the API sees only the configured origin.
- Redirects: a 302 to a foreign host is passed back to the client unfollowed,
  so the bypass secret is only ever sent to `API_ORIGIN`.
- Unauthorized diagnostics calls (no token, wrong token) answer 401 even past
  Vercel protection; with diagnostics disabled every route answers 404.

## Feasibility status for statement processing

| Topic | Status | Basis |
|---|---|---|
| Function duration | Recorded, not measured against parsing | Vercel Hobby: 300 s default and maximum per invocation, including streamed responses (docs, 2026-08). No parser exists yet. |
| Request and response body size | Recorded | 4.5 MB maximum per function request or response body (docs). The planned 10 MB statement limit therefore requires direct uploads to Supabase Storage from the browser using short-lived signed upload URLs; the API must never receive file bytes. Untested. |
| Direct uploads to private Storage | Untested | No bucket, policies or signed-URL issuance exist. Planned for M6. |
| Bounded PDF processing and retries | Untested design | Persisted job state, leased and capped attempts, and bounded steps polled by the UI remain the plan; the 300 s ceiling must be measured with representative synthetic PDFs before any support claim. |
| Scheduled expiration cleanup | Identified, unverified | Vercel cron jobs are available on Hobby at most once per day with about an hour of timing slack (docs). That satisfies a 24-hour expiration policy at daily granularity; a cron route has not been created or observed running. |
| Model calls | Measured: one bounded call succeeded | From the deployed API through the operator-only probe on 2026-09-12: requested `gpt-5-mini`, served by `gpt-5-mini-2025-08-07`, reply "OK", finish reason stop, 3.3 s latency, 13 prompt and 10 completion tokens (0 reasoning), no retries, 20 s timeout. Cost at the official prices ($0.25 and $2.00 per million input and output tokens): about $0.000023 against a pre-computed ceiling of $0.000178 and an authorized maximum of $0.10. This was a single authorized request; recurring calls, evaluations and a spending budget are separate decisions. `gpt-5-mini` is a configurable placeholder, not the product model choice. |
| Application authentication and authorization | Not started | Supabase Auth sign-in, memberships, row and storage policies and per-request membership checks are M4. Vercel deployment protection is unrelated to CashLens user authentication. |

## Not verified yet

- Direct uploads, PDF parsing duration, retries and cleanup, as tabled above.
- Feature-specific access controls: production is a health-only scaffold
  (merged 2026-09-12) and stays that way until they exist.

## Consequences

- Merges to `main` deploy both projects to production automatically.
  Production carries no Supabase or OpenAI credentials; adding them is a
  deliberate step in the milestone that needs them, together with the usage
  controls for paid calls.
- The production domains stay public by decision (access model above).
  Access to private data is enforced in the application; a custom domain
  would change the address, not the authorization.
- Bypass secrets are rotated from the Vercel project settings; rotating the
  API secret requires updating `API_PROTECTION_BYPASS` on the web project.
- Previews have no default `API_ORIGIN`: a branch reaches an API only when
  given an explicit override, otherwise `/api/*` answers 503. Nothing reaches
  the production API silently.
- The operator diagnostics stay off outside an explicitly enabled branch
  preview; enabling them on production would need a deliberate decision.

## Amendment 2026-09-13: production feature gate

Integrating M4 and M5 puts demo sessions, accounts, provisioning and
workspace reads into the production build. Production must stay a
health-only scaffold until access controls, email delivery and usage limits
are verified, so the build carries an explicit switch instead of relying on
missing credentials.

- API: `FEATURES_ENABLED` (`app/config.py`). Unset, product routes are on in
  development, test and preview and off in production; `true` or `false`
  overrides either way. When off, every product route answers 404 through a
  router-level dependency before any identity or Supabase work, so
  production needs no database credentials and health is unaffected. When on
  without Supabase configured the routes answer 503, which keeps
  misconfiguration visible. Diagnostics under `/api/ops` keep their own gate.
- Web: `src/lib/features.ts` mirrors the gate from the build-time Supabase
  configuration. Without `NEXT_PUBLIC_SUPABASE_URL` and the publishable key,
  the landing page, the header and the demo, account and workspace routes
  show a scaffold notice and make no API calls. Production carries neither.
- Enabling features in production later is a deliberate operator step: set
  `FEATURES_ENABLED=true` and the credentials on `cashlens-api` and the two
  `NEXT_PUBLIC_` values on `cashlens-web`, after the M4 email items and the
  usage controls for paid features are in place. No merge flips it.
- Verified locally on 2026-09-13 before merging: unit tests for the switch
  and the 404 policy; the API started with `APP_ENV=production` and no
  `.env` served health 200 and answered 404 for the demo, account,
  provisioning, workspace and diagnostics routes with no errors logged;
  with `FEATURES_ENABLED=true` and still no credentials the same routes
  answered 503; a web build with empty `NEXT_PUBLIC_` values rendered the
  scaffold notice on the landing, demo, demo workspace and private
  workspace pages and made no API calls. The production deployments are
  checked the same way after the merge (checklist).
