"""Self-learning Just-In-Time (JIT) forecasting engine."""

import logging
import math
from collections import defaultdict
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

DEFAULT_ALPHA = 0.3
ALPHA_CANDIDATES = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7]
SERVICE_Z = 1.65
FORECAST_HORIZON_DAYS = 30
DEMAND_LOOKBACK_DAYS = 60
ALPHA_TUNING_DAYS = 30
DEFAULT_LEAD_TIME_DAYS = 7
ANOMALY_SIGMA = 2.5
MIN_ACCURACY_HISTORY_DAYS = 7
ACCURACY_ROLLING_DAYS = 30

CONFIDENCE_LABELS = (
    (40, "Limited data"),
    (70, "Improving"),
    (101, "Reliable"),
)


def _table_exists(cur, table_name):
    cur.execute(
        """
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = %s
        """,
        (table_name,),
    )
    return cur.fetchone() is not None


def _load_lead_times(cur):
    lead_times = {}
    if not _table_exists(cur, "supplier_lead_times"):
        return lead_times
    cur.execute(
        """
        SELECT sku, lead_time_days
        FROM supplier_lead_times
        WHERE lead_time_days IS NOT NULL
        """
    )
    for sku, days in cur.fetchall():
        if sku and days:
            lead_times[sku] = int(days)
    return lead_times


def _load_incoming_stock(cur, horizon_end):
    incoming = defaultdict(float)
    if not _table_exists(cur, "purchase_orders"):
        return incoming

    cur.execute(
        """
        SELECT sku, COALESCE(SUM(quantity), 0)
        FROM purchase_orders
        WHERE expected_delivery_date IS NOT NULL
          AND expected_delivery_date <= %s
          AND COALESCE(status, '') != 'cancelled'
        GROUP BY sku
        """,
        (horizon_end,),
    )
    for sku, qty in cur.fetchall():
        if sku:
            incoming[sku] = float(qty)
    return incoming


def _mean(values):
    if not values:
        return 0.0
    return sum(values) / len(values)


def _std_demand(daily_series):
    if not daily_series:
        return 0.0
    mean = _mean(daily_series)
    variance = sum((x - mean) ** 2 for x in daily_series) / len(daily_series)
    return math.sqrt(variance)


def _ewma_velocity(daily_series, alpha=DEFAULT_ALPHA):
    if not daily_series:
        return 0.0
    velocity = float(daily_series[0])
    for demand in daily_series[1:]:
        velocity = (alpha * demand) + ((1 - alpha) * velocity)
    return velocity


def _clean_anomalies(daily_series):
    """Mechanism 3 — replace outlier days with the series mean."""
    if not daily_series:
        return [], 0

    mu = _mean(daily_series)
    sigma = _std_demand(daily_series)
    threshold = mu + (ANOMALY_SIGMA * sigma)
    cleaned = []
    anomaly_count = 0

    for demand in daily_series:
        if sigma > 0 and demand > threshold:
            cleaned.append(mu)
            anomaly_count += 1
        else:
            cleaned.append(demand)

    return cleaned, anomaly_count


def _replay_ewma_and_score(daily_series, alpha, replay_days=ALPHA_TUNING_DAYS):
    """Mechanism 1 — MAE from replaying EWMA over recent demand history."""
    series = (
        daily_series[-replay_days:]
        if len(daily_series) >= replay_days
        else list(daily_series)
    )
    if len(series) < 2:
        return float("inf")

    velocity = float(series[0])
    errors = []
    for idx in range(1, len(series)):
        predicted = velocity
        actual = series[idx]
        errors.append(abs(actual - predicted))
        velocity = (alpha * actual) + ((1 - alpha) * velocity)

    return sum(errors) / len(errors) if errors else float("inf")


def _auto_tune_alpha(daily_series):
    """Mechanism 1 — pick alpha with lowest replay MAE."""
    if len(daily_series) < 2:
        return DEFAULT_ALPHA

    best_alpha = DEFAULT_ALPHA
    best_mae = float("inf")
    for alpha in ALPHA_CANDIDATES:
        mae = _replay_ewma_and_score(daily_series, alpha)
        if mae < best_mae:
            best_mae = mae
            best_alpha = alpha
    return best_alpha


def _weekday_weights(daily_series, day_keys):
    """Mechanism 2 — learn per-weekday demand multipliers."""
    weekday_totals = [0.0] * 7
    weekday_counts = [0] * 7

    for day_key, demand in zip(day_keys, daily_series):
        try:
            weekday = datetime.strptime(day_key, "%Y-%m-%d").weekday()
        except ValueError:
            continue
        weekday_totals[weekday] += demand
        weekday_counts[weekday] += 1

    weekday_avgs = [
        (weekday_totals[i] / weekday_counts[i]) if weekday_counts[i] else 0.0
        for i in range(7)
    ]
    overall_avg = _mean([avg for avg in weekday_avgs if avg > 0])

    if overall_avg <= 0:
        return [1.0] * 7

    return [
        (avg / overall_avg) if avg > 0 else 1.0
        for avg in weekday_avgs
    ]


def _project_weighted_demand(velocity, weekday_weights, today, horizon_days):
    """Mechanism 2 — project demand using weekday weights."""
    total = 0.0
    for offset in range(1, horizon_days + 1):
        future_day = today + timedelta(days=offset)
        weight = weekday_weights[future_day.weekday()]
        total += velocity * weight
    return total


def _confidence_score(daily_series, cleaned_series):
    """Mechanism 5 — score forecast trust from coverage and stability."""
    data_days = sum(1 for demand in daily_series if demand > 0)
    coverage_score = min(data_days / 60.0, 1.0)

    mu = _mean(cleaned_series)
    sigma = _std_demand(cleaned_series)
    if mu > 0:
        cv = sigma / mu
        stability_score = 1.0 - min(cv, 1.0)
    else:
        stability_score = 0.0

    confidence = (coverage_score * 0.6) + (stability_score * 0.4)
    confidence_pct = round(confidence * 100)

    label = CONFIDENCE_LABELS[-1][1]
    for threshold, text in CONFIDENCE_LABELS:
        if confidence_pct < threshold:
            label = text
            break

    return confidence_pct, label


def _accuracy_pct(predicted, actual):
    error_ratio = abs(predicted - actual) / max(actual, 1.0)
    return max(0.0, min(100.0, 100.0 - (error_ratio * 100.0)))


def _load_yesterday_predictions(cur, sku_ids, yesterday):
    """Mechanism 4 — fetch prior-run velocity estimates for accuracy scoring."""
    if not _table_exists(cur, "forecast_log") or not sku_ids:
        return {}

    cur.execute(
        """
        SELECT DISTINCT ON (sku_id) sku_id, predicted_velocity
        FROM forecast_log
        WHERE sku_id = ANY(%s)
          AND forecast_date = %s
        ORDER BY sku_id, created_at DESC
        """,
        (list(sku_ids), yesterday.isoformat()),
    )
    return {sku: float(pred) for sku, pred in cur.fetchall()}


def _load_rolling_accuracy(cur, sku_ids):
    """Mechanism 4 — 30-day rolling mean accuracy per SKU."""
    if not _table_exists(cur, "forecast_accuracy") or not sku_ids:
        return {}

    cur.execute(
        """
        SELECT sku_id,
               COUNT(*) AS day_count,
               AVG(accuracy_pct) AS avg_accuracy
        FROM forecast_accuracy
        WHERE sku_id = ANY(%s)
          AND forecast_date >= CURRENT_DATE - %s
        GROUP BY sku_id
        """,
        (list(sku_ids), ACCURACY_ROLLING_DAYS),
    )

    rolling = {}
    for sku, day_count, avg_accuracy in cur.fetchall():
        if day_count >= MIN_ACCURACY_HISTORY_DAYS:
            rolling[sku] = round(float(avg_accuracy), 1)
        else:
            rolling[sku] = None
    return rolling


def _save_forecast_params(cur, params_rows, tables_ok):
    if not tables_ok.get("forecast_params") or not params_rows:
        return
    for sku_id, best_alpha, anomaly_days in params_rows:
        cur.execute(
            """
            INSERT INTO forecast_params (sku_id, best_alpha, anomaly_days, last_tuned_at)
            VALUES (%s, %s, %s, NOW())
            ON CONFLICT (sku_id) DO UPDATE SET
                best_alpha = EXCLUDED.best_alpha,
                anomaly_days = EXCLUDED.anomaly_days,
                last_tuned_at = NOW()
            """,
            (sku_id, best_alpha, anomaly_days),
        )


def _save_forecast_log(cur, log_rows, tables_ok):
    if not tables_ok.get("forecast_log") or not log_rows:
        return
    cur.executemany(
        """
        INSERT INTO forecast_log (
            sku_id, forecast_date, predicted_velocity, projected_stock_30d,
            reorder_point, needs_reorder, confidence_pct
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        log_rows,
    )


def _save_forecast_accuracy(cur, accuracy_rows, tables_ok):
    if not tables_ok.get("forecast_accuracy") or not accuracy_rows:
        return
    cur.executemany(
        """
        INSERT INTO forecast_accuracy (
            sku_id, forecast_date, predicted_velocity, actual_demand, accuracy_pct
        )
        VALUES (%s, %s, %s, %s, %s)
        """,
        accuracy_rows,
    )


def _zero_demand_result(
    sku, name, current_stock, min_stock, lead_time_days, incoming_30d,
    best_alpha=DEFAULT_ALPHA, anomaly_days=0, confidence_pct=0,
    confidence_label="Limited data", rolling_accuracy_pct=None,
):
    min_stock_alert = current_stock <= min_stock
    return {
        "sku_id": sku,
        "sku": sku,
        "sku_name": name,
        "name": name,
        "current_stock": current_stock,
        "current": current_stock,
        "min_stock": min_stock,
        "min_stock_alert": min_stock_alert,
        "velocity_daily": 0.0,
        "best_alpha": best_alpha,
        "projected_stock_30d": float(current_stock),
        "reorder_point": float(min_stock),
        "safety_stock": 0.0,
        "suggested_order_qty": 0,
        "suggested": 0,
        "days_until_stockout": 999,
        "urgency": "OK",
        "needs_reorder": False,
        "incoming_30d": incoming_30d,
        "lead_time_days": lead_time_days,
        "confidence_pct": confidence_pct,
        "confidence_label": confidence_label,
        "rolling_accuracy_pct": rolling_accuracy_pct,
        "anomaly_days_excluded": anomaly_days,
    }


def calculate_jit_forecast(conn):
    """
    Self-learning JIT forecast per SKU.

    Execution order:
      1. Fetch products, demand, lead times, incoming stock
      2. Per SKU: clean anomalies → tune alpha → EWMA velocity → weekday weights
         → 30-day projection → safety stock / reorder point → confidence / urgency
      3. Log run to forecast_log
      4. Score accuracy vs yesterday's forecast_log
      5. Save best_alpha + anomaly_days to forecast_params
    """
    today = datetime.now().date()
    yesterday = today - timedelta(days=1)
    start_day = today - timedelta(days=DEMAND_LOOKBACK_DAYS - 1)
    horizon_end = today + timedelta(days=FORECAST_HORIZON_DAYS)

    day_keys = []
    d = start_day
    while d <= today:
        day_keys.append(d.isoformat())
        d += timedelta(days=1)

    with conn.cursor() as cur:
        tables_ok = {
            "forecast_params": _table_exists(cur, "forecast_params"),
            "forecast_log": _table_exists(cur, "forecast_log"),
            "forecast_accuracy": _table_exists(cur, "forecast_accuracy"),
        }
        for table_name, exists in tables_ok.items():
            if not exists:
                logger.warning(
                    "forecast table %s missing — using defaults for that feature",
                    table_name,
                )

        cur.execute(
            """
            SELECT sku, COALESCE(name, sku), COALESCE(stock, 0),
                   COALESCE(min_stock, 0), COALESCE(status, 'Active')
            FROM products
            ORDER BY sku
            """
        )
        products = cur.fetchall()

        cur.execute(
            """
            SELECT sku, substr(CAST(date AS TEXT), 1, 10) AS day_key,
                   COALESCE(SUM(ABS(change)), 0) AS units_out
            FROM movements
            WHERE change < 0
              AND date IS NOT NULL
              AND substr(CAST(date AS TEXT), 1, 10) >= %s
            GROUP BY sku, day_key
            """,
            (start_day.isoformat(),),
        )
        demand_by_sku_day = defaultdict(dict)
        for sku, day_key, units_out in cur.fetchall():
            demand_by_sku_day[sku][day_key] = float(units_out)

        lead_times = _load_lead_times(cur)
        incoming_by_sku = _load_incoming_stock(cur, horizon_end)

        active_skus = [
            sku for sku, _, _, _, status in products
            if str(status).lower() != "old"
        ]
        yesterday_predictions = _load_yesterday_predictions(cur, active_skus, yesterday)
        rolling_accuracy = _load_rolling_accuracy(cur, active_skus)

    results = []
    params_rows = []
    log_rows = []
    accuracy_rows = []
    today_key = today.isoformat()
    today_actual_by_sku = {}

    for sku, name, current_stock, min_stock, status in products:
        if str(status).lower() == "old":
            continue

        current_stock = int(current_stock)
        min_stock = int(min_stock)
        lead_time_days = lead_times.get(sku, DEFAULT_LEAD_TIME_DAYS)
        incoming_30d = incoming_by_sku.get(sku, 0.0)
        rolling_acc = rolling_accuracy.get(sku)

        daily_series = [
            demand_by_sku_day.get(sku, {}).get(day_key, 0.0)
            for day_key in day_keys
        ]
        today_actual_by_sku[sku] = demand_by_sku_day.get(sku, {}).get(today_key, 0.0)
        total_outgoing = sum(daily_series)

        if total_outgoing <= 0:
            confidence_pct, confidence_label = _confidence_score(daily_series, daily_series)
            results.append(_zero_demand_result(
                sku, name, current_stock, min_stock, lead_time_days, incoming_30d,
                confidence_pct=confidence_pct,
                confidence_label=confidence_label,
                rolling_accuracy_pct=rolling_acc,
            ))
            log_rows.append((
                sku, today, 0.0, float(current_stock), float(min_stock),
                False, confidence_pct,
            ))
            continue

        cleaned_series, anomaly_days = _clean_anomalies(daily_series)
        best_alpha = _auto_tune_alpha(cleaned_series)
        velocity = _ewma_velocity(cleaned_series, best_alpha)
        weekday_weights = _weekday_weights(cleaned_series, day_keys)
        confidence_pct, confidence_label = _confidence_score(daily_series, cleaned_series)

        sigma_demand = _std_demand(cleaned_series)
        safety_stock = SERVICE_Z * sigma_demand * math.sqrt(lead_time_days)
        reorder_point = (velocity * lead_time_days) + safety_stock

        weighted_demand_30d = _project_weighted_demand(
            velocity, weekday_weights, today, FORECAST_HORIZON_DAYS,
        )
        projected_stock = current_stock - weighted_demand_30d + incoming_30d

        needs_reorder = projected_stock <= reorder_point
        suggested_raw = (velocity * lead_time_days) + safety_stock - projected_stock
        suggested_order_qty = max(math.ceil(suggested_raw), 0) if needs_reorder else 0

        if velocity <= 0:
            days_until_stockout = 999
            urgency = "OK"
            needs_reorder = False
            suggested_order_qty = 0
        else:
            days_until_stockout = current_stock / velocity
            if days_until_stockout < lead_time_days:
                urgency = "CRITICAL"
            elif days_until_stockout < lead_time_days * 2:
                urgency = "WARNING"
            else:
                urgency = "OK"

        min_stock_alert = current_stock <= min_stock
        if min_stock_alert and urgency == "OK" and velocity > 0:
            urgency = "WARNING"

        results.append({
            "sku_id": sku,
            "sku": sku,
            "sku_name": name,
            "name": name,
            "current_stock": current_stock,
            "current": current_stock,
            "min_stock": min_stock,
            "min_stock_alert": min_stock_alert,
            "velocity_daily": round(velocity, 3),
            "best_alpha": best_alpha,
            "projected_stock_30d": round(projected_stock, 1),
            "reorder_point": round(reorder_point, 1),
            "safety_stock": round(safety_stock, 1),
            "suggested_order_qty": int(suggested_order_qty),
            "suggested": int(suggested_order_qty),
            "days_until_stockout": (
                round(days_until_stockout, 1) if days_until_stockout < 999 else 999
            ),
            "urgency": urgency,
            "needs_reorder": bool(needs_reorder),
            "incoming_30d": round(incoming_30d, 0),
            "lead_time_days": lead_time_days,
            "confidence_pct": confidence_pct,
            "confidence_label": confidence_label,
            "rolling_accuracy_pct": rolling_acc,
            "anomaly_days_excluded": anomaly_days,
        })

        params_rows.append((sku, best_alpha, anomaly_days))
        log_rows.append((
            sku, today, round(velocity, 3), round(projected_stock, 1),
            round(reorder_point, 1), bool(needs_reorder), confidence_pct,
        ))

    # Score accuracy vs yesterday's predictions (Mechanism 4)
    for sku in today_actual_by_sku:
        predicted = yesterday_predictions.get(sku)
        if predicted is None:
            continue
        actual = today_actual_by_sku[sku]
        accuracy_rows.append((
            sku, today, predicted, actual, round(_accuracy_pct(predicted, actual), 1),
        ))

    with conn.cursor() as cur:
        _save_forecast_params(cur, params_rows, tables_ok)
        _save_forecast_log(cur, log_rows, tables_ok)
        _save_forecast_accuracy(cur, accuracy_rows, tables_ok)
    conn.commit()

    # Refresh rolling accuracy after new accuracy rows were saved
    if tables_ok.get("forecast_accuracy") and accuracy_rows:
        with conn.cursor() as cur:
            rolling_accuracy = _load_rolling_accuracy(cur, list(today_actual_by_sku.keys()))
        for row in results:
            sku = row["sku_id"]
            if sku in rolling_accuracy:
                row["rolling_accuracy_pct"] = rolling_accuracy[sku]

    urgency_order = {"CRITICAL": 0, "WARNING": 1, "OK": 2}
    results.sort(
        key=lambda r: (
            urgency_order.get(r["urgency"], 9),
            r["days_until_stockout"],
            -r["velocity_daily"],
        )
    )
    return results
