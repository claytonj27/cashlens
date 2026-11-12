-- 0019: let a run record that it was simulated, and that it cost nothing (milestone 10).
--
-- 0018 allowed the providers that existed when it was written. Showing the interface working
-- before any model has been paid for needs one more: an adapter that answers from the fact
-- sheet itself, so the four validity layers run against a real answer on real figures.
--
-- Two values, both about being able to say what happened rather than having to imply it:
--
--   * provider 'simulated' — so a simulated run is distinguishable in the reviewer view and in
--     any later query, instead of borrowing the name of a provider that was never called. Its
--     mode is always 'replay', because the adapter reports itself as not live.
--
--   * cost_source 'simulated' — so a zero cost is recorded as genuinely nothing rather than as
--     a price-table lookup that never happened. 'unknown' would be wrong in the other
--     direction: the cost is known, and it is zero.
--
-- Nothing is dropped and no existing row changes: both checks are replaced by wider ones.

alter table public.ai_runs drop constraint ai_runs_provider_check;
alter table public.ai_runs add constraint ai_runs_provider_check
  check (provider in ('openai', 'openrouter', 'replay', 'scripted', 'simulated'));

alter table public.ai_runs drop constraint ai_runs_cost_source_check;
alter table public.ai_runs add constraint ai_runs_cost_source_check
  check (cost_source in ('price_table', 'provider', 'reserved_estimate', 'unknown', 'simulated'));

comment on column public.ai_runs.cost_source is
  'Where the cost figure came from. "simulated" means no call was made and nothing was spent; "unknown" means a call was made whose price this deployment cannot resolve. They are never the same thing.';

notify pgrst, 'reload schema';
