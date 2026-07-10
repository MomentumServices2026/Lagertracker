#!/usr/bin/env python3
"""Copy inventory data from old Supabase project to new one."""

import getpass
import json
import os
import sys
from urllib.parse import unquote, urlparse

import psycopg

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_FILE = os.path.join(SCRIPT_DIR, "app_db_config.json")
DEFAULT_NEW_HOST = "db.shupvtztgpqkrbdmuskd.supabase.co"

TABLES_IN_ORDER = [
    ("sections", ["name"]),
    ("products", ["sku", "name", "brand", "stock", "min_stock", "status", "group_name"]),
    ("movements", ["id", "sku", "change", "reason", "customer", "date"]),
    ("locations", ["id", "sku", "location", "quantity"]),
    ("forecast_params", ["sku_id", "best_alpha", "anomaly_days", "last_tuned_at"]),
    ("forecast_log", [
        "id", "sku_id", "forecast_date", "predicted_velocity", "projected_stock_30d",
        "reorder_point", "needs_reorder", "confidence_pct", "created_at",
    ]),
    ("forecast_accuracy", [
        "id", "sku_id", "forecast_date", "predicted_velocity", "actual_demand",
        "accuracy_pct", "created_at",
    ]),
    ("linen_sections", ["name"]),
    ("linen_items", ["sku", "name", "brand", "stock", "min_stock", "status", "group_name"]),
    ("linen_movements", ["id", "sku", "change", "reason", "customer", "date"]),
]

SEQUENCE_TABLES = {"movements", "locations", "forecast_log", "forecast_accuracy", "linen_movements"}


DEFAULT_OLD_HOST = "db.kctxqmrnglnxvjdvmvtr.supabase.co"


def load_source_config():
    if os.environ.get("SUPABASE_OLD_URL"):
        return parse_connection_url(os.environ["SUPABASE_OLD_URL"])
    if os.environ.get("SUPABASE_OLD_HOST"):
        return {
            "host": os.environ["SUPABASE_OLD_HOST"],
            "port": os.environ.get("SUPABASE_OLD_PORT", "5432"),
            "dbname": os.environ.get("SUPABASE_OLD_NAME", "postgres"),
            "user": os.environ.get("SUPABASE_OLD_USER", "postgres"),
            "password": os.environ.get("SUPABASE_OLD_PASSWORD", ""),
        }
    with open(CONFIG_FILE, encoding="utf-8") as f:
        cfg = json.load(f)
    if cfg.get("host") == DEFAULT_NEW_HOST and os.environ.get("SUPABASE_OLD_PASSWORD"):
        return {
            "host": DEFAULT_OLD_HOST,
            "port": "5432",
            "dbname": "postgres",
            "user": "postgres",
            "password": os.environ["SUPABASE_OLD_PASSWORD"],
        }
    return cfg


def parse_connection_url(url):
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("postgresql", "postgres"):
        raise SystemExit("SUPABASE_NEW_URL must start with postgresql://")
    password = unquote(parsed.password or "")
    if not password:
        raise SystemExit("No password found in SUPABASE_NEW_URL.")
    return {
        "host": parsed.hostname,
        "port": str(parsed.port or 5432),
        "dbname": (parsed.path or "/postgres").lstrip("/") or "postgres",
        "user": unquote(parsed.username or "postgres"),
        "password": password,
    }


def load_dest_config():
    url = os.environ.get("SUPABASE_NEW_URL", "").strip()
    if url:
        return parse_connection_url(url)

    host = os.environ.get("SUPABASE_NEW_HOST", DEFAULT_NEW_HOST)
    password = os.environ.get("SUPABASE_NEW_PASSWORD", "").strip()
    if not password:
        print("Enter the NEW database password from Supabase → Connect → Direct.")
        print("(This is NOT your API/publishable key. Reset it in Supabase if unsure.)")
        password = getpass.getpass("New Supabase database password: ").strip()
    if not password:
        raise SystemExit("Password required. Set SUPABASE_NEW_URL or SUPABASE_NEW_PASSWORD.")
    if password.startswith("sb_publishable_") or password.startswith("eyJ"):
        raise SystemExit(
            "That looks like an API key, not the database password.\n"
            "In Supabase: Connect → Direct → Reset database password."
        )
    return {
        "host": host,
        "port": os.environ.get("SUPABASE_NEW_PORT", "5432"),
        "dbname": os.environ.get("SUPABASE_NEW_NAME", "postgres"),
        "user": os.environ.get("SUPABASE_NEW_USER", "postgres"),
        "password": password,
    }


def connect(cfg, label):
    try:
        return psycopg.connect(
            host=cfg["host"],
            port=int(cfg.get("port", 5432)),
            dbname=cfg.get("dbname", "postgres"),
            user=cfg.get("user", "postgres"),
            password=cfg["password"],
            sslmode="require",
            connect_timeout=30,
        )
    except psycopg.OperationalError as exc:
        msg = str(exc)
        if "password authentication failed" in msg:
            raise SystemExit(
                f"\n{label} login failed — wrong database password.\n\n"
                "Fix:\n"
                "  1. Supabase → Connect → Direct → Reset database password\n"
                "  2. Use that new password (NOT the sb_publishable_ API key)\n"
                "  3. Re-run this script\n\n"
                "Tip: paste the full connection string:\n"
                "  SUPABASE_NEW_URL='postgresql://postgres:PASSWORD@db.shupvtztgpqkrbdmuskd.supabase.co:5432/postgres' \\\n"
                "  python3 scripts/migrate_supabase_data.py\n"
            ) from exc
        raise


def copy_table(src, dest, table, columns):
    col_list = ", ".join(columns)
    has_identity_id = table in SEQUENCE_TABLES and columns[0] == "id"
    override = " OVERRIDING SYSTEM VALUE" if has_identity_id else ""
    with src.cursor() as sc, dest.cursor() as dc:
        sc.execute(f"SELECT {col_list} FROM {table}")
        rows = sc.fetchall()
        if not rows:
            print(f"  {table}: 0 rows (skip)")
            return 0
        dc.execute(f"DELETE FROM {table}")
        placeholders = ", ".join(["%s"] * len(columns))
        dc.executemany(
            f"INSERT INTO {table} ({col_list}){override} VALUES ({placeholders})",
            rows,
        )
        if has_identity_id:
            dc.execute(
                f"SELECT setval(pg_get_serial_sequence(%s, 'id'), COALESCE((SELECT MAX(id) FROM {table}), 1), (SELECT COUNT(*) > 0 FROM {table}))",
                (table,),
            )
    dest.commit()
    print(f"  {table}: {len(rows)} rows copied")
    return len(rows)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        dest_cfg = load_dest_config()
        print(f"Testing connection to {dest_cfg['host']} as {dest_cfg['user']}...")
        conn = connect(dest_cfg, "Destination")
        with conn.cursor() as cur:
            cur.execute("select count(*) from products")
            print(f"Connected OK. products table has {cur.fetchone()[0]} rows.")
        conn.close()
        return

    src_cfg = load_source_config()
    dest_cfg = load_dest_config()
    print(f"Source: {src_cfg['host']}")
    print(f"Dest:   {dest_cfg['host']} (user: {dest_cfg['user']})")
    print("Copying...")

    src = connect(src_cfg, "Source")
    dest = connect(dest_cfg, "Destination")
    try:
        total = 0
        for table, columns in TABLES_IN_ORDER:
            total += copy_table(src, dest, table, columns)
        print(f"Done. {total} total rows copied.")
    finally:
        src.close()
        dest.close()


if __name__ == "__main__":
    main()
