import tkinter as tk
from tkinter import messagebox, ttk, simpledialog, filedialog
from datetime import datetime, timedelta
from collections import defaultdict
import math
import csv
import json
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
import smtplib
from email.message import EmailMessage
import os
import sys

APP_CONFIG_FILE = "app_db_config.json"

APP_NAME = "Momentum Services Inventory Software"
COLOR_BG = "#eef3f8"
COLOR_SURFACE = "#ffffff"
COLOR_SURFACE_ALT = "#f3f8fc"
COLOR_PRIMARY = "#0c3b5d"
COLOR_PRIMARY_ACCENT = "#146b8a"
COLOR_TEXT = "#102a43"
COLOR_TEXT_MUTED = "#486581"
COLOR_BORDER = "#d9e2ec"

# ---------------- DATABASE ----------------
try:
    import psycopg
except Exception:
    psycopg = None

class CursorProxy:
    def __init__(self, inner_cursor, is_postgres=False):
        self.inner = inner_cursor
        self.is_postgres = is_postgres

    def execute(self, query, params=()):
        q = query
        p = params if params is not None else ()
        if self.is_postgres:
            if q.strip() == "INSERT OR IGNORE INTO sections(name) VALUES (?)":
                q = "INSERT INTO sections(name) VALUES (%s) ON CONFLICT (name) DO NOTHING"
            elif q.strip() == "INSERT OR IGNORE INTO sections(name) VALUES ('General')":
                q = "INSERT INTO sections(name) VALUES ('General') ON CONFLICT (name) DO NOTHING"
            elif q.strip() == "INSERT OR IGNORE INTO sections(name) SELECT DISTINCT COALESCE(group_name, 'General') FROM products":
                q = "INSERT INTO sections(name) SELECT DISTINCT COALESCE(group_name, 'General') FROM products ON CONFLICT (name) DO NOTHING"
            q = q.replace("?", "%s")
            if p == () or p == []:
                return self.inner.execute(q)
        return self.inner.execute(q, p)

    def fetchone(self):
        return self.inner.fetchone()

    def fetchall(self):
        return self.inner.fetchall()

def _load_or_prompt_db_config():
    cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), APP_CONFIG_FILE)
    cfg = {}
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}

    host = os.environ.get("SUPABASE_DB_HOST", cfg.get("host", "")).strip()
    port = os.environ.get("SUPABASE_DB_PORT", str(cfg.get("port", "5432"))).strip()
    dbname = os.environ.get("SUPABASE_DB_NAME", cfg.get("dbname", "postgres")).strip()
    user = os.environ.get("SUPABASE_DB_USER", cfg.get("user", "postgres")).strip()
    password = os.environ.get("SUPABASE_DB_PASSWORD", cfg.get("password", "")).strip()
    if host and user and password and dbname and port:
        return {"mode": "supabase", "host": host, "port": port, "dbname": dbname, "user": user, "password": password}

    prompt = tk.Tk()
    prompt.withdraw()
    messagebox.showinfo(
        APP_NAME,
        "Supabase connection is required.\nPlease enter your Supabase database details.",
        parent=prompt,
    )

    host = simpledialog.askstring(APP_NAME, "Supabase DB host (db.<project>.supabase.co):", parent=prompt) or ""
    port = simpledialog.askstring(APP_NAME, "Port:", initialvalue="5432", parent=prompt) or "5432"
    dbname = simpledialog.askstring(APP_NAME, "Database name:", initialvalue="postgres", parent=prompt) or "postgres"
    user = simpledialog.askstring(APP_NAME, "Database user:", initialvalue="postgres", parent=prompt) or "postgres"
    password = simpledialog.askstring(APP_NAME, "Database password:", show="*", parent=prompt) or ""
    prompt.destroy()

    if not host.strip() or not password.strip():
        messagebox.showerror(APP_NAME, "Supabase credentials are required. App will now close.")
        sys.exit(1)

    saved = {
        "mode": "supabase",
        "host": host.strip(),
        "port": port.strip() or "5432",
        "dbname": dbname.strip() or "postgres",
        "user": user.strip() or "postgres",
        "password": password.strip(),
    }
    try:
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(saved, f, indent=2)
    except Exception:
        pass
    return saved

def _init_database():
    config = _load_or_prompt_db_config()
    if psycopg is None:
        messagebox.showerror(APP_NAME, "Supabase mode requires 'psycopg'.\nInstall via:\npython3 -m pip install psycopg[binary]")
        sys.exit(1)
    try:
        conn = psycopg.connect(
            host=config["host"],
            port=int(config["port"]),
            dbname=config["dbname"],
            user=config["user"],
            password=config["password"],
            sslmode="require",
        )
    except Exception as e:
        messagebox.showerror(APP_NAME, f"Could not connect to Supabase:\n{e}\n\nApp requires Supabase and will now close.")
        sys.exit(1)

    conn.autocommit = False
    cur = CursorProxy(conn.cursor(), is_postgres=True)
    # Keep startup fast/reliable: avoid heavy remote DDL here.
    # Database schema should be prepared via `sql/supabase_setup.sql`.
    try:
        cur.execute("SELECT 1 FROM products LIMIT 1")
        cur.execute("SELECT 1 FROM sections LIMIT 1")
    except Exception as e:
        messagebox.showerror(
            APP_NAME,
            "Supabase schema is not ready or inaccessible.\n"
            "Run `sql/supabase_setup.sql` in Supabase SQL editor first.\n\n"
            f"Details: {e}"
        )
        sys.exit(1)
    return conn, cur

conn, cur = _init_database()
SUPABASE_CONNECTED = True
SUPABASE_DEVICE_COUNT = None
AUTO_REFRESH_RUNNING = False

def check_supabase_connection():
    global SUPABASE_CONNECTED, SUPABASE_DEVICE_COUNT
    try:
        cur.execute("SELECT 1")
        _ = cur.fetchone()
        SUPABASE_CONNECTED = True
        try:
            # Approximate "devices" by active distinct client addresses for this DB.
            cur.execute("""
                SELECT COUNT(DISTINCT client_addr)
                FROM pg_stat_activity
                WHERE datname = current_database()
                  AND client_addr IS NOT NULL
            """)
            row = cur.fetchone()
            SUPABASE_DEVICE_COUNT = int(row[0]) if row and row[0] is not None else 0
        except Exception:
            SUPABASE_DEVICE_COUNT = None
    except Exception:
        SUPABASE_CONNECTED = False
        SUPABASE_DEVICE_COUNT = None
    return SUPABASE_CONNECTED

def sync_from_supabase():
    """Lightweight manual/auto sync without restarting the app."""
    global AUTO_REFRESH_RUNNING
    if AUTO_REFRESH_RUNNING:
        return
    AUTO_REFRESH_RUNNING = True
    try:
        refresh()
        update_mark_old_button_state()
        if "drag_hint_var" in globals():
            drag_hint_var.set(f"Last synced: {datetime.now().strftime('%H:%M:%S')}")
    except Exception as e:
        if "drag_hint_var" in globals():
            drag_hint_var.set(f"Sync failed: {e}")
    finally:
        AUTO_REFRESH_RUNNING = False

def auto_sync_loop():
    # Soft periodic sync for multi-computer real-time feel.
    sync_from_supabase()
    root.after(10000, auto_sync_loop)

# ---------------- FUNCTIONS ----------------

drag_source_iid = None
drag_target_iid = None

def get_sections():
    cur.execute("SELECT name FROM sections ORDER BY name")
    rows = [r[0] for r in cur.fetchall()]
    return rows or ["General"]

def refresh_section_selector():
    if "section_box" not in globals():
        return
    values = get_sections()
    section_box["values"] = values
    if section_var.get() not in values:
        section_var.set("General")

def add_section():
    name = simpledialog.askstring("New Section", "Section name:", parent=root)
    if not name:
        return
    section_name = name.strip()
    if not section_name:
        return
    cur.execute("INSERT OR IGNORE INTO sections(name) VALUES (?)", (section_name,))
    conn.commit()
    refresh_section_selector()

def delete_section():
    section_name = section_var.get().strip()
    if not section_name:
        return
    if section_name == "General":
        messagebox.showerror("Error", "General section cannot be deleted.")
        return

    if not messagebox.askyesno(
        "Delete Section",
        f"Delete section '{section_name}'?\nItems in this section will be moved to 'General'."
    ):
        return

    cur.execute("UPDATE products SET group_name='General' WHERE COALESCE(group_name, 'General')=?", (section_name,))
    cur.execute("DELETE FROM sections WHERE name=?", (section_name,))
    conn.commit()
    refresh_section_selector()
    refresh()

def generate_next_sku():
    cur.execute("SELECT sku FROM products WHERE sku LIKE 'MS%'")
    max_num = 0
    for (sku,) in cur.fetchall():
        suffix = sku[2:]
        if suffix.isdigit():
            max_num = max(max_num, int(suffix))
    return f"MS{max_num + 1:04d}"

def selected_sku():
    selected = tree.selection()
    if not selected:
        messagebox.showerror("Error", "Please select a product first.")
        return None
    selected_iid = selected[0]
    if tree.parent(selected_iid) == "":
        messagebox.showerror("Error", "Please select an item row, not a section.")
        return None
    values = tree.item(selected_iid, "values")
    return values[0]

def add_product():
    sku = sku_entry.get().strip().upper()
    if not sku:
        sku = generate_next_sku()

    cur.execute("SELECT sku FROM products WHERE sku=?", (sku,))
    if cur.fetchone():
        messagebox.showerror("Error", "SKU already exists!")
        return

    group_name = section_var.get().strip() or "General"
    cur.execute("INSERT OR IGNORE INTO sections(name) VALUES (?)", (group_name,))

    try:
        stock_val = int(stock_entry.get().strip())
        min_val = int(min_entry.get().strip())
    except ValueError:
        messagebox.showerror("Error", "Stock and Min Stock must be whole numbers.")
        return

    cur.execute("INSERT INTO products VALUES (?,?,?,?,?,?,?)",
                (sku, name_entry.get(), brand_entry.get(),
                 stock_val, min_val, "Active", group_name))
    conn.commit()
    # Clear Add Product form after successful insert.
    sku_entry.configure(state="normal")
    sku_entry.delete(0, tk.END)
    name_entry.delete(0, tk.END)
    brand_entry.delete(0, tk.END)
    stock_entry.delete(0, tk.END)
    min_entry.delete(0, tk.END)
    sku_entry.insert(0, generate_next_sku())
    sku_entry.configure(state="readonly")
    refresh()

def update_stock(sku, change, reason="Manual"):
    cur.execute("UPDATE products SET stock = stock + ? WHERE sku=?", (change, sku))
    cur.execute("INSERT INTO movements (sku, change, reason, customer, date) VALUES (?,?,?,?,?)",
                (sku, change, reason, "", datetime.now()))
    conn.commit()
    refresh()

def mark_old(sku):
    cur.execute("UPDATE products SET status='Old' WHERE sku=?", (sku,))
    conn.commit()
    refresh()

def mark_active(sku):
    cur.execute("UPDATE products SET status='Active' WHERE sku=?", (sku,))
    conn.commit()
    refresh()

def mark_damaged(sku):
    update_stock(sku, -1, "Damaged")

def delete_product(sku):
    if messagebox.askyesno("Confirm", "Delete item?"):
        cur.execute("DELETE FROM products WHERE sku=?", (sku,))
        conn.commit()
        refresh()

def import_csv_products():
    file_path = filedialog.askopenfilename(
        title="Import Product CSV",
        filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]
    )
    if not file_path:
        return

    imported = 0
    skipped_existing = 0
    skipped_invalid = 0
    skipped_error = 0

    cur.execute("SELECT sku FROM products")
    existing_skus = {row[0] for row in cur.fetchall()}

    try:
        with open(file_path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames:
                messagebox.showerror("Import Error", "CSV has no header row.")
                return

            for row in reader:
                sku_raw = (row.get("sku") or row.get("SKU") or "").strip().upper()
                if not sku_raw.startswith("MS"):
                    skipped_invalid += 1
                    continue

                if sku_raw in existing_skus:
                    skipped_existing += 1
                    continue

                try:
                    name = (row.get("name") or row.get("Name") or "").strip()
                    brand = (row.get("brand") or row.get("Brand") or "").strip()
                    stock = int((row.get("stock") or row.get("Stock") or "0").strip() or "0")
                    min_stock = int((row.get("min_stock") or row.get("Min Stock") or row.get("min") or "0").strip() or "0")
                    status = (row.get("status") or row.get("Status") or "Active").strip() or "Active"
                    group_name = (row.get("group_name") or row.get("Group") or row.get("section") or "General").strip() or "General"

                    cur.execute("INSERT OR IGNORE INTO sections(name) VALUES (?)", (group_name,))
                    cur.execute(
                        "INSERT INTO products VALUES (?,?,?,?,?,?,?)",
                        (sku_raw, name, brand, stock, min_stock, status, group_name),
                    )
                    existing_skus.add(sku_raw)
                    imported += 1
                except Exception:
                    skipped_error += 1

        conn.commit()
        refresh_section_selector()
        refresh()
        sku_entry.configure(state="normal")
        sku_entry.delete(0, tk.END)
        sku_entry.insert(0, generate_next_sku())
        sku_entry.configure(state="readonly")
        messagebox.showinfo(
            "Import Complete",
            (
                f"Imported: {imported}\n"
                f"Skipped (already exists): {skipped_existing}\n"
                f"Skipped (invalid SKU): {skipped_invalid}\n"
                f"Skipped (row error): {skipped_error}"
            ),
        )
    except Exception as e:
        messagebox.showerror("Import Error", f"Could not import CSV:\n{e}")

def refresh():
    saved_sku = None
    sel = tree.selection()
    if sel:
        vals = tree.item(sel[0], "values")
        if vals:
            saved_sku = vals[0]

    for row in tree.get_children():
        tree.delete(row)

    section_nodes = {}
    for section in get_sections():
        section_nodes[section] = tree.insert("", "end", text=section, open=True, values=("", "", "", "", "", "", ""))

    cur.execute("""
        SELECT sku, name, brand, stock, min_stock, status, COALESCE(group_name, 'General')
        FROM products
        ORDER BY COALESCE(group_name, 'General'), name
    """)
    for sku, name, brand, stock, min_stock, status, group_name in cur.fetchall():
        low = "⚠ LOW" if stock <= min_stock else ""
        parent = section_nodes.get(group_name)
        if parent is None:
            parent = tree.insert("", "end", text=group_name, open=True, values=("", "", "", "", "", "", ""))
            section_nodes[group_name] = parent
        iid = tree.insert(parent, "end", values=(sku, name, brand, stock, min_stock, status, low))
        if saved_sku is not None and sku == saved_sku:
            tree.selection_set(iid)
            tree.focus(iid)
            tree.see(iid)

def search():
    q = search_entry.get()
    for row in tree.get_children():
        tree.delete(row)

    results_parent = tree.insert("", "end", text="Search Results", open=True, values=("", "", "", "", "", "", ""))
    cur.execute("""
        SELECT sku, name, brand, stock, min_stock, status, COALESCE(group_name, 'General')
        FROM products
        WHERE sku LIKE ? OR name LIKE ? OR brand LIKE ? OR COALESCE(group_name, 'General') LIKE ?
        ORDER BY COALESCE(group_name, 'General'), name
    """, (f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%"))
    found = False
    for sku, name, brand, stock, min_stock, status, group_name in cur.fetchall():
        low = "⚠ LOW" if stock <= min_stock else ""
        tree.insert(results_parent, "end", values=(sku, name, brand, stock, min_stock, status, low), text=group_name)
        found = True
    if not found:
        tree.insert(results_parent, "end", values=("-", "No items found", "-", "-", "-", "-", ""))

def on_tree_drag_start(event):
    global drag_source_iid, drag_target_iid
    row = tree.identify_row(event.y)
    drag_source_iid = row if row and tree.parent(row) != "" else None
    drag_target_iid = None
    if drag_source_iid:
        tree.item(drag_source_iid, tags=("drag_source",))
        if "drag_hint_var" in globals():
            source_vals = tree.item(drag_source_iid, "values")
            item_name = source_vals[1] if len(source_vals) > 1 else source_vals[0]
            drag_hint_var.set(f"Dragging: {item_name} ... move over a section and release.")
    else:
        if "drag_hint_var" in globals():
            drag_hint_var.set("")

def on_tree_drag_motion(event):
    global drag_target_iid
    if not drag_source_iid:
        return

    target_iid = tree.identify_row(event.y)
    if not target_iid:
        return

    if drag_target_iid and drag_target_iid != target_iid:
        existing_tags = tuple(t for t in tree.item(drag_target_iid, "tags") if t != "drop_target")
        tree.item(drag_target_iid, tags=existing_tags)

    drag_target_iid = target_iid
    existing_tags = tree.item(target_iid, "tags")
    if "drop_target" not in existing_tags:
        tree.item(target_iid, tags=tuple(existing_tags) + ("drop_target",))

    if "drag_hint_var" in globals():
        if tree.parent(target_iid) == "":
            target_section = tree.item(target_iid, "text")
        else:
            target_section = tree.item(tree.parent(target_iid), "text")
        drag_hint_var.set(f"Drop target: {target_section}")

def on_tree_drop(event):
    global drag_source_iid, drag_target_iid
    if not drag_source_iid:
        return

    target_iid = tree.identify_row(event.y)
    if not target_iid or target_iid == drag_source_iid:
        existing_tags = tuple(t for t in tree.item(drag_source_iid, "tags") if t != "drag_source")
        tree.item(drag_source_iid, tags=existing_tags)
        if drag_target_iid:
            existing_tags = tuple(t for t in tree.item(drag_target_iid, "tags") if t != "drop_target")
            tree.item(drag_target_iid, tags=existing_tags)
        drag_source_iid = None
        drag_target_iid = None
        if "drag_hint_var" in globals():
            drag_hint_var.set("")
        return

    src_vals = tree.item(drag_source_iid, "values")
    src_iid = drag_source_iid
    tgt_iid = target_iid
    drag_source_iid = None
    drag_target_iid = None
    if not src_vals:
        if "drag_hint_var" in globals():
            drag_hint_var.set("")
        return

    source_sku = src_vals[0]
    if tree.parent(tgt_iid) == "":
        target_group = tree.item(tgt_iid, "text")
    else:
        target_group = tree.item(tree.parent(tgt_iid), "text")
    if not target_group:
        target_group = "General"
    cur.execute("UPDATE products SET group_name=? WHERE sku=?", (target_group, source_sku))
    conn.commit()

    # Clear transient drag visuals.
    if tree.exists(src_iid):
        existing_tags = tuple(t for t in tree.item(src_iid, "tags") if t != "drag_source")
        tree.item(src_iid, tags=existing_tags)
    if tree.exists(tgt_iid):
        existing_tags = tuple(t for t in tree.item(tgt_iid, "tags") if t != "drop_target")
        tree.item(tgt_iid, tags=existing_tags)
    if "drag_hint_var" in globals():
        drag_hint_var.set(f"Moved item to section: {target_group}")

    refresh()

def restart_application():
    """Restart process so edits to this file are loaded (dev workflow)."""
    try:
        conn.close()
    except Exception:
        pass
    script = os.path.abspath(__file__)
    os.chdir(os.path.dirname(script))
    os.execv(sys.executable, [sys.executable, script])

# ---------------- EDIT PRODUCT WINDOW ----------------

def open_edit_window():
    sku = selected_sku()
    if not sku:
        return

    cur.execute("SELECT * FROM products WHERE sku=?", (sku,))
    product = cur.fetchone()
    if not product:
        messagebox.showerror("Error", "Product not found.")
        return

    win = tk.Toplevel(root)
    win.title(f"{APP_NAME} - Edit Product {sku}")
    win.geometry("350x320")

    tk.Label(win, text=f"Editing {sku}", font=("Helvetica", 14, "bold")).pack(pady=10)

    name_var = tk.StringVar(value=product[1])
    brand_var = tk.StringVar(value=product[2])
    stock_var = tk.StringVar(value=product[3])
    min_var = tk.StringVar(value=product[4])
    status_var = tk.StringVar(value=product[5])

    def field(label, var):
        frame = tk.Frame(win)
        frame.pack(fill="x", pady=5)
        tk.Label(frame, text=label, width=12, anchor="w").pack(side="left")
        entry = ttk.Entry(frame, textvariable=var)
        entry.pack(side="right", fill="x", expand=True)
        return entry

    field("Name", name_var)
    field("Brand", brand_var)
    field("Stock", stock_var)
    field("Min Stock", min_var)
    field("Status", status_var)

    def save_changes():
        try:
            stock_val = int(stock_var.get().strip())
            min_val = int(min_var.get().strip())
            cur.execute("""
                UPDATE products
                SET name=?, brand=?, stock=?, min_stock=?, status=?
                WHERE sku=?
            """, (name_var.get(), brand_var.get(), stock_val,
                  min_val, status_var.get(), sku))
            conn.commit()
            refresh()
            win.destroy()
        except ValueError:
            messagebox.showerror("Error", "Stock and Min Stock must be whole numbers.")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    ttk.Button(win, text="Save Changes", command=save_changes).pack(pady=15)

DEFAULT_LEAD_TIME_DAYS = 7
DEFAULT_SERVICE_Z = 1.65

def calculate_forecast_recommendations(lead_time=DEFAULT_LEAD_TIME_DAYS, service_z=DEFAULT_SERVICE_Z):
    cur.execute("SELECT sku, COALESCE(name, sku), COALESCE(stock, 0), COALESCE(min_stock, 0) FROM products")
    product_rows = cur.fetchall()
    name_map = {sku: name for sku, name, _, _ in product_rows}
    stock_map = {sku: stock for sku, _, stock, _ in product_rows}
    min_map = {sku: min_stock for sku, _, _, min_stock in product_rows}

    cur.execute("""
        SELECT sku, substr(CAST(date AS TEXT), 1, 10) AS day_key, ABS(change) AS qty
        FROM movements
        WHERE change < 0 AND date IS NOT NULL
        ORDER BY day_key
    """)
    demand_by_sku_day = defaultdict(lambda: defaultdict(float))
    for sku, day_key, qty in cur.fetchall():
        demand_by_sku_day[sku][day_key] += qty

    def percentile(values, p):
        if not values:
            return 0.0
        ordered = sorted(values)
        idx = (len(ordered) - 1) * p
        lo = int(math.floor(idx))
        hi = int(math.ceil(idx))
        if lo == hi:
            return ordered[lo]
        return ordered[lo] + (ordered[hi] - ordered[lo]) * (idx - lo)

    rows = []
    for sku, name, current, min_stock in product_rows:
        day_map = demand_by_sku_day.get(sku, {})

        # New / sparse items fallback: no demand history yet.
        if not day_map:
            forecast_daily = 0.0
            reorder_point = float(min_stock)
            suggested = max(int(math.ceil(reorder_point - current)), 0)

            if current <= 0:
                priority = "EMPTY"
            elif current <= min_stock:
                priority = "LOW"
            elif current <= reorder_point:
                priority = "SOON"
            else:
                priority = "OK"

            rows.append((priority, sku, name_map.get(sku, sku), current, min_stock, forecast_daily, reorder_point, suggested))
            continue

        parsed_days = sorted(datetime.strptime(d, "%Y-%m-%d").date() for d in day_map.keys())
        end_day = max(parsed_days[-1], datetime.now().date())
        start_day = end_day - timedelta(days=119)

        series = []
        d = start_day
        while d <= end_day:
            key = d.isoformat()
            series.append(float(day_map.get(key, 0.0)))
            d += timedelta(days=1)

        if sum(series) <= 0:
            continue

        # Outlier control: cap extreme spikes at the 95th percentile of non-zero days.
        # This avoids collapsing intermittent demand (many zero days) to zero.
        positive_series = [x for x in series if x > 0]
        if positive_series:
            p95 = percentile(positive_series, 0.95)
            clipped = [min(x, p95) if x > 0 else 0.0 for x in series]
        else:
            clipped = list(series)

        # Recency-weighted demand (EWMA).
        alpha = 0.35
        ewma = clipped[0]
        for x in clipped[1:]:
            ewma = (alpha * x) + ((1 - alpha) * ewma)

        # Short-term trend using linear regression slope on last 28 days.
        trend_window = clipped[-28:] if len(clipped) >= 28 else clipped
        n = len(trend_window)
        xs = list(range(n))
        x_mean = (n - 1) / 2 if n else 0
        y_mean = sum(trend_window) / n if n else 0
        denom = sum((x - x_mean) ** 2 for x in xs) if n > 1 else 0
        slope = (sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, trend_window)) / denom) if denom else 0.0

        # Croston-SBA branch for intermittent demand (many zero days).
        alpha_c = 0.2
        z_hat = None
        p_hat = None
        interval = 1
        for x in clipped:
            if x > 0:
                if z_hat is None:
                    z_hat = x
                    p_hat = interval
                else:
                    z_hat = z_hat + alpha_c * (x - z_hat)
                    p_hat = p_hat + alpha_c * (interval - p_hat)
                interval = 1
            else:
                interval += 1
        croston_rate = ((z_hat / p_hat) * (1 - alpha_c / 2)) if z_hat and p_hat else 0.0

        zero_ratio = clipped.count(0.0) / len(clipped)
        intermittent_weight = min(max((zero_ratio - 0.30) / 0.50, 0.0), 1.0)
        recent_mean = sum(clipped[-14:]) / min(len(clipped), 14)
        smooth_rate = (0.7 * ewma) + (0.3 * recent_mean)
        base_daily = ((1 - intermittent_weight) * smooth_rate) + (intermittent_weight * croston_rate)
        forecast_daily = max(base_daily + (slope * lead_time / 2), 0.0)

        # Hybrid volatility estimate (classical + robust MAD).
        std_daily = math.sqrt(sum((x - forecast_daily) ** 2 for x in clipped) / len(clipped))
        med = percentile(clipped, 0.50)
        mad = percentile([abs(x - med) for x in clipped], 0.50)
        robust_std = 1.4826 * mad
        demand_std = (0.6 * std_daily) + (0.4 * robust_std)

        lead_time_mean = forecast_daily * lead_time
        lead_time_std = demand_std * math.sqrt(lead_time)
        reorder_point = max(float(min_map.get(sku, 0)), lead_time_mean + (service_z * lead_time_std))
        if not math.isfinite(reorder_point):
            reorder_point = float(min_map.get(sku, 0))

        current = stock_map.get(sku, 0)
        min_stock = min_map.get(sku, 0)
        suggested = max(math.ceil(reorder_point - current), 0)
        if not math.isfinite(forecast_daily):
            forecast_daily = 0.0

        if current <= 0:
            priority = "EMPTY"
        elif current <= min_stock:
            priority = "LOW"
        elif current <= reorder_point:
            priority = "SOON"
        else:
            priority = "OK"

        rows.append((priority, sku, name_map.get(sku, sku), current, min_stock, forecast_daily, reorder_point, suggested))

    priority_order = {"EMPTY": 0, "LOW": 1, "SOON": 2, "OK": 3}
    rows.sort(key=lambda r: (priority_order.get(r[0], 9), -(r[5])))
    return rows

def show_forecast_tab():
    fwin = tk.Toplevel(root)
    fwin.title(f"{APP_NAME} - Auto Forecast")
    fwin.geometry("980x520")
    fwin.configure(bg=COLOR_BG)

    header = tk.Frame(fwin, bg=COLOR_SURFACE, padx=12, pady=10)
    header.pack(fill="x", padx=12, pady=(12, 6))
    tk.Label(
        header,
        text=f"Auto Forecast (Lead Time: {DEFAULT_LEAD_TIME_DAYS}d, Service z: {DEFAULT_SERVICE_Z})",
            bg=COLOR_SURFACE,
        font=("Helvetica", 12, "bold"),
    ).pack(side="left")
    ttk.Button(header, text="Back to Home", command=fwin.destroy).pack(side="right")

    info = tk.Label(
        fwin,
        bg=COLOR_BG,
        anchor="w",
        justify="left",
        text="Recommendations prioritize items that are Empty, Low, or below forecasted reorder point.",
    )
    info.pack(fill="x", padx=14, pady=(0, 8))

    explain_frame = tk.Frame(fwin, bg=COLOR_SURFACE, padx=12, pady=10)
    explain_frame.pack(fill="x", padx=12, pady=(0, 8))
    tk.Label(
        explain_frame,
        text="How this forecast works",
        bg=COLOR_SURFACE,
        font=("Helvetica", 11, "bold"),
        anchor="w",
    ).pack(fill="x")
    tk.Label(
        explain_frame,
        bg=COLOR_SURFACE,
        fg=COLOR_TEXT_MUTED,
        justify="left",
        anchor="w",
        text=(
            "We look at how fast each item has been going out.\n"
            "Then we estimate how much you should have on hand before new stock arrives.\n"
            "If current stock is below that safe level, we suggest how much to order.\n\n"
            "Priority guide:\n"
            "- EMPTY = no stock left\n"
            "- LOW = at or below minimum stock\n"
            "- SOON = not low yet, but likely to be soon\n\n"
            "Use this as a smart suggestion for ordering, not a final rule."
        ),
    ).pack(fill="x", pady=(4, 0))

    cols = ("Priority", "SKU", "Name", "Current", "Min", "Avg/day", "ROP", "Suggested Order")
    tree_forecast = ttk.Treeview(fwin, columns=cols, show="headings", height=16)
    for c in cols:
        tree_forecast.heading(c, text=c)
        tree_forecast.column(c, anchor="center", width=130)
    tree_forecast.pack(fill="both", expand=True, padx=12, pady=(0, 12))

    forecast_rows = [r for r in calculate_forecast_recommendations() if r[0] in ("EMPTY", "LOW", "SOON")]
    if not forecast_rows:
        tree_forecast.insert("", "end", values=("OK", "-", "-", "-", "-", "-", "-", "No urgent reorders"))
        return

    for priority, sku, name, current, min_stock, avg_daily, rop, suggested in forecast_rows:
        tree_forecast.insert("", "end", values=(
            priority,
            sku,
            name,
            current,
            min_stock,
            f"{avg_daily:.2f}",
            f"{rop:.1f}",
            suggested,
        ))

def show_security_tab():
    swin = tk.Toplevel(root)
    swin.title(f"{APP_NAME} - Security")
    swin.geometry("700x360")
    swin.configure(bg=COLOR_BG)

    header = tk.Frame(swin, bg=COLOR_SURFACE, padx=12, pady=10)
    header.pack(fill="x", padx=12, pady=(12, 6))
    tk.Label(
        header,
        text="Security Notes",
        bg=COLOR_SURFACE,
        fg=COLOR_PRIMARY,
        font=("Helvetica", 14, "bold"),
    ).pack(side="left")
    ttk.Button(header, text="Back to Home", command=swin.destroy).pack(side="right")

    card = tk.Frame(swin, bg=COLOR_SURFACE, padx=14, pady=12)
    card.pack(fill="both", expand=True, padx=12, pady=(0, 12))

    tk.Label(
        card,
        text="Supabase",
        bg=COLOR_SURFACE,
        fg=COLOR_PRIMARY,
        font=("Helvetica", 12, "bold"),
    ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))

    tk.Label(card, text="Project:", bg=COLOR_SURFACE, fg=COLOR_TEXT).grid(row=1, column=0, sticky="w", pady=4)
    tk.Label(card, text="Momentum Services Inventory", bg=COLOR_SURFACE, fg=COLOR_TEXT_MUTED).grid(row=1, column=1, sticky="w", pady=4)

    tk.Label(card, text="Database Password:", bg=COLOR_SURFACE, fg=COLOR_TEXT).grid(row=2, column=0, sticky="w", pady=4)
    _cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), APP_CONFIG_FILE)
    _pw_display = "(set via app_db_config.json or SUPABASE_DB_PASSWORD)"
    if os.path.exists(_cfg_path):
        try:
            with open(_cfg_path, "r", encoding="utf-8") as _f:
                _pw_display = json.load(_f).get("password", _pw_display)
        except Exception:
            pass
    if os.environ.get("SUPABASE_DB_PASSWORD"):
        _pw_display = os.environ["SUPABASE_DB_PASSWORD"]
    tk.Label(
        card,
        text=_pw_display,
        bg=COLOR_SURFACE,
        fg=COLOR_TEXT,
        font=("Helvetica", 11, "bold"),
    ).grid(row=2, column=1, sticky="w", pady=4)

    tk.Label(
        card,
        text=(
            "Security notes:\n"
            "- Supabase password is shown here as a permanent reference.\n"
            "- Treat this screen as internal-only operational documentation."
        ),
        bg=COLOR_SURFACE,
        fg=COLOR_TEXT_MUTED,
        font=("Helvetica", 9),
        justify="left",
        anchor="w",
    ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(14, 0))

# ---------------- PDF EXPORT ----------------

def export_pdf():
    # Save reports inside this project folder (portable across machines).
    folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Inventory Reports")
    try:
        os.makedirs(folder, exist_ok=True)
    except Exception as e:
        messagebox.showerror("PDF Error", f"Could not create report folder:\n{e}")
        return

    # Metric date filename
    date_str = datetime.now().strftime("%d-%m-%Y")
    filename = f"{date_str}.pdf"
    filepath = os.path.join(folder, filename)

    # Handle duplicates
    counter = 1
    while os.path.exists(filepath):
        filepath = os.path.join(folder, f"{date_str} ({counter}).pdf")
        counter += 1

    try:
        c = canvas.Canvas(filepath, pagesize=letter)
    except Exception as e:
        messagebox.showerror("PDF Error", f"Could not create PDF file:\n{e}")
        return

    page_w, page_h = letter
    left = 30
    right = page_w - 30
    now = datetime.now()
    header_date = now.strftime("%d %b %Y, %H:%M")
    page_no = 1

    def draw_page_shell(section_title):
        c.setFillColorRGB(0.12, 0.18, 0.30)
        c.rect(0, page_h - 70, page_w, 70, stroke=0, fill=1)
        c.setFillColorRGB(1, 1, 1)
        c.setFont("Helvetica-Bold", 18)
        c.drawString(left, page_h - 42, "Inventory Report")
        c.setFont("Helvetica", 9)
        c.drawRightString(right, page_h - 28, f"Generated: {header_date}")
        c.drawRightString(right, 20, f"Page {page_no}")
        c.setFillColorRGB(0, 0, 0)
        c.setFont("Helvetica-Bold", 12)
        c.drawString(left, page_h - 92, section_title)
        return page_h - 110

    def draw_table_header(y, cols):
        c.setFillColorRGB(0.93, 0.95, 0.98)
        c.rect(left, y - 14, right - left, 18, stroke=0, fill=1)
        c.setFillColorRGB(0, 0, 0)
        c.setFont("Helvetica-Bold", 9)
        for x, title in cols:
            c.drawString(x, y - 2, title)
        return y - 20

    recommendations = calculate_forecast_recommendations()
    forecast_map = {row[1]: row[7] for row in recommendations}

    cur.execute("""
        SELECT sku, name, brand, stock, min_stock, status, COALESCE(group_name, 'General')
        FROM products
        ORDER BY COALESCE(group_name, 'General'), name
    """)
    product_rows = cur.fetchall()

    # Cover summary section
    y = draw_page_shell("Overview")
    total_products = len(product_rows)
    low_products = sum(1 for r in product_rows if r[3] <= r[4])
    total_units = sum(int(r[3]) for r in product_rows)
    urgent_count = sum(1 for r in recommendations if r[0] in ("EMPTY", "LOW", "SOON"))

    c.setFont("Helvetica", 10)
    summary = [
        f"Total Products: {total_products}",
        f"Low Stock Items: {low_products}",
        f"Total Units in Stock: {total_units}",
        f"Urgent Reorder Recommendations: {urgent_count}",
    ]
    for line in summary:
        c.drawString(left, y, line)
        y -= 16

    y -= 8
    cols_main = [
        (35, "SKU"), (95, "Name"), (220, "Brand"), (300, "Section"),
        (380, "Stock"), (420, "Min"), (455, "Status"), (495, "Suggested Order")
    ]
    y = draw_table_header(y, cols_main)

    c.setFont("Helvetica", 8.5)
    zebra = False
    for sku, name, brand, stock, min_stock, status, section in product_rows:
        if y < 48:
            c.showPage()
            page_no += 1
            y = draw_page_shell("Inventory Lines")
            y = draw_table_header(y, cols_main)
            c.setFont("Helvetica", 8.5)
            zebra = False

        if zebra:
            c.setFillColorRGB(0.98, 0.98, 0.98)
            c.rect(left, y - 10, right - left, 14, stroke=0, fill=1)
            c.setFillColorRGB(0, 0, 0)
        zebra = not zebra

        c.drawString(35, y, str(sku)[:10])
        c.drawString(95, y, str(name)[:22])
        c.drawString(220, y, str(brand)[:14])
        c.drawString(300, y, str(section)[:13])
        c.drawRightString(412, y, str(stock))
        c.drawRightString(448, y, str(min_stock))
        c.drawString(455, y, str(status)[:7])
        c.drawRightString(562, y, str(forecast_map.get(sku, 0)))
        y -= 14

    # Forecast section
    c.showPage()
    page_no += 1
    y = draw_page_shell("JIT Forecast Recommendations")
    cols_rec = [
        (35, "Priority"), (90, "SKU"), (145, "Name"),
        (330, "Current"), (385, "Avg/day"), (445, "ROP"), (470, "Suggested Order")
    ]
    y = draw_table_header(y, cols_rec)
    c.setFont("Helvetica", 9)

    urgent_rows = [r for r in recommendations if r[0] in ("EMPTY", "LOW", "SOON")]
    if not urgent_rows:
        c.drawString(35, y, "No urgent reorder recommendations right now.")
    else:
        for priority, sku, name, current, _min_stock, avg_daily, rop, suggested in urgent_rows[:30]:
            if y < 48:
                c.showPage()
                page_no += 1
                y = draw_page_shell("JIT Forecast Recommendations")
                y = draw_table_header(y, cols_rec)
                c.setFont("Helvetica", 9)
            c.drawString(35, y, str(priority))
            c.drawString(90, y, str(sku))
            c.drawString(145, y, str(name)[:28])
            c.drawRightString(368, y, str(current))
            c.drawRightString(430, y, f"{avg_daily:.2f}")
            c.drawRightString(490, y, f"{rop:.1f}")
            c.drawRightString(562, y, str(suggested))
            y -= 14

    try:
        c.save()
        messagebox.showinfo("PDF", f"Saved:\n{filepath}")
    except Exception as e:
        messagebox.showerror("PDF Error", f"Could not save PDF:\n{e}")

# ---------------- EMAIL ----------------

def send_email():
    msg = EmailMessage()
    msg["Subject"] = "Inventory Report"
    msg["From"] = "your@email.com"
    msg["To"] = email_entry.get()

    # Always attach the latest PDF
    folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Inventory Reports")
    files = sorted(os.listdir(folder))
    if not files:
        messagebox.showerror("Error", "No PDF found.")
        return

    latest = os.path.join(folder, files[-1])

    with open(latest, "rb") as f:
        msg.add_attachment(f.read(), maintype="application", subtype="pdf", filename=os.path.basename(latest))

    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as s:
            s.starttls()
            s.login("your@email.com", "yourpassword")
            s.send_message(msg)
        messagebox.showinfo("Email", "Sent!")
    except Exception as e:
        messagebox.showerror("Error", str(e))

# ---------------- ANALYTICS ----------------

def show_analytics():
    try:
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
        from matplotlib.figure import Figure
    except ImportError:
        messagebox.showerror(
            "Missing dependency",
            "Analytics charts need matplotlib.\nInstall with:\npython3 -m pip install matplotlib",
        )
        return

    win = tk.Toplevel(root)
    win.title(f"{APP_NAME} - Analytics")
    win.geometry("1100x760")
    win.configure(bg=COLOR_BG)

    # -------- Summary KPIs --------
    cur.execute("""
        SELECT
            COUNT(*),
            COALESCE(SUM(CASE WHEN stock <= min_stock THEN 1 ELSE 0 END), 0),
            COALESCE(SUM(stock), 0),
            COALESCE(SUM(min_stock), 0)
        FROM products
    """)
    total_items, low_items, total_stock, _ = cur.fetchone()

    cur.execute("SELECT COUNT(*) FROM movements")
    movement_count = cur.fetchone()[0]

    kpi_frame = tk.Frame(win, bg=COLOR_SURFACE, padx=12, pady=10)
    kpi_frame.pack(fill="x", padx=12, pady=(12, 6))

    kpis = [
        ("Products", total_items),
        ("Low Stock", low_items),
        ("Total Units", total_stock),
        ("Movement Logs", movement_count),
    ]
    for title, value in kpis:
        card = tk.Frame(kpi_frame, bg=COLOR_SURFACE_ALT, padx=14, pady=10)
        card.pack(side="left", fill="x", expand=True, padx=5)
        tk.Label(card, text=title, bg=COLOR_SURFACE_ALT, fg=COLOR_TEXT_MUTED,
                 font=("Helvetica", 10, "bold")).pack(anchor="w")
        tk.Label(card, text=str(value), bg=COLOR_SURFACE_ALT, fg=COLOR_TEXT,
                 font=("Helvetica", 18, "bold")).pack(anchor="w")

    # -------- Controls --------
    controls = tk.Frame(win, bg=COLOR_SURFACE, padx=12, pady=8)
    controls.pack(fill="x", padx=12, pady=(0, 6))

    ttk.Button(controls, text="Back to Home", command=win.destroy).pack(side="left", padx=(0, 12))
    tk.Label(controls, text="Calendar Year:", bg=COLOR_SURFACE, fg=COLOR_TEXT).pack(side="left")
    year_var = tk.StringVar()
    year_box = ttk.Combobox(controls, textvariable=year_var, state="readonly", width=8)
    year_box.pack(side="left", padx=(8, 16))

    # -------- Chart area --------
    chart_frame = tk.Frame(win, bg=COLOR_SURFACE)
    chart_frame.pack(fill="both", expand=True, padx=12, pady=(0, 6))

    fig = Figure(figsize=(10.5, 7), dpi=100)
    ax1 = fig.add_subplot(221)
    ax2 = fig.add_subplot(222)
    ax3 = fig.add_subplot(223)
    ax4 = fig.add_subplot(224)
    chart = FigureCanvasTkAgg(fig, master=chart_frame)
    chart.get_tk_widget().pack(fill="both", expand=True)

    # -------- JIT Forecast table --------
    jit_frame = tk.Frame(win, bg=COLOR_SURFACE, padx=12, pady=8)
    jit_frame.pack(fill="x", padx=12, pady=(0, 10))
    tk.Label(
        jit_frame,
        text="JIT Reorder Forecast (top demand SKUs)",
        bg=COLOR_SURFACE,
        font=("Helvetica", 11, "bold")
    ).pack(anchor="w")

    jit_cols = ("SKU", "Name", "Avg/day", "Current", "ROP", "Suggested Order")
    jit_tree = ttk.Treeview(jit_frame, columns=jit_cols, show="headings", height=5)
    for c in jit_cols:
        jit_tree.heading(c, text=c)
        jit_tree.column(c, anchor="center", width=120)
    jit_tree.pack(fill="x", pady=(6, 0))

    # -------- Year options (calendar-style view) --------
    cur.execute("SELECT DISTINCT substr(CAST(date AS TEXT), 1, 4) FROM movements WHERE date IS NOT NULL ORDER BY 1")
    db_years = [int(y[0]) for y in cur.fetchall() if y[0] and y[0].isdigit()]
    this_year = datetime.now().year
    future_years = [this_year + i for i in range(0, 6)]
    all_years = sorted(set(db_years + future_years))
    year_box["values"] = [str(y) for y in all_years]
    year_var.set(str(this_year))

    def update_dashboard(*_):
        try:
            selected_year = int(year_var.get())
        except ValueError:
            selected_year = this_year

        lead_time = DEFAULT_LEAD_TIME_DAYS
        service_z = DEFAULT_SERVICE_Z

        # Common datasets
        stock_ok = max(total_items - low_items, 0)

        cur.execute("""
            SELECT m.sku, COALESCE(p.name, m.sku) AS item_name, COALESCE(SUM(ABS(m.change)), 0) AS moves
            FROM movements m
            LEFT JOIN products p ON p.sku = m.sku
            GROUP BY m.sku, item_name
            ORDER BY moves DESC
            LIMIT 8
        """)
        movers = cur.fetchall()
        mover_names = [row[1] for row in movers]
        mover_vals = [row[2] for row in movers]

        cur.execute("""
            SELECT substr(CAST(date AS TEXT), 1, 10) AS day_key, COALESCE(SUM(change), 0)
            FROM movements
            WHERE date IS NOT NULL
            GROUP BY day_key
            ORDER BY day_key DESC
            LIMIT 30
        """)
        trend_rows = list(reversed(cur.fetchall()))
        trend_days = [row[0] for row in trend_rows]
        trend_vals = [row[1] for row in trend_rows]

        # Yearly calendar-style monthly movement (whole year timeline)
        cur.execute("""
            SELECT CAST(substr(CAST(date AS TEXT), 6, 2) AS INTEGER) AS m, COALESCE(SUM(change), 0)
            FROM movements
            WHERE CAST(date AS TEXT) LIKE ?
            GROUP BY m
            ORDER BY m
        """, (f"{selected_year}-%",))
        month_data = {row[0]: row[1] for row in cur.fetchall() if row[0]}
        month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                       "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        month_vals = [month_data.get(i, 0) for i in range(1, 13)]

        # JIT forecast: demand and reorder point from outbound variability
        for item in jit_tree.get_children():
            jit_tree.delete(item)

        cur.execute("""
            SELECT sku, COALESCE(name, sku), COALESCE(stock, 0)
            FROM products
        """)
        current_stock = {}
        sku_to_name = {}
        for sku, name, stock in cur.fetchall():
            current_stock[sku] = stock
            sku_to_name[sku] = name

        cur.execute("""
            SELECT sku, substr(CAST(date AS TEXT), 1, 10) AS day_key, ABS(change) AS qty
            FROM movements
            WHERE change < 0 AND date IS NOT NULL
            ORDER BY day_key
        """)
        demand_by_sku_day = defaultdict(lambda: defaultdict(float))
        for sku, day_key, qty in cur.fetchall():
            demand_by_sku_day[sku][day_key] += qty

        scored = []
        for sku, day_map in demand_by_sku_day.items():
            day_values = list(day_map.values())
            if not day_values:
                continue
            avg_daily = sum(day_values) / len(day_values)
            variance = sum((x - avg_daily) ** 2 for x in day_values) / len(day_values)
            std_daily = math.sqrt(variance)

            reorder_point = (avg_daily * lead_time) + (service_z * std_daily * math.sqrt(lead_time))
            current = current_stock.get(sku, 0)
            suggested = max(math.ceil(reorder_point - current), 0)
            scored.append((sku, avg_daily, current, reorder_point, suggested))

        scored.sort(key=lambda x: x[1], reverse=True)
        for sku, avg_daily, current, rop, suggested in scored[:8]:
            jit_tree.insert("", "end", values=(
                sku,
                sku_to_name.get(sku, sku),
                f"{avg_daily:.2f}",
                current,
                f"{rop:.1f}",
                suggested,
            ))

        # Draw charts
        ax1.clear()
        ax2.clear()
        ax3.clear()
        ax4.clear()

        if total_items > 0:
            ax1.pie(
                [stock_ok, low_items],
                labels=["Healthy", "Low"],
                autopct="%1.0f%%",
                colors=["#16a34a", "#ef4444"],
                startangle=90,
            )
        else:
            ax1.text(0.5, 0.5, "No product data", ha="center", va="center")
        ax1.set_title("Stock Health")

        if mover_names:
            ax2.barh(mover_names, mover_vals, color="#3b82f6")
            ax2.invert_yaxis()
        else:
            ax2.text(0.5, 0.5, "No movement data", ha="center", va="center")
        ax2.set_title("Top Moved Items")
        ax2.set_xlabel("Total Movements")

        if trend_days:
            ax3.plot(trend_days, trend_vals, marker="o", color="#0ea5e9")
            ax3.tick_params(axis="x", rotation=35)
        else:
            ax3.text(0.5, 0.5, "No trend data", ha="center", va="center")
        ax3.set_title("Net Daily Movement (Last 30 days)")
        ax3.set_ylabel("Net Change")

        bar_colors = ["#22c55e" if v >= 0 else "#ef4444" for v in month_vals]
        ax4.bar(month_names, month_vals, color=bar_colors)
        ax4.axhline(0, color="#475569", linewidth=0.8)
        ax4.set_title(f"Order Change Calendar - {selected_year}")
        ax4.set_ylabel("Net Monthly Change")
        ax4.tick_params(axis="x", rotation=25)

        fig.tight_layout()
        chart.draw()

    year_box.bind("<<ComboboxSelected>>", update_dashboard)
    update_dashboard()

# ---------------- UI ----------------

root = tk.Tk()
root.title(APP_NAME)
root.geometry("1280x760")
root.minsize(1100, 680)
root.configure(bg=COLOR_BG)
root.option_add("*Font", "Helvetica 10")

style = ttk.Style()
style.theme_use("clam")

style.configure("Treeview",
                background=COLOR_SURFACE,
                foreground=COLOR_TEXT,
                rowheight=30,
                fieldbackground=COLOR_SURFACE,
                bordercolor=COLOR_BORDER)

style.configure("Treeview.Heading",
                background="#dbe7f3",
                foreground=COLOR_PRIMARY,
                font=("Helvetica", 11, "bold"))

style.configure("TButton",
                font=("Helvetica", 10),
                padding=7,
                background=COLOR_PRIMARY,
                foreground="#ffffff",
                borderwidth=0)
style.map("TButton",
          background=[("active", COLOR_PRIMARY_ACCENT), ("pressed", COLOR_PRIMARY_ACCENT)])
style.configure("TEntry", fieldbackground=COLOR_SURFACE, foreground=COLOR_TEXT, bordercolor=COLOR_BORDER)
style.configure("TCombobox", fieldbackground=COLOR_SURFACE, foreground=COLOR_TEXT)

# ---------------- TOP BAR ----------------
top_frame = tk.Frame(root, bg=COLOR_SURFACE, padx=14, pady=12)
top_frame.pack(fill="x")

title_wrap = tk.Frame(top_frame, bg=COLOR_SURFACE)
title_wrap.pack(side="left")
tk.Label(
    title_wrap,
    text="M",
    font=("Helvetica", 20, "bold"),
    fg="#2f6b3f",
    bg=COLOR_SURFACE
).pack(side="left")
tk.Label(
    title_wrap,
    text=APP_NAME[1:],
    font=("Helvetica", 18, "bold"),
    fg=COLOR_PRIMARY,
    bg=COLOR_SURFACE
).pack(side="left")

search_entry = ttk.Entry(top_frame, width=30)
search_entry.pack(side="right", padx=5)

ttk.Button(top_frame, text="Search", command=search).pack(side="right")
ttk.Button(top_frame, text="Refresh", command=sync_from_supabase).pack(side="right", padx=(0, 10))

status_wrap = tk.Frame(top_frame, bg=COLOR_SURFACE)
status_wrap.pack(side="right", padx=(0, 12))
status_dot = tk.Canvas(status_wrap, width=14, height=14, bg=COLOR_SURFACE, highlightthickness=0)
status_dot.pack(side="left", padx=(0, 6))
status_oval = status_dot.create_oval(2, 2, 12, 12, fill="#16a34a", outline="")
status_text = tk.Label(status_wrap, text="Supabase: Connected", bg=COLOR_SURFACE, fg=COLOR_TEXT_MUTED)
status_text.pack(side="left")

def refresh_connection_indicator():
    ok = check_supabase_connection()
    if ok:
        status_dot.itemconfig(status_oval, fill="#16a34a")
        if SUPABASE_DEVICE_COUNT is None:
            status_text.config(text="Supabase: Connected | Devices: unknown")
        else:
            status_text.config(text=f"Supabase: Connected | Devices: {SUPABASE_DEVICE_COUNT}")
    else:
        status_dot.itemconfig(status_oval, fill="#dc2626")
        status_text.config(text="Supabase: Disconnected | Devices: 0")
    root.after(10000, refresh_connection_indicator)

# ---------------- MAIN AREA ----------------
main_frame = tk.Frame(root, bg=COLOR_BG)
main_frame.pack(fill="both", expand=True, padx=10, pady=10)

# LEFT PANEL
form_frame = tk.Frame(main_frame, bg=COLOR_SURFACE, padx=16, pady=16)
form_frame.pack(side="left", fill="y")

tk.Label(form_frame, text="Add Product",
         font=("Helvetica", 14, "bold"),
         fg=COLOR_PRIMARY,
         bg=COLOR_SURFACE).grid(row=0, columnspan=2, pady=(0,10))

def styled_entry(row, label):
    tk.Label(form_frame, text=label, bg=COLOR_SURFACE, fg=COLOR_TEXT).grid(row=row, column=0, sticky="w", pady=5)
    e = ttk.Entry(form_frame)
    e.grid(row=row, column=1, pady=5)
    return e

sku_entry = styled_entry(1, "SKU")
sku_entry.configure(state="readonly")
name_entry = styled_entry(2, "Name")
brand_entry = styled_entry(3, "Brand")
stock_entry = styled_entry(4, "Stock")
min_entry = styled_entry(5, "Min Stock")

tk.Label(form_frame, text="Section", bg=COLOR_SURFACE, fg=COLOR_TEXT).grid(row=6, column=0, sticky="w", pady=5)
section_var = tk.StringVar(value="General")
section_box = ttk.Combobox(form_frame, textvariable=section_var, state="readonly")
section_box.grid(row=6, column=1, pady=5, sticky="ew")

ttk.Button(form_frame, text="Add Product", command=add_product)\
    .grid(row=7, columnspan=2, pady=10, sticky="ew")
ttk.Button(form_frame, text="Add Section", command=add_section)\
    .grid(row=8, columnspan=2, pady=(0, 5), sticky="ew")
ttk.Button(form_frame, text="Delete Section", command=delete_section)\
    .grid(row=9, columnspan=2, pady=(0, 5), sticky="ew")
ttk.Button(form_frame, text="Import CSV", command=import_csv_products)\
    .grid(row=10, columnspan=2, pady=(0, 5), sticky="ew")

# ---------------- RIGHT PANEL ----------------
right_frame = tk.Frame(main_frame, bg=COLOR_BG)
right_frame.pack(side="right", fill="both", expand=True)

cols = ("SKU", "Name", "Brand", "Stock", "Min", "Status", "Alert")
tree = ttk.Treeview(right_frame, columns=cols, show="tree headings")
tree.heading("#0", text="Section")

TREE_COL_LAYOUT = {
    "#0": (130, False, "w"),
    "SKU": (82, False, "center"),
    "Name": (210, True, "w"),
    "Brand": (150, True, "w"),
    "Stock": (62, False, "center"),
    "Min": (62, False, "center"),
    "Status": (72, False, "center"),
    "Alert": (78, False, "center"),
}

def fit_tree_columns(_event=None):
    tree.update_idletasks()
    total = tree.winfo_width()
    if total <= 1:
        return

    fixed_total = 0
    stretch_cols = []
    for col, (default_w, stretch, anchor) in TREE_COL_LAYOUT.items():
        tree.heading(col, text="Section" if col == "#0" else col)
        if stretch:
            stretch_cols.append((col, default_w, anchor))
        else:
            tree.column(col, width=default_w, minwidth=max(default_w - 12, 50), stretch=False, anchor=anchor)
            fixed_total += default_w

    remaining = max(total - fixed_total - 8, sum(w for _, w, _ in stretch_cols))
    if stretch_cols:
        weights = [w for _, w, _ in stretch_cols]
        weight_sum = sum(weights) or 1
        for col, default_w, anchor in stretch_cols:
            share = int(remaining * (default_w / weight_sum))
            tree.column(col, width=max(share, 90), minwidth=90, stretch=True, anchor=anchor)

for col, (default_w, stretch, anchor) in TREE_COL_LAYOUT.items():
    tree.heading(col, text="Section" if col == "#0" else col)
    tree.column(col, width=default_w, minwidth=max(default_w - 12, 50), stretch=stretch, anchor=anchor)

tree.pack(fill="both", expand=True)
right_frame.bind("<Configure>", fit_tree_columns)
root.after(150, fit_tree_columns)
tree.tag_configure("drag_source", background="#dbeafe")
tree.tag_configure("drop_target", background="#bbf7d0")
tree.bind("<ButtonPress-1>", on_tree_drag_start)
tree.bind("<B1-Motion>", on_tree_drag_motion)
tree.bind("<ButtonRelease-1>", on_tree_drop)

drag_hint_var = tk.StringVar(value="")
tk.Label(
    right_frame,
    textvariable=drag_hint_var,
    bg=COLOR_BG,
    fg=COLOR_TEXT_MUTED,
    anchor="w"
).pack(fill="x", pady=(4, 0))

# ---------------- ACTION BAR ----------------
action_frame = tk.Frame(right_frame, bg=COLOR_SURFACE, pady=10)
action_frame.pack(fill="x", pady=5)

def safe_update(change):
    sku = selected_sku()
    if sku:
        update_stock(sku, change)

def safe_mark_old():
    sku = selected_sku()
    if sku:
        mark_old(sku)

def safe_mark_active():
    sku = selected_sku()
    if sku:
        mark_active(sku)

def update_mark_old_button_state(_event=None):
    if "mark_old_btn" not in globals():
        return
    selected = tree.selection()
    if not selected:
        mark_old_btn.config(text="Mark Old", command=safe_mark_old)
        return
    iid = selected[0]
    if tree.parent(iid) == "":
        mark_old_btn.config(text="Mark Old", command=safe_mark_old)
        return
    values = tree.item(iid, "values")
    status = values[5] if len(values) > 5 else ""
    if str(status).strip().lower() == "old":
        mark_old_btn.config(text="Unmark Old", command=safe_mark_active)
    else:
        mark_old_btn.config(text="Mark Old", command=safe_mark_old)

def safe_damage():
    sku = selected_sku()
    if sku:
        mark_damaged(sku)

def safe_delete():
    sku = selected_sku()
    if sku:
        delete_product(sku)

def btn(text, cmd):
    b = ttk.Button(action_frame, text=text, command=cmd)
    b.pack(side="left", padx=5)
    return b

btn("+ Add", lambda: safe_update(1))
btn("- Remove", lambda: safe_update(-1))
mark_old_btn = btn("Mark Old", safe_mark_old)
btn("Damage", safe_damage)
btn("Delete", safe_delete)
btn("Edit", lambda: open_edit_window())
btn("Export PDF", export_pdf)
btn("Analytics", show_analytics)
btn("Forecast", show_forecast_tab)
btn("Security", show_security_tab)
tree.bind("<<TreeviewSelect>>", update_mark_old_button_state, add="+")

# EMAIL SECTION
email_frame = tk.Frame(root, bg=COLOR_SURFACE, padx=12, pady=12)
email_frame.pack(fill="x")

tk.Label(email_frame, text="Send Report:", bg=COLOR_SURFACE, fg=COLOR_TEXT).pack(side="left")

email_entry = ttk.Entry(email_frame, width=30)
email_entry.pack(side="left", padx=5)

ttk.Button(email_frame, text="Send Email", command=send_email)\
    .pack(side="left")

sku_entry.configure(state="normal")
sku_entry.insert(0, generate_next_sku())
sku_entry.configure(state="readonly")
refresh_section_selector()
refresh()
update_mark_old_button_state()
refresh_connection_indicator()
auto_sync_loop()
root.mainloop()
