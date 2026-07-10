# Lager Tracker (Momentum Services Inventory)

A unified inventory management app with a mobile-friendly web interface and an optional Mac desktop client. Both connect to the same **Supabase (PostgreSQL)** database.

## What's in this repo

| Component | Purpose | Deployed to Vercel? |
|-----------|---------|---------------------|
| `web_app.py` + `api/` | Mobile web UI + REST API (Flask) | Yes |
| `inventory_app.py` | Mac desktop app (Tkinter) | No — run locally |
| `sql/` | Supabase schema scripts | Reference only |

**Live app:** https://lagertracker.vercel.app

## Deploy to Vercel

### 1. Push to GitHub

```bash
git add .
git commit -m "Your changes"
git push origin main
```

Vercel redeploys automatically from `main`.

**Never commit** `app_db_config.json`, `.web_session_secret`, or `.env` — they are in `.gitignore`.

### 2. Environment Variables (Vercel project settings)

| Variable | Required | Description |
|----------|----------|-------------|
| `SUPABASE_DB_HOST` | Yes | e.g. `db.xxxx.supabase.co` |
| `SUPABASE_DB_PASSWORD` | Yes | Supabase database password |
| `SUPABASE_DB_USER` | No | Default: `postgres` |
| `SUPABASE_DB_NAME` | No | Default: `postgres` |
| `SUPABASE_DB_PORT` | No | Default: `5432` |
| `SESSION_SECRET` | Recommended | Random string for session cookies |
| `WEB_PASSCODE` | No | 4-digit lock screen code (default: `0170`) |

### 3. Supabase setup

Run these SQL scripts in the Supabase SQL editor (in order):

1. `sql/supabase_setup.sql` — main inventory tables
2. `sql/supabase_bed_linen_setup.sql` — bed linen module
3. `sql/supabase_dinghy_setup.sql` — dinghy module
4. `sql/forecast_tables_migration.sql` — JIT forecast tables (if not already in setup)

See `sql/README.md` for details.

## Local development (optional)

To test UI changes before pushing to GitHub:

```bash
cp app_db_config.example.json app_db_config.json
# Edit app_db_config.json with your Supabase credentials

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
flask --app web_app run --port 8080
```

Open `http://127.0.0.1:8080` on your Mac only. Production use is via Vercel.

### Desktop app (Mac only)

```bash
pip install -r requirements.txt
python3 inventory_app.py
```

Or double-click `Launch Momentum Services Inventory 2.command`.

Requires `app_db_config.json` or `SUPABASE_DB_*` environment variables.

For analytics charts, also install desktop extras:

```bash
pip install -r requirements-desktop.txt
```

## Architecture

```
Browser / iPhone
      │
      ▼
┌─────────────────┐
│  Vercel         │
│  api/index.py   │
│  (serverless)   │
└────────┬────────┘
         │
         ▼
┌────────────────┐
│    Supabase    │
│   PostgreSQL   │
└────────────────┘
```

The web UI is a single-page app embedded in `web_app.py`.

## Features

- Stock management with sections, search, low-stock alerts
- Bed linen and dinghy storage (separate inventories)
- JIT self-learning forecast engine
- Analytics dashboards with Chart.js
- PDF export (inventory + AI analytics reports)
- 4-digit passcode lock screen
- Mobile-optimized bottom navigation
- Drag-and-drop section reordering (More screen)

Desktop-only extras: CSV import, email reports.

## Dependencies

See `requirements.txt`:

- `flask` — web framework
- `psycopg[binary]` — Supabase/PostgreSQL
- `reportlab` — PDF reports
- `matplotlib` — desktop app charts only
