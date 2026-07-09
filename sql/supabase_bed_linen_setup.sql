-- Bed Linen storage (separate from main inventory products)
-- Run this once in Supabase SQL Editor. Does not touch products/sections/movements.

select now();

create table if not exists public.linen_sections (
  name text primary key
);

create table if not exists public.linen_items (
  sku text primary key,
  name text not null default '',
  brand text not null default '',
  stock integer not null default 0,
  min_stock integer not null default 0,
  status text not null default 'Active',
  group_name text not null default 'General'
);

create table if not exists public.linen_movements (
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
    select 1 from pg_constraint where conname = 'linen_items_group_name_fk'
  ) then
    alter table public.linen_items
      add constraint linen_items_group_name_fk
      foreign key (group_name) references public.linen_sections(name)
      on update cascade on delete restrict;
  end if;
end $$;

do $$
begin
  if not exists (
    select 1 from pg_constraint where conname = 'linen_movements_sku_fk'
  ) then
    alter table public.linen_movements
      add constraint linen_movements_sku_fk
      foreign key (sku) references public.linen_items(sku)
      on update cascade on delete cascade;
  end if;
end $$;

create index if not exists idx_linen_items_group_name on public.linen_items(group_name);
create index if not exists idx_linen_items_name on public.linen_items(name);
create index if not exists idx_linen_movements_sku on public.linen_movements(sku);
create index if not exists idx_linen_movements_date on public.linen_movements(date);

insert into public.linen_sections(name)
values ('General')
on conflict (name) do nothing;

select table_name
from information_schema.tables
where table_schema = 'public'
  and table_name in ('linen_sections', 'linen_items', 'linen_movements')
order by table_name;
