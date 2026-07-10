-- Lager Tracker — run this ENTIRE file in Supabase SQL Editor (one click Run).
-- If it fails, use part1_tables.sql then part2_constraints.sql instead.

-- MAIN INVENTORY TABLES
create table if not exists public.sections (
  name text primary key
);

create table if not exists public.products (
  sku text primary key,
  name text not null default '',
  brand text not null default '',
  stock integer not null default 0,
  min_stock integer not null default 0,
  status text not null default 'Active',
  group_name text not null default 'General'
);

create table if not exists public.movements (
  id bigint generated always as identity primary key,
  sku text not null,
  change integer not null,
  reason text not null default 'Manual',
  customer text not null default '',
  date timestamptz not null default now()
);

create table if not exists public.locations (
  id bigint generated always as identity primary key,
  sku text not null,
  location text not null,
  quantity integer not null default 0
);

-- JIT FORECAST TABLES
create table if not exists public.forecast_params (
  sku_id text primary key,
  best_alpha double precision default 0.3,
  anomaly_days integer default 0,
  last_tuned_at timestamptz default now()
);

create table if not exists public.forecast_log (
  id bigint generated always as identity primary key,
  sku_id text,
  forecast_date date,
  predicted_velocity double precision,
  projected_stock_30d double precision,
  reorder_point double precision,
  needs_reorder boolean,
  confidence_pct integer,
  created_at timestamptz default now()
);

create table if not exists public.forecast_accuracy (
  id bigint generated always as identity primary key,
  sku_id text,
  forecast_date date,
  predicted_velocity double precision,
  actual_demand double precision,
  accuracy_pct double precision,
  created_at timestamptz default now()
);

-- BED LINEN TABLES
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

-- FOREIGN KEYS (safe to re-run)
alter table public.products drop constraint if exists products_group_name_fk;
alter table public.products
  add constraint products_group_name_fk
  foreign key (group_name) references public.sections(name)
  on update cascade on delete restrict;

alter table public.movements drop constraint if exists movements_sku_fk;
alter table public.movements
  add constraint movements_sku_fk
  foreign key (sku) references public.products(sku)
  on update cascade on delete cascade;

alter table public.locations drop constraint if exists locations_sku_fk;
alter table public.locations
  add constraint locations_sku_fk
  foreign key (sku) references public.products(sku)
  on update cascade on delete cascade;

alter table public.linen_items drop constraint if exists linen_items_group_name_fk;
alter table public.linen_items
  add constraint linen_items_group_name_fk
  foreign key (group_name) references public.linen_sections(name)
  on update cascade on delete restrict;

alter table public.linen_movements drop constraint if exists linen_movements_sku_fk;
alter table public.linen_movements
  add constraint linen_movements_sku_fk
  foreign key (sku) references public.linen_items(sku)
  on update cascade on delete cascade;

-- INDEXES
create index if not exists idx_products_group_name on public.products(group_name);
create index if not exists idx_products_name on public.products(name);
create index if not exists idx_products_brand on public.products(brand);
create index if not exists idx_movements_sku on public.movements(sku);
create index if not exists idx_movements_date on public.movements(date);
create index if not exists idx_movements_reason on public.movements(reason);
create index if not exists idx_locations_sku on public.locations(sku);
create index if not exists idx_locations_location on public.locations(location);
create index if not exists idx_forecast_log_sku_date on public.forecast_log(sku_id, forecast_date);
create index if not exists idx_forecast_log_created on public.forecast_log(created_at);
create index if not exists idx_forecast_accuracy_sku_date on public.forecast_accuracy(sku_id, forecast_date);
create index if not exists idx_linen_items_group_name on public.linen_items(group_name);
create index if not exists idx_linen_items_name on public.linen_items(name);
create index if not exists idx_linen_movements_sku on public.linen_movements(sku);
create index if not exists idx_linen_movements_date on public.linen_movements(date);

-- DEFAULT DATA
insert into public.sections(name) values ('General') on conflict (name) do nothing;
insert into public.linen_sections(name) values ('General') on conflict (name) do nothing;

-- DISABLE ROW LEVEL SECURITY (direct DB access from app)
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
