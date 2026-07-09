# Lager Tracker (Momentum Services Inventory)

A unified inventory management app with a mobile-friendly web interface and an optional Mac desktop client. Both connect to the same **Supabase (PostgreSQL)** database.

## What's in this repo

| Component | Purpose | Deployed to Vercel? |
|-----------|---------|---------------------|
| `web_app.py` + `api/` | Mobile web UI + REST API (Flask) | Yes |
| `inventory_app.py` | Mac desktop app (Tkinter) | No — run locally |
| `sql/` | Supabase schema scripts | Reference only |

## Deploy to Vercel

### 1. Push to GitHub

```bash
git init
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/YOUR_USER/lager-tracker.git
git push -u origin main
```

**Never commit** `app_db_config.json`, `.web_session_secret`, or `certs/` — they are in `.gitignore`.

### 2. Connect Vercel

1. Go to [vercel.com](https://vercel.com) → **Add New Project** → import your GitHub repo.
2. Vercel auto-detects Python via `api/index.py` and `requirements.txt`.
3. Add these **Environment Variables** in the Vercel project settings:

| Variable | Required | Description |
|----------|----------|-------------|
| `SUPABASE_DB_HOST` | Yes | e.g. `db.xxxx.supabase.co` |
| `SUPABASE_DB_PASSWORD` | Yes | Supabase database password |
| `SUPABASE_DB_USER` | No | Default: `postgres` |
| `SUPABASE_DB_NAME` | No | Default: `postgres` |
| `SUPABASE_DB_PORT` | No | Default: `5432` |
| `SESSION_SECRET` | Yes | Random string for session cookies |
| `WEB_PASSCODE` | No | 4-digit lock screen code (default: `0170`) |

4. Deploy. Your app will be at `https://your-project.vercel.app`.

### 3. Supabase setup

Run these SQL scripts in the Supabase SQL editor (in order):

1. `sql/supabase_setup.sql` — main inventory tables
2. `sql/supabase_bed_linen_setup.sql` — bed linen module
3. `sql/forecast_tables_migration.sql` — JIT forecast tables (if not already in setup)

See `sql/README.md` for details.

## Local development

### Web app (same UI as Vercel)

**Option A — config file:**

```bash
cp app_db_config.example.json app_db_config.json
# Edit app_db_config.json with your Supabase credentials

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 web_app.py
```

Open `http://127.0.0.1:8080` (or `https://` if LAN certs exist).

**Option B — environment variables:**

```bash
cp .env.example .env
# Edit .env, then:
export $(grep -v '^#' .env | xargs)
python3 web_app.py
```

**Option C — background server (Mac, auto-restart):**

```bash
./start_web_background.sh
```

### Desktop app (Mac only)

```bash
pip install -r requirements.txt
python3 inventory_app.py
```

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
┌─────────────────┐     ┌──────────────────┐
│  Vercel         │     │  Local Mac       │
│  api/index.py   │     │  web_app.py      │
│  (serverless)   │     │  (dev server)    │
└────────┬────────┘     └────────┬─────────┘
         │                       │
         └───────────┬───────────┘
                     ▼
            ┌────────────────┐
            │    Supabase    │
            │   PostgreSQL   │
            └────────────────┘
```

The web UI is a single-page app embedded in `web_app.py` — identical on Vercel and local dev.

## Features

- Stock management with sections, search, low-stock alerts
- Bed linen storage (separate inventory)
- JIT self-learning forecast engine
- Analytics dashboards with Chart.js
- PDF export (inventory + AI analytics reports)
- 4-digit passcode lock screen
- Mobile-optimized bottom navigation

Desktop-only extras: CSV import, email reports, drag-and-drop sections.

## Dependencies

See `requirements.txt`:

- `flask` — web framework
- `psycopg[binary]` — Supabase/PostgreSQL
- `reportlab` — PDF reports
- `matplotlib` — desktop app charts only
