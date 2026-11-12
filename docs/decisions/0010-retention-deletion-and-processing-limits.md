# 0010: retention, deletion and processing limits

Status: accepted, 2026-09-13. All three M8 tasks are implemented on `feat/m8-retention-cleanup`: retention and the bounded sweep, owner-requested business deletion, and bounded extraction with upload quotas. No schedule exists and cleanup is off in every deployment.
Supersedes nothing. The full plan is
[CashLens M8 privacy and retention plan](../planning/CashLens-M8-Privacy-and-Retention-Plan.md).

## Context

M7 closed with four concerns carried forward: per-page processing isolation,
resource exhaustion under adversarial input, lease-expiry behaviour, and the
absence of any scheduled expiry mechanism. M8 has to answer them, and to add the
privacy gate the checklist requires before any real public upload.

The design was written against the code on `main` at `bbbf250` and against what
the development project and the platform actually do, both inspected rather than
assumed.

## Decisions

**Retention windows, as implemented.** An upload that never reached review is
ended 24 hours after creation, from the immutable `expires_at`. An import that
did reach review is ended seven days after it FIRST reached review, from a new
`staged_at` stamped once by `stage_import` and never advanced, so viewing an
import, changing its mapping or retrying it cannot extend its retention. The
seven-day window is not a longer version of the first rule: an upload nobody
opened is abandoned, while a file awaiting a decision is reviewed work, and the
two deserve different treatment. Confirmed transactions and their provenance
never expire. Source files follow the existing lifecycle, with a failed removal
retried and recorded.

**Existing rows were backfilled from `created_at`, not from deployment time.**
It is the only immutable evidence of when an import could first have been
staged, and it is a lower bound, so a backfilled import becomes eligible no
later than the truth. `updated_at` was rejected as the basis: it advances
whenever a row is reviewed or re-staged, so using it would have let merely
looking at an import extend its retention.

**Retention is per class, not one expiry.** Original files, staged rows,
confirmed transactions, operational error metadata and anonymous visitor
sessions each get their own rule, timestamp and mechanism. Confirmed financial
history has no expiry at all: it is the product, and it is kept until its owner
deletes it. A single blanket rule would either delete the history or fail to
clean up the files, and no compromise between those is correct.

**Provenance is protected structurally, not by convention.**
`transaction_sources.import_row_id` is `ON DELETE RESTRICT`. Verified in a
rolled-back transaction: deleting an `import_rows` row, or the
`document_imports` row above it, fails while a confirmed transaction cites it.
A sweep that ever targets a confirmed import will error rather than quietly
destroy evidence. We keep that property and rely on it.

**Deleting a business is one statement.** Verified in the same transaction:
`delete from public.businesses where id = $1` succeeds, because the cascade
removes transactions first and frees the restricted rows. No hand-ordered
deletion function is needed for that case. Deleting only the financial data,
keeping the business, does need an order: transactions before imports.

**Deletion authority is recorded, not inferred.** `membership_role` has exactly
one value, so a membership says only that someone belongs to a business.
`businesses.created_by` records who provisioned it and is the authority to
delete it; a member who did not create it is refused, and a business with more
than one membership is refused outright because roles and invitations do not
exist. Existing rows were backfilled only where there was exactly one
membership. Ownership is never taken from the first row a query returns.

**Deletion is two-phase, and the file inventory is a table after all.** The
design pass argued a `deleted_at` marker would avoid a queue. Implementing it
showed why it cannot: the object paths live only on the import rows that
deletion removes, so anything that cascades with them loses exactly what it
existed to track. `storage_cleanup` therefore has no foreign key to businesses
or imports, and a failed removal stays retryable long after those rows are gone.
`businesses.deletion_requested_at` still revokes every scoped read immediately,
and the business row survives as a tombstone until its last file is confirmed
gone, so nothing is reported finished early.

**Backups are not promised.** Database deletion is immediate and permanent,
original files are removed with the request or by the next cleanup and shown as
pending until they are, application logs never carried statement content, and
provider backups age out on the provider's schedule. The interface says exactly
that and no more.

**A deleted business is never revived.** Provisioning skips a business awaiting
deletion when it resumes the earliest one and when it matches an idempotency
key, so asking again creates a new business instead of reviving what the owner
asked to remove.

**Account deletion is out of scope, and why.** `business_memberships` and
`workspaces` cascade from `auth.users`, but `businesses` has no user foreign
key, so deleting the auth user orphans the business and every transaction in it
with no member left who can reach them. That needs its own ordered design. It is
recorded as a known gap rather than attempted at the edge of this milestone.

**Extraction is bounded by a forked child, because nothing in-process can do
it.** This replaces the earlier plan to settle the question later. Tracing
pypdf showed that `extract_text` decompresses the content stream and tokenizes
it into operations before the first visitor callback; measured on a synthetic
page, 72 per cent of the time was spent before any callback fired, and 120,000
text operators produced only 155 callbacks. A deadline checked in the visitor is
therefore a cooperative improvement to the common case and not a hard bound, and
it is recorded as such rather than described as one. Signals were ruled out with
evidence: the import route is a synchronous handler that Starlette runs on a
worker thread, where `signal.signal` raises outright. A thread cannot be killed.

One capability check on the protected preview, against a synthetic allocation
and a short spin, settled the rest: `fork` is available, `RLIMIT_AS` makes an
oversized allocation raise `MemoryError`, `RLIMIT_CPU` kills a spinning child,
and a parent-initiated kill stops the child in about a second, reaps it and
leaves no pid behind. That last property decided it: a child left running would
be frozen with the execution environment and resume on a later invocation.

**The child is a fresh interpreter, not a fork.** The capability check proved a
fork could be limited and killed, but it forked while nothing else held a lock.
Inspecting the real path showed the request runs on an AnyIO worker thread with
other threads alive and a module-cached HTTP client whose pool lock is shared;
a fork copies only the calling thread, so such a lock would stay locked forever
in the child. Extraction therefore runs as `sys.executable -m
app.imports.extract_worker`, inheriting no locks, threads or sockets, with the
file on stdin and one JSON object on stdout. `multiprocessing` with `spawn` was
rejected too, because it re-imports `__main__`, which here is the server entry
point. The child gets an address-space limit, a CPU limit whose soft value sits
below the hard one, a lowered pypdf decompression limit and an environment built
from scratch that carries no credential. The parent enforces the wall clock and
kills and reaps on timeout and on every failure path it sees.

**A limit a platform refuses is reported, not assumed.** Each limit is applied
separately and its outcome returned, because Darwin refuses `RLIMIT_AS` while
Linux applies it, and an extraction must not become impossible because a
platform lacks one. The parent's wall clock holds regardless.

**An orphaned child is a named gap, not a covered one.** If the parent dies
without running its cleanup, such as the platform ending the function at its
limit, the child survives. Its own `RLIMIT_CPU` stops it, which is the practical
backstop and the reason that limit is set rather than trusting the parent alone.
No supported mechanism on this runtime reaps it with the parent.

**A post-decompression size check is not a memory bound, and pypdf already
provides the real one.** `zlib_maximum_output_length` is passed as `max_length`
to `zlib.decompressobj().decompress`, so the output buffer is bounded while the
data is being produced. Checking a size after decompressing cannot prevent the
memory that decompressing already allocated. The default is 75 MB and is
configuration, not new code; M8 lowers it and scopes it around extraction.

**Quotas are database-backed and serialized, never process-local.** Active
imports per user across their businesses, initiation rate over rolling windows,
outstanding undeleted bytes and attempts per job are evaluated inside one
transaction holding an advisory lock on the caller, so two requests cannot both
pass. A per-instance counter would mean nothing on this platform. The
`document_imports` row is the reservation, and it is inserted in the same
transaction as the counts that allow it: an earlier version checked first and
let the route insert afterwards, and the development check caught two concurrent
reservations both being granted because neither row existed when either was
counted.

All five limits are counted by a query inside the lock, so all five are exact.
The earlier draft called the rolling windows and the byte total approximations;
that was wrong. The windows are counted from timestamps rather than sampled, and
the byte total is exact against what is recorded.

**Initiations need a table of their own.** Deleting a business cascades its
imports away, so counting initiations from those rows would let deleting a
business reset a rate limit. `import_starts` therefore has no foreign key to
businesses, as `storage_cleanup` has none. An exact replay withdraws its
initiation because it is not a new upload; ordinary cancellation does not, or
cancelling would reset the limit.

**A file-size limit does not bound CPU or memory, and we do not claim it does.**
Measured: a 0.43 MB file expanded to 3 MB of operators and took 2.6 seconds. The
platform's 300 s and 2 GB apply to the whole function, not to one extraction, so
substituting the function timeout for a per-job bound would not satisfy the
requirement and is not treated as though it did. Until the forked child exists,
that platform timeout is the only hard limit, and it is described that way.

**Cleanup has its own switch, separate from diagnostics.** Reusing
`OPS_DIAGNOSTICS_ENABLED` was inspected and rejected: it gates seven routes,
among them a database probe and a paid model probe, so enabling cleanup through
it would switch those on at the same time. Cleanup is
`POST /api/maintenance/retention` behind `RETENTION_CLEANUP_ENABLED` and
`RETENTION_CLEANUP_TOKEN`, both off by default, 404 when unset and 401 on a bad
token. The plan's earlier placement under the diagnostics router is superseded.

**Ending an import has one implementation.** `end_import` holds the transitions
M7 established, taking job rows before the import row, invalidating any open
lease and removing staged rows in one transaction. The owner's `cancel_import`
performs its membership check and calls it; the sweep calls it with a reason
code. Expiry therefore inherits the lease protections rather than reimplementing
them, and a parse that returns after an expiry is refused exactly as it is after
a cancellation.

**A nominal schedule is not a guaranteed maximum deletion time.** Normal delay,
an outage and a repeated Storage failure are three different causes with three
different remedies, and the interface reports what is guaranteed rather than
what is typical. A removal that has not succeeded is shown as pending, never as
done, and is retried on every subsequent run with no ceiling that would silently
abandon a file.

**The cleanup route is separated from its trigger, because Vercel crons cannot
serve this milestone.** Vercel triggers cron jobs against the *production*
deployment URL only, and production must stay a credential-free scaffold with
features disabled. So the route is scheduler-agnostic, behind its own switch as
above, and the M8 trigger is `pg_cron` with `pg_net` in the development Supabase
project: inside the stack already in use, no plan upgrade, no production
involvement. Whatever eventually drives it points at a stable alias, never a
deployment URL or a feature branch's alias, because either would stop silently
when the branch moved or was retired. A `vercel.json` cron on the
daily schedule is added later, when production is actually enabled, and how
Vercel authenticates that request is re-checked then rather than guessed now.

**A daily schedule is described honestly.** Hobby crons run once per day with up
to 59 minutes of jitter, so an abandoned upload is normally gone within about
two days of upload. That is the ordinary case and not a ceiling: an outage or a
repeated Storage failure extends it without bound until it is fixed. The
interface says confirming or cancelling deletes immediately, that an abandoned
upload is normally gone within a couple of days, and that a removal still in
progress is pending. It never states a maximum.

**Test isolation comes from narrowing, not from exemptions.** The sweep functions
take an optional business filter, which is a genuine operator capability and a
strict narrowing of the unscoped run that shares all its code. No id is ever
special-cased. The unscoped run is exercised only when a pre-check confirms no
other business has eligible rows, and it fails loudly otherwise.

## Consequences

- `visitors.last_seen_at` is now maintained: resuming a demo session calls
  `touch_visitor`, throttled in the database to at most one write an hour per
  visitor. A failed touch never denies the request and only delays that
  visitor's expiry.
- The sweep's selections are bounded in SQL and served by partial indexes added
  in migration 0013 on `document_imports(expires_at)`, `document_imports(staged_at)`,
  the pending-Storage backlog and `visitors(last_seen_at)`.
- The owner's pending staged import first reached review on 2026-09-13 at 16:37
  UTC, so under the seven-day rule it becomes eligible on 2026-09-20 at 16:37
  UTC. It was not touched. The design pass left this open; the seven-day rule
  settles it as policy, not by exempting a row.
- The opportunistic sweep in `create_import` is replaced, not extended.
- M8 closes no email gate. Signup confirmation and custom SMTP stay open.

## Alternatives considered

**One 24-hour expiry for everything.** Rejected: it would delete the confirmed
history the product exists to show.

**A deletion queue table.** Rejected for now: the `deleted_at` marker plus the
existing `object_deleted_at is null` retry covers the same ground with one column
and no new mechanism. Revisit if deletion ever spans more than Storage objects.

**A thread with a timeout for extraction.** Rejected: a Python thread cannot be
killed, so the timeout returns control while the work keeps consuming CPU until
the platform terminates the invocation. It would look like a limit without being
one.

**Vercel Cron for M8.** Rejected on a documented fact, not a preference: it only
targets production deployments, and production must stay a scaffold.
