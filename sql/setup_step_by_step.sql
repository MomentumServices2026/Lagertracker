-- Run ONE block at a time in Supabase SQL Editor.
-- Copy a single block, paste, click Run. Wait for Success before the next.

-- BLOCK 1 (test — run this first)
select 1 as ok;

-- BLOCK 2
create table if not exists public.sections (name text primary key);

-- BLOCK 3
create table if not exists public.products (
  sku text primary key,
  name text not null default '',
  brand text not null default '',
  stock integer not null default 0,
  min_stock integer not null default 0,
  status text not null default 'Active',
  group_name text not null default 'General'
);

-- BLOCK 4
create table if not exists public.movements (
  id bigserial primary key,
  sku text not null,
  change integer not null,
  reason text not null default 'Manual',
  customer text not null default '',
  date timestamptz not null default now()
);

-- BLOCK 5
create table if not exists public.locations (
  id bigserial primary key,
  sku text not null,
  location text not null,
  quantity integer not null default 0
);

-- BLOCK 6
create table if not exists public.forecast_params (
  sku_id text primary key,
  best_alpha double precision default 0.3,
  anomaly_days integer default 0,
  last_tuned_at timestamptz default now()
);

-- BLOCK 7
create table if not exists public.forecast_log (
  id bigserial primary key,
  sku_id text,
  forecast_date date,
  predicted_velocity double precision,
  projected_stock_30d double precision,
  reorder_point double precision,
  needs_reorder boolean,
  confidence_pct integer,
  created_at timestamptz default now()
);

-- BLOCK 8
create table if not exists public.forecast_accuracy (
  id bigserial primary key,
  sku_id text,
  forecast_date date,
  predicted_velocity double precision,
  actual_demand double precision,
  accuracy_pct double precision,
  created_at timestamptz default now()
);

-- BLOCK 9
create table if not exists public.linen_sections (name text primary key);

-- BLOCK 10
create table if not exists public.linen_items (
  sku text primary key,
  name text not null default '',
  brand text not null default '',
  stock integer not null default 0,
  min_stock integer not null default 0,
  status text not null default 'Active',
  group_name text not null default 'General'
);

-- BLOCK 11
create table if not exists public.linen_movements (
  id bigserial primary key,
  sku text not null,
  change integer not null,
  reason text not null default 'Manual',
  customer text not null default '',
  date timestamptz not null default now()
);

-- BLOCK 12
insert into public.sections(name) values ('General') on conflict (name) do nothing;

-- BLOCK 13
insert into public.linen_sections(name) values ('General') on conflict (name) do nothing;

-- BLOCK 14
alter table public.sections disable row level security;
alter table public.products disable row level security;
alter table public.movements disable row level security;
alter table public.locations disable row level security;
alter table public.forecast_params disable row level security;
alter table public.forecast_log disable row level security;
alter table public.forecast_accuracy disable row level security;
alter table public.linen_sections disable row level security;
alter table public.linen_items disable row level security;
alter table public.linen_movements disable row level security;

-- BLOCK 15 (verify)
select table_name from information_schema.tables where table_schema = 'public' order by table_name;
