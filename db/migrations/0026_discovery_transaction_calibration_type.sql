-- 0026: the transaction calibration question type, and nothing else.
--
-- This file exists alone on purpose. Postgres will not let a value added to an existing enum be
-- referenced in the transaction that added it, and db/migrate.py runs one transaction per file,
-- so the constraints in 0027 that name 'transaction_calibration' need this to have committed
-- first. Do not merge this into another migration: it would fail with "unsafe use of new value
-- of enum type", and the failure would look like a constraint problem rather than an ordering
-- one.
--
-- Design: docs/transaction-calibration-design.md and
-- docs/transaction-calibration-schema-proposal.md.

alter type public.discovery_question_type add value if not exists 'transaction_calibration';

notify pgrst, 'reload schema';
