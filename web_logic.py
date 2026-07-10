"""Shared database and business logic for the web app."""

import calendar
import json
import math
import os
import time
from collections import defaultdict
from datetime import datetime, timedelta

try:
    import psycopg
except ImportError:
    psycopg = None

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(SCRIPT_DIR, "app_db_config.json")

DEFAULT_LEAD_TIME_DAYS = 7
DEFAULT_SERVICE_Z = 1.65

_CONFIG_CACHE = None
_CONFIG_RETRY_ERRNOS = {11, 35}  # macOS iCloud/Desktop: EDEADLK, EAGAIN


def _retry_os_call(func, attempts=5):
    """Retry file/IO operations that fail transiently on synced Desktop folders."""
    last_err = None
    for attempt in range(attempts):
        try:
            return func()
        except OSError as exc:
            last_err = exc
            if exc.errno in _CONFIG_RETRY_ERRNOS and attempt < attempts - 1:
                time.sleep(0.05 * (2 ** attempt))
                continue
            raise
    raise last_err


def load_config(force_reload=False):
    global _CONFIG_CACHE
    if _CONFIG_CACHE is not None and not force_reload:
        return _CONFIG_CACHE

    env_host = os.environ.get("SUPABASE_DB_HOST")
    if env_host:
        _CONFIG_CACHE = {
            "mode": "supabase",
            "host": env_host,
            "port": os.environ.get("SUPABASE_DB_PORT", "5432"),
            "dbname": os.environ.get("SUPABASE_DB_NAME", "postgres"),
            "user": os.environ.get("SUPABASE_DB_USER", "postgres"),
            "password": os.environ.get("SUPABASE_DB_PASSWORD", ""),
        }
        return _CONFIG_CACHE

    def _read():
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)

    _CONFIG_CACHE = _retry_os_call(_read)
    return _CONFIG_CACHE


def _project_ref(host):
    prefix = "db."
    suffix = ".supabase.co"
    if host.startswith(prefix) and host.endswith(suffix):
        return host[len(prefix) : -len(suffix)]
    return os.environ.get("SUPABASE_PROJECT_REF", "")


def _vercel_pooler_config(cfg):
    """Vercel serverless cannot use Supabase direct IPv6 — use the transaction pooler."""
    if os.environ.get("VERCEL") != "1" and os.environ.get("SUPABASE_USE_POOLER") != "1":
        return cfg

    direct_host = cfg["host"]
    pooler_host = os.environ.get("SUPABASE_DB_POOLER_HOST", "")
    if not pooler_host and direct_host.startswith("db.") and direct_host.endswith(".supabase.co"):
        pooler_host = os.environ.get(
            "SUPABASE_DB_POOLER_REGION",
            "aws-0-eu-central-1.pooler.supabase.com",
        )

    if not pooler_host:
        return cfg

    user = cfg.get("user", "postgres")
    ref = _project_ref(direct_host)
    if user == "postgres" and ref:
        user = f"postgres.{ref}"

    return {
        **cfg,
        "host": pooler_host,
        "port": os.environ.get("SUPABASE_DB_POOLER_PORT", "6543"),
        "user": user,
        "use_pooler": True,
    }


def get_conn():
    if psycopg is None:
        raise RuntimeError("psycopg is not installed")

    cfg = _vercel_pooler_config(load_config())
    connect_kwargs = {
        "host": cfg["host"],
        "port": int(cfg.get("port", 5432)),
        "dbname": cfg.get("dbname", "postgres"),
        "user": cfg["user"],
        "password": cfg["password"],
        "sslmode": "require",
        "connect_timeout": 15,
    }
    if cfg.get("use_pooler"):
        connect_kwargs["prepare_threshold"] = None

    last_err = None
    for attempt in range(4):
        try:
            return psycopg.connect(**connect_kwargs)
        except Exception as exc:
            last_err = exc
            if attempt < 3:
                time.sleep(0.15 * (2 ** attempt))
    raise last_err


def generate_next_sku(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT sku FROM products WHERE sku LIKE 'MS%'")
        max_num = 0
        for (sku,) in cur.fetchall():
            suffix = sku[2:]
            if suffix.isdigit():
                max_num = max(max_num, int(suffix))
    return f"MS{max_num + 1:04d}"


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


def calculate_forecast_recommendations(conn, lead_time=DEFAULT_LEAD_TIME_DAYS, service_z=DEFAULT_SERVICE_Z):
    with conn.cursor() as cur:
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

    rows = []
    for sku, name, current, min_stock in product_rows:
        day_map = demand_by_sku_day.get(sku, {})
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
            rows.append({
                "priority": priority, "sku": sku, "name": name_map.get(sku, sku),
                "current": current, "min_stock": min_stock,
                "avg_daily": round(forecast_daily, 2), "rop": round(reorder_point, 1),
                "suggested": suggested,
            })
            continue

        parsed_days = sorted(datetime.strptime(d, "%Y-%m-%d").date() for d in day_map.keys())
        end_day = max(parsed_days[-1], datetime.now().date())
        start_day = end_day - timedelta(days=119)
        series = []
        d = start_day
        while d <= end_day:
            series.append(float(day_map.get(d.isoformat(), 0.0)))
            d += timedelta(days=1)

        if sum(series) <= 0:
            continue

        positive_series = [x for x in series if x > 0]
        if positive_series:
            p95 = percentile(positive_series, 0.95)
            clipped = [min(x, p95) if x > 0 else 0.0 for x in series]
        else:
            clipped = list(series)

        alpha = 0.35
        ewma = clipped[0]
        for x in clipped[1:]:
            ewma = (alpha * x) + ((1 - alpha) * ewma)

        trend_window = clipped[-28:] if len(clipped) >= 28 else clipped
        n = len(trend_window)
        xs = list(range(n))
        x_mean = (n - 1) / 2 if n else 0
        y_mean = sum(trend_window) / n if n else 0
        denom = sum((x - x_mean) ** 2 for x in xs) if n > 1 else 0
        slope = (sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, trend_window)) / denom) if denom else 0.0

        alpha_c = 0.2
        z_hat = p_hat = None
        interval = 1
        for x in clipped:
            if x > 0:
                if z_hat is None:
                    z_hat, p_hat = x, interval
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

        std_daily = math.sqrt(sum((x - forecast_daily) ** 2 for x in clipped) / len(clipped))
        med = percentile(clipped, 0.50)
        mad = percentile([abs(x - med) for x in clipped], 0.50)
        demand_std = (0.6 * std_daily) + (0.4 * (1.4826 * mad))

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

        rows.append({
            "priority": priority, "sku": sku, "name": name_map.get(sku, sku),
            "current": current, "min_stock": min_stock,
            "avg_daily": round(forecast_daily, 2), "rop": round(reorder_point, 1),
            "suggested": suggested,
        })

    priority_order = {"EMPTY": 0, "LOW": 1, "SOON": 2, "OK": 3}
    rows.sort(key=lambda r: (priority_order.get(r["priority"], 9), -r["avg_daily"]))
    return rows


def get_movement_activity_summary(conn, year=None, month=None, days=30):
    if year and month:
        start = datetime(year, month, 1).date()
        end = datetime(year, month, calendar.monthrange(year, month)[1]).date()
        period_label = start.strftime("%B %Y")
        date_from = start.isoformat()
        date_to = end.isoformat()
    else:
        end = datetime.now().date()
        start = end - timedelta(days=days - 1)
        period_label = f"Last {days} days"
        date_from = start.isoformat()
        date_to = end.isoformat()

    with conn.cursor() as cur:
        cur.execute("""
            SELECT substr(CAST(date AS TEXT), 1, 10) AS day,
                   COALESCE(SUM(CASE WHEN change > 0 THEN change ELSE 0 END), 0) AS inbound,
                   COALESCE(SUM(CASE WHEN change < 0 THEN ABS(change) ELSE 0 END), 0) AS outbound,
                   COALESCE(SUM(ABS(change)), 0) AS activity,
                   COUNT(*) AS movement_count
            FROM movements
            WHERE date IS NOT NULL
              AND substr(CAST(date AS TEXT), 1, 10) >= %s
              AND substr(CAST(date AS TEXT), 1, 10) <= %s
            GROUP BY 1 ORDER BY 1
        """, (date_from, date_to))
        by_day = {
            r[0]: {
                "day": r[0],
                "inbound": int(r[1]),
                "outbound": int(r[2]),
                "activity": int(r[3]),
                "movement_count": int(r[4]),
            }
            for r in cur.fetchall()
        }

    trend = []
    d = start
    while d <= end:
        key = d.isoformat()
        trend.append(by_day.get(key, {
            "day": key,
            "inbound": 0,
            "outbound": 0,
            "activity": 0,
            "movement_count": 0,
        }))
        d += timedelta(days=1)

    total_activity = sum(t["activity"] for t in trend)
    active_days = sum(1 for t in trend if t["activity"] > 0)
    busiest = max(trend, key=lambda t: t["activity"], default=None)

    return {
        "days": trend,
        "summary": {
            "total_activity": total_activity,
            "total_inbound": sum(t["inbound"] for t in trend),
            "total_outbound": sum(t["outbound"] for t in trend),
            "total_movements": sum(t["movement_count"] for t in trend),
            "active_days": active_days,
            "avg_activity_per_active_day": round(total_activity / active_days, 1) if active_days else 0,
            "busiest_day": busiest["day"] if busiest and busiest["activity"] else None,
            "busiest_activity": busiest["activity"] if busiest else 0,
        },
        "period_label": period_label,
        "year": year,
        "month": month,
    }


def get_activity_year_options(conn):
    this_year = datetime.now().year
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT substr(CAST(date AS TEXT), 1, 4) FROM movements "
            "WHERE date IS NOT NULL ORDER BY 1"
        )
        db_years = [int(y[0]) for y in cur.fetchall() if y[0] and str(y[0]).isdigit()]
    return sorted(set(db_years + [this_year + i for i in range(3)]))


def get_daily_movement_detail(conn, day):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT m.sku, COALESCE(p.name, m.sku), m.change, m.reason,
                   COALESCE(m.customer, ''),
                   substr(CAST(m.date AS TEXT), 12, 8) AS time
            FROM movements m
            LEFT JOIN products p ON p.sku = m.sku
            WHERE substr(CAST(m.date AS TEXT), 1, 10) = %s
            ORDER BY m.date DESC
        """, (day,))
        rows = cur.fetchall()

        cur.execute("""
            SELECT reason, COUNT(*), COALESCE(SUM(ABS(change)), 0)
            FROM movements
            WHERE substr(CAST(date AS TEXT), 1, 10) = %s
            GROUP BY reason ORDER BY 3 DESC
        """, (day,))
        by_reason = [
            {"reason": r[0], "count": int(r[1]), "units": int(r[2])}
            for r in cur.fetchall()
        ]

        cur.execute("""
            SELECT m.sku, COALESCE(p.name, m.sku), COALESCE(SUM(ABS(m.change)), 0)
            FROM movements m
            LEFT JOIN products p ON p.sku = m.sku
            WHERE substr(CAST(m.date AS TEXT), 1, 10) = %s
            GROUP BY m.sku, p.name ORDER BY 3 DESC LIMIT 10
        """, (day,))
        top_items = [
            {"sku": r[0], "name": r[1], "units": int(r[2])}
            for r in cur.fetchall()
        ]

    inbound = sum(r[2] for r in rows if r[2] > 0)
    outbound = sum(abs(r[2]) for r in rows if r[2] < 0)
    movements = [
        {
            "sku": r[0],
            "name": r[1],
            "change": int(r[2]),
            "reason": r[3],
            "customer": r[4] or "",
            "time": r[5] or "",
        }
        for r in rows
    ]

    return {
        "day": day,
        "inbound": inbound,
        "outbound": outbound,
        "activity": inbound + outbound,
        "movement_count": len(rows),
        "by_reason": by_reason,
        "top_items": top_items,
        "movements": movements,
    }


def get_analytics_data(conn, year=None, include_activity=True, include_jit=True):
    year = year or datetime.now().year
    lead_time = DEFAULT_LEAD_TIME_DAYS
    service_z = DEFAULT_SERVICE_Z

    activity = None
    trend = []
    if include_activity:
        activity = get_movement_activity_summary(conn, days=30)
        trend = [
            {"day": d["day"], "change": d["inbound"] - d["outbound"]}
            for d in activity["days"]
        ]

    with conn.cursor() as cur:
        cur.execute("""
            SELECT COUNT(*),
                   COALESCE(SUM(CASE WHEN stock <= min_stock THEN 1 ELSE 0 END), 0),
                   COALESCE(SUM(stock), 0)
            FROM products
        """)
        total_items, low_items, total_stock = cur.fetchone()

        cur.execute("SELECT COUNT(*) FROM movements")
        movement_count = cur.fetchone()[0]

        cur.execute("""
            SELECT m.sku, COALESCE(p.name, m.sku),
                   COALESCE(SUM(CASE WHEN m.change > 0 THEN m.change ELSE 0 END), 0),
                   COALESCE(SUM(CASE WHEN m.change < 0 THEN ABS(m.change) ELSE 0 END), 0),
                   COALESCE(SUM(ABS(m.change)), 0)
            FROM movements m
            LEFT JOIN products p ON p.sku = m.sku
            GROUP BY m.sku, p.name
            ORDER BY 5 DESC LIMIT 5
        """)
        movers = [
            {
                "sku": r[0],
                "name": r[1],
                "inbound": int(r[2]),
                "outbound": int(r[3]),
                "moves": int(r[4]),
            }
            for r in cur.fetchall()
        ]

        cur.execute("""
            SELECT CAST(substr(CAST(date AS TEXT), 6, 2) AS INTEGER), COALESCE(SUM(change), 0)
            FROM movements WHERE CAST(date AS TEXT) LIKE %s
            GROUP BY 1 ORDER BY 1
        """, (f"{year}-%",))
        month_data = {r[0]: r[1] for r in cur.fetchall() if r[0]}
        months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        monthly = [{"month": months[i], "change": month_data.get(i + 1, 0)} for i in range(12)]

        jit = []
        if include_jit:
            cur.execute("SELECT sku, COALESCE(name, sku), COALESCE(stock, 0) FROM products")
            product_rows = cur.fetchall()
            stock_map = {r[0]: r[2] for r in product_rows}
            name_map = {r[0]: r[1] for r in product_rows}

            cur.execute("""
                SELECT sku, substr(CAST(date AS TEXT), 1, 10), ABS(change)
                FROM movements WHERE change < 0 AND date IS NOT NULL
            """)
            demand_by_sku_day = defaultdict(lambda: defaultdict(float))
            for sku, day_key, qty in cur.fetchall():
                demand_by_sku_day[sku][day_key] += qty

            for sku, day_map in demand_by_sku_day.items():
                day_values = list(day_map.values())
                if not day_values:
                    continue
                avg_daily = sum(day_values) / len(day_values)
                variance = sum((x - avg_daily) ** 2 for x in day_values) / len(day_values)
                std_daily = math.sqrt(variance)
                rop = (avg_daily * lead_time) + (service_z * std_daily * math.sqrt(lead_time))
                current = stock_map.get(sku, 0)
                suggested = max(math.ceil(rop - current), 0)
                jit.append({
                    "sku": sku, "name": name_map.get(sku, sku),
                    "avg_daily": round(avg_daily, 2), "current": current,
                    "rop": round(rop, 1), "suggested": suggested,
                })
            jit.sort(key=lambda x: x["avg_daily"], reverse=True)

    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT substr(CAST(date AS TEXT), 1, 4) FROM movements WHERE date IS NOT NULL ORDER BY 1")
        db_years = [int(y[0]) for y in cur.fetchall() if y[0] and str(y[0]).isdigit()]

    this_year = datetime.now().year
    years = sorted(set(db_years + [this_year + i for i in range(6)]))

    result = {
        "kpis": {
            "products": total_items,
            "low_stock": low_items,
            "total_units": total_stock,
            "movements": movement_count,
            "healthy": max(total_items - low_items, 0),
        },
        "movers": movers,
        "monthly": monthly,
        "years": years,
        "year": year,
    }
    if include_activity:
        result["trend"] = trend
        result["activity"] = activity
    if include_jit:
        result["jit"] = jit[:8]
    return result


def _pdf_table(data, col_widths=None, font_size=7):
    """Spreadsheet-style table for PDF reports."""
    from reportlab.lib import colors
    from reportlab.platypus import Table, TableStyle

    table = Table(data, colWidths=col_widths, repeatRows=1)
    style = TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dbe7f3")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#0c3b5d")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), font_size),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("ALIGN", (1, 1), (2, -1), "LEFT"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d9e2ec")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ])
    table.setStyle(style)
    return table


def _clip(text, n=28):
    s = str(text or "")
    return s if len(s) <= n else s[: n - 1] + "…"


def build_ai_analytics_report_pdf(conn):
    """Detailed PDF analytics report with spreadsheet-style tables for AI purchase advice."""
    import io

    from reportlab.lib.pagesizes import landscape, letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer

    now = datetime.now()
    year = now.year
    analytics = get_analytics_data(conn, year)
    forecasts = calculate_forecast_recommendations(conn)
    kpis = analytics["kpis"]
    forecast_map = {r["sku"]: r["suggested"] for r in forecasts}

    with conn.cursor() as cur:
        cur.execute("""
            SELECT sku, name, brand, stock, min_stock, status, COALESCE(group_name, 'General')
            FROM products
            ORDER BY COALESCE(group_name, 'General'), name
        """)
        products = cur.fetchall()

        cur.execute("""
            SELECT COALESCE(group_name, 'General'), COUNT(*), COALESCE(SUM(stock), 0),
                   COALESCE(SUM(CASE WHEN stock <= min_stock THEN 1 ELSE 0 END), 0)
            FROM products
            GROUP BY 1 ORDER BY 1
        """)
        section_stats = cur.fetchall()

        cur.execute("""
            SELECT sku, reason, change, substr(CAST(date AS TEXT), 1, 10)
            FROM movements
            WHERE date IS NOT NULL
            ORDER BY date DESC LIMIT 25
        """)
        recent_movements = cur.fetchall()

    urgent = [r for r in forecasts if r["priority"] in ("EMPTY", "LOW", "SOON")]
    low_stock = [p for p in products if p[3] <= p[4]]
    old_stock = [p for p in products if str(p[5]).strip().lower() == "old"]
    empty = [p for p in products if p[3] <= 0]

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(letter),
        leftMargin=0.45 * inch,
        rightMargin=0.45 * inch,
        topMargin=0.5 * inch,
        bottomMargin=0.5 * inch,
        title="AI Analytics Report",
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "ReportTitle",
        parent=styles["Heading1"],
        fontSize=16,
        textColor="#0c3b5d",
        spaceAfter=6,
    )
    section_style = ParagraphStyle(
        "SectionTitle",
        parent=styles["Heading2"],
        fontSize=11,
        textColor="#0c3b5d",
        spaceBefore=10,
        spaceAfter=6,
    )
    body_style = ParagraphStyle(
        "Body",
        parent=styles["Normal"],
        fontSize=8,
        leading=11,
        textColor="#486581",
    )

    story = []
    story.append(Paragraph("Momentum Services — AI Analytics Report", title_style))
    story.append(Paragraph(
        f"Generated: {now.strftime('%d %b %Y, %H:%M')} · "
        f"Lead time: {DEFAULT_LEAD_TIME_DAYS}d · Service z: {DEFAULT_SERVICE_Z}",
        body_style,
    ))
    story.append(Spacer(1, 8))
    story.append(Paragraph(
        "Feed this PDF to ChatGPT or Claude for purchase advice, reorder prioritisation, and cash-flow planning.",
        body_style,
    ))
    story.append(Spacer(1, 10))

    summary_data = [
        ["Metric", "Value", "Metric", "Value"],
        ["Total SKUs", str(kpis["products"]), "Low stock SKUs", str(kpis["low_stock"])],
        ["Healthy SKUs", str(kpis["healthy"]), "Total units", str(kpis["total_units"])],
        ["Movement records", str(kpis["movements"]), "Urgent reorders", str(len(urgent))],
        ["Empty (0 stock)", str(len(empty)), "Marked Old", str(len(old_stock))],
    ]
    story.append(_pdf_table(summary_data, col_widths=[1.8 * inch, 1.2 * inch, 1.8 * inch, 1.2 * inch], font_size=8))

    story.append(Paragraph("Urgent reorder recommendations", section_style))
    if urgent:
        urgent_data = [["Priority", "SKU", "Name", "Stock", "Min", "Avg/day", "ROP", "Order"]]
        for r in urgent:
            urgent_data.append([
                r["priority"], r["sku"], _clip(r["name"], 24),
                str(r["current"]), str(r["min_stock"]),
                str(r["avg_daily"]), str(r["rop"]), str(r["suggested"]),
            ])
        story.append(_pdf_table(
            urgent_data,
            col_widths=[0.7 * inch, 0.75 * inch, 2.4 * inch, 0.55 * inch, 0.5 * inch, 0.65 * inch, 0.6 * inch, 0.55 * inch],
        ))
    else:
        story.append(Paragraph("No urgent reorders flagged.", body_style))

    story.append(Paragraph("Top demand — JIT forecast", section_style))
    if analytics.get("jit"):
        jit_data = [["SKU", "Name", "Avg/day", "Stock", "ROP", "Suggested order"]]
        for j in analytics["jit"]:
            jit_data.append([
                j["sku"], _clip(j["name"], 30), str(j["avg_daily"]),
                str(j["current"]), str(j["rop"]), str(j["suggested"]),
            ])
        story.append(_pdf_table(
            jit_data,
            col_widths=[0.8 * inch, 3.2 * inch, 0.7 * inch, 0.6 * inch, 0.7 * inch, 0.9 * inch],
        ))
    else:
        story.append(Paragraph("Insufficient movement history for JIT forecast.", body_style))

    story.append(PageBreak())
    story.append(Paragraph("Most moved items", section_style))
    if analytics["movers"]:
        mover_data = [["SKU", "Name", "Total movements"]]
        for m in analytics["movers"]:
            mover_data.append([m["sku"], _clip(m["name"], 40), str(m["moves"])])
        story.append(_pdf_table(mover_data, col_widths=[1 * inch, 4.5 * inch, 1.2 * inch]))
    else:
        story.append(Paragraph("No movement data.", body_style))

    story.append(Paragraph(f"Monthly net stock change ({year})", section_style))
    month_data = [["Month", "Net change"]]
    for m in analytics["monthly"]:
        month_data.append([m["month"], str(m["change"])])
    story.append(_pdf_table(month_data, col_widths=[1.5 * inch, 1.5 * inch], font_size=8))

    story.append(Paragraph("Inventory by section", section_style))
    sec_data = [["Section", "SKUs", "Total units", "Low stock SKUs"]]
    for sec, count, units, low in section_stats:
        sec_data.append([_clip(sec, 22), str(count), str(units), str(low)])
    story.append(_pdf_table(sec_data, col_widths=[2.5 * inch, 0.8 * inch, 1 * inch, 1.2 * inch]))

    story.append(Paragraph("Low stock items (at or below minimum)", section_style))
    if low_stock:
        low_data = [["SKU", "Name", "Brand", "Section", "Stock", "Min", "Status"]]
        for sku, name, brand, stock, min_stock, status, section in low_stock:
            low_data.append([
                sku, _clip(name, 22), _clip(brand, 14), _clip(section, 14),
                str(stock), str(min_stock), status,
            ])
        story.append(_pdf_table(
            low_data,
            col_widths=[0.75 * inch, 2 * inch, 1.2 * inch, 1.2 * inch, 0.55 * inch, 0.5 * inch, 0.7 * inch],
        ))
    else:
        story.append(Paragraph("All items above minimum stock.", body_style))

    story.append(PageBreak())
    story.append(Paragraph("Full inventory snapshot", section_style))
    full_data = [["SKU", "Name", "Brand", "Section", "Stock", "Min", "Status", "Order"]]
    for sku, name, brand, stock, min_stock, status, section in products:
        full_data.append([
            sku, _clip(name, 20), _clip(brand, 12), _clip(section, 12),
            str(stock), str(min_stock), status[:7], str(forecast_map.get(sku, 0)),
        ])
    story.append(_pdf_table(
        full_data,
        col_widths=[0.72 * inch, 1.85 * inch, 1.1 * inch, 1.1 * inch, 0.52 * inch, 0.48 * inch, 0.62 * inch, 0.52 * inch],
        font_size=6.5,
    ))

    story.append(Paragraph("Recent movements (last 25)", section_style))
    if recent_movements:
        mov_data = [["SKU", "Reason", "Change", "Date"]]
        for sku, reason, change, day in recent_movements:
            mov_data.append([sku, _clip(reason, 20), str(change), day or ""])
        story.append(_pdf_table(mov_data, col_widths=[0.9 * inch, 2.5 * inch, 0.7 * inch, 1.1 * inch]))
    else:
        story.append(Paragraph("No movements logged.", body_style))

    story.append(Spacer(1, 14))
    story.append(Paragraph("Suggested AI questions", section_style))
    for q in [
        "Create a prioritised purchase list from urgent and JIT tables.",
        "Which orders can wait vs must be placed this week?",
        "Flag overstocked items tying up cash.",
        "Which sections have the worst low-stock ratio?",
        "What does monthly movement suggest for next month's buying?",
    ]:
        story.append(Paragraph(f"• {q}", body_style))

    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()


def build_inventory_report_pdf(conn):
    """Build the same Inventory Report PDF as the desktop app (export_pdf)."""
    import io

    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    recommendations = calculate_forecast_recommendations(conn)
    forecast_map = {row["sku"]: row["suggested"] for row in recommendations}

    with conn.cursor() as cur:
        cur.execute("""
            SELECT sku, name, brand, stock, min_stock, status, COALESCE(group_name, 'General')
            FROM products
            ORDER BY COALESCE(group_name, 'General'), name
        """)
        product_rows = cur.fetchall()

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    page_w, page_h = letter
    left = 30
    right = page_w - 30
    now = datetime.now()
    header_date = now.strftime("%d %b %Y, %H:%M")
    page_no = 1

    def draw_page_shell(section_title):
        nonlocal page_no
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

    y = draw_page_shell("Overview")
    total_products = len(product_rows)
    low_products = sum(1 for r in product_rows if r[3] <= r[4])
    total_units = sum(int(r[3]) for r in product_rows)
    urgent_count = sum(1 for r in recommendations if r["priority"] in ("EMPTY", "LOW", "SOON"))

    c.setFont("Helvetica", 10)
    for line in [
        f"Total Products: {total_products}",
        f"Low Stock Items: {low_products}",
        f"Total Units in Stock: {total_units}",
        f"Urgent Reorder Recommendations: {urgent_count}",
    ]:
        c.drawString(left, y, line)
        y -= 16

    y -= 8
    cols_main = [
        (35, "SKU"), (95, "Name"), (220, "Brand"), (300, "Section"),
        (380, "Stock"), (420, "Min"), (455, "Status"), (495, "Suggested Order"),
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

    c.showPage()
    page_no += 1
    y = draw_page_shell("JIT Forecast Recommendations")
    cols_rec = [
        (35, "Priority"), (90, "SKU"), (145, "Name"),
        (330, "Current"), (385, "Avg/day"), (445, "ROP"), (470, "Suggested Order"),
    ]
    y = draw_table_header(y, cols_rec)
    c.setFont("Helvetica", 9)

    urgent_rows = [r for r in recommendations if r["priority"] in ("EMPTY", "LOW", "SOON")]
    if not urgent_rows:
        c.drawString(35, y, "No urgent reorder recommendations right now.")
    else:
        for row in urgent_rows[:30]:
            if y < 48:
                c.showPage()
                page_no += 1
                y = draw_page_shell("JIT Forecast Recommendations")
                y = draw_table_header(y, cols_rec)
                c.setFont("Helvetica", 9)
            c.drawString(35, y, str(row["priority"]))
            c.drawString(90, y, str(row["sku"]))
            c.drawString(145, y, str(row["name"])[:28])
            c.drawRightString(368, y, str(row["current"]))
            c.drawRightString(430, y, f"{row['avg_daily']:.2f}")
            c.drawRightString(490, y, f"{row['rop']:.1f}")
            c.drawRightString(562, y, str(row["suggested"]))
            y -= 14

    c.save()
    buffer.seek(0)
    return buffer.getvalue()
