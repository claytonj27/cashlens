-- 0003: restrict the demo-mutation bypass to the database operator connection.
-- Migrations and the seed loader connect directly as the postgres role. The
-- API's routine credential (service_role reached through the authenticator
-- login) must not be able to lift demo immutability, even if it sets the
-- session flag, so the guard now requires the flag AND an operator session.

create or replace function public.guard_demo_rows() returns trigger
language plpgsql as $$
declare
  v_mode public.data_mode;
begin
  if current_setting('cashlens.allow_demo_mutation', true) = 'on'
     and session_user = 'postgres' and current_user = 'postgres' then
    if tg_op = 'DELETE' then return old; else return new; end if;
  end if;
  if tg_table_name = 'businesses' then
    if tg_op = 'INSERT' then v_mode := new.data_mode;
    elsif tg_op = 'UPDATE' then v_mode := case when old.data_mode = 'demo' or new.data_mode = 'demo' then 'demo' else 'private' end;
    else v_mode := old.data_mode; end if;
  else
    if tg_op = 'DELETE' then
      select b.data_mode into v_mode from public.businesses b where b.id = old.business_id;
    else
      select b.data_mode into v_mode from public.businesses b where b.id = new.business_id;
    end if;
  end if;
  if v_mode = 'demo' then
    raise exception 'demo data is immutable (% on %)', tg_op, tg_table_name using errcode = 'insufficient_privilege';
  end if;
  if tg_op = 'DELETE' then return old; else return new; end if;
end $$;

comment on function public.guard_demo_rows() is
  'Rejects writes to demo rows unless cashlens.allow_demo_mutation is on for a direct postgres operator session.';
