-- 0020: a cumulative budget for the milestone, not just a daily one (milestone 10).
--
-- 0018 gave `reserve_ai_usage` two rolling 24-hour caps: one per identity, one for the whole
-- deployment. Those bound a day. They cannot bound a milestone: $2 a day for a fortnight is
-- $28, and nothing in the function would have noticed. The owner approved $25 for the whole of
-- M10 across every provider, every repair, every grader and every rerun, so there has to be a
-- limit that counts everything ever spent rather than everything spent today.
--
-- `p_total_cap_micro` counts every reservation row, with no time window at all. Settled rows
-- count what they cost; unsettled rows that have not expired count their estimate, exactly as
-- the daily caps do, so a call in flight and a call whose usage never came back both still
-- occupy budget. Simulated and replayed runs hold zero, so they cost nothing against it.
--
-- The budget is therefore never "reset between tasks": the only way to raise it is to change
-- the setting deliberately, which is a decision someone makes rather than a side effect of the
-- clock rolling over.
--
-- The old signature is dropped rather than overloaded, so there is exactly one reservation
-- function and no caller can accidentally reach a version without the total cap.

drop function public.reserve_ai_usage(uuid, text, uuid, text, bigint, bigint, bigint, integer, integer);

create function public.reserve_ai_usage(
  p_reservation_id uuid,
  p_identity_kind text,
  p_identity_id uuid,
  p_capability text,
  p_estimated_cost_micro bigint,
  p_identity_daily_cap_micro bigint,
  p_global_daily_cap_micro bigint,
  p_total_cap_micro bigint,
  p_max_in_flight integer,
  p_expires_seconds integer
) returns table (
  allowed boolean, reason text, identity_spend_micro bigint,
  global_spend_micro bigint, total_spend_micro bigint, in_flight integer
)
language plpgsql security definer set search_path = public, pg_temp as $$
#variable_conflict use_column
declare
  v_identity bigint;
  v_global bigint;
  v_total bigint;
  v_in_flight integer;
  v_estimate bigint := greatest(coalesce(p_estimated_cost_micro, 0), 0);
begin
  if p_identity_kind not in ('user', 'visitor') then
    raise exception 'invalid identity' using errcode = 'invalid_parameter_value';
  end if;

  -- Fixed order, always: the global lock first, then the identity.
  perform pg_advisory_xact_lock(hashtext('ai_usage:global'));
  perform pg_advisory_xact_lock(hashtext('ai_usage:' || p_identity_kind || ':' || p_identity_id::text));

  select coalesce(sum(
    case when r.settled_at is not null then coalesce(r.cost_micro, 0)
         when r.expires_at > now() then r.estimated_cost_micro
         else 0 end), 0)
    into v_identity
    from public.usage_reservations r
    where r.identity_kind = p_identity_kind and r.identity_id = p_identity_id
      and r.created_at > now() - interval '24 hours';

  select coalesce(sum(
    case when r.settled_at is not null then coalesce(r.cost_micro, 0)
         when r.expires_at > now() then r.estimated_cost_micro
         else 0 end), 0)
    into v_global
    from public.usage_reservations r
    where r.created_at > now() - interval '24 hours';

  -- No window. Everything this deployment has ever held or spent.
  select coalesce(sum(
    case when r.settled_at is not null then coalesce(r.cost_micro, 0)
         when r.expires_at > now() then r.estimated_cost_micro
         else 0 end), 0)
    into v_total
    from public.usage_reservations r;

  select count(*) into v_in_flight
    from public.usage_reservations r
    where r.identity_kind = p_identity_kind and r.identity_id = p_identity_id
      and r.settled_at is null and r.expires_at > now();

  if v_in_flight >= greatest(coalesce(p_max_in_flight, 1), 1) then
    return query select false, 'in_flight'::text, v_identity, v_global, v_total, v_in_flight; return;
  end if;
  -- The milestone total is checked before the daily caps: running out of the approved budget
  -- altogether is a different answer from being busy today, and the caller says so.
  if v_total + v_estimate > p_total_cap_micro then
    return query select false, 'total_cap'::text, v_identity, v_global, v_total, v_in_flight; return;
  end if;
  if v_identity + v_estimate > p_identity_daily_cap_micro then
    return query select false, 'identity_cap'::text, v_identity, v_global, v_total, v_in_flight; return;
  end if;
  if v_global + v_estimate > p_global_daily_cap_micro then
    return query select false, 'global_cap'::text, v_identity, v_global, v_total, v_in_flight; return;
  end if;

  insert into public.usage_reservations (
    id, identity_kind, identity_id, capability, estimated_cost_micro, expires_at
  ) values (
    p_reservation_id, p_identity_kind, p_identity_id, p_capability, v_estimate,
    now() + make_interval(secs => greatest(coalesce(p_expires_seconds, 600), 1))
  );
  return query select true, 'ok'::text, v_identity, v_global, v_total, v_in_flight;
end $$;

revoke all on function public.reserve_ai_usage(uuid, text, uuid, text, bigint, bigint, bigint, bigint, integer, integer) from public, anon, authenticated;
grant execute on function public.reserve_ai_usage(uuid, text, uuid, text, bigint, bigint, bigint, bigint, integer, integer) to service_role;

comment on function public.reserve_ai_usage(uuid, text, uuid, text, bigint, bigint, bigint, bigint, integer, integer) is
  'Counts spend and inserts the reservation in one transaction under a global then an identity advisory lock. Three limits: a rolling day per identity, a rolling day for the deployment, and a cumulative total for the milestone that has no time window and so cannot be escaped by waiting.';

-- What has been spent against the milestone, for an operator to read without doing arithmetic.
create function public.ai_milestone_spend()
returns table (
  settled_micro bigint, held_micro bigint, total_micro bigint,
  live_runs integer, simulated_runs integer
)
language sql stable security definer set search_path = public, pg_temp as $$
  select
    coalesce(sum(case when r.settled_at is not null then coalesce(r.cost_micro, 0) else 0 end), 0),
    coalesce(sum(case when r.settled_at is null and r.expires_at > now() then r.estimated_cost_micro else 0 end), 0),
    coalesce(sum(
      case when r.settled_at is not null then coalesce(r.cost_micro, 0)
           when r.expires_at > now() then r.estimated_cost_micro
           else 0 end), 0),
    (select count(*)::integer from public.ai_runs a where a.mode = 'live'),
    (select count(*)::integer from public.ai_runs a where a.mode <> 'live')
  from public.usage_reservations r;
$$;

revoke all on function public.ai_milestone_spend() from public, anon, authenticated;
grant execute on function public.ai_milestone_spend() to service_role;

notify pgrst, 'reload schema';
