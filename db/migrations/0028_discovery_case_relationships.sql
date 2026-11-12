-- 0028: the authored relationships between calibration cases.
--
-- The authored calibration library carries two kinds of relationship the case table of 0027
-- could not hold. A controlled pair whose difference is the pattern-context prose rather than
-- one typed column: the loader's column-by-column proof (0027, and app/discovery/seed.py) is the
-- right thing to keep, so such a variant records the claim instead — which case it varies and
-- which feature the author changed — and the loader proves only that the baseline exists in the
-- same family. And a progressive reveal: the same row asked a second time with one added fact,
-- stored as a second question whose case names its original and must equal it in every column
-- but `context`, which the loader proves.
--
-- Four nullable columns on the existing case table and one index predicate. No new table, no
-- new grant, no policy: the columns are question-bank data, never served to a respondent (the
-- API selects its case fields by name), and they exist so that later analysis can ask "which
-- single feature moved a reading" and "did the added fact change the reading, and in which
-- field" by joining rows that already exist.

alter table public.discovery_transaction_cases
  -- A variant whose baseline it cannot inherit from. Deferrable so a load, which is one
  -- transaction, may write the two rows in either order and a family may be removed in one
  -- statement; `no action` rather than `restrict` because only the former honours deferral, and
  -- either way a baseline cannot vanish from under its variants without the failure being loud.
  add column baseline_question_id uuid
    references public.discovery_transaction_cases (question_id)
    on delete no action deferrable initially deferred,
  -- The feature the author changed, in the author's words. Free text: it is grouped by, never
  -- proved, and the typed `changed_feature` stays the proved one.
  add column authored_changed_feature text
    check (authored_changed_feature is null or length(authored_changed_feature) between 1 and 60),
  -- The original a reveal re-asks.
  add column reveal_of_question_id uuid
    references public.discovery_transaction_cases (question_id)
    on delete no action deferrable initially deferred,
  -- On an original: the fact a later stage would add, kept even where no second stage is
  -- served yet. {"added_context": "..."}. Never served.
  add column reveal jsonb
    check (reveal is null or jsonb_typeof(reveal) = 'object');

-- A variant names its baseline and its feature together or not at all.
alter table public.discovery_transaction_cases
  add constraint a_variant_names_its_feature
    check ((baseline_question_id is null) = (authored_changed_feature is null));
-- A variant is proved one way or claimed the other, never both; and a case is a variant or a
-- reveal, not both.
alter table public.discovery_transaction_cases
  add constraint a_variant_is_proved_or_claimed_not_both
    check (baseline_question_id is null or changed_feature is null);
alter table public.discovery_transaction_cases
  add constraint a_case_is_a_variant_or_a_reveal_not_both
    check (baseline_question_id is null or reveal_of_question_id is null);

-- At most one baseline per family, where a baseline is now a case that is neither a typed
-- variant, nor a claimed variant, nor a reveal.
drop index public.discovery_transaction_cases_one_baseline;
create unique index discovery_transaction_cases_one_baseline
  on public.discovery_transaction_cases (case_family)
  where changed_feature is null
    and baseline_question_id is null
    and reveal_of_question_id is null;

create index discovery_transaction_cases_by_baseline
  on public.discovery_transaction_cases (baseline_question_id)
  where baseline_question_id is not null;
create index discovery_transaction_cases_by_original
  on public.discovery_transaction_cases (reveal_of_question_id)
  where reveal_of_question_id is not null;

comment on column public.discovery_transaction_cases.baseline_question_id is
  'For a variant whose difference is prose rather than one typed column: the baseline it varies. Recorded, not proved; the typed changed_feature is the proved kind. Never served.';
comment on column public.discovery_transaction_cases.authored_changed_feature is
  'The feature the author changed, in the author''s words, for a variant that names a baseline. Grouped by in analysis. Never served.';
comment on column public.discovery_transaction_cases.reveal_of_question_id is
  'For a progressive reveal''s second stage: the original this case re-asks with one added fact. The loader proves the two rows match in every column but context. Never served.';
comment on column public.discovery_transaction_cases.reveal is
  'On an original: the fact a second stage adds, {"added_context": "..."}. Kept even where no second stage is served. Never served.';

notify pgrst, 'reload schema';
