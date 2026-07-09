# Supabase SQL setup

**Important:** Paste only the SQL file contents into the Supabase SQL Editor — not this README.

## Run in this order

1. `supabase_setup.sql` — main inventory tables + forecast tables
2. `supabase_bed_linen_setup.sql` — bed linen module
3. `forecast_tables_migration.sql` — only if forecast tables are missing from step 1

## Steps

1. Open [Supabase](https://supabase.com) → your project → **SQL Editor**
2. Open the `.sql` file locally, copy all contents, paste into the editor
3. Click **Run**

If a query hangs, run `select now();` first, refresh the editor, and retry.

## Optional: disable RLS for open access

```sql
alter table public.sections disable row level security;
alter table public.products disable row level security;
alter table public.movements disable row level security;
alter table public.locations disable row level security;
```
