# CashLens M8: privacy, retention, deletion and processing safeguards

A design for milestone 8, written against the code on `main` at `bbbf250` and
against what the development project and the hosting platform actually do,
both inspected while writing it.

**Tasks 1 and 2 are implemented** on `feat/m8-retention-cleanup`: retention and
the bounded sweep (migration 0013, `app/retention.py`,
`db/checks/retention_check.py`), and owner-requested business deletion
(migration 0014, the deletion routes in `app/private.py`,
`db/checks/business_deletion_check.py`). **Task 3 is implemented** too: extraction runs in its own
process with enforced limits, and the upload quotas are database-backed
(migrations 0015 to 0017, `app/imports/extract.py`, `extract_worker.py`,
`db/checks/quota_check.py`).

Nothing is scheduled: the sweep runs only when a caller invokes it, cleanup is
off by default in every deployment, and activation is section 4.6.

Precedence as usual: the [statement import plan](CashLens-Statement-Import-Plan.md),
then the [checklist](IMPLEMENTATION-CHECKLIST.md). Decisions that outlive this
plan belong in [ADR 0010](../decisions/0010-retention-deletion-and-processing-limits.md).

## What is already true

Inspected before proposing anything:

- The uploaded file is **already** deleted the moment an import is confirmed,
  cancelled or failed (`_delete_object` in `app/imports/routes.py`), and the
  deletion time is recorded in `document_imports.object_deleted_at`. A failed
  removal leaves that column null and is retried by the next cancellation or by
  the opportunistic sweep added in M7.
- `_sweep_expired` exists but runs only when the same owner starts another
  import, and only cancels `awaiting_upload` imports past `expires_at`. It is
  not a scheduled mechanism and must not be described as one.
- `cancel_import` (migration 0012) already ends an import, invalidates any open
  lease and removes staged rows in one transaction.
- `transaction_sources.import_row_id` is `ON DELETE RESTRICT`. Verified in a
  rolled-back transaction on the development project: deleting an
  `import_rows` row, or the `document_imports` row above it, **fails** while a
  confirmed transaction cites it. Deleting the whole `businesses` row
  **succeeds**, because the cascade removes transactions first and frees the
  rows. Provenance is therefore structurally protected, and any sweep that
  targets confirmed imports will error rather than quietly delete evidence.
- `visitors.last_seen_at` exists, defaults to `created_at`, and **is never
  updated**: `ensure_visitor` only inserts. Any idle-based rule for visitors is
  meaningless until that column is maintained.
- There is no index on `document_imports(expires_at)`, on its `status`, or on
  `visitors(last_seen_at)`. A project-wide sweep needs them.
- Quotas: `create_import` allows one import in progress per business and caps a
  single file at 10 MB, 20 pages, 5,000 rows and 500 KB of extracted text.
  There is no cap on imports per day and no cap on total stored bytes.

## 1. Retention and cleanup

Five classes, five rules. There is deliberately no blanket expiry: confirmed
financial history is the product and is kept until its owner deletes it.

### 1.1 Original uploaded files

| | |
| --- | --- |
| Rule | Deleted as soon as the import reaches a terminal state. A file whose import is abandoned is deleted after `expires_at`. |
| Authoritative timestamp | `document_imports.expires_at` (creation + 24 h). `object_deleted_at` records the removal. |
| Eligibility | `object_deleted_at is null` **and** (`status in ('confirmed','cancelled','failed')` **or** `expires_at < now()`). |
| Mechanism | Immediate path already exists. The sweep is the backstop, in batches, ordered by `expires_at`. |
| Retry | A Storage failure leaves `object_deleted_at` null, which is exactly the eligibility condition, so the next run retries it. Per-row `attempts` and `last_error` are recorded so a permanently stuck object is visible rather than silently retried forever. |
| UI may truthfully say | "Your file is deleted as soon as you confirm or cancel. If you abandon an upload, a scheduled cleanup deletes it, normally within a day of expiry. Until a removal actually succeeds it is shown as still pending, never as done." |

### 1.2 Staged rows and abandoned imports

| | |
| --- | --- |
| Rule | An upload that never reached review is ended 24 hours after it was created. An import that did reach review is ended **seven days after it first reached review**, not after the upload window: a file waiting for a decision is reviewed work, not an abandoned upload. |
| Authoritative timestamp | `document_imports.expires_at` for the first, `document_imports.staged_at` for the second. `staged_at` is stamped once by `stage_import` and never advanced, so viewing an import, changing its mapping or retrying it cannot extend its retention. |
| Eligibility | (`status = 'awaiting_upload'` and `expires_at < now()`) or (`status = 'staged'` and `staged_at < now() - interval '7 days'`). Confirmed imports are never eligible, and the `RESTRICT` above makes that a structural guarantee rather than a promise. |
| Mechanism | `retention_sweep_imports` selects a bounded batch in SQL and calls `end_import`, the transitions M7 established, now shared with the owner's own cancellation. No new deletion path. |
| Retry | `end_import` is idempotent; a second call reports `already_cancelled` and changes nothing. |
| UI may truthfully say | "Rows you have not confirmed are cleared when the import ends. An upload you never open is cancelled after a day; a file waiting for your decision is kept for a week." |

### 1.3 Confirmed transactions and retained provenance

| | |
| --- | --- |
| Rule | **No expiry.** Kept until the owner deletes them. |
| Authoritative timestamp | None. |
| Eligibility | Owner-initiated deletion only (section 2). |
| Mechanism | None scheduled. |
| Retry | n/a. |
| UI may truthfully say | "Your confirmed history stays until you delete it. The original file is gone; the page and line references remain as evidence." |

### 1.4 Operational error metadata

| | |
| --- | --- |
| Rule | Kept for as long as the import it belongs to, then removed with it. |
| Authoritative timestamp | The parent import's. |
| Eligibility | Cascade only. |
| Mechanism | `document_imports.error_code` / `error_message` (capped at 300 characters) and `import_jobs.safe_error` already hold codes, not content. `import_jobs` cascades from `document_imports`. |
| Retry | n/a. |
| UI may truthfully say | "We keep a short error code so you can retry. It never contains anything from your statement." |

### 1.5 Anonymous visitor sessions and demo workspaces

| | |
| --- | --- |
| Rule | A visitor idle for 30 days is removed; the cascade removes their demo workspaces. The shared demo businesses themselves are never touched. |
| Authoritative timestamp | `visitors.last_seen_at`, now maintained: resuming a demo session calls `touch_visitor`, which the database throttles to at most one write an hour per visitor. A failed touch never denies the request; it only delays that visitor's expiry. |
| Eligibility | `last_seen_at < now() - interval '30 days'`. |
| Mechanism | Sweep deletes visitors in batches; `workspaces.visitor_id` is `ON DELETE CASCADE`. |
| Retry | Idempotent; a re-run deletes nothing more. |
| UI may truthfully say | "Demo sessions are anonymous and are forgotten after 30 days without a visit." |

### 1.6 What a schedule can and cannot promise

Eligibility and removal are different moments, and the gap between them has three separate
causes that must not be reported as one.

**Normal delay.** A row becomes eligible at its retention boundary and is removed by the next
run. With a daily run that is up to 24 hours; the Hobby plan's cron adds up to 59 minutes of
scheduling jitter ([Vercel cron usage and pricing](https://vercel.com/docs/cron-jobs/usage-and-pricing),
2026-07-15). For an abandoned upload that is roughly two days from upload in the ordinary case.

**Outage.** If the scheduler does not fire, or the deployment is unreachable, nothing is
removed and nothing reports a problem on its own. `objects_pending` in each run's result is the
signal, and a backlog that does not shrink between runs is what an operator watches for.

**Failure and retry.** A Storage removal that fails leaves `object_deleted_at` null, records the
attempt count and the last error, and stays eligible. It is retried on every subsequent run for
as long as it keeps failing. There is no attempt ceiling that would silently abandon a file.

So the interface says what normally happens and what is guaranteed, and it never presents the
schedule as a maximum: confirming or cancelling deletes the file immediately, an abandoned
upload is normally gone within about two days, and a removal that has not yet succeeded is shown
as still pending rather than reported as done. **A nominal daily schedule is not a guaranteed
maximum deletion time**, because an outage or a repeated Storage failure extends it without
bound until it is fixed.

### 1.7 Sweep shape

One sweep, bounded and restartable:

- A single `run_retention_sweep(p_limit integer)` database function per class,
  each taking a batch limit and returning what it did. Each run takes
  `pg_advisory_xact_lock('retention_sweep')` so two overlapping runs cannot
  interleave; the second returns immediately having done nothing.
- Batch limits default to 200 imports and 500 visitors per run, ordered oldest
  first, so a backlog drains over successive runs instead of one long
  invocation. The route reports how many remain.
- Storage removal happens after the database transaction commits, one object at
  a time, and each success is recorded individually. A partial failure leaves
  the remaining objects eligible.
- Every step is idempotent: eligibility is recomputed from current state, never
  from a list captured earlier.
- New partial indexes: `document_imports(expires_at) where status in
  ('awaiting_upload','staged')`, `document_imports(expires_at) where
  object_deleted_at is null`, and `visitors(last_seen_at)`.

## 2. Owner-controlled deletion

The smallest coherent scope for M8 is two operations. Account deletion is
deliberately **not** included, for a reason recorded below.

### 2.1 Who may delete a business

`membership_role` has exactly one value, `owner`, so a membership row says only
that someone belongs to a business and never that they may destroy it. Rather
than invent roles, migration 0014 records who provisioned a business in
`businesses.created_by`, and that is the deletion authority. Existing rows were
backfilled only where a business has exactly one membership, which is what
provisioning creates; a business with none or several was left null and cannot
be deleted through this feature at all. Ownership is never taken from the first
row a query happens to return.

Four refusals, all server-side:

- no membership for the caller: 404, indistinguishable from a business that does
  not exist;
- a membership but not `created_by`: 403, because membership is not authority;
- more than one membership: 409, because roles and invitations do not exist and
  one member must not destroy another's data;
- `data_mode <> 'private'`: 404, and `guard_demo_rows` already fires BEFORE
  INSERT OR DELETE OR UPDATE on `businesses`, so a demo row cannot be deleted by
  any path at all.

The owner also types the business name, and the server checks it against the
stored name rather than trusting the page.

### 2.2 What one request does

`POST /api/private/businesses/{id}/deletion` is idempotent. The first call, in
one transaction:

1. authorizes as above and locks the business row;
2. sets `deletion_requested_at`, which is what refuses every scoped read and
   write from that moment;
3. copies the path of every file the business still has into `storage_cleanup`,
   **before** anything cascades;
4. ends every import still in flight through `end_import`, so an open lease is
   invalidated and a worker that returns cannot write;
5. deletes transactions first, then balance snapshots, imports, accounts and
   connections. Transactions must go first or the `RESTRICT` on
   `transaction_sources` blocks the import rows behind them.

A repeat call finds the work done and reports the same state.

### 2.3 The inventory outlives the rows it came from

`storage_cleanup` deliberately has **no foreign key** to businesses or imports.
The object paths live only on rows that step 5 removes, so a queue that
cascaded with them would lose exactly the files it existed to track. Because it
is independent, a removal that keeps failing stays retryable long after the
import row that named it is gone, which the development check proves directly.

### 2.4 The business row is the last thing to go

The business survives as a tombstone: every scoped read refuses it, but the
owner can still ask how far the removal has got. A cleanup run deletes its
queued files and then calls `finalize_business_deletion`, which removes the
business row **only** when no queued file for it remains. Memberships and the
workspace cascade with it. So nothing is reported as finished before it is, and
the status endpoint answering 404 is what "finished" means.

### 2.5 What may and may not be promised

| Layer | Truthful statement |
| --- | --- |
| Database | Accounts, transactions, provenance, imports and snapshots are removed immediately and transactionally. A failed request removes nothing. |
| Original files | Queued before anything cascades, removed by cleanup, and reported as still pending until Storage confirms. Never counted as deleted early. |
| Application logs | Carry a request reference, a business id and counts. They never carried statement content, so there is nothing to erase. Platform log retention applies and is the platform's. |
| Operational metadata | The `storage_cleanup` row survives until its file is gone, holding a path, an attempt count and an error code. It is removed with the queue entry, and holds nothing from the statement. |
| Provider backups | **Not promised.** Supabase keeps its own backups; a deleted row can persist in them until they age out on the provider's schedule. The interface says so. |
| The account | Untouched. Deleting a business never changes a password or removes a login. |

### 2.6 Why account deletion is not in M8

`business_memberships` and `workspaces` cascade from `auth.users`, but
`businesses` has no user foreign key. Deleting the auth user therefore **orphans
the business and every transaction in it**, with no member left who can reach or
remove them. Account deletion needs its businesses removed first, in a defined
order, and that is its own design with its own failure modes. Recorded here so
it is a known gap rather than an oversight.

## 3. Processing and upload safeguards

Task 3 is **designed and measured, not implemented.** The feasibility questions the earlier
draft left open were settled by tracing pypdf and by one bounded capability check on the
protected preview.

### 3.1 Where the time actually goes

`page.extract_text(visitor_text=...)` does three things in order, and only the third one calls
back into our code:

1. `get_contents()` resolves and **decompresses** the page's content stream;
2. `ContentStream(...)` tokenizes the whole decompressed stream into operations;
3. the operations are walked, and the text visitor fires as text-showing operators are reached.

Steps 1 and 2 finish before the first callback. Measured on a synthetic one-page file whose
content stream holds 120,000 tiny text operators (3.08 MB decompressed, 0.43 MB on disk):

| | |
| --- | --- |
| Time before the first visitor callback | 1.91 s |
| Total `extract_text` | 2.64 s |
| Share of the page that is uninterruptible | **72 %** |
| Visitor callbacks for 120,000 operators | 155 |

So a deadline checked inside the visitor is **not** a hard deadline, and it is weaker still than
the callback count suggests, because pypdf coalesces text and fires far fewer callbacks than
there are operators. The earlier draft's "sub-page granularity" is a real improvement to the
common case and nothing more; it is written down here as such.

### 3.2 The five limits, and what each one actually does

| Limit | Where it applies | What it does **not** do |
| --- | --- | --- |
| Input bytes, 10 MB | Bucket and server, before reading | Says nothing about CPU or memory: a 0.43 MB file expanded to 3 MB of operators and burned 2.6 s |
| Decompressed content | `zlib_maximum_output_length`, **inside** `zlib.decompressobj().decompress(..., max_length=N)` | Nothing to fix here: this is a true bound during decompression, not a size check afterwards. A check after decompressing cannot prevent the memory it already allocated |
| Extracted text and chunk counts | Accumulated in the visitor | Cannot bound steps 1 and 2, which is where most of a pathological page goes |
| Cooperative deadline | Between pages, phases, and now inside the visitor | Cooperative only. It cannot interrupt decompression or content-stream tokenizing |
| Execution termination | Nothing today | The platform's 300 s function timeout ends the whole request, not the extraction, and is not a per-job bound |
| Memory | Nothing today | The platform's 2 GB is the whole function; exceeding it kills the invocation rather than refusing the file |

pypdf 6.18.1 already defaults `zlib_maximum_output_length` to 75 MB and exposes
`apply_configuration(...)` as a context manager, so lowering it is configuration, not new code.
Verified locally: lowering it to 256 KB turned the probe file into `LimitReachedError` raised
during decompression.

### 3.3 Signals are ruled out, with evidence

The import route is a synchronous `def` handler, which Starlette runs on an AnyIO worker
thread. Measured: `threading.current_thread() is threading.main_thread()` is `False` there, and
`signal.signal(...)` raises `ValueError: signal only works in main thread of the main
interpreter`. So `SIGALRM` cannot be armed from the request at all. A thread is no better: a
Python thread cannot be killed, so a timeout would return while the work kept running.

### 3.4 Process isolation, as built

The feasibility check proved a forked child could be limited and killed. Implementing it
surfaced a hazard that check could not: **the earlier probe forked while nothing else held a
lock.** In the real path the request runs on an AnyIO worker thread with other threads alive,
and the Supabase client is module-cached with an HTTP connection pool whose lock is shared.
Inspected in the running app: three live threads, the request not on the main one, and
`httpcore pool._optional_thread_lock` on the shared client. A fork copies only the calling
thread, so any lock another thread held at that instant stays locked forever in the child.

So the boundary is **a fresh interpreter, not a fork**: `sys.executable -m
app.imports.extract_worker`, with the file on standard input and one JSON object on standard
output. It inherits no Python locks, no threads and no sockets. `multiprocessing` with `spawn`
was rejected as well, because it re-imports `__main__`, which here is the server entry point.

The worker receives only what parsing needs. Its environment is built from scratch with five
`CASHLENS_EXTRACT_*` values plus `PATH`, `PYTHONPATH`, `HOME` and `PYTHONUNBUFFERED`; no
credential, URL or connection setting is passed, and a test asserts the whole set. It applies
its limits before importing the parser, so even loading pypdf is covered. It publishes nothing:
the parent stages the result through the existing lease-guarded `stage_import`.

The parent uses `subprocess.run` with a timeout. Underneath, `communicate()` drains stdout and
stderr while the input is written, so a large result cannot wedge a pipe, and on timeout it
kills the child and reaps it. Every ordinary failure path ends the same way. The result is
rejected unless it is the shape the parser promises: row widths must match the header and the
line numbers must line up, or it is refused rather than trusted.

Each limit is applied independently and the outcome is reported back, because a platform that
refuses one must not make files unreadable. Linux applies both; Darwin refuses `RLIMIT_AS`
outright, so local runs log it as unavailable rather than pretending it held. The parent's wall
clock is enforced whatever the platform allows, so there is always a bound.

### 3.5 What this guarantees, and what it does not

Enforced now: wall clock per extraction, by the parent; CPU seconds and address space in the
child, where the platform allows them; decompressed output, by pypdf while decompressing; the
size of the result the parent will accept; and how many extractions run at once in one
instance.

Not guaranteed, and not claimed:

- `RLIMIT_AS` bounds **address space**, which is close to but not the same as resident memory.
- These are **per child**. They do not by themselves bound what one function instance uses in
  aggregate, which is why `extract_max_concurrent` caps concurrent extractions. That cap is
  process-local on purpose: it bounds **this instance** and says nothing about other instances,
  and the platform's own memory ceiling still applies to the whole function.
- **If the parent is killed, the child is orphaned.** `subprocess.run` cleans up on timeout and
  on every exception it sees, but a parent that dies without running its own cleanup, such as
  the platform terminating the function at 300 s or an out-of-memory kill, leaves the child
  behind. The child's own `RLIMIT_CPU` still stops it, which is the practical backstop and the
  reason the CPU limit is set rather than relying on the parent alone. There is no supported
  way on this runtime to have the kernel reap it with the parent, so this gap is written down
  rather than covered.
- A platform that refuses a limit weakens the guarantee to the wall clock alone, and says so in
  the log rather than failing silently.
- No real malicious corpus has been run. The evidence is synthetic files.

The extraction wall clock sits inside the end-to-end budget: 60 s by default, within the 180 s
job lease, which leaves room for the Storage read before it and the staging write after it,
all inside the platform's 300 s. Staging still verifies the lease and the import's state at the
moment it writes, so a slow extraction cannot publish into a lease someone else now holds.

### 3.6 Quotas, as built

The **import row is the reservation**, and it is written in the same transaction, under the
same advisory lock on the caller, as the counts that allow it. An earlier version checked first
and let the route insert afterwards; `db/checks/quota_check.py` caught that two concurrent
reservations were both allowed, because neither row existed when either was counted. Migration
0017 moved the insert inside. Nothing is counted in a process-local variable.

The caller is taken from the authenticated workspace, never from a request body. Counts span
every business the caller belongs to, excluding any awaiting deletion.

| Limit | Setting | Default | Exactness |
| --- | --- | --- | --- |
| Active imports across the caller's businesses | `quota_active_imports` | 3 | Exact |
| Upload initiations, rolling hour | `quota_starts_per_hour` | 10 | Exact |
| Upload initiations, rolling 24 hours | `quota_starts_per_day` | 40 | Exact |
| Outstanding undeleted bytes | `quota_outstanding_bytes` | 50 MB | Exact against recorded sizes |
| Attempts per job | unchanged | 3 | Exact |

All five are counted by a query inside the lock, so all five are exact. The rolling windows are
counted from timestamps, not sampled, and the earlier draft's "approximate" label was wrong and
has been removed. The byte total is exact against what is recorded: `byte_size` once known, and
the declared estimate before that.

The per-business rule that only one import may be in progress at a time is unchanged. It is
what the workspace shows and what the review page links to; the per-user quotas are the ceiling
across businesses that did not exist before.

**What consumes what.**

- An **active slot** is held while an import is `awaiting_upload` or `staged`. Confirmed,
  cancelled and failed imports hold none.
- **Outstanding bytes** are held by any import whose `object_deleted_at` is null, whatever its
  status. A file whose removal failed therefore keeps its reservation while the object still
  exists, which is the point: the space is genuinely in use.
- An **initiation** is recorded for every upload started, in `import_starts`.

**Why initiations need their own table.** Deleting a business cascades its `document_imports`
away. Counting initiations from those rows would mean deleting a business reset the caller's
rate limit, which is a concrete bypass. `import_starts` therefore has no foreign key to
businesses, exactly as `storage_cleanup` has none, and the check proves that neither cancelling
an import nor deleting a business changes the count. Rows older than any counted window are
cleared opportunistically under the lock the caller already holds, so the table stays bounded
without another sweep.

**Replay.** An exact replay is not a new upload: the file is already in the account and the
import just created is cancelled at once, so its initiation is withdrawn too. Ordinary
cancellation deliberately does not withdraw one, or cancelling would reset the rate limit.

**Declared bytes are an estimate.** They are what the reservation uses before a file exists.
The real size is checked against the per-file limit when the upload is read, independently of
any quota, and replaces the estimate once staging records `byte_size`.

**Release.** Terminal state releases the slot at once; the bytes release when the object is
actually gone. Expiry releases through the task 1 sweep, so **until the schedule is activated an
abandoned upload holds its slot and its bytes for the full retention window**. That is the
visible consequence of leaving activation open, and it is why the active limit is 3 rather
than 1.

**Refusals** name the limit and the recovery: finish or cancel an import, wait, or free space.
They carry only the caller's own usage and never anything about anyone else.

### 3.7 What was built

- `app/imports/extract_worker.py`, new: the child. Limits, pypdf configuration, parse, one JSON
  object out.
- `app/imports/extract.py`, new: the parent. Timeout, kill and reap, result validation, the
  per-instance concurrency cap, and a warning when a platform refused a limit.
- `app/imports/routes.py`: PDF staging goes through the child; creation reserves atomically;
  an exact replay withdraws its initiation.
- `app/config.py`: eight extraction settings and four quota settings, all with defaults.
- Migrations 0015 (`import_starts`, the reservation and usage functions), 0016 (fixing `min()`
  on a uuid column, which made every reservation fail), 0017 (check and insert in one
  transaction).

### 3.8 Verification

`tests/test_extract.py` runs real child processes: a supported PDF still parses, an unsupported
one is a controlled refusal, a stalled worker is stopped by the wall clock and no worker process
is left behind, a CPU limit ends the child with a signal, a compression bomb is refused while
decompressing, an oversized result is refused rather than returned, a large result does not
deadlock the pipes, a malformed result is rejected, and the worker environment carries no
credential.

`db/checks/quota_check.py`, 14/14 on the development project: active slots counted across three
businesses, a fourth refused, another user unaffected, two concurrent reservations and only one
granted, a confirmed import releasing its slot and its bytes, a failed removal keeping its
bytes, cancelling and deleting a business both leaving initiation history intact, a replay
withdrawing its initiation, and the hourly limit naming its reason.

Unchanged and re-run: 168 API tests, the import flow check 54/54, the five pack PDFs 72/72
through the isolated extractor, business deletion 29/29, retention 27/27, and all migrations
from empty with 33/33 RLS checks.

## 4. Scheduling and deployment

### 4.1 The constraint that decides it

Vercel triggers a cron job by making a GET request **to the project's
production deployment URL** ([Vercel cron jobs](https://vercel.com/docs/cron-jobs),
2026-08-11). There is no preview schedule. Production is required to stay a
credential-free scaffold with features disabled, so a Vercel cron job cannot run
CashLens cleanup during M8 at all: it would either hit a gated 404 or require
production credentials that must not exist yet.

### 4.2 The route, as built

`POST /api/maintenance/retention`, in its own router with its own switch.

It is deliberately **not** in the diagnostics router. `OPS_DIAGNOSTICS_ENABLED`
gates seven routes, among them a database probe and a **paid model probe**, so
turning cleanup on through that flag would switch all of them on at the same
time. Cleanup therefore has `RETENTION_CLEANUP_ENABLED` and
`RETENTION_CLEANUP_TOKEN` of its own. The route answers 404 unless both are set
and 401 unless the caller presents the token in `X-Cleanup-Token`, compared with
`secrets.compare_digest`. Both defaults are off, in every environment.

The body takes `limit`, `visitor_limit`, and optional `business_id` and
`visitor_ids`. The two optional fields narrow a run to resources the caller
already knows; they filter on top of the retention conditions, never instead of
them, so a narrowed run can reach fewer rows than an unnarrowed one and never a
row that is not yet eligible. The response reports `imports_expired`,
`rows_removed`, `objects_deleted`, `objects_failed`, `objects_pending`,
`visitors_removed`, `stopped_early` and `duration_ms`.

### 4.3 The trigger, not yet connected

- **During M8**: `pg_cron` in the development Supabase project, with `pg_net`
  posting to the route. Inside the stack already in use, no plan upgrade, not
  limited to once a day, and production is not involved. Enabling both
  extensions is an owner action in the Supabase dashboard.
- **Later**: when production is eventually enabled, a `vercel.json` cron on the
  daily schedule pointing at the same route. How Vercel authenticates that
  request must be re-checked against the documentation at that time; it is not
  settled here because it is not needed here.

### 4.4 What the schedule must point at

A preview deployment URL changes with every push, and a branch alias disappears
when the branch is merged or deleted. A schedule pointing at either would stop
silently: the request would fail, nothing would be cleaned up, and no owner
would see a difference until a backlog had built.

So the target is a **stable alias**, never a deployment URL and never a feature
branch's alias:

- While M8 is being tested, the owner drives the route by hand against the
  branch preview, which is fine because each invocation is deliberate.
- When a schedule is first switched on, it points at the production domain,
  which survives every deployment and every branch being retired. That is only
  possible once production is enabled, which is also when a Vercel cron becomes
  possible at all.
- If a schedule is ever wanted before then, it points at a **stable preview
  alias** that the owner assigns deliberately and re-points when the branch
  changes, and re-pointing it is part of retiring that branch.

Whatever the target, a run that does not happen leaves `objects_pending`
growing. That is the signal an operator watches, because nothing else reports an
absent schedule.

### 4.5 Operational properties

| Concern | Answer |
| --- | --- |
| Authentication | Existing operator token, constant-time compare, route 404 when diagnostics are off. |
| Batch limits | Caller-supplied, defaulted, with the remaining count in the response so a backlog is visible. |
| Overlapping runs | `pg_advisory_xact_lock` per sweep; a second concurrent run does nothing and says so. |
| Observability | One structured log line per run: counts by class, failures, duration, request reference. No paths, no file names, no financial values. |
| Retry | Per-item attempt counts and last error; eligibility is recomputed each run, so a transient failure simply retries next time. |

### 4.6 Activation, and what only the owner can do

Nothing below has been done. Cleanup is off in every deployment, no schedule
exists, and the route cannot be reached without a token that is not set anywhere
except a preview the owner controls. These are the exact remaining steps.

Already done for the branch preview, and only there:
`RETENTION_CLEANUP_ENABLED` and `RETENTION_CLEANUP_TOKEN` are set as
branch-scoped preview variables on `cashlens-api` for
`feat/m8-retention-cleanup`. The token was generated locally and stored as an
encrypted Vercel environment variable; it is not committed, not printed and not
logged. Both previews keep Vercel Authentication on, so a caller also sends the
project's protection-bypass secret and reaches the route through the web proxy,
exactly as the existing checks reach the API. Production holds neither
variable, so the route answers 404 there.

Still to do, and only the owner can decide them:

1. **Keep running it by hand** while it is under review, narrowed with
   `business_id` to a temporary business, reading the response counts. Nothing
   recurring exists yet.
2. **If a schedule is wanted:** enable `pg_cron` and `pg_net` in the development
   Supabase project's dashboard and point a job at the stable target from
   section 4.4. This is the step that turns cleanup from a deliberate action
   into a recurring one, and it is deliberately not part of task 1.
3. **Decide the target before scheduling anything.** A job aimed at this
   branch's alias stops silently the moment the branch is merged or deleted.
4. **Switch cleanup off again** on the preview when testing is finished, by
   removing the two branch-scoped variables.

**Bounded execution.** A run stops adding Storage work when its 120-second budget
is nearly spent, reports `stopped_early`, and leaves the rest eligible. The
platform terminates a function at 300 seconds, so the budget sits well inside it
and a run always gets to return its counts. A caller should treat a long run as
an instruction to lower `limit`, not to raise a timeout.

## 5. Protecting the existing demonstration data

### 5.1 What must survive

Cash Lens (180 CSV transactions), Gift Genie (88 PDF transactions, with May to
August deliberately unimported and reserved for later trend demonstrations),
Cedar Corner PDF Test (empty), the pending staged import
`beec9206-f8bf-4c6e-8fc0-03f0c36b23d5`, and the three shared demo businesses.

### 5.2 The pending import under the rules as implemented

The design pass raised this as an unresolved conflict: under a single 24-hour
rule the owner's pending staged import would have been swept. The seven-day
staged rule settles it, and settles it as policy rather than as an exemption.

That import first reached review on 2026-09-13 at 16:37 UTC, so `staged_at`
holds that value and it becomes eligible on **2026-09-20 at 16:37 UTC**. It is
not eligible now, and no run performed for this task touched it. Nothing was
cancelled, extended or reset on the owner's behalf.

The distinction is defensible on its own merits and would have been made
regardless of which rows exist today: an upload nobody opened is abandoned, and
a file waiting for a decision is reviewed work. A hidden exemption for a
specific id would have been a production bypass disguised as a fixture, and
remains off the table.

### 5.3 How the tests avoid the owner's data without a bypass

- Every acceptance check creates its own temporary user, business and imports,
  and asserts only on ids it created, exactly as the existing checks do.
- The sweep functions take a batch limit and an **optional** `p_business_id`,
  and the visitor sweep an optional `p_visitor_ids`. Narrowing is a real
  operator capability, not a test hook: the unscoped run is the superset of the
  scoped one and shares all its code, so a scoped test exercises the same logic.
  The retention conditions are in the same `where` clause either way, so a
  narrowed run can never reach a row that is not yet eligible, and authorization
  is unchanged by it.
- The unscoped run is exercised against a temporary business whose rows are the
  only eligible ones at that moment, and the check asserts beforehand that no
  other business has eligible rows. If the owner's pending import is eligible,
  the check **fails loudly** rather than proceeding, which surfaces 5.2 instead
  of hiding it.
- Preservation is verified by read-only queries before and after every run.

### 5.4 Demo transaction counts reconciled

Read-only, on the development project. The two reported figures are both
correct; they count different things.

| Business | All rows | Posted and not removed | Pending, not removed | Soft-removed |
| --- | --- | --- | --- | --- |
| Juniper Kitchen | 323 | 320 | 1 | 2 |
| Northline Services | 210 | 207 | 1 | 2 |
| Willow Studio | 131 | 128 | 1 | 2 |

The earlier 320 / 207 / 128 is `status = 'posted' and removed_at is null`, which
is exactly what the workspace route filters on. The later 323 / 210 / 131 counted
every row regardless of status or removal. The fixtures deliberately include one
pending and two soft-removed transactions per demo business so those states are
exercised. **This is counting semantics, not a discrepancy, and no fixture
should be changed to make the numbers agree.**

## 5A. Traffic analytics, and what it actually collects

Added for the interview demo release. This section describes what is implemented, not a plan.

**Nothing is collected without a choice.** Google Analytics 4 runs on the branch preview only,
behind two environment variables that are unset in local development, in automated checks and in
production. Consent Mode starts with `analytics_storage`, `ad_storage`, `ad_user_data` and
`ad_personalization` all denied, and Google's script is **not requested at all** until a visitor
chooses to allow it. The choice is offered before any collection, stored in that browser, and
reversible from a control in the site footer at any time.

**Advertising features, Google Signals and User-ID are all off.** Nothing sent identifies a
person, an account, a business or a workspace.

**Page views are sent by hand, from sanitised values.** The tag is configured with
`send_page_view: false`, so the only page view for a navigation is the one the application sends
itself, built from a route template rather than the browser's URL:

- `/demo/juniper-kitchen/analysis` is reported as `/demo/[slug]/analysis`
- `/workspace?workspace=<uuid>` is reported as `/workspace`
- `/workspace/imports/<id>` is reported as `/workspace/imports/[id]`

Query strings and fragments are dropped entirely, and the referrer is reduced to the site origin.
That last point is load-bearing for this release: the recipient share link carries a token in the
query string, and a raw `page_location` or `page_referrer` would carry it to Google.

**Some screens never report at all.** Authentication (`/login`, `/signup`, `/forgot-password`,
`/auth/*`) carries credentials in flight; statement import and review (`/workspace/import`,
`/workspace/imports/*`) carries a person's own bank statement; and the operator dashboard
(`/admin/*`) is not a product surface. None of them sends a page view or an event, and the
consent notice is not shown on them either.

**Events are a fixed list, with a fixed set of parameters.** The events are `demo_opened`,
`analysis_viewed`, `scenario_applied`, `evidence_opened`, `ai_question_submitted`,
`ai_answer_received` and `ai_request_failed`. The only parameters any of them may carry are a
coarse section label, a capability name, a provider route, a synthetic demo business slug, and a
coarse outcome. Any other field a call site passes is dropped before sending rather than
forwarded, and a value that is not short, lowercase and alphanumeric is dropped too.

A failure event means a request that produced nothing. An answer that was produced and had some
claims withheld is reported as an answer, because it is one.

**Never sent:** question text, model answers, transaction descriptions, amounts, uploaded file
names, email addresses, user ids, workspace ids, business ids, transaction ids, credentials, or
the preview share token.

**Enhanced measurement.** The automatic events Google can collect read real URLs, form contents
and search terms. This deployment sends its own page views instead and relies on none of them;
enhanced measurement should be left off in the property, and outbound-click, site-search and
form-interaction collection in particular should not be enabled, because they would read values
this application deliberately never sends.

## 6. Implementation breakdown

Three bounded tasks. Each ends somewhere a reviewer can stop.

### Task 1: retention rules, indexes and the sweep

- **Scope**: sections 1.1 to 1.7. The five retention rules, the sweep functions
  with batch limits and the advisory lock, the operator route, the partial
  indexes, and maintaining `visitors.last_seen_at`. Replaces `_sweep_expired`.
- **Depends on**: nothing.
- **Code and migrations**: migration for the indexes and the sweep functions;
  `app/imports/routes.py` (remove the opportunistic sweep), `app/ops.py` (the
  route), `app/auth.py` (touch `last_seen_at`).
- **Acceptance**: eligible and ineligible rows either side of every boundary; a
  confirmed import is never selected, and the `RESTRICT` is asserted directly; a
  Storage failure leaves `object_deleted_at` null and the next run succeeds; two
  concurrent runs do not interleave and the second reports it did nothing; a
  batch limit smaller than the backlog drains over runs and reports the
  remainder; a partial failure mid-batch leaves every unprocessed item eligible.
- **Preview**: invoke the route by hand with the operator token against the
  preview, on temporary resources, with the owner's data asserted unchanged
  before and after. Diagnostics switched off afterwards.
- **Stops at**: cleanup works and is scheduled by hand. No owner-facing deletion.

### Task 2: owner-controlled deletion

- **Scope**: sections 2.1 to 2.5. Delete financial data, delete a business, the
  `deleted_at` marker and the two-phase hand-off to the sweep, the confirmation
  screen, and the truthful wording about logs and backups.
- **Depends on**: task 1, for phase two.
- **Code and migrations**: migration for `businesses.deleted_at` and the two
  deletion functions; `app/private.py` and `app/workspaces.py` (filter the
  marker); `app/imports/routes.py`; a settings page in the web app.
- **Acceptance**: another member's business, another owner's business and a demo
  business are all refused; a business with two memberships is refused; deleting
  financial data leaves the business and workspace usable; the marker revokes
  every scoped read immediately while objects are still pending; a failure
  during Storage leaves the business marked and drained on the next run; a
  deletion racing a confirmation of the same import produces one valid outcome.
- **Preview**: full delete of a temporary business through the deployed proxy,
  Storage emptied, owner's data asserted unchanged.
- **Stops at**: owners can delete their own data. Account deletion still absent
  and recorded as such.

### Task 3: bound extraction and add quotas

One task. Scope is sections 3.4 to 3.8 and nothing else.

- **Scope**: move extraction into a forked child with `RLIMIT_AS`, `RLIMIT_CPU`
  (soft below hard) and a lowered pypdf `zlib_maximum_output_length`, with a
  parent wall-clock timeout that kills, joins and confirms the pid is gone. Add
  the visitor-level deadline and the text and chunk caps, which improve the
  common case and are not presented as a hard bound. Add the four
  database-backed quotas with their reservation lifecycle.
- **Depends on**: nothing. Tasks 1 and 2 are independent of it.
- **Code and migrations**: `app/imports/extract.py` (new), `pdfparse.py`,
  `budget.py`, `routes.py`, `config.py`, and one migration for the quota
  function and an index on `document_imports (business_id, created_at)`. No new
  table: the import row is the reservation.
- **Acceptance**: the list in section 3.8, including that a stopped child leaves
  no pid, that nothing partial is written, and that two concurrent creations
  cannot both take the last active slot.
- **Preview**: the pathological fixtures and one concurrency check through the
  deployed proxy, on temporary resources, with observed durations recorded as
  single samples rather than a benchmark.
- **Stops at**: extraction is bounded and quotas hold. The scheduler stays
  unactivated, and the remaining limitations in section 3.5 are written down
  rather than claimed closed.

## 7. Release records

M8 does not close any email gate. Fresh signup confirmation and custom SMTP with
verified external delivery stay open and unclaimed. The M7 limitations stay as
they are. The concerns carried into this milestone from the M7 review are
answered here as follows, and only as follows: per-page processing isolation by
section 3.3 and 3.4, resource exhaustion by 3.3 and 3.5, lease expiry by 3.6 as
already-correct plus regression coverage, and the missing scheduled mechanism by
section 4. None of them may be marked resolved until the code exists and the
checks have run.
