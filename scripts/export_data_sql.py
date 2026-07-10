#!/usr/bin/env python3
"""Export all inventory data from current app_db_config.json to sql/import_data.sql."""

import json
import os

import psycopg

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_FILE = os.path.join(SCRIPT_DIR, "app_db_config.json")
OUT_FILE = os.path.join(SCRIPT_DIR, "sql", "import_data.sql")

TABLES = [
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
IDENTITY_TABLES = {"movements", "locations", "forecast_log", "forecast_accuracy", "linen_movements"}


def esc(val):
    if val is None:
        return "NULL"
    if isinstance(val, bool):
        return "true" if val else "false"
    if hasattr(val, "isoformat"):
        return "'" + val.isoformat().replace("'", "''") + "'"
    if isinstance(val, (int, float)):
        return str(val)
    return "'" + str(val).replace("'", "''") + "'"


def main():
    with open(CONFIG_FILE, encoding="utf-8") as f:
        cfg = json.load(f)

    conn = psycopg.connect(
        host=cfg["host"],
        port=int(cfg["port"]),
        dbname=cfg["dbname"],
        user=cfg["user"],
        password=cfg["password"],
        sslmode="require",
    )

    lines = [
        "-- Exported inventory data",
        "-- Run in NEW Supabase project SQL Editor",
        "BEGIN;",
        "",
    ]
    insert_count = 0

    with conn.cursor() as cur:
        for table, cols in TABLES:
            cur.execute(f"SELECT {', '.join(cols)} FROM {table}")
            rows = cur.fetchall()
            if not rows:
                continue
            lines.append(f"-- {table}: {len(rows)} rows")
            lines.append(f"DELETE FROM {table};")
            col_str = ", ".join(cols)
            override = " OVERRIDING SYSTEM VALUE" if table in IDENTITY_TABLES else ""
            for row in rows:
                vals = ", ".join(esc(v) for v in row)
                lines.append(f"INSERT INTO {table} ({col_str}){override} VALUES ({vals});")
                insert_count += 1
            lines.append("")

    lines.append("COMMIT;")
    conn.close()

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"Exported {insert_count} rows to {OUT_FILE}")


if __name__ == "__main__":
    main()
