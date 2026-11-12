# ADR 0006: Accounts, sessions and private workspace provisioning

- Status: accepted
- Date: 2026-09-13
- Deciders: project author
- Related: [ADR 0004](0004-deployment-topology-and-protection.md) (access model), [ADR 0005](0005-data-foundation-and-migrations.md) (ownership schema), [data contract](../data-contract.md)

## Context

Milestone 4 adds self-service accounts so reviewers never depend on a
manually created user, alongside anonymous demo exploration. The public
landing page offers three paths: explore the demo, create an account, log in.
Private businesses must be created for a confirmed user through an authorized,
idempotent backend operation and must never receive demo transactions.

## Decisions

Sign-up and sign-in
- Supabase Auth email and password with email confirmation kept on. The
  browser uses the publishable key with the implicit flow, chosen because
  it does not depend on the browser that started the sign-up (the PKCE flow
  keeps a verifier in that browser only). Whether links opened on another
  device complete correctly has not been tested. The session lives in the
  browser client's storage, not in cookies, so it cannot be replayed by a
  cross-site request.
- Flows implemented with the standard client methods: sign-up with a
  confirmation redirect to `/auth/callback`, sign-in with password,
  resend confirmation, password reset by email with a recovery redirect,
  new-password form on the recovered session, sign-out. Error codes from the
  Auth server are translated to honest messages, including the case where a
  deployment can only email approved addresses.
- Confirmation, resend and password-recovery links all redirect to the
  same exact URL, `/auth/callback` on the deployment's origin; recovery is
  recognised from the `type=recovery` value Supabase puts in the URL
  fragment and from the `PASSWORD_RECOVERY` event. The redirect allowlist
  therefore needs exactly `http://localhost:3000/auth/callback` and the
  branch preview `https://cashlens-web-git-feat-m4-private-ownership-<team>.vercel.app/auth/callback`;
  no wildcard entries. Production is not enabled.
- Implicit-flow handling on `/auth/callback`: the client library consumes
  the fragment on load; the page waits for the resulting session or
  recovery event, then replaces the URL without the fragment before
  navigating on. Fragments are never sent to any server, so neither the
  web proxy nor the API can log them, and the API logs no Authorization
  headers. This was verified locally by reading the API log after
  authenticated calls; the email-link steps themselves await the manual
  test.

Calling the API as a signed-in user
- The browser sends the session's access token in the Authorization header
  through the same-origin proxy. The API validates it by asking the Auth
  server which user it belongs to (`/auth/v1/user`), requires the
  `authenticated` audience, and caches the answer per token hash for at
  most sixty seconds and never past the token's own expiry. No signing
  secret is stored anywhere in this project.
- Business scope is always derived from the validated user through
  `business_memberships`; ids in requests are never trusted.

Demo visitors
- `POST /api/demo/session` issues an opaque visitor id in an HttpOnly,
  SameSite=Lax cookie scoped to `/api` and stores it in `visitors`. A
  visitor can open any demo business, which creates or resumes a workspace
  through the `ensure_demo_workspace` function. Workspaces of other visitors
  are unreachable: the cookie is the only key, no client role can read the
  table, and the API only resolves the caller's own cookie.
- State-changing demo and private routes additionally require the
  `X-CashLens-Client: web` header. The API configures no CORS, so a
  cross-origin page cannot obtain preflight approval to send that header,
  and an ordinary HTML form cannot send custom headers or JSON at all; tests
  cover a foreign-origin preflight (no allow headers), form-encoded posts
  with a foreign Origin (403) and a cookie without the header (403). The
  visitor cookie is also SameSite=Lax, so browsers omit it from cross-site
  posts in the first place.

Private provisioning
- `POST /api/private/provision` calls the `provision_private_business`
  function with the validated user id. The function is executable only by
  the service role, takes an advisory lock per user, creates the private
  business, owner membership and workspace on first use, and afterwards
  returns the existing ones unchanged. A business name is required only on
  first use. It never references demo rows. Both provisioning functions run
  with definer rights (migration 0005) because the service role cannot read
  `auth.users`, which the function consults to reject unknown ids; execution
  stays restricted to the service role.
- `GET /api/private/workspace` reports the business, account and transaction
  counts (zero) and an explicit statement that uploads are not available
  yet. The page shows that empty state without any upload control.

Caching and response hygiene
- Every `/api/*` response carries `Cache-Control: no-store`.
- Logs contain no tokens, keys or cookie values.

Email delivery
- Unverified. Supabase's documentation states that the default email
  service sends only to pre-authorized addresses (other recipients fail with
  "Email address not authorized"), is limited to 2 messages per hour, and
  is intended for non-production use; email-and-password accounts for
  outside users need custom SMTP (Authentication → SMTP settings). A single
  sign-up probe on 2026-09-13 with an external address was accepted by the
  API and the throwaway user deleted, but an accepted request says nothing
  about delivery, and the project's own mail configuration cannot be read
  without a management token. No further probes will be sent.
- Until a real inbox receives the confirmation and the link completes
  provisioning, no document may claim that arbitrary users can sign up. If
  the manual test shows no delivery, the requirement is custom SMTP: a
  transactional provider account, sender address and name, host, port,
  username and password entered under Authentication → SMTP settings, plus
  the provider's SPF and DKIM records at the sending domain. Email
  confirmation stays on either way.

## Consequences

- Public self-service signup is gated: custom SMTP through a transactional
  provider and a verified confirmation delivery to an external, non-team
  recipient are required before anyone outside the project team is invited
  to sign up. The default mailer is kept only for the owner's own tests
  meanwhile. Provider selection is a later decision; nothing is purchased
  and email confirmation stays on.
- Private Storage bucket and its policies move to M6, where uploads begin;
  nothing in M4 stores files.
- The API now depends on Supabase from every session route; without the
  URL and secret key configured it answers 503 rather than pretending.
- Preview deployments need the two `NEXT_PUBLIC_` values on the web project
  and the Supabase values on the API project, both branch-scoped; production
  has neither by design, and since the production feature gate (ADR 0004
  amendment, 2026-09-13) its product routes answer 404 there regardless.
- Token validation by round trip costs one Auth call per new token; moving
  to local verification against the project's public JWKS is a later
  optimization, not a security requirement.

## Open questions

1. Whether the project's current mail configuration delivers to outside
   recipients at all, and the rate limit under interview traffic.
2. Cross-device completion of confirmation and recovery links.
3. Whether to add a magic-link sign-in option later; not needed now.

## Amendment 2026-09-13: email links redirected to localhost from the M6 preview

Observed by the author on the M6 branch preview: a self-service signup and a
password-recovery email both arrived (the first observed deliveries to the
author's own address), but each link redirected to `http://localhost:3000`
with the auth fragment, which nothing was serving.

Root cause, confirmed: the pages send exactly the current origin's
`/auth/callback` as the redirect target (verified by intercepting the
deployed pages' outgoing `/auth/v1/signup` and `/auth/v1/recover` requests
without sending anything: `redirect_to` was the M6 preview callback). That
URL is not in the project's redirect allowlist, which holds only the local
and M4-preview callbacks, so Supabase Auth fell back to the project's Site
URL, `http://localhost:3000`. The allowlist is a dashboard setting with no
management token available here, so the code cannot fix it.

Required dashboard action (Authentication → URL Configuration → Redirect
URLs): add exactly
`https://cashlens-web-git-feat-m6-statement-imports-<team>.vercel.app/auth/callback`
and keep the existing entries. Each preview branch needs its own exact entry
until a longer-lived preview host exists; no wildcards.

Verified without email, on the local build against the allowlisted local
callback, using admin-generated links for a temporary user: the confirmation
link completes on `/auth/callback` and lands in the workspace with the
fragment removed; the recovery link lands on the new-password form before
any workspace navigation, the new password is accepted and the old one
refused; a reused link shows an explanatory message with the fragment
removed and a way to request a new one. The callback page now also clears
error fragments and explains expired links. After the author added the allowlist
entry on 2026-09-13, the same check passed 14/14 against the protected M6
preview with a temporary user, which also shows the entry is honoured. What
remains pending is the author's own retest of a fresh confirmation link and
a fresh recovery link on the preview; delivery of the two earlier emails does not establish
either, and it says nothing about unrestricted public signup: custom SMTP
and an external-recipient delivery check remain required before public
signup.

## Amendment 2026-09-13: additional businesses and workspace selection

Manual PDF acceptance needed a second private business for the same login,
separate from the author's existing one. The schema already allowed it
(memberships are keyed by business and user, workspaces are unique per user
and business), but three layers resolved exactly one business: provisioning
returned the caller's earliest membership, `_membership` selected only that
one, and every private page took its workspace from `/api/me`. This is the
smallest change that opens a second business without a switcher.

- Provisioning keeps its default. Calling `provision_private_business`
  without a key still resumes the caller's earliest business and is
  idempotent, so ordinary onboarding is untouched. A caller-scoped
  idempotency key instead asks for an additional business. The key is stored
  on the membership under a unique index per user (migration 0011), so a
  retry or a concurrent duplicate returns the business the first call
  created. A boolean alone would have created a second business on every
  retry. The signature gains a defaulted third parameter, so the existing
  two-argument callers are unaffected; the function stays service-role only
  and the caller's identity still comes from the validated token, never from
  the request body.
- `POST /api/private/businesses` is the explicit, authenticated request for
  an additional business, taking a name and the caller's key.
- `GET /api/me` gains `businesses`, every private business the caller belongs
  to with its workspace id, earliest first. `business` and `workspace_id`
  still carry the earliest one, so existing callers are unaffected.
- `GET /api/private/workspace` accepts an optional `workspace` id, resolved
  through the same resolver the scoped routes use (ADR 0007). A workspace the
  caller does not own, or a malformed id, is 404. It never falls back to
  another business, because a silent fallback during an upload would put
  transactions in the wrong place.
- `GET /api/private/imports/{id}` answers which workspace an import belongs
  to, from the record and the caller's membership. The review page uses it
  instead of trusting the link, so a query parameter cannot move an import or
  point its confirmation at another business. The scoped routes already
  refuse a mismatched workspace with 404; this makes the page take the
  authoritative answer rather than relying on that refusal.
- Web: the workspace and upload pages accept `?workspace=`, show the business
  name so the owner can see where transactions will go, and show an
  unavailable state rather than another business when the selection fails.
  Links without a selection behave exactly as before. The header keeps the
  selection while the reader is inside the workspace pages.

Deliberately not included: a business switcher, invitations, roles, renaming
and deletion. A second business is reachable only by its link, which is
enough for the manual test and keeps the surface small.

