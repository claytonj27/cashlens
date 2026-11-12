-- 0022: record how a run was routed, when routing was a decision at all (milestone 10).
--
-- The direct provider answers with the model it was asked for. A router does not promise that:
-- one model slug can be served by several upstream providers at different prices, chosen per
-- request. `model_requested` and `model_used` already carry the two model ids, but neither can
-- say which upstream served the call, nor what the routing was told to refuse.
--
-- Without somewhere to put that, the honest options were to drop it or to imply a pinned
-- backend the response never confirmed. This column is the third option: store what was sent
-- (fallbacks, parameter requirement, data collection, price ceiling) and whatever the answer
-- disclosed about who served it — including, explicitly, that it disclosed nothing.
--
-- Null for every existing row and for every direct call, which is correct rather than missing:
-- a call to a provider with one backend was not routed.

alter table public.ai_runs add column if not exists routing jsonb;

comment on column public.ai_runs.routing is
  'How a routed call was directed, for providers where that is a per-request decision. Holds the provider preferences sent (allow_fallbacks, require_parameters, data_collection, max_price) and the upstream the response named, which may be null when the provider did not name one. Null for direct providers, which are not routed.';

notify pgrst, 'reload schema';
