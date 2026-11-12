-- 0024: cash-flow buckets for every row of an uploaded statement.
--
-- The existing `classify` capability answers about a handful of rows the deterministic rules
-- could not place, and is allowed to say "I cannot tell". This is a different question asked of
-- the whole file: sort every transaction into one of eleven cash-flow buckets so a bookkeeper
-- can review the lot and export it. Both use the same provider boundary, the same reservation
-- ledger and the same run rows; only the question differs.
--
-- It lives on `import_rows` rather than in a table of its own because that is already the right
-- grain. One staged row is one transaction from the file, `row_index` already preserves the
-- file's order, and `raw` already preserves every original column, which is what the export has
-- to reproduce. A second table keyed by the same thing would be a join for nothing.
--
-- `cashflow` holds the classification as it stands:
--
--   {
--     "direction": "inflow" | "outflow",     -- computed from the amount, never from the model
--     "category":  "Operating expense",       -- one of the eleven, valid for that direction
--     "confidence": "high" | "medium" | "low",
--     "reason": "...",                        -- short, for a human deciding whether to accept
--     "source": "ai" | "manual",              -- who last set the category
--     "run_id": uuid | null,                  -- the batch that produced it, for the trace
--     "decided_at": timestamptz
--   }
--
-- Direction is stored alongside the category on purpose. It is derived from the amount in
-- application code before the batch is built and re-checked afterwards, and keeping it here
-- means a later reader does not have to re-derive it from a sign convention it would have to
-- guess at. A manual correction overwrites `category`, `source` and `decided_at` and leaves
-- `direction` alone, because a person cannot change arithmetic either.
--
-- Null means not yet classified, which is what makes a run resumable: a batch that was refused
-- by a spend cap leaves its rows null and the next run picks up exactly those.

-- The capability is allowlisted at the database, so a new one has to be admitted here before a
-- run row for it can be written. Without this the classification fails on its very first batch,
-- with a database error rather than anything the owner could act on.
alter table public.ai_runs drop constraint if exists ai_runs_capability_check;
alter table public.ai_runs add constraint ai_runs_capability_check
  check (capability in ('brief', 'classify', 'scenario_interpret', 'scenario_explain', 'ask', 'cashflow'));

alter table public.import_rows
  add column if not exists cashflow jsonb;

comment on column public.import_rows.cashflow is
  'Cash-flow bucket for this row: direction computed from the amount, category from the model or '
  'a human correction, with confidence, reason and the run that produced it. Null until classified.';

-- The review screen filters by direction, category and confidence across every row of one
-- import, and the resume path asks for the rows that have none. Both are answered from the
-- import id, so one partial index on the unclassified rows is what earns its keep.
create index if not exists import_rows_unclassified
  on public.import_rows (import_id, row_index)
  where cashflow is null;

notify pgrst, 'reload schema';
