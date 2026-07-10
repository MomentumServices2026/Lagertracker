"""Dinghy storage — separate Supabase tables from main inventory."""

import os
from datetime import datetime

SCHEMA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sql", "supabase_dinghy_setup.sql")
_schema_ready = False


def ensure_dinghy_schema(conn):
    global _schema_ready
    if _schema_ready:
        return
    if not os.path.exists(SCHEMA_FILE):
        raise RuntimeError("Missing supabase_dinghy_setup.sql")
    with open(SCHEMA_FILE, "r", encoding="utf-8") as f:
        sql = f.read()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.autocommit = False
    _schema_ready = True


def generate_next_dinghy_sku(conn):
    ensure_dinghy_schema(conn)
    with conn.cursor() as cur:
        cur.execute("SELECT sku FROM dinghy_items WHERE sku LIKE 'DG%'")
        max_num = 0
        for (sku,) in cur.fetchall():
            suffix = sku[2:]
            if suffix.isdigit():
                max_num = max(max_num, int(suffix))
    return f"DG{max_num + 1:04d}"


def list_dinghy_products(conn):
    ensure_dinghy_schema(conn)
    with conn.cursor() as cur:
        cur.execute("SELECT name FROM dinghy_sections ORDER BY name")
        sections = [r[0] for r in cur.fetchall()] or ["General"]
        cur.execute("""
            SELECT sku, name, brand, stock, min_stock, status, COALESCE(group_name, 'General')
            FROM dinghy_items
            ORDER BY COALESCE(group_name, 'General'), name
        """)
        products = [
            {
                "sku": r[0], "name": r[1], "brand": r[2], "stock": r[3],
                "min_stock": r[4], "status": r[5], "group_name": r[6],
            }
            for r in cur.fetchall()
        ]
    return {"products": products, "sections": sections}


def adjust_dinghy_stock(conn, sku, change, reason="Mobile Web"):
    ensure_dinghy_schema(conn)
    with conn.cursor() as cur:
        cur.execute("SELECT stock FROM dinghy_items WHERE sku=%s", (sku,))
        if not cur.fetchone():
            return None
        cur.execute("UPDATE dinghy_items SET stock = stock + %s WHERE sku=%s", (change, sku))
        cur.execute(
            "INSERT INTO dinghy_movements (sku, change, reason, customer, date) VALUES (%s,%s,%s,%s,%s)",
            (sku, change, reason, "", datetime.now()),
        )
        cur.execute("SELECT stock FROM dinghy_items WHERE sku=%s", (sku,))
        new_stock = cur.fetchone()[0]
    conn.commit()
    return new_stock
