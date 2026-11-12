-- 0008: per-lease tokens for import jobs (milestone 6 review fix).
-- finish_import_job matched a job by id and state only, so a worker whose
-- lease had expired could close a newer lease that another worker had
-- acquired for the same job. Every lease now carries a token; only the
-- holder of the current token can finish it, and a stale finisher is a
-- no-op that the API logs.

alter table public.import_jobs add column lease_token uuid;

drop function public.acquire_import_job(uuid, public.import_job_kind, text, integer);
drop function public.finish_import_job(uuid, public.import_job_state, text);

create function public.acquire_import_job(
  p_import_id uuid, p_kind public.import_job_kind, p_idempotency_key text, p_lease_seconds integer default 180
) returns table (job_id uuid, state public.import_job_state, attempts integer, lease_token uuid)
language plpgsql security definer set search_path = public, pg_temp as $$
#variable_conflict use_column
declare
  v_job public.import_jobs%rowtype;
begin
  perform pg_advisory_xact_lock(hashtext('import_job:' || p_idempotency_key));
  select * into v_job from public.import_jobs j where j.idempotency_key = p_idempotency_key;
  if v_job.id is null then
    insert into public.import_jobs (import_id, kind, idempotency_key, lease_expires_at, lease_token)
      values (p_import_id, p_kind, p_idempotency_key, now() + make_interval(secs => p_lease_seconds), gen_random_uuid())
      returning * into v_job;
  elsif v_job.state = 'succeeded' then
    return query select v_job.id, v_job.state, v_job.attempts, null::uuid; return;
  elsif v_job.state = 'leased' and v_job.lease_expires_at > now() then
    raise exception 'job_in_progress' using errcode = 'lock_not_available';
  elsif v_job.attempts >= v_job.max_attempts then
    raise exception 'attempts_exhausted' using errcode = 'program_limit_exceeded';
  else
    update public.import_jobs j
      set state = 'leased', attempts = j.attempts + 1, finished_at = null, safe_error = null,
          lease_expires_at = now() + make_interval(secs => p_lease_seconds), lease_token = gen_random_uuid()
      where j.id = v_job.id returning * into v_job;
  end if;
  return query select v_job.id, v_job.state, v_job.attempts, v_job.lease_token;
end $$;

create function public.finish_import_job(
  p_job_id uuid, p_lease_token uuid, p_state public.import_job_state, p_safe_error text default null
) returns boolean language plpgsql security definer set search_path = public, pg_temp as $$
declare
  v_updated integer;
begin
  update public.import_jobs
    set state = p_state, finished_at = now(), safe_error = left(p_safe_error, 300), lease_token = null
    where id = p_job_id and state = 'leased' and lease_token = p_lease_token;
  get diagnostics v_updated = row_count;
  return v_updated = 1;
end $$;

comment on function public.finish_import_job(uuid, uuid, public.import_job_state, text) is
  'Closes the lease identified by its token. Returns false, changing nothing, when the lease was re-acquired by someone else or already closed.';

revoke all on function public.acquire_import_job(uuid, public.import_job_kind, text, integer) from public, anon, authenticated;
revoke all on function public.finish_import_job(uuid, uuid, public.import_job_state, text) from public, anon, authenticated;
grant execute on function public.acquire_import_job(uuid, public.import_job_kind, text, integer) to service_role;
grant execute on function public.finish_import_job(uuid, uuid, public.import_job_state, text) to service_role;
