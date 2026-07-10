-- Dinghy storage (separate from main inventory products)
-- Run this once in Supabase SQL Editor. Does not touch products/sections/movements.

select now();

create table if not exists public.dinghy_sections (
  name text primary key
);

create table if not exists public.dinghy_items (
  sku text primary key,
  name text not null default '',
  brand text not null default '',
  stock integer not null default 0,
  min_stock integer not null default 0,
  status text not null default 'Active',
  group_name text not null default 'General'
);

create table if not exists public.dinghy_movements (
  id bigint generated always as identity primary key,
  sku text not null,
  change integer not null,
  reason text not null default 'Manual',
  customer text not null default '',
  date timestamptz not null default now()
);

do $$
begin
  if not exists (
    select 1 from pg_constraint where conname = 'dinghy_items_group_name_fk'
  ) then
    alter table public.dinghy_items
      add constraint dinghy_items_group_name_fk
      foreign key (group_name) references public.dinghy_sections(name)
      on update cascade on delete restrict;
  end if;
end $$;

do $$
begin
  if not exists (
    select 1 from pg_constraint where conname = 'dinghy_movements_sku_fk'
  ) then
    alter table public.dinghy_movements
      add constraint dinghy_movements_sku_fk
      foreign key (sku) references public.dinghy_items(sku)
      on update cascade on delete cascade;
  end if;
end $$;

create index if not exists idx_dinghy_items_group_name on public.dinghy_items(group_name);
create index if not exists idx_dinghy_items_name on public.dinghy_items(name);
create index if not exists idx_dinghy_movements_sku on public.dinghy_movements(sku);
create index if not exists idx_dinghy_movements_date on public.dinghy_movements(date);

insert into public.dinghy_sections(name)
values ('General')
on conflict (name) do nothing;

alter table public.dinghy_sections disable row level security;
alter table public.dinghy_items disable row level security;
alter table public.dinghy_movements disable row level security;

select table_name
from information_schema.tables
where table_schema = 'public'
  and table_name in ('dinghy_sections', 'dinghy_items', 'dinghy_movements')
order by table_name;
