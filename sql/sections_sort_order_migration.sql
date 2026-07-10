-- Add custom section ordering for the More screen drag-and-drop UI.
-- Safe to run multiple times.

alter table public.sections add column if not exists sort_order integer;

update public.sections as s
set sort_order = ranked.rn
from (
  select name, row_number() over (order by name) * 10 as rn
  from public.sections
) as ranked
where s.name = ranked.name
  and s.sort_order is null;
