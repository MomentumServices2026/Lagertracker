select now();

create table if not exists public.sections (
  name text primary key,
  sort_order integer
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

do $$
begin
  if not exists (
    select 1 from pg_constraint where conname = 'products_group_name_fk'
  ) then
    alter table public.products
      add constraint products_group_name_fk
      foreign key (group_name) references public.sections(name)
      on update cascade on delete restrict;
  end if;
end $$;

do $$
begin
  if not exists (
    select 1 from pg_constraint where conname = 'movements_sku_fk'
  ) then
    alter table public.movements
      add constraint movements_sku_fk
      foreign key (sku) references public.products(sku)
      on update cascade on delete cascade;
  end if;
end $$;

do $$
begin
  if not exists (
    select 1 from pg_constraint where conname = 'locations_sku_fk'
  ) then
    alter table public.locations
      add constraint locations_sku_fk
      foreign key (sku) references public.products(sku)
      on update cascade on delete cascade;
  end if;
end $$;

create index if not exists idx_products_group_name on public.products(group_name);
create index if not exists idx_products_name on public.products(name);
create index if not exists idx_products_brand on public.products(brand);
create index if not exists idx_movements_sku on public.movements(sku);
create index if not exists idx_movements_date on public.movements(date);
create index if not exists idx_movements_reason on public.movements(reason);
create index if not exists idx_locations_sku on public.locations(sku);
create index if not exists idx_locations_location on public.locations(location);

insert into public.sections(name)
values ('General')
on conflict (name) do nothing;

-- Self-learning JIT forecast tables
create table if not exists public.forecast_params (
  sku_id         text primary key,
  best_alpha     double precision default 0.3,
  anomaly_days   integer default 0,
  last_tuned_at  timestamptz default now()
);

create table if not exists public.forecast_log (
  id                   bigint generated always as identity primary key,
  sku_id               text,
  forecast_date        date,
  predicted_velocity   double precision,
  projected_stock_30d  double precision,
  reorder_point        double precision,
  needs_reorder        boolean,
  confidence_pct       integer,
  created_at           timestamptz default now()
);

create table if not exists public.forecast_accuracy (
  id                   bigint generated always as identity primary key,
  sku_id               text,
  forecast_date        date,
  predicted_velocity   double precision,
  actual_demand        double precision,
  accuracy_pct         double precision,
  created_at           timestamptz default now()
);

create index if not exists idx_forecast_log_sku_date on public.forecast_log(sku_id, forecast_date);
create index if not exists idx_forecast_log_created on public.forecast_log(created_at);
create index if not exists idx_forecast_accuracy_sku_date on public.forecast_accuracy(sku_id, forecast_date);

select table_name
from information_schema.tables
where table_schema = 'public'
  and table_name in ('sections', 'products', 'movements', 'locations',
                     'forecast_params', 'forecast_log', 'forecast_accuracy')
order by table_name;
