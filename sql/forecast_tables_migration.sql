-- Run once on Supabase to enable self-learning JIT forecast persistence.
-- Safe to re-run (IF NOT EXISTS).

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
