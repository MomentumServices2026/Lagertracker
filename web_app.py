#!/usr/bin/env python3
"""Mobile-friendly full-featured web interface for Lager Tracker."""

import os
import socket
import time
from datetime import datetime, timedelta

from flask import Flask, Response, jsonify, render_template_string, request, session

from bed_linen_logic import (
    adjust_linen_stock,
    ensure_linen_schema,
    generate_next_linen_sku,
    list_linen_products,
)
from jit_engine import calculate_jit_forecast
from security_config import (
    WEB_PASSCODE,
    build_ssl_context,
    get_session_secret,
    https_enabled,
    is_vercel,
    url_scheme,
)
from web_logic import (
    DEFAULT_LEAD_TIME_DAYS,
    DEFAULT_SERVICE_Z,
    build_ai_analytics_report_pdf,
    build_inventory_report_pdf,
    calculate_forecast_recommendations,
    generate_next_sku,
    get_activity_year_options,
    get_analytics_data,
    get_daily_movement_detail,
    get_movement_activity_summary,
    get_conn,
)

PORT = int(os.environ.get("WEB_PORT", "8080"))
if os.environ.get("VERCEL_GIT_COMMIT_SHA"):
    APP_VERSION = os.environ["VERCEL_GIT_COMMIT_SHA"][:12]
else:
    try:
        APP_VERSION = str(int(os.path.getmtime(os.path.abspath(__file__))))
    except OSError:
        APP_VERSION = "1"
app = Flask(__name__)
app.secret_key = get_session_secret()
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=3650)  # ~10 years — one-time activation per device
if https_enabled():
    app.config["SESSION_COOKIE_SECURE"] = True

AUTH_EXEMPT_PATHS = {"/api/auth/verify", "/api/auth/status"}
JIT_CACHE_TTL_SECONDS = 30 * 60
_jit_forecast_cache = {"payload": None, "ts": 0.0}
_cache = {
    "analytics": {"data": None, "ts": 0.0, "ttl": 600, "key": None},
    "activity": {"data": None, "ts": 0.0, "ttl": 300, "key": None},
    "year_options": {"data": None, "ts": 0.0, "ttl": 3600, "key": None},
    "sections": {"data": None, "ts": 0.0, "ttl": 3600, "key": None},
    "network": {"data": None, "ts": 0.0, "ttl": 3600, "key": None},
}


def _get_cache(name, cache_key="default", force=False):
    entry = _cache[name]
    if force:
        return None
    if entry["data"] is not None and entry.get("key") == cache_key:
        if time.time() - entry["ts"] < entry["ttl"]:
            return entry["data"]
    return None


def _set_cache(name, cache_key, data):
    entry = _cache[name]
    entry["data"] = data
    entry["ts"] = time.time()
    entry["key"] = cache_key


def _api_error(route_name, exc):
    app.logger.error("[%s] %s", route_name, exc)
    return jsonify({"error": "Internal server error", "detail": str(exc)}), 500


def _close_conn(conn):
    if conn:
        conn.close()


def is_authenticated():
    return session.get("authenticated") is True


@app.before_request
def require_passcode():
    if request.path in AUTH_EXEMPT_PATHS:
        return None
    if request.path.startswith("/api/") and not is_authenticated():
        return jsonify({"error": "Unauthorized", "auth_required": True}), 401
    return None


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


PAGE = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-title" content="Inventory">
  <meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
  <meta http-equiv="Pragma" content="no-cache">
  <meta name="app-version" content="{{ app_version }}">
  <title>Momentum Inventory</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
  <style>
    :root {
      --bg: #eef3f8; --surface: #fff; --surface-alt: #f3f8fc;
      --primary: #0c3b5d; --accent: #146b8a; --text: #102a43;
      --muted: #486581; --border: #d9e2ec;
      --green: #16a34a; --red: #ef4444; --amber: #f59e0b;
      --nav-h: 64px;
    }
    * { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
    body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg); color: var(--text); padding-bottom: calc(var(--nav-h) + 16px); }
    header {
      background: var(--primary); color: #fff; padding: 14px 18px;
      position: sticky; top: 0; z-index: 20;
      display: flex; justify-content: space-between; align-items: center; gap: 12px;
    }
    header .header-text { flex: 1; min-width: 0; }
    header h1 { margin: 0; font-size: 1.2rem; font-weight: 700; }
    header .sub { margin: 2px 0 0; font-size: 0.8rem; opacity: 0.8; }
    .header-refresh {
      width: 44px; height: 44px; border: none; border-radius: 12px; flex-shrink: 0;
      background: rgba(255,255,255,0.18); color: #fff; font-size: 1.35rem;
      cursor: pointer; touch-action: manipulation;
    }
    .header-refresh:active { background: rgba(255,255,255,0.32); }
    .screen { display: none; padding: 12px 14px 8px; }
    .screen.active { display: block; }
    .toolbar { display: flex; gap: 8px; margin-bottom: 12px; }
    .toolbar input, .form input, .form select {
      width: 100%; padding: 11px 12px; border: 1px solid var(--border);
      border-radius: 10px; font-size: 16px; background: var(--surface);
    }
    .stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-bottom: 12px; }
    .stat, .kpi {
      background: var(--surface); border-radius: 12px; padding: 12px 10px;
      text-align: center; border: 1px solid var(--border);
    }
    .stat strong, .kpi strong { display: block; font-size: 1.3rem; color: var(--primary); }
    .stat span, .kpi span { font-size: 0.72rem; color: var(--muted); }
    .chip-row { display: flex; gap: 8px; overflow-x: auto; padding-bottom: 4px; margin-bottom: 10px; }
    .chip {
      flex-shrink: 0; padding: 7px 14px; border-radius: 999px; border: 1px solid var(--border);
      background: var(--surface); font-size: 0.85rem; cursor: pointer;
    }
    .chip.active { background: var(--primary); color: #fff; border-color: var(--primary); }
    .section-title {
      font-size: 0.78rem; font-weight: 700; color: var(--accent);
      text-transform: uppercase; letter-spacing: 0.04em; margin: 14px 0 6px;
    }
    .card {
      background: var(--surface); border-radius: 14px; padding: 14px;
      margin-bottom: 8px; border: 1px solid var(--border);
    }
    .card-tap { cursor: pointer; }
    .card.low { border-left: 4px solid var(--red); }
    .card.old { opacity: 0.75; }
    .row1 { display: flex; justify-content: space-between; align-items: center; }
    .sku { font-weight: 700; color: var(--primary); }
    .stock { font-size: 1.3rem; font-weight: 700; }
    .stock.low { color: var(--red); }
    .meta { color: var(--muted); font-size: 0.82rem; margin-top: 4px; }
    .badge {
      display: inline-block; padding: 2px 8px; border-radius: 999px;
      font-size: 0.7rem; font-weight: 700; margin-left: 6px;
    }
    .badge-empty { background: #fee2e2; color: #991b1b; }
    .badge-low { background: #fef3c7; color: #92400e; }
    .badge-ok { background: #dcfce7; color: #166534; }
    .badge-old { background: #f1f5f9; color: #475569; }
    .actions { display: flex; gap: 8px; margin-top: 10px; }
    .actions-full { margin-top: 8px; }
    .move-section {
      margin-top: 10px; display: flex; align-items: center; gap: 8px;
      padding-top: 10px; border-top: 1px solid var(--border);
    }
    .move-section label { font-size: 0.82rem; color: var(--muted); font-weight: 600; flex-shrink: 0; }
    .move-section select {
      flex: 1; padding: 10px 12px; border-radius: 10px; border: 1px solid var(--border);
      font-size: 16px; background: var(--surface); color: var(--text);
    }
    .btn {
      flex: 1; border: none; border-radius: 10px; padding: 12px;
      font-size: 1rem; font-weight: 600; cursor: pointer;
      touch-action: manipulation;
    }
    .btn-primary { background: var(--primary); color: #fff; }
    .btn-green { background: var(--green); color: #fff; }
    .btn-red { background: var(--red); color: #fff; }
    .btn-ghost { background: var(--surface-alt); color: var(--text); border: 1px solid var(--border); }
    .form { background: var(--surface); border-radius: 14px; padding: 16px; border: 1px solid var(--border); }
    .form label { display: block; font-size: 0.82rem; color: var(--muted); margin: 10px 0 4px; }
    .form label:first-child { margin-top: 0; }
    .kpis { display: grid; grid-template-columns: repeat(2, 1fr); gap: 8px; margin-bottom: 14px; }
    .chart-card {
      background: var(--surface); border-radius: 14px; padding: 14px;
      margin-bottom: 12px; border: 1px solid var(--border);
    }
    .section-title,
    .detail-stat span,
    .kpi span,
    .stat span,
    #headerSub {
      text-transform: capitalize;
    }
    .chart-card h3 { margin: 0 0 10px; font-size: 0.95rem; color: var(--primary); }
    .chart-sub { margin: 0 0 10px; font-size: 0.78rem; color: var(--muted); line-height: 1.4; }
    .chart-card-tap { cursor: pointer; touch-action: manipulation; }
    .chart-card-tap:active { background: var(--surface-alt); }
    .activity-card-head {
      display: flex; justify-content: space-between; align-items: flex-start; gap: 10px;
      margin-bottom: 6px;
    }
    .activity-card-head h3 { margin: 0; flex: 1; min-width: 0; }
    .activity-filters {
      display: flex; gap: 6px; flex-shrink: 0;
    }
    .activity-filters select {
      padding: 6px 8px; border: 1px solid var(--border); border-radius: 8px;
      font-size: 0.75rem; background: var(--surface); color: var(--text);
      min-width: 72px; max-width: 92px;
    }
    .activity-filters select:disabled {
      opacity: 0.45; background: var(--surface-alt);
    }
    .activity-day-row {
      display: flex; justify-content: space-between; align-items: center; gap: 10px;
      padding: 12px 0; border-bottom: 1px solid var(--border); cursor: pointer;
      touch-action: manipulation;
    }
    .activity-day-row:active { opacity: 0.7; }
    .activity-day-row strong { display: block; color: var(--primary); font-size: 0.92rem; }
    .activity-day-row span { font-size: 0.78rem; color: var(--muted); }
    .activity-pill {
      flex-shrink: 0; text-align: right; font-size: 0.78rem; font-weight: 700; color: var(--primary);
    }
    .activity-pill small { display: block; font-weight: 500; color: var(--muted); }
    .detail-stats {
      display: grid; grid-template-columns: repeat(2, 1fr); gap: 8px; margin-bottom: 14px;
    }
    .detail-stat {
      background: var(--surface-alt); border-radius: 10px; padding: 10px; text-align: center;
    }
    .detail-stat strong { display: block; font-size: 1.15rem; color: var(--primary); }
    .detail-stat span { font-size: 0.72rem; color: var(--muted); }
    .movement-row {
      padding: 10px 0; border-bottom: 1px solid var(--border); font-size: 0.84rem;
    }
    .movement-row .row1 { align-items: flex-start; }
    .movement-row .chg-in { color: var(--green); font-weight: 700; }
    th.sortable { cursor: pointer; user-select: none; white-space: nowrap; }
    th.sortable:active { color: var(--accent); }
    .jit-name { max-width: 110px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .jit-alpha { font-variant-numeric: tabular-nums; color: var(--muted); cursor: help; }
    .confidence-badge {
      display: inline-block; font-size: 0.72rem; padding: 2px 6px; border-radius: 6px;
      white-space: nowrap; font-weight: 600;
    }
    .confidence-limited { background: #fef3c7; color: #92400e; }
    .confidence-improving { background: #dbeafe; color: #1e40af; }
    .confidence-reliable { background: #dcfce7; color: #166534; }
    .intel-card {
      background: linear-gradient(135deg, #102a43 0%, #1e4d6b 100%);
      color: #fff; border-radius: 14px; padding: 16px; margin-bottom: 12px;
    }
    .intel-card h3 { margin: 0 0 10px; font-size: 0.95rem; font-weight: 700; }
    .intel-stats { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
    .intel-stat {
      background: rgba(255,255,255,0.12); border-radius: 10px; padding: 10px;
    }
    .intel-stat strong { display: block; font-size: 1.2rem; }
    .intel-stat span { font-size: 0.72rem; opacity: 0.85; }
    .intel-hint { margin: 10px 0 0; font-size: 0.75rem; opacity: 0.75; line-height: 1.4; }
    .sheet-scroll { max-height: 70vh; overflow-y: auto; -webkit-overflow-scrolling: touch; }
    .chart-wrap { position: relative; height: 220px; }
    .chart-wrap.tall { height: 260px; }
    .chart-wrap.movers { height: 300px; }
    .table-wrap { overflow-x: auto; }
    table { width: 100%; border-collapse: collapse; font-size: 0.82rem; }
    th, td { padding: 8px 6px; text-align: left; border-bottom: 1px solid var(--border); }
    th { color: var(--muted); font-weight: 600; }
    .empty { text-align: center; padding: 40px 16px; color: var(--muted); }
    .info-box {
      background: var(--surface-alt); border-radius: 12px; padding: 12px;
      font-size: 0.85rem; color: var(--muted); line-height: 1.5; margin-bottom: 12px;
    }
    nav {
      position: fixed; bottom: 0; left: 0; right: 0; height: var(--nav-h);
      background: var(--surface); border-top: 1px solid var(--border);
      display: grid; grid-template-columns: repeat(5, 1fr); z-index: 30;
      padding-bottom: env(safe-area-inset-bottom);
    }
    nav button {
      border: none; background: none; padding: 8px 4px; font-size: 0.65rem;
      color: var(--muted); cursor: pointer; display: flex; flex-direction: column;
      align-items: center; gap: 3px;
    }
    nav button .ico { font-size: 1.2rem; }
    nav button.active { color: var(--primary); font-weight: 700; }
    .overlay {
      display: none; position: fixed; inset: 0; background: rgba(16,42,67,0.45);
      z-index: 40; align-items: flex-end; justify-content: center;
    }
    .overlay.open { display: flex; }
    .sheet {
      background: var(--surface); width: 100%; max-height: 88vh; overflow-y: auto;
      border-radius: 18px 18px 0 0; padding: 18px 16px 24px;
    }
    .sheet h2 { margin: 0 0 14px; font-size: 1.1rem; color: var(--primary); }
    .sheet .handle {
      width: 40px; height: 4px; background: var(--border); border-radius: 99px;
      margin: 0 auto 14px;
    }
    .toast {
      position: fixed; bottom: calc(var(--nav-h) + 12px); left: 50%;
      transform: translateX(-50%); background: var(--primary); color: #fff;
      padding: 10px 18px; border-radius: 999px; font-size: 0.9rem;
      opacity: 0; transition: opacity 0.2s; pointer-events: none; z-index: 50;
    }
    .toast.show { opacity: 1; }
    .year-select { margin-bottom: 12px; }
    .export-tile {
      display: flex; flex-direction: column; align-items: center; justify-content: center;
      width: 100%; padding: 20px 16px; margin-top: 12px; border: 1px solid var(--border);
      border-radius: 14px; background: var(--surface); cursor: pointer;
      touch-action: manipulation;
    }
    .export-tile:active { background: var(--surface-alt); }
    .export-tile .export-ico { font-size: 2rem; line-height: 1; margin-bottom: 8px; }
    .export-tile .export-label { font-size: 0.95rem; font-weight: 700; color: var(--primary); }
    .export-tile .export-hint {
      font-size: 0.75rem; color: var(--muted); margin-top: 6px; text-align: center; line-height: 1.4;
    }
    .back-bar { margin-bottom: 10px; }
    .back-btn {
      border: none; background: var(--surface); color: var(--primary); font-weight: 700;
      padding: 10px 14px; border-radius: 10px; border: 1px solid var(--border); cursor: pointer;
      touch-action: manipulation;
    }
    .lock-screen {
      display: none; position: fixed; inset: 0; z-index: 10000;
      background: #f2f2f7; align-items: center; justify-content: center;
      padding: 24px 20px 32px;
    }
    .lock-screen.visible { display: flex; flex-direction: column; }
    body.locked header, body.locked .screen, body.locked nav { visibility: hidden; }
    .lock-inner {
      width: 100%; max-width: 360px; margin: auto 0;
      display: flex; flex-direction: column; align-items: center;
    }
    .lock-icon { font-size: 2.4rem; margin-bottom: 10px; }
    .lock-title {
      margin: 0 0 28px; font-size: 1.35rem; font-weight: 600; color: #1c1c1e;
    }
    .lock-dots {
      display: flex; gap: 18px; margin-bottom: 12px; min-height: 18px;
    }
    .lock-dots span {
      width: 14px; height: 14px; border-radius: 50%;
      border: 2px solid #8e8e93; background: transparent;
      transition: background 0.15s, border-color 0.15s;
    }
    .lock-dots span.filled { background: #1c1c1e; border-color: #1c1c1e; }
    .lock-dots.shake { animation: lockShake 0.45s ease; }
    @keyframes lockShake {
      0%, 100% { transform: translateX(0); }
      20% { transform: translateX(-8px); }
      40% { transform: translateX(8px); }
      60% { transform: translateX(-6px); }
      80% { transform: translateX(6px); }
    }
    .lock-error {
      min-height: 20px; margin: 0 0 18px; font-size: 0.9rem; color: #ff3b30; text-align: center;
    }
    .lock-keypad {
      display: grid; grid-template-columns: repeat(3, 1fr); gap: 18px;
      width: 100%; max-width: 300px;
    }
    .lock-key {
      aspect-ratio: 1; border: none; border-radius: 50%;
      background: #fff; color: #1c1c1e; font-size: 2rem; font-weight: 400;
      box-shadow: 0 1px 0 rgba(0,0,0,0.06); cursor: pointer;
      touch-action: manipulation; user-select: none;
      display: flex; align-items: center; justify-content: center;
    }
    .lock-key:active { background: #e5e5ea; }
    .lock-key.blank { visibility: hidden; pointer-events: none; box-shadow: none; }
    .lock-key.delete { font-size: 1.5rem; font-weight: 500; }
  </style>
</head>
<body>
  <div id="lockScreen" class="lock-screen">
    <div class="lock-inner">
      <div class="lock-icon">🔒</div>
      <h2 class="lock-title">Enter Passcode</h2>
      <div class="lock-dots" id="lockDots">
        <span></span><span></span><span></span><span></span>
      </div>
      <p class="lock-error" id="lockError"></p>
      <div class="lock-keypad">
        <button type="button" class="lock-key" onclick="lockDigit('1')">1</button>
        <button type="button" class="lock-key" onclick="lockDigit('2')">2</button>
        <button type="button" class="lock-key" onclick="lockDigit('3')">3</button>
        <button type="button" class="lock-key" onclick="lockDigit('4')">4</button>
        <button type="button" class="lock-key" onclick="lockDigit('5')">5</button>
        <button type="button" class="lock-key" onclick="lockDigit('6')">6</button>
        <button type="button" class="lock-key" onclick="lockDigit('7')">7</button>
        <button type="button" class="lock-key" onclick="lockDigit('8')">8</button>
        <button type="button" class="lock-key" onclick="lockDigit('9')">9</button>
        <button type="button" class="lock-key blank" tabindex="-1" aria-hidden="true"></button>
        <button type="button" class="lock-key" onclick="lockDigit('0')">0</button>
        <button type="button" class="lock-key delete" onclick="lockDelete()">⌫</button>
      </div>
    </div>
  </div>
  <header>
    <div class="header-text">
      <h1>Momentum Inventory</h1>
      <div class="sub" id="headerSub">Stock overview</div>
    </div>
    <button type="button" class="header-refresh" onclick="hardRefresh()" title="Reload app">↻</button>
  </header>

  <!-- STOCK -->
  <div id="screen-stock" class="screen active">
    <div class="toolbar">
      <input id="searchQ" type="search" placeholder="Search SKU, name, brand…">
    </div>
    <div class="stats">
      <div class="stat"><strong id="sTotal">–</strong><span>Items</span></div>
      <div class="stat"><strong id="sLow">–</strong><span>Low</span></div>
      <div class="stat"><strong id="sUnits">–</strong><span>Units</span></div>
    </div>
    <div class="chip-row" id="sectionChips"></div>
    <div id="productList"></div>
  </div>

  <!-- ADD -->
  <div id="screen-add" class="screen">
    <div class="form">
      <label>SKU</label>
      <input id="addSku" placeholder="Auto-generated">
      <label>Name</label>
      <input id="addName">
      <label>Brand</label>
      <input id="addBrand">
      <label>Stock</label>
      <input id="addStock" type="number" inputmode="numeric" value="0">
      <label>Min Stock</label>
      <input id="addMin" type="number" inputmode="numeric" value="0">
      <label>Section</label>
      <select id="addSection"></select>
      <button class="btn btn-primary" style="width:100%;margin-top:16px" onclick="addProduct()">Add Product</button>
    </div>
  </div>

  <!-- ANALYTICS -->
  <div id="screen-analytics" class="screen">
    <div class="kpis" id="analyticsKpis"></div>
    <div class="year-select">
      <select id="yearSelect" onchange="loadAnalytics()"></select>
    </div>
    <div class="chart-card"><h3>Stock Health</h3><div class="chart-wrap"><canvas id="chartHealth"></canvas></div></div>
    <div class="chart-card"><h3>Top Moved Items</h3>
      <p class="chart-sub">All time · green = in · red = out</p>
      <div class="chart-wrap movers"><canvas id="chartMovers"></canvas></div></div>
    <div class="chart-card chart-card-tap" id="activitySection" onclick="handleActivityTileClick(event)">
      <div class="activity-card-head">
        <h3 id="activityCardTitle">Warehouse Activity</h3>
        <div class="activity-filters" onclick="event.stopPropagation()">
          <select id="activityYear" onchange="onActivityPeriodChange()" aria-label="Activity year"></select>
          <select id="activityMonth" onchange="onActivityPeriodChange()" aria-label="Activity month"></select>
        </div>
      </div>
      <p class="chart-sub" id="activitySummary">Loading activity…</p>
      <div class="chart-wrap tall"><canvas id="chartTrend"></canvas></div>
    </div>
    <div class="chart-card"><h3>Monthly Change</h3><div class="chart-wrap tall"><canvas id="chartMonthly"></canvas></div></div>
    <div class="chart-card" id="jitSection">
      <h3>JIT Reorder Forecast</h3>
      <p class="chart-sub" id="jitSummary">Urgent reorders only · tap column headers to sort</p>
      <div class="table-wrap"><table id="jitTable"><thead><tr>
        <th class="sortable" onclick="sortJit('sku')">SKU</th>
        <th class="sortable" onclick="sortJit('name')">Name</th>
        <th class="sortable" onclick="sortJit('current_stock')">Stock</th>
        <th class="sortable" onclick="sortJit('projected_stock_30d')">30d Proj</th>
        <th class="sortable" onclick="sortJit('velocity_daily')">Vel/day</th>
        <th class="sortable" onclick="sortJit('reorder_point')">ROP</th>
        <th class="sortable" onclick="sortJit('suggested_order_qty')">Order</th>
        <th class="sortable" onclick="sortJit('days_until_stockout')">Days Out</th>
        <th class="sortable" onclick="sortJit('min_stock')">Min</th>
        <th class="sortable" onclick="sortJit('confidence_pct')">Conf</th>
        <th class="sortable" onclick="sortJit('best_alpha')" title="Sensitivity — higher = reacts faster to demand changes">α</th>
        <th class="sortable" onclick="sortJit('urgency')">Status</th>
      </tr></thead><tbody></tbody></table></div>
    </div>
  </div>

  <!-- FORECAST -->
  <div id="screen-forecast" class="screen">
    <div id="forecastIntelCard" class="intel-card" style="display:none">
      <h3>Forecast Intelligence</h3>
      <div class="intel-stats">
        <div class="intel-stat"><strong id="forecastAvgConf">—</strong><span>Avg confidence</span></div>
        <div class="intel-stat"><strong id="forecastAvgAcc">—</strong><span>30-day accuracy</span></div>
      </div>
      <p class="intel-hint">Accuracy improves automatically as data grows</p>
    </div>
    <div class="info-box">
      Self-learning reorder forecast — adapts to each SKU's demand pattern.
      <br><br>
      <strong>CRITICAL</strong> = stockout before lead time · <strong>WARNING</strong> = reorder soon
    </div>
    <p class="chart-sub" id="forecastSummary">Loading forecast…</p>
    <div class="table-wrap">
      <table id="forecastTable"><thead><tr>
        <th class="sortable" onclick="sortForecast('sku')">SKU</th>
        <th class="sortable" onclick="sortForecast('name')">Name</th>
        <th class="sortable" onclick="sortForecast('current_stock')">Stock</th>
        <th class="sortable" onclick="sortForecast('projected_stock_30d')">30d Proj</th>
        <th class="sortable" onclick="sortForecast('velocity_daily')">Vel/day</th>
        <th class="sortable" onclick="sortForecast('reorder_point')">ROP</th>
        <th class="sortable" onclick="sortForecast('suggested_order_qty')">Order</th>
        <th class="sortable" onclick="sortForecast('days_until_stockout')">Days Out</th>
        <th class="sortable" onclick="sortForecast('confidence_pct')">Conf</th>
        <th class="sortable" onclick="sortForecast('best_alpha')" title="Sensitivity — higher = reacts faster to demand changes">α</th>
        <th class="sortable" onclick="sortForecast('urgency')">Status</th>
      </tr></thead><tbody></tbody></table>
    </div>
  </div>

  <!-- BED LINEN (separate from main stock) -->
  <div id="screen-linen" class="screen">
    <div class="back-bar">
      <button type="button" class="back-btn" onclick="go('more')">← Back to More</button>
    </div>
    <div class="toolbar">
      <input id="linenSearchQ" type="search" placeholder="Search SKU, name, type…">
      <button type="button" class="btn btn-primary" style="flex:0;padding:10px 14px" onclick="openLinenAdd()">+ Add</button>
    </div>
    <div class="stats">
      <div class="stat"><strong id="lTotal">–</strong><span>Items</span></div>
      <div class="stat"><strong id="lLow">–</strong><span>Low</span></div>
      <div class="stat"><strong id="lUnits">–</strong><span>Units</span></div>
    </div>
    <div class="chip-row" id="linenSectionChips"></div>
    <div id="linenProductList"></div>
  </div>

  <!-- MORE -->
  <div id="screen-more" class="screen">
    <div class="form" style="margin-bottom:12px">
      <label>New Section</label>
      <input id="newSection" placeholder="Section name">
      <button class="btn btn-primary" style="width:100%;margin-top:12px" onclick="addSection()">Add Section</button>
    </div>
    <div class="chart-card">
      <h3>Sections</h3>
      <div id="sectionList"></div>
    </div>
    <button type="button" class="export-tile" onclick="exportReport()">
      <span class="export-ico">📤</span>
      <span class="export-label">Export Report</span>
      <span class="export-hint">PDF · same report as the desktop app</span>
    </button>
    <button type="button" class="export-tile" onclick="openLinen()">
      <span class="export-ico">🛏️</span>
      <span class="export-label">Bed Linen Storage</span>
      <span class="export-hint">Separate tracking — not shown in main Stock</span>
    </button>
    <button type="button" class="export-tile" onclick="exportAiReport()">
      <span class="export-ico">🤖</span>
      <span class="export-label">AI Analytics Report</span>
      <span class="export-hint">PDF tables · feed to ChatGPT for purchase advice</span>
    </button>
    <div class="chart-card" style="margin-top:12px">
      <h3>Connect from iPhone</h3>
      <div id="connectInfo" class="info-box" style="margin:0">Loading…</div>
      <p style="font-size:0.82rem;color:var(--muted);margin:8px 0 0" id="connectHint">
        Use the secure URL shown above. Same Wi-Fi as this Mac (not guest network).
      </p>
    </div>
    <div class="info-box" style="margin-top:12px">
      PDF export, CSV import, and email reports are available in the Mac desktop app.
    </div>
  </div>

  <!-- LINEN PRODUCT SHEET -->
  <div class="overlay" id="linenSheet" onclick="if(event.target===this)closeLinenSheet()">
    <div class="sheet">
      <div class="handle"></div>
      <h2 id="linenSheetTitle">Linen Item</h2>
      <div class="form">
        <label>Name</label><input id="linenEditName">
        <label>Type / Brand</label><input id="linenEditBrand">
        <label>Stock</label><input id="linenEditStock" type="number">
        <label>Min Stock</label><input id="linenEditMin" type="number">
        <label>Status</label>
        <select id="linenEditStatus"><option value="Active">Active</option><option value="Old">Old</option></select>
        <label>Section</label><select id="linenEditSection"></select>
      </div>
      <div class="actions" style="margin-top:14px">
        <button class="btn btn-green" onclick="linenSheetStock(1)">+ 1</button>
        <button class="btn btn-red" onclick="linenSheetStock(-1)">− 1</button>
      </div>
      <div class="actions">
        <button class="btn btn-red" id="linenToggleOldBtn" onclick="toggleLinenOld()">Mark Old</button>
      </div>
      <div class="actions">
        <button class="btn btn-primary" onclick="saveLinenProduct()">Save Changes</button>
        <button class="btn btn-red" onclick="deleteLinenProduct()">Delete</button>
      </div>
    </div>
  </div>

  <!-- LINEN ADD SHEET -->
  <div class="overlay" id="linenAddSheet" onclick="if(event.target===this)closeLinenAdd()">
    <div class="sheet">
      <div class="handle"></div>
      <h2>Add Bed Linen Item</h2>
      <div class="form">
        <label>SKU</label><input id="linenAddSku" placeholder="Auto-generated">
        <label>Name</label><input id="linenAddName">
        <label>Type / Brand</label><input id="linenAddBrand">
        <label>Stock</label><input id="linenAddStock" type="number" value="0">
        <label>Min Stock</label><input id="linenAddMin" type="number" value="0">
        <label>Section</label><select id="linenAddSection"></select>
      </div>
      <button class="btn btn-primary" style="width:100%;margin-top:16px" onclick="addLinenProduct()">Add Item</button>
    </div>
  </div>

  <!-- PRODUCT SHEET -->
  <div class="overlay" id="activitySheet" onclick="if(event.target===this)closeActivitySheet()">
    <div class="sheet">
      <div class="handle"></div>
      <h2 id="activitySheetTitle">Warehouse Activity</h2>
      <div id="activitySheetBody" class="sheet-scroll"></div>
    </div>
  </div>

  <div class="overlay" id="productSheet" onclick="if(event.target===this)closeSheet()">
    <div class="sheet">
      <div class="handle"></div>
      <h2 id="sheetTitle">Product</h2>
      <div class="form">
        <label>Name</label><input id="editName">
        <label>Brand</label><input id="editBrand">
        <label>Stock</label><input id="editStock" type="number">
        <label>Min Stock</label><input id="editMin" type="number">
        <label>Status</label>
        <select id="editStatus"><option value="Active">Active</option><option value="Old">Old</option></select>
        <label>Section</label><select id="editSection"></select>
      </div>
      <div class="actions" style="margin-top:14px">
        <button class="btn btn-green" onclick="sheetStock(1)">+ 1</button>
        <button class="btn btn-red" onclick="sheetStock(-1)">− 1</button>
      </div>
      <div class="actions">
        <button class="btn btn-red" id="toggleOldBtn" onclick="toggleOld()">Mark Old</button>
        <button class="btn btn-ghost" onclick="markDamaged()">Damage −1</button>
      </div>
      <div class="actions">
        <button class="btn btn-primary" onclick="saveProduct()">Save Changes</button>
        <button class="btn btn-red" onclick="deleteProduct()">Delete</button>
      </div>
    </div>
  </div>

  <nav>
    <button class="active" data-screen="stock" onclick="go('stock')"><span class="ico">📦</span>Stock</button>
    <button data-screen="add" onclick="go('add')"><span class="ico">➕</span>Add</button>
    <button data-screen="analytics" onclick="go('analytics')"><span class="ico">📊</span>Analytics</button>
    <button data-screen="forecast" onclick="go('forecast')"><span class="ico">🔮</span>Forecast</button>
    <button data-screen="more" onclick="go('more')"><span class="ico">⚙️</span>More</button>
  </nav>
  <div class="toast" id="toast"></div>

  <script>
    let products = [], sections = [], activeSection = 'General', activeSku = null;
    let linenProducts = [], linenSections = [], linenActiveSection = 'General', linenActiveSku = null;
    let charts = {};
    let activityDays = [];
    let cachedActivity = null;
    let activeScreen = 'stock';
    let passcodeEntry = '';
    const PASSCODE_LEN = 4;

    function showLockScreen() {
      passcodeEntry = '';
      updateLockDots();
      document.getElementById('lockError').textContent = '';
      document.getElementById('lockScreen').classList.add('visible');
      document.body.classList.add('locked');
    }

    function hideLockScreen() {
      document.getElementById('lockScreen').classList.remove('visible');
      document.body.classList.remove('locked');
    }

    function updateLockDots() {
      const dots = document.querySelectorAll('#lockDots span');
      dots.forEach((dot, i) => dot.classList.toggle('filled', i < passcodeEntry.length));
    }

    function lockDigit(d) {
      if (passcodeEntry.length >= PASSCODE_LEN) return;
      passcodeEntry += d;
      updateLockDots();
      document.getElementById('lockError').textContent = '';
      if (passcodeEntry.length === PASSCODE_LEN) verifyPasscode();
    }

    function lockDelete() {
      passcodeEntry = passcodeEntry.slice(0, -1);
      updateLockDots();
      document.getElementById('lockError').textContent = '';
    }

    async function verifyPasscode() {
      try {
        const res = await fetch('/api/auth/verify', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          credentials: 'same-origin',
          body: JSON.stringify({passcode: passcodeEntry})
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok) {
          hideLockScreen();
          loadProducts();
          return;
        }
        const dots = document.getElementById('lockDots');
        dots.classList.remove('shake');
        void dots.offsetWidth;
        dots.classList.add('shake');
        document.getElementById('lockError').textContent = 'Wrong passcode';
        setTimeout(() => {
          passcodeEntry = '';
          updateLockDots();
        }, 400);
      } catch (e) {
        document.getElementById('lockError').textContent = 'Could not verify';
        passcodeEntry = '';
        updateLockDots();
      }
    }

    async function initAuth() {
      try {
        const res = await fetch('/api/auth/status', {credentials: 'same-origin'});
        const data = await res.json();
        if (data.authenticated) hideLockScreen();
        else showLockScreen();
      } catch (e) {
        showLockScreen();
      }
    }

    function formatDayLabel(day) {
      const d = new Date(day + 'T12:00:00');
      return d.toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' });
    }

    function formatDayShort(day) {
      return day.slice(5).replace('-', '/');
    }

    const MONTH_NAMES = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
      'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

    function buildActivityFilterOptions(years) {
      const now = new Date();
      const yearSel = document.getElementById('activityYear');
      const monthSel = document.getElementById('activityMonth');
      if (!yearSel || !monthSel) return;
      const yrList = (years && years.length) ? years : [now.getFullYear()];
      yearSel.innerHTML =
        '<option value="rolling">30 Days</option>' +
        yrList.map(y => `<option value="${y}">${y}</option>`).join('');
      monthSel.innerHTML = MONTH_NAMES.map((m, i) =>
        `<option value="${i + 1}">${m}</option>`
      ).join('');
    }

    function resetActivityFilters() {
      const yearSel = document.getElementById('activityYear');
      const monthSel = document.getElementById('activityMonth');
      if (!yearSel || !monthSel) return;
      yearSel.value = 'rolling';
      monthSel.value = String(new Date().getMonth() + 1);
      monthSel.disabled = true;
      cachedActivity = null;
    }

    function syncActivityMonthState() {
      const monthSel = document.getElementById('activityMonth');
      if (!monthSel) return;
      monthSel.disabled = document.getElementById('activityYear').value === 'rolling';
    }

    function getActivityPeriod() {
      const yearVal = document.getElementById('activityYear').value;
      if (yearVal === 'rolling') return { mode: 'rolling' };
      return {
        mode: 'month',
        year: +yearVal,
        month: +document.getElementById('activityMonth').value,
      };
    }

    function onActivityPeriodChange() {
      syncActivityMonthState();
      loadActivityChart();
    }

    function renderActivityChart(act) {
      activityDays = act.days;
      cachedActivity = act;
      const s = act.summary;
      const busiest = s.busiest_day ? formatDayLabel(s.busiest_day) : '—';
      document.getElementById('activityCardTitle').textContent =
        `Warehouse Activity — ${act.period_label}`;
      document.getElementById('activitySummary').textContent =
        `${s.total_movements} movements · ${s.total_activity} units · ` +
        `avg ${s.avg_activity_per_active_day}/active day · busiest: ${busiest}`;

      makeChart('chartTrend', {
        type: 'bar',
        data: {
          labels: activityDays.map(d => formatDayShort(d.day)),
          datasets: [
            {
              label: 'Returns & Deliveries',
              data: activityDays.map(d => d.inbound),
              backgroundColor: 'rgba(34,197,94,0.85)',
              borderRadius: 3,
            },
            {
              label: 'Out To Customers',
              data: activityDays.map(d => d.outbound),
              backgroundColor: 'rgba(239,68,68,0.85)',
              borderRadius: 3,
            },
          ],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          interaction: { mode: 'index', intersect: false },
          onClick: (evt, elements) => {
            const native = evt.native || evt.event;
            if (native) native.stopPropagation();
            if (elements.length) {
              const idx = elements[0].index;
              if (activityDays[idx]) openDayDetail(activityDays[idx].day);
            } else {
              openActivityOverview();
            }
          },
          onHover: (evt, elements) => {
            const canvas = evt.native?.target || evt.event?.target;
            if (canvas) canvas.style.cursor = elements.length ? 'pointer' : 'default';
          },
          plugins: {
            legend: { position: 'bottom', labels: { boxWidth: 12, font: { size: 11 } } },
            tooltip: {
              callbacks: {
                footer: (items) => {
                  const idx = items[0].dataIndex;
                  const d = activityDays[idx];
                  return d ? `${d.movement_count} movements · ${d.activity} units` : '';
                },
              },
            },
          },
          scales: {
            x: {
              stacked: true,
              ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 10, font: { size: 10 } },
            },
            y: { stacked: true, beginAtZero: true, ticks: { precision: 0 } },
          },
        },
      }, true);
    }

    function analyticsForceRefresh() {
      return jitForecastForceRefresh() ? '&force=1' : '';
    }

    function analyticsForceQuery() {
      return jitForecastForceRefresh() ? '?force=1' : '';
    }

    let activityObserver = null;
    let jitObserver = null;

    function setupDeferredAnalyticsLoads() {
      if (activityObserver) activityObserver.disconnect();
      if (jitObserver) jitObserver.disconnect();

      const activityEl = document.getElementById('activitySection');
      if (activityEl) {
        activityObserver = new IntersectionObserver((entries) => {
          if (entries[0].isIntersecting) {
            loadActivityChart();
            activityObserver.disconnect();
            activityObserver = null;
          }
        }, { threshold: 0.1 });
        activityObserver.observe(activityEl);
      }

      const jitEl = document.getElementById('jitSection');
      if (jitEl) {
        jitObserver = new IntersectionObserver((entries) => {
          if (entries[0].isIntersecting) {
            loadJitForecast();
            jitObserver.disconnect();
            jitObserver = null;
          }
        }, { threshold: 0.1 });
        jitObserver.observe(jitEl);
      }
    }

    async function loadActivityChart() {
      try {
        const period = getActivityPeriod();
        const force = analyticsForceRefresh();
        const url = period.mode === 'rolling'
          ? `/api/analytics/activity${analyticsForceQuery()}`
          : `/api/analytics/activity?year=${period.year}&month=${period.month}${force}`;
        const data = await api(url);
        renderActivityChart(data);
      } catch (e) { toast(e.message); }
    }

    function handleActivityTileClick(event) {
      if (event.target.closest('canvas')) return;
      openActivityOverview();
    }

    function movementRowHtml(m) {
      const cls = m.change > 0 ? 'chg-in' : 'chg-out';
      const sign = m.change > 0 ? '+' : '';
      const extra = [m.reason, m.customer, m.time].filter(Boolean).join(' · ');
      return `<div class="movement-row">
        <div class="row1">
          <div><span class="sku">${m.sku}</span> ${m.name}</div>
          <div class="${cls}">${sign}${m.change}</div>
        </div>
        ${extra ? `<div class="meta">${extra}</div>` : ''}
      </div>`;
    }

    function closeActivitySheet() {
      document.getElementById('activitySheet').classList.remove('open');
    }

    function renderActivityOverview(activity) {
      const s = activity.summary;
      const days = [...activity.days].sort((a, b) => b.activity - a.activity);
      const activeRows = days.filter(d => d.activity > 0);
      document.getElementById('activitySheetTitle').textContent =
        activity.period_label || 'Warehouse Activity';
      document.getElementById('activitySheetBody').innerHTML = `
        <div class="detail-stats">
          <div class="detail-stat"><strong>${s.total_activity}</strong><span>units moved</span></div>
          <div class="detail-stat"><strong>${s.total_movements}</strong><span>stock movements</span></div>
          <div class="detail-stat"><strong>${s.total_inbound}</strong><span>returns & deliveries</span></div>
          <div class="detail-stat"><strong>${s.total_outbound}</strong><span>out to customers</span></div>
          <div class="detail-stat"><strong>${s.active_days}</strong><span>active days</span></div>
          <div class="detail-stat"><strong>${s.avg_activity_per_active_day}</strong><span>avg / active day</span></div>
        </div>
        <div class="section-title">busiest days</div>
        ${activeRows.length ? activeRows.map(d => `
          <div class="activity-day-row" onclick="openDayDetail('${d.day}')">
            <div>
              <strong>${formatDayLabel(d.day)}</strong>
              <span>${d.movement_count} movements · ${d.inbound} in · ${d.outbound} out</span>
            </div>
            <div class="activity-pill">${d.activity}<small>units</small></div>
          </div>
        `).join('') : '<div class="empty">No movement in the last 30 days</div>'}
        <div class="section-title" style="margin-top:16px">quiet days</div>
        ${days.filter(d => d.activity === 0).slice(0, 5).map(d => `
          <div class="activity-day-row" onclick="openDayDetail('${d.day}')">
            <div><strong>${formatDayLabel(d.day)}</strong><span>no stock movement</span></div>
            <div class="activity-pill">0<small>units</small></div>
          </div>
        `).join('') || '<div class="meta">Every day had some activity</div>'}
      `;
      document.getElementById('activitySheet').classList.add('open');
    }

    async function openActivityOverview() {
      try {
        if (!cachedActivity) await loadActivityChart();
        if (cachedActivity) renderActivityOverview(cachedActivity);
      } catch (e) { toast(e.message); }
    }

    async function openDayDetail(day) {
      try {
        const data = await api(`/api/analytics/day/${day}`);
        document.getElementById('activitySheetTitle').textContent = formatDayLabel(day);
        const inbound = data.movements.filter(m => m.change > 0);
        const outbound = data.movements.filter(m => m.change < 0);
        const inboundRows = inbound.length
          ? inbound.map(movementRowHtml).join('')
          : '<div class="empty">No returns or deliveries</div>';
        const outboundRows = outbound.length
          ? outbound.map(movementRowHtml).join('')
          : '<div class="empty">Nothing sent to customers</div>';

        const topItems = data.top_items.length ? data.top_items.map(t =>
          `<div class="movement-row"><div class="row1"><div>${t.sku} · ${t.name}</div><div>${t.units} units</div></div></div>`
        ).join('') : '';

        const byReason = data.by_reason.length ? data.by_reason.map(r =>
          `<div class="movement-row"><div class="row1"><div>${r.reason}</div><div>${r.units} u · ${r.count}×</div></div></div>`
        ).join('') : '';

        document.getElementById('activitySheetBody').innerHTML = `
          <button type="button" class="back-btn" style="margin-bottom:12px" onclick="openActivityOverview()">← All days</button>
          <div class="detail-stats">
            <div class="detail-stat"><strong>${data.activity}</strong><span>units moved</span></div>
            <div class="detail-stat"><strong>${data.movement_count}</strong><span>stock movements</span></div>
            <div class="detail-stat"><strong>${data.inbound}</strong><span>returns & deliveries</span></div>
            <div class="detail-stat"><strong>${data.outbound}</strong><span>out to customers</span></div>
          </div>
          <div class="section-title">out to customers</div>
          ${outboundRows}
          <div class="section-title">returns & deliveries</div>
          ${inboundRows}
          ${topItems ? `<div class="section-title">top items</div>${topItems}` : ''}
          ${byReason ? `<div class="section-title">by reason</div>${byReason}` : ''}
        `;
        document.getElementById('activitySheet').classList.add('open');
      } catch (e) { toast(e.message); }
    }

    function hardRefresh() {
      const base = (window.location.pathname || '/').split('?')[0];
      window.location.replace(base + '?v=' + Date.now());
    }

    const subtitles = {
      stock: 'stock overview', add: 'add new product', analytics: 'analytics dashboard',
      forecast: 'reorder forecast', more: 'sections & settings', linen: 'bed linen storage'
    };

    function toast(msg) {
      const el = document.getElementById('toast');
      el.textContent = msg; el.classList.add('show');
      setTimeout(() => el.classList.remove('show'), 2000);
    }

    function go(screen) {
      if (activeScreen === 'analytics' && screen !== 'analytics') {
        resetActivityFilters();
      }
      activeScreen = screen;
      document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
      document.getElementById('screen-' + screen).classList.add('active');
      document.querySelectorAll('nav button').forEach(b => {
        b.classList.toggle('active', b.dataset.screen === screen);
      });
      document.getElementById('headerSub').textContent = subtitles[screen] || '';
      if (screen === 'stock') loadProducts();
      if (screen === 'add') prepAddForm();
      if (screen === 'analytics') loadAnalytics();
      if (screen === 'forecast') loadForecast();
      if (screen === 'more') { loadSections(); loadConnectInfo(); }
      if (screen === 'linen') loadLinenProducts();
    }

    function openLinen() { go('linen'); }

    async function api(url, opts={}) {
      const res = await fetch(url, {credentials: 'same-origin', ...opts});
      const data = await res.json().catch(() => ({}));
      if (res.status === 401 && data.auth_required) {
        showLockScreen();
        throw new Error('Passcode required');
      }
      if (!res.ok) throw new Error(data.error || 'Request failed');
      return data;
    }

    async function loadProducts() {
      try {
        const data = await api('/api/products');
        products = data.products;
        sections = data.sections;
        renderChips();
        renderProducts();
        fillSectionSelects();
      } catch (e) { toast(e.message); }
    }

    function fillSectionSelects() {
      ['addSection', 'editSection'].forEach(id => {
        const el = document.getElementById(id);
        if (!el) return;
        el.innerHTML = sections.map(s => `<option value="${s}">${s}</option>`).join('');
      });
    }

    function sectionOptions(current) {
      const cur = current || 'General';
      return sections.map(s =>
        `<option value="${esc(s)}"${s === cur ? ' selected' : ''}>${esc(s)}</option>`
      ).join('');
    }

    function renderChips() {
      const otherSections = sections.filter(s => s !== 'General');
      const chips = ['General', ...otherSections];
      document.getElementById('sectionChips').innerHTML = chips.map(s =>
        `<button type="button" class="chip ${s===activeSection?'active':''}" data-section="${esc(s)}" onclick="filterSection(this.dataset.section)">${esc(s)}</button>`
      ).join('');
    }

    function filterSection(s) {
      activeSection = s;
      renderChips();
      renderProducts();
    }

    function isGeneralFilter() {
      return activeSection === 'General';
    }

    function renderProducts() {
      const q = document.getElementById('searchQ').value.trim().toLowerCase();
      let list = products.filter(p => {
        const section = p.group_name || 'General';
        if (!isGeneralFilter() && section !== activeSection) return false;
        if (!q) return true;
        return [p.sku,p.name,p.brand,section].join(' ').toLowerCase().includes(q);
      });

      document.getElementById('sTotal').textContent = list.length;
      document.getElementById('sLow').textContent = list.filter(p => p.stock <= p.min_stock).length;
      document.getElementById('sUnits').textContent = list.reduce((s,p) => s + p.stock, 0);

      const el = document.getElementById('productList');
      if (!list.length) { el.innerHTML = '<div class="empty">No items found.</div>'; return; }

      const grouped = {};
      list.forEach(p => {
        const g = isGeneralFilter() ? (p.group_name || 'General') : activeSection;
        (grouped[g] = grouped[g] || []).push(p);
      });

      let html = '';
      Object.keys(grouped).sort().forEach(sec => {
        if (isGeneralFilter()) html += `<div class="section-title">${esc(sec)}</div>`;
        grouped[sec].forEach(p => {
          const low = p.stock <= p.min_stock;
          const old = (p.status||'').toLowerCase() === 'old';
          const sku = esc(p.sku);
          html += `<div class="card ${low?'low':''} ${old?'old':''}">
            <div class="card-tap" data-open-sku="${sku}">
              <div class="row1">
                <div class="sku">${sku}${old?'<span class="badge badge-old">OLD</span>':''}</div>
                <div class="stock ${low?'low':''}">${p.stock}</div>
              </div>
              <div>${esc(p.name||'—')}</div>
              <div class="meta">${esc(p.brand||'—')} · Min ${p.min_stock}${low?' · <strong style="color:var(--red)">Low stock</strong>':''}</div>
            </div>
            <div class="actions">
              <button type="button" class="btn btn-red" data-action="minus" data-sku="${sku}">− 1</button>
              <button type="button" class="btn btn-green" data-action="plus" data-sku="${sku}">+ 1</button>
            </div>
            <div class="actions actions-full">
              <button type="button" class="btn ${old ? 'btn-green' : 'btn-red'}" data-action="${old ? 'mark-active' : 'mark-old'}" data-sku="${sku}">
                ${old ? 'Mark Active' : 'Mark Old'}
              </button>
            </div>
            <div class="move-section">
              <label>Section</label>
              <select data-action="move-section" data-sku="${sku}" data-current="${esc(p.group_name||'General')}">
                ${sectionOptions(p.group_name || 'General')}
              </select>
            </div>
          </div>`;
        });
      });
      el.innerHTML = html;
    }

    document.getElementById('searchQ').addEventListener('input', renderProducts);

    function esc(text) {
      return String(text)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
    }

    document.getElementById('productList').addEventListener('change', async (ev) => {
      const sel = ev.target.closest('select[data-action="move-section"]');
      if (!sel) return;
      ev.stopPropagation();
      const sku = sel.dataset.sku;
      const section = sel.value;
      if (section === sel.dataset.current) return;
      try {
        await api(`/api/products/${encodeURIComponent(sku)}/section`, {
          method: 'POST',
          headers: {'Content-Type':'application/json'},
          body: JSON.stringify({group_name: section})
        });
        await loadProducts();
        toast(`Moved ${sku} to ${section}`);
      } catch (e) {
        sel.value = sel.dataset.current;
        toast(e.message);
      }
    });

    document.getElementById('productList').addEventListener('click', async (ev) => {
      const btn = ev.target.closest('button[data-action]');
      if (btn) {
        ev.preventDefault();
        ev.stopPropagation();
        const sku = btn.dataset.sku;
        const action = btn.dataset.action;
        if (action === 'minus') await quickStock(sku, -1);
        if (action === 'plus') await quickStock(sku, 1);
        if (action === 'mark-old') await setProductStatus(sku, 'Old');
        if (action === 'mark-active') await setProductStatus(sku, 'Active');
        return;
      }
      const tap = ev.target.closest('[data-open-sku]');
      if (tap) openSheet(tap.dataset.openSku);
    });

    async function quickStock(sku, delta) {
      try {
        const data = await api('/api/stock', {
          method: 'POST', headers: {'Content-Type':'application/json'},
          body: JSON.stringify({sku, change: delta})
        });
        const idx = products.findIndex(p => p.sku === sku);
        if (idx >= 0) products[idx].stock = data.stock;
        renderProducts();
        toast(`${sku}: ${data.stock} in stock`);
      } catch (e) { toast(e.message); }
    }

    async function setProductStatus(sku, status) {
      try {
        const result = await api(`/api/products/${encodeURIComponent(sku)}/status`, {
          method: 'POST',
          headers: {'Content-Type':'application/json'},
          body: JSON.stringify({status})
        });
        await loadProducts();
        toast(status === 'Old' ? `${sku} marked Old` : `${sku} marked Active`);
        return result;
      } catch (e) { toast(e.message); }
    }

    async function prepAddForm() {
      try {
        const data = await api('/api/next-sku');
        document.getElementById('addSku').value = data.sku;
        fillSectionSelects();
      } catch (e) { toast(e.message); }
    }

    async function addProduct() {
      try {
        await api('/api/products', {
          method: 'POST',
          headers: {'Content-Type':'application/json'},
          body: JSON.stringify({
            sku: document.getElementById('addSku').value,
            name: document.getElementById('addName').value,
            brand: document.getElementById('addBrand').value,
            stock: +document.getElementById('addStock').value,
            min_stock: +document.getElementById('addMin').value,
            group_name: document.getElementById('addSection').value,
          })
        });
        toast('Product added');
        ['addName','addBrand'].forEach(id => document.getElementById(id).value = '');
        document.getElementById('addStock').value = 0;
        document.getElementById('addMin').value = 0;
        prepAddForm();
        go('stock');
      } catch (e) { toast(e.message); }
    }

    function openSheet(sku) {
      activeSku = sku;
      const p = products.find(x => x.sku === sku);
      if (!p) return;
      document.getElementById('sheetTitle').textContent = sku;
      document.getElementById('editName').value = p.name || '';
      document.getElementById('editBrand').value = p.brand || '';
      document.getElementById('editStock').value = p.stock;
      document.getElementById('editMin').value = p.min_stock;
      document.getElementById('editStatus').value = p.status || 'Active';
      fillSectionSelects();
      document.getElementById('editSection').value = p.group_name || 'General';
      const isOld = (p.status||'').toLowerCase() === 'old';
      const toggleBtn = document.getElementById('toggleOldBtn');
      toggleBtn.textContent = isOld ? 'Mark Active' : 'Mark Old';
      toggleBtn.className = 'btn ' + (isOld ? 'btn-green' : 'btn-red');
      document.getElementById('productSheet').classList.add('open');
    }

    function closeSheet() {
      document.getElementById('productSheet').classList.remove('open');
      activeSku = null;
    }

    async function sheetStock(delta) {
      try {
        const data = await api('/api/stock', {
          method: 'POST', headers: {'Content-Type':'application/json'},
          body: JSON.stringify({sku: activeSku, change: delta})
        });
        document.getElementById('editStock').value = data.stock;
        toast(`${activeSku}: ${data.stock} in stock`);
        await loadProducts();
      } catch (e) { toast(e.message); }
    }

    async function saveProduct() {
      try {
        await api(`/api/products/${encodeURIComponent(activeSku)}`, {
          method: 'PUT', headers: {'Content-Type':'application/json'},
          body: JSON.stringify({
            name: document.getElementById('editName').value,
            brand: document.getElementById('editBrand').value,
            stock: +document.getElementById('editStock').value,
            min_stock: +document.getElementById('editMin').value,
            status: document.getElementById('editStatus').value,
            group_name: document.getElementById('editSection').value,
          })
        });
        toast('Saved'); closeSheet(); loadProducts();
      } catch (e) { toast(e.message); }
    }

    async function toggleOld() {
      if (!activeSku) return;
      const p = products.find(x => x.sku === activeSku);
      if (!p) return;
      const isOld = (p.status || '').trim().toLowerCase() === 'old';
      await setProductStatus(activeSku, isOld ? 'Active' : 'Old');
      closeSheet();
    }

    async function markDamaged() {
      try {
        await api(`/api/products/${encodeURIComponent(activeSku)}/damage`, {method:'POST'});
        toast('Marked damaged'); closeSheet(); loadProducts();
      } catch (e) { toast(e.message); }
    }

    async function deleteProduct() {
      if (!confirm('Delete this item?')) return;
      try {
        await api(`/api/products/${encodeURIComponent(activeSku)}`, {method:'DELETE'});
        toast('Deleted'); closeSheet(); loadProducts();
      } catch (e) { toast(e.message); }
    }

    function confidenceBadgeClass(label) {
      if (label === 'Reliable') return 'confidence-reliable';
      if (label === 'Improving') return 'confidence-improving';
      return 'confidence-limited';
    }

    function confidenceBadgeHtml(row) {
      const icon = row.confidence_label === 'Reliable' ? '✓'
        : row.confidence_label === 'Improving' ? '~' : '⚠';
      const cls = confidenceBadgeClass(row.confidence_label);
      return `<span class="confidence-badge ${cls}" title="${row.confidence_pct}% confidence">${icon} ${row.confidence_label}</span>`;
    }

    function jitForecastForceRefresh() {
      return new URLSearchParams(window.location.search).has('v');
    }

    let forecastRows = [];
    let forecastSort = { key: 'urgency', asc: true };

    function sortForecast(column) {
      if (forecastSort.key === column) forecastSort.asc = !forecastSort.asc;
      else { forecastSort.key = column; forecastSort.asc = true; }
      renderForecastTable(forecastRows);
    }

    function renderForecastIntel(summary) {
      const card = document.getElementById('forecastIntelCard');
      if (!card || !summary) return;
      card.style.display = 'block';
      document.getElementById('forecastAvgConf').textContent =
        summary.avg_confidence_pct != null ? `${summary.avg_confidence_pct}%` : '—';
      document.getElementById('forecastAvgAcc').textContent =
        summary.avg_rolling_accuracy_pct != null ? `${summary.avg_rolling_accuracy_pct}%` : '—';
    }

    function renderForecastTable(rows) {
      if (rows) forecastRows = rows;
      const key = forecastSort.key;
      const asc = forecastSort.asc;
      const sorted = [...forecastRows].sort((a, b) => {
        let av = a[key];
        let bv = b[key];
        if (key === 'urgency') {
          av = jitUrgencyRank(av);
          bv = jitUrgencyRank(bv);
        } else if (typeof av === 'string') {
          av = av.toLowerCase();
          bv = String(bv).toLowerCase();
        }
        if (av < bv) return asc ? -1 : 1;
        if (av > bv) return asc ? 1 : -1;
        return 0;
      });

      const urgentCount = forecastRows.filter(r => r.needs_reorder).length;
      const criticalCount = forecastRows.filter(r => r.urgency === 'CRITICAL').length;
      const summary = document.getElementById('forecastSummary');
      if (summary) {
        summary.textContent = forecastRows.length
          ? `${forecastRows.length} SKUs · ${urgentCount} need reorder${urgentCount === 1 ? '' : 's'} · ${criticalCount} critical · tap headers to sort`
          : 'No products in forecast';
      }

      const tbody = document.querySelector('#forecastTable tbody');
      if (!tbody) return;
      tbody.innerHTML = sorted.length
        ? sorted.map(r => {
            const days = r.days_until_stockout >= 999 ? '—' : r.days_until_stockout;
            return `<tr>
              <td>${r.sku}</td>
              <td class="jit-name" title="${r.name}">${r.name}</td>
              <td>${r.current_stock}</td>
              <td>${r.projected_stock_30d}</td>
              <td>${r.velocity_daily}</td>
              <td>${r.reorder_point}</td>
              <td>${r.suggested_order_qty}</td>
              <td>${days}</td>
              <td>${confidenceBadgeHtml(r)}</td>
              <td class="jit-alpha" title="Sensitivity — higher = reacts faster to demand changes">${r.best_alpha}</td>
              <td><span class="badge ${jitBadgeClass(r.urgency)}">${r.urgency}</span></td>
            </tr>`;
          }).join('')
        : '<tr><td colspan="11">No products in forecast</td></tr>';
    }

    async function loadForecast() {
      try {
        const force = jitForecastForceRefresh() ? '?force=1' : '';
        const data = await api('/api/jit-forecast' + force);
        renderForecastIntel(data.summary);
        renderForecastTable(data.rows || []);
      } catch (e) { toast(e.message); }
    }

    let jitRows = [];
    let jitSort = { key: 'urgency', asc: true };

    function jitUrgencyRank(value) {
      return { CRITICAL: 0, WARNING: 1, OK: 2 }[value] ?? 9;
    }

    function jitBadgeClass(urgency) {
      if (urgency === 'CRITICAL') return 'badge-empty';
      if (urgency === 'WARNING') return 'badge-low';
      return 'badge-ok';
    }

    function sortJit(column) {
      if (jitSort.key === column) jitSort.asc = !jitSort.asc;
      else { jitSort.key = column; jitSort.asc = true; }
      renderJitTable(jitRows);
    }

    function renderJitTable(rows) {
      if (rows) jitRows = rows;
      const urgent = jitRows.filter(r => r.needs_reorder);
      const key = jitSort.key;
      const asc = jitSort.asc;
      const sorted = [...urgent].sort((a, b) => {
        let av = a[key];
        let bv = b[key];
        if (key === 'urgency') {
          av = jitUrgencyRank(av);
          bv = jitUrgencyRank(bv);
        } else if (typeof av === 'string') {
          av = av.toLowerCase();
          bv = String(bv).toLowerCase();
        }
        if (av < bv) return asc ? -1 : 1;
        if (av > bv) return asc ? 1 : -1;
        return 0;
      });

      const criticalCount = urgent.filter(r => r.urgency === 'CRITICAL').length;
      const summary = document.getElementById('jitSummary');
      if (summary) {
        summary.textContent = urgent.length
          ? `${urgent.length} urgent reorder${urgent.length === 1 ? '' : 's'} · ${criticalCount} critical`
          : 'No urgent reorders — all SKUs covered for the next 30 days';
      }

      document.querySelector('#jitTable tbody').innerHTML = sorted.length
        ? sorted.map(r => {
            const days = r.days_until_stockout >= 999 ? '—' : r.days_until_stockout;
            const minCell = r.min_stock_alert
              ? `<strong style="color:var(--red)">${r.min_stock}</strong>`
              : `${r.min_stock}`;
            return `<tr>
              <td>${r.sku}</td>
              <td class="jit-name" title="${r.name}">${r.name}</td>
              <td>${r.current_stock}</td>
              <td>${r.projected_stock_30d}</td>
              <td>${r.velocity_daily}</td>
              <td>${r.reorder_point}</td>
              <td>${r.suggested_order_qty}</td>
              <td>${days}</td>
              <td>${minCell}</td>
              <td>${confidenceBadgeHtml(r)}</td>
              <td class="jit-alpha" title="Sensitivity — higher = reacts faster to demand changes">${r.best_alpha}</td>
              <td><span class="badge ${jitBadgeClass(r.urgency)}">${r.urgency}</span></td>
            </tr>`;
          }).join('')
        : '<tr><td colspan="12">No urgent reorders right now</td></tr>';
    }

    async function loadJitForecast() {
      try {
        const force = jitForecastForceRefresh() ? '?force=1' : '';
        const data = await api('/api/jit-forecast' + force);
        renderJitTable(data.rows || []);
      } catch (e) { toast(e.message); }
    }

    const TOOLTIP_IDLE_MS = 2000;

    function createAutoHideTooltipPlugin() {
      return {
        id: 'autoHideTooltip',
        afterEvent(chart, args) {
          const interactive = [
            'mousemove', 'touchstart', 'touchmove', 'click',
            'pointermove', 'pointerdown', 'pointerenter',
          ];
          if (!interactive.includes(args.event.type)) return;

          if (chart.$tooltipIdleTimer) {
            clearTimeout(chart.$tooltipIdleTimer);
            chart.$tooltipIdleTimer = null;
          }

          if (chart.getActiveElements().length > 0) {
            chart.$tooltipIdleTimer = setTimeout(() => {
              chart.setActiveElements([]);
              if (chart.tooltip) chart.tooltip.setActiveElements([], {x: 0, y: 0});
              chart.update('none');
              chart.$tooltipIdleTimer = null;
            }, TOOLTIP_IDLE_MS);
          }
        },
        beforeDestroy(chart) {
          if (chart.$tooltipIdleTimer) clearTimeout(chart.$tooltipIdleTimer);
        },
      };
    }

    function withAutoHideTooltip(cfg) {
      cfg.plugins = [...(cfg.plugins || []), createAutoHideTooltipPlugin()];
      cfg.options = cfg.options || {};
      cfg.options.plugins = cfg.options.plugins || {};
      cfg.options.plugins.tooltip = {
        ...cfg.options.plugins.tooltip,
        animation: { duration: 400, easing: 'easeOutQuart' },
      };
      return cfg;
    }

    function makeChart(id, cfg, autoHideTooltip = false) {
      if (charts[id]) {
        if (charts[id].$tooltipIdleTimer) clearTimeout(charts[id].$tooltipIdleTimer);
        charts[id].destroy();
      }
      charts[id] = new Chart(
        document.getElementById(id),
        autoHideTooltip ? withAutoHideTooltip(cfg) : cfg
      );
    }

    async function loadAnalytics() {
      try {
        const year = document.getElementById('yearSelect').value || new Date().getFullYear();
        const data = await api(`/api/analytics?year=${year}${analyticsForceRefresh()}`);
        const k = data.kpis;
        document.getElementById('analyticsKpis').innerHTML = `
          <div class="kpi"><strong>${k.products}</strong><span>products</span></div>
          <div class="kpi"><strong>${k.low_stock}</strong><span>low stock</span></div>
          <div class="kpi"><strong>${k.total_units}</strong><span>total units</span></div>
          <div class="kpi"><strong>${k.movements}</strong><span>movements</span></div>`;

        const yearSel = document.getElementById('yearSelect');
        if (!yearSel.options.length) {
          yearSel.innerHTML = data.years.map(y => `<option value="${y}" ${y==data.year?'selected':''}>${y}</option>`).join('');
        }

        makeChart('chartHealth', {
          type: 'doughnut',
          data: {
            labels: ['Healthy','Low'],
            datasets: [{ data: [k.healthy, k.low_stock], backgroundColor: ['#16a34a','#ef4444'] }]
          },
          options: { plugins: { legend: { position: 'bottom' } } }
        }, true);

        makeChart('chartMovers', {
          type: 'bar',
          data: {
            labels: data.movers.map(m => m.name),
            datasets: [
              {
                label: 'Returns & Deliveries',
                data: data.movers.map(m => m.inbound),
                backgroundColor: 'rgba(34,197,94,0.85)',
                borderRadius: 3,
              },
              {
                label: 'Out To Customers',
                data: data.movers.map(m => m.outbound),
                backgroundColor: 'rgba(239,68,68,0.85)',
                borderRadius: 3,
              },
            ],
          },
          options: {
            indexAxis: 'y',
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
              legend: { position: 'bottom', labels: { boxWidth: 12, font: { size: 11 } } },
              tooltip: {
                callbacks: {
                  footer: (items) => {
                    const idx = items[0].dataIndex;
                    const m = data.movers[idx];
                    return m ? `${m.moves} total units moved` : '';
                  },
                },
              },
            },
            scales: {
              x: { stacked: true, beginAtZero: true, ticks: { precision: 0 } },
              y: {
                stacked: true,
                ticks: {
                  autoSkip: false,
                  font: { size: 11 },
                  color: '#102a43',
                },
                grid: { display: false },
              },
            },
          },
        }, true);

        if (!document.getElementById('activityYear').options.length) {
          buildActivityFilterOptions(data.years);
        }
        resetActivityFilters();
        setupDeferredAnalyticsLoads();

        makeChart('chartMonthly', {
          type: 'bar',
          data: {
            labels: data.monthly.map(m => m.month),
            datasets: [{ data: data.monthly.map(m => m.change),
              backgroundColor: data.monthly.map(m => m.change >= 0 ? '#22c55e' : '#ef4444') }]
          },
          options: { plugins: { legend: { display: false } } }
        });
      } catch (e) { toast(e.message); }
    }

    async function loadConnectInfo() {
      try {
        const info = await api('/api/network');
        const urls = info.urls.map(u =>
          `<div style="margin:6px 0"><a href="${u}" style="color:var(--primary);font-weight:600;word-break:break-all">${u}</a></div>`
        ).join('');
        document.getElementById('connectInfo').innerHTML =
          `<div>Server: <strong>${info.status}</strong>${info.secure ? ' · <strong>HTTPS</strong>' : ''}</div>${urls}`;
        const hint = document.getElementById('connectHint');
        if (hint) {
          if (info.deployment === 'vercel') {
            hint.innerHTML = 'Open this URL on any device with internet access. Connection is encrypted (HTTPS).';
          } else if (info.secure) {
            hint.innerHTML = 'Connection is encrypted (HTTPS). Same Wi-Fi as this Mac (not guest network).';
          } else {
            hint.innerHTML = 'Run <strong>./enable_lan_security.sh</strong> on your Mac to enable HTTPS.';
          }
        }
      } catch (e) {
        document.getElementById('connectInfo').textContent = 'Could not load network info.';
      }
    }

    async function loadSections() {
      try {
        const data = await api('/api/sections');
        sections = data.sections;
        document.getElementById('sectionList').innerHTML = sections.map(s =>
          `<div style="display:flex;justify-content:space-between;align-items:center;padding:10px 0;border-bottom:1px solid var(--border)">
            <span>${s}</span>
            ${s!=='General'?`<button class="btn btn-ghost" style="flex:0;padding:6px 12px" onclick="deleteSection('${s}')">Delete</button>`:''}
          </div>`).join('');
      } catch (e) { toast(e.message); }
    }

    async function addSection() {
      const name = document.getElementById('newSection').value.trim();
      if (!name) return toast('Enter a section name');
      try {
        await api('/api/sections', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({name})});
        document.getElementById('newSection').value = '';
        toast('Section added'); loadSections();
      } catch (e) { toast(e.message); }
    }

    async function deleteSection(name) {
      if (!confirm(`Delete "${name}"? Items will move to General.`)) return;
      try {
        await api(`/api/sections/${encodeURIComponent(name)}`, {method:'DELETE'});
        toast('Section deleted'); loadSections();
      } catch (e) { toast(e.message); }
    }

    // -------- Bed Linen (separate storage) --------
    async function loadLinenProducts() {
      try {
        const data = await api('/api/linen/products');
        linenProducts = data.products;
        linenSections = data.sections;
        renderLinenChips();
        renderLinenProducts();
        fillLinenSectionSelects();
      } catch (e) { toast(e.message); }
    }

    function fillLinenSectionSelects() {
      ['linenAddSection', 'linenEditSection'].forEach(id => {
        const el = document.getElementById(id);
        if (!el) return;
        el.innerHTML = linenSections.map(s => `<option value="${s}">${s}</option>`).join('');
      });
    }

    function linenSectionOptions(current) {
      const cur = current || 'General';
      return linenSections.map(s =>
        `<option value="${esc(s)}"${s === cur ? ' selected' : ''}>${esc(s)}</option>`
      ).join('');
    }

    function renderLinenChips() {
      const other = linenSections.filter(s => s !== 'General');
      const chips = ['General', ...other];
      document.getElementById('linenSectionChips').innerHTML = chips.map(s =>
        `<button type="button" class="chip ${s===linenActiveSection?'active':''}" data-section="${esc(s)}" onclick="filterLinenSection(this.dataset.section)">${esc(s)}</button>`
      ).join('');
    }

    function filterLinenSection(s) {
      linenActiveSection = s;
      renderLinenChips();
      renderLinenProducts();
    }

    function isLinenGeneralFilter() { return linenActiveSection === 'General'; }

    function renderLinenProducts() {
      const q = document.getElementById('linenSearchQ').value.trim().toLowerCase();
      let list = linenProducts.filter(p => {
        const section = p.group_name || 'General';
        if (!isLinenGeneralFilter() && section !== linenActiveSection) return false;
        if (!q) return true;
        return [p.sku, p.name, p.brand, section].join(' ').toLowerCase().includes(q);
      });

      document.getElementById('lTotal').textContent = list.length;
      document.getElementById('lLow').textContent = list.filter(p => p.stock <= p.min_stock).length;
      document.getElementById('lUnits').textContent = list.reduce((s, p) => s + p.stock, 0);

      const el = document.getElementById('linenProductList');
      if (!list.length) { el.innerHTML = '<div class="empty">No bed linen items yet. Tap + Add.</div>'; return; }

      const grouped = {};
      list.forEach(p => {
        const g = isLinenGeneralFilter() ? (p.group_name || 'General') : linenActiveSection;
        (grouped[g] = grouped[g] || []).push(p);
      });

      let html = '';
      Object.keys(grouped).sort().forEach(sec => {
        if (isLinenGeneralFilter()) html += `<div class="section-title">${esc(sec)}</div>`;
        grouped[sec].forEach(p => {
          const low = p.stock <= p.min_stock;
          const old = (p.status || '').toLowerCase() === 'old';
          const sku = esc(p.sku);
          html += `<div class="card ${low?'low':''} ${old?'old':''}">
            <div class="card-tap" data-linen-sku="${sku}">
              <div class="row1">
                <div class="sku">${sku}${old?'<span class="badge badge-old">OLD</span>':''}</div>
                <div class="stock ${low?'low':''}">${p.stock}</div>
              </div>
              <div>${esc(p.name||'—')}</div>
              <div class="meta">${esc(p.brand||'—')} · Min ${p.min_stock}${low?' · <strong style="color:var(--red)">Low stock</strong>':''}</div>
            </div>
            <div class="actions">
              <button type="button" class="btn btn-red" data-linen-action="minus" data-linen-sku="${sku}">− 1</button>
              <button type="button" class="btn btn-green" data-linen-action="plus" data-linen-sku="${sku}">+ 1</button>
            </div>
            <div class="actions actions-full">
              <button type="button" class="btn ${old ? 'btn-green' : 'btn-red'}" data-linen-action="${old ? 'mark-active' : 'mark-old'}" data-linen-sku="${sku}">
                ${old ? 'Mark Active' : 'Mark Old'}
              </button>
            </div>
            <div class="move-section">
              <label>Section</label>
              <select data-linen-action="move-section" data-linen-sku="${sku}" data-current="${esc(p.group_name||'General')}">
                ${linenSectionOptions(p.group_name || 'General')}
              </select>
            </div>
          </div>`;
        });
      });
      el.innerHTML = html;
    }

    document.getElementById('linenSearchQ').addEventListener('input', renderLinenProducts);

    document.getElementById('linenProductList').addEventListener('change', async (ev) => {
      const sel = ev.target.closest('select[data-linen-action="move-section"]');
      if (!sel) return;
      ev.stopPropagation();
      const sku = sel.dataset.linenSku;
      const section = sel.value;
      if (section === sel.dataset.current) return;
      try {
        await api(`/api/linen/products/${encodeURIComponent(sku)}/section`, {
          method: 'POST', headers: {'Content-Type':'application/json'},
          body: JSON.stringify({group_name: section})
        });
        await loadLinenProducts();
        toast(`Moved ${sku} to ${section}`);
      } catch (e) { sel.value = sel.dataset.current; toast(e.message); }
    });

    document.getElementById('linenProductList').addEventListener('click', async (ev) => {
      const btn = ev.target.closest('button[data-linen-action]');
      if (btn) {
        ev.preventDefault(); ev.stopPropagation();
        const sku = btn.dataset.linenSku;
        const action = btn.dataset.linenAction;
        if (action === 'minus') await quickLinenStock(sku, -1);
        if (action === 'plus') await quickLinenStock(sku, 1);
        if (action === 'mark-old') await setLinenProductStatus(sku, 'Old');
        if (action === 'mark-active') await setLinenProductStatus(sku, 'Active');
        return;
      }
      const tap = ev.target.closest('[data-linen-sku]');
      if (tap && !ev.target.closest('button') && !ev.target.closest('select')) openLinenSheet(tap.dataset.linenSku);
    });

    async function quickLinenStock(sku, delta) {
      try {
        const data = await api('/api/linen/stock', {
          method: 'POST', headers: {'Content-Type':'application/json'},
          body: JSON.stringify({sku, change: delta})
        });
        const idx = linenProducts.findIndex(p => p.sku === sku);
        if (idx >= 0) linenProducts[idx].stock = data.stock;
        renderLinenProducts();
        toast(`${sku}: ${data.stock} in stock`);
      } catch (e) { toast(e.message); }
    }

    async function setLinenProductStatus(sku, status) {
      await api(`/api/linen/products/${encodeURIComponent(sku)}/status`, {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({status})
      });
      await loadLinenProducts();
      toast(status === 'Old' ? `${sku} marked Old` : `${sku} marked Active`);
    }

    function openLinenSheet(sku) {
      linenActiveSku = sku;
      const p = linenProducts.find(x => x.sku === sku);
      if (!p) return;
      document.getElementById('linenSheetTitle').textContent = sku;
      document.getElementById('linenEditName').value = p.name || '';
      document.getElementById('linenEditBrand').value = p.brand || '';
      document.getElementById('linenEditStock').value = p.stock;
      document.getElementById('linenEditMin').value = p.min_stock;
      document.getElementById('linenEditStatus').value = p.status || 'Active';
      fillLinenSectionSelects();
      document.getElementById('linenEditSection').value = p.group_name || 'General';
      const isOld = (p.status || '').toLowerCase() === 'old';
      const btn = document.getElementById('linenToggleOldBtn');
      btn.textContent = isOld ? 'Mark Active' : 'Mark Old';
      btn.className = 'btn ' + (isOld ? 'btn-green' : 'btn-red');
      document.getElementById('linenSheet').classList.add('open');
    }

    function closeLinenSheet() {
      document.getElementById('linenSheet').classList.remove('open');
      linenActiveSku = null;
    }

    async function linenSheetStock(delta) {
      await quickLinenStock(linenActiveSku, delta);
      document.getElementById('linenEditStock').value =
        linenProducts.find(p => p.sku === linenActiveSku)?.stock ?? 0;
    }

    async function saveLinenProduct() {
      await api(`/api/linen/products/${encodeURIComponent(linenActiveSku)}`, {
        method: 'PUT', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({
          name: document.getElementById('linenEditName').value,
          brand: document.getElementById('linenEditBrand').value,
          stock: +document.getElementById('linenEditStock').value,
          min_stock: +document.getElementById('linenEditMin').value,
          status: document.getElementById('linenEditStatus').value,
          group_name: document.getElementById('linenEditSection').value,
        })
      });
      toast('Saved'); closeLinenSheet(); loadLinenProducts();
    }

    async function toggleLinenOld() {
      const p = linenProducts.find(x => x.sku === linenActiveSku);
      const isOld = (p?.status || '').toLowerCase() === 'old';
      await setLinenProductStatus(linenActiveSku, isOld ? 'Active' : 'Old');
      closeLinenSheet();
    }

    async function deleteLinenProduct() {
      if (!confirm('Delete this bed linen item?')) return;
      await api(`/api/linen/products/${encodeURIComponent(linenActiveSku)}`, {method:'DELETE'});
      toast('Deleted'); closeLinenSheet(); loadLinenProducts();
    }

    async function openLinenAdd() {
      try {
        const data = await api('/api/linen/next-sku');
        document.getElementById('linenAddSku').value = data.sku;
        fillLinenSectionSelects();
        document.getElementById('linenAddSheet').classList.add('open');
      } catch (e) { toast(e.message); }
    }

    function closeLinenAdd() { document.getElementById('linenAddSheet').classList.remove('open'); }

    async function addLinenProduct() {
      await api('/api/linen/products', {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({
          sku: document.getElementById('linenAddSku').value,
          name: document.getElementById('linenAddName').value,
          brand: document.getElementById('linenAddBrand').value,
          stock: +document.getElementById('linenAddStock').value,
          min_stock: +document.getElementById('linenAddMin').value,
          group_name: document.getElementById('linenAddSection').value,
        })
      });
      toast('Item added'); closeLinenAdd(); loadLinenProducts();
    }

    let exportInProgress = false;

    async function shareExportFile(url, mimeType, fallbackName, shareTitle) {
      if (exportInProgress) return;
      exportInProgress = true;
      toast('Generating report…');
      try {
        const res = await fetch(url, {credentials: 'same-origin'});
        if (res.status === 401) {
          showLockScreen();
          throw new Error('Passcode required');
        }
        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          throw new Error(err.error || err.detail || 'Export failed');
        }
        const blob = await res.blob();
        if (!blob.size) throw new Error('Empty PDF received');

        const cd = res.headers.get('Content-Disposition') || '';
        const match = cd.match(/filename="([^"]+)"/);
        const filename = match ? match[1] : fallbackName;
        const file = new File([blob], filename, { type: mimeType });

        if (navigator.share && navigator.canShare && navigator.canShare({ files: [file] })) {
          try {
            await navigator.share({ files: [file], title: shareTitle });
            toast('Choose Save to Files to pick a folder');
            return;
          } catch (e) {
            if (e.name === 'AbortError') throw e;
          }
        }

        const objUrl = URL.createObjectURL(blob);
        const opened = window.open(objUrl, '_blank');
        if (opened) {
          setTimeout(() => URL.revokeObjectURL(objUrl), 60000);
          toast('PDF opened — tap Share to save to Files');
          return;
        }

        const a = document.createElement('a');
        a.href = objUrl;
        a.download = filename;
        a.target = '_blank';
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(objUrl), 60000);
        toast('Report downloaded');
      } finally {
        exportInProgress = false;
      }
    }

    async function exportReport() {
      try {
        const date = new Date().toLocaleDateString('en-GB').replace(/\\//g, '-');
        await shareExportFile('/api/export/pdf', 'application/pdf', `${date}.pdf`, 'Inventory Report');
      } catch (e) {
        if (e.name !== 'AbortError') toast(e.message || 'Export cancelled');
      }
    }

    async function exportAiReport() {
      try {
        const date = new Date().toLocaleDateString('en-GB').replace(/\\//g, '-');
        await shareExportFile(
          '/api/export/ai-report',
          'application/pdf',
          `ai-analytics-${date}.pdf`,
          'AI Analytics Report'
        );
      } catch (e) {
        if (e.name !== 'AbortError') toast(e.message || 'Export cancelled');
      }
    }

    initAuth().then(() => {
      if (!document.body.classList.contains('locked')) loadProducts();
    });
    setInterval(() => {
      if (document.body.classList.contains('locked')) return;
      if (document.getElementById('screen-stock').classList.contains('active')) loadProducts();
      if (document.getElementById('screen-linen').classList.contains('active')) loadLinenProducts();
    }, 30000);
  </script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(PAGE, app_version=APP_VERSION)


@app.route("/api/auth/status")
def auth_status():
    return jsonify({"authenticated": is_authenticated()})


@app.route("/api/auth/verify", methods=["POST"])
def auth_verify():
    data = request.get_json(silent=True) or {}
    if data.get("passcode") == WEB_PASSCODE:
        session.permanent = True
        session["authenticated"] = True
        return jsonify({"ok": True})
    return jsonify({"error": "Wrong passcode"}), 401


@app.route("/api/network")
def api_network():
    cached = _get_cache("network")
    if cached is not None:
        return jsonify(cached)

    if is_vercel():
        vercel_url = os.environ.get("VERCEL_URL", "")
        urls = [f"https://{vercel_url}"] if vercel_url else []
        payload = {
            "status": "running",
            "secure": True,
            "scheme": "https",
            "ip": vercel_url,
            "port": 443,
            "hostname": vercel_url,
            "urls": urls,
            "deployment": "vercel",
        }
        _set_cache("network", "default", payload)
        return jsonify(payload)

    import subprocess
    ip = lan_ip()
    port = PORT
    hostname = "Mac.local"
    try:
        hostname = subprocess.check_output(["scutil", "--get", "LocalHostName"], text=True).strip()
    except Exception:
        pass
    urls = []
    scheme = url_scheme()
    if ip and ip != "127.0.0.1":
        urls.append(f"{scheme}://{ip}:{port}")
    if hostname:
        urls.append(f"{scheme}://{hostname}.local:{port}")
        urls.append(f"{scheme}://{hostname}.fritz.box:{port}")
    payload = {
        "status": "running",
        "secure": https_enabled(),
        "scheme": scheme,
        "ip": ip,
        "port": port,
        "hostname": hostname,
        "urls": urls,
        "deployment": "local",
    }
    _set_cache("network", "default", payload)
    return jsonify(payload)


@app.route("/api/version")
def api_version():
    return jsonify({"version": APP_VERSION})


@app.route("/api/export/ai-report")
def api_export_ai_report():
    conn = None
    try:
        conn = get_conn()
        pdf_bytes = build_ai_analytics_report_pdf(conn)
        date_str = datetime.now().strftime("%d-%m-%Y")
        filename = f"ai-analytics-{date_str}.pdf"
        return Response(
            pdf_bytes,
            mimetype="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except Exception as e:
        app.logger.error("[api_export_ai_report] %s", e)
        return jsonify({"error": f"Could not generate report: {e}"}), 500
    finally:
        _close_conn(conn)


@app.route("/api/export/pdf")
def api_export_pdf():
    conn = None
    try:
        conn = get_conn()
        pdf_bytes = build_inventory_report_pdf(conn)
        date_str = datetime.now().strftime("%d-%m-%Y")
        filename = f"{date_str}.pdf"
        return Response(
            pdf_bytes,
            mimetype="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except Exception as e:
        app.logger.error("[api_export_pdf] %s", e)
        return jsonify({"error": f"Could not generate report: {e}"}), 500
    finally:
        _close_conn(conn)


# -------- Bed Linen API (separate tables — does not touch main stock) --------

@app.route("/api/linen/products")
def api_linen_products():
    conn = get_conn()
    try:
        return jsonify(list_linen_products(conn))
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


@app.route("/api/linen/next-sku")
def api_linen_next_sku():
    conn = get_conn()
    try:
        return jsonify({"sku": generate_next_linen_sku(conn)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


@app.route("/api/linen/products", methods=["POST"])
def api_linen_add():
    data = request.get_json(silent=True) or {}
    sku = (data.get("sku") or "").strip().upper()
    if not sku:
        return jsonify({"error": "SKU required"}), 400
    group_name = (data.get("group_name") or "General").strip() or "General"
    try:
        stock = int(data.get("stock", 0))
        min_stock = int(data.get("min_stock", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "Stock values must be numbers"}), 400

    conn = get_conn()
    try:
        ensure_linen_schema(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT sku FROM linen_items WHERE sku=%s", (sku,))
            if cur.fetchone():
                return jsonify({"error": "SKU already exists"}), 400
            cur.execute(
                "INSERT INTO linen_sections(name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (group_name,)
            )
            cur.execute(
                "INSERT INTO linen_items VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (sku, data.get("name", ""), data.get("brand", ""), stock, min_stock, "Active", group_name),
            )
        conn.commit()
        return jsonify({"ok": True, "sku": sku})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


@app.route("/api/linen/products/<sku>", methods=["PUT"])
def api_linen_update(sku):
    data = request.get_json(silent=True) or {}
    try:
        stock = int(data.get("stock", 0))
        min_stock = int(data.get("min_stock", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "Stock values must be numbers"}), 400
    group_name = (data.get("group_name") or "General").strip() or "General"

    conn = get_conn()
    try:
        ensure_linen_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO linen_sections(name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (group_name,)
            )
            cur.execute("""
                UPDATE linen_items SET name=%s, brand=%s, stock=%s, min_stock=%s, status=%s, group_name=%s
                WHERE sku=%s
            """, (data.get("name", ""), data.get("brand", ""), stock, min_stock,
                  data.get("status", "Active"), group_name, sku))
            if cur.rowcount == 0:
                return jsonify({"error": "Item not found"}), 404
        conn.commit()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


@app.route("/api/linen/products/<sku>", methods=["DELETE"])
def api_linen_delete(sku):
    conn = get_conn()
    try:
        ensure_linen_schema(conn)
        with conn.cursor() as cur:
            cur.execute("DELETE FROM linen_items WHERE sku=%s", (sku,))
            if cur.rowcount == 0:
                return jsonify({"error": "Item not found"}), 404
        conn.commit()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


@app.route("/api/linen/stock", methods=["POST"])
def api_linen_stock():
    data = request.get_json(silent=True) or {}
    sku = (data.get("sku") or "").strip().upper()
    try:
        change = int(data.get("change"))
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid change value"}), 400

    conn = get_conn()
    try:
        new_stock = adjust_linen_stock(conn, sku, change)
        if new_stock is None:
            return jsonify({"error": "Item not found"}), 404
        return jsonify({"sku": sku, "stock": new_stock})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


@app.route("/api/linen/products/<sku>/section", methods=["POST"])
def api_linen_move_section(sku):
    data = request.get_json(silent=True) or {}
    group_name = (data.get("group_name") or "").strip()
    if not group_name:
        return jsonify({"error": "Section name required"}), 400
    sku = sku.strip().upper()

    conn = get_conn()
    try:
        ensure_linen_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO linen_sections(name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (group_name,)
            )
            cur.execute("UPDATE linen_items SET group_name=%s WHERE sku=%s", (group_name, sku))
            if cur.rowcount == 0:
                return jsonify({"error": "Item not found"}), 404
        conn.commit()
        return jsonify({"ok": True, "sku": sku, "group_name": group_name})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


@app.route("/api/linen/products/<sku>/status", methods=["POST"])
def api_linen_status(sku):
    data = request.get_json(silent=True) or {}
    status = (data.get("status") or "").strip()
    if status.lower() == "old":
        status = "Old"
    elif status.lower() == "active":
        status = "Active"
    else:
        return jsonify({"error": "Status must be Active or Old"}), 400
    sku = sku.strip().upper()

    conn = get_conn()
    try:
        ensure_linen_schema(conn)
        with conn.cursor() as cur:
            cur.execute("UPDATE linen_items SET status=%s WHERE sku=%s", (status, sku))
            if cur.rowcount == 0:
                return jsonify({"error": "Item not found"}), 404
        conn.commit()
        return jsonify({"ok": True, "sku": sku, "status": status})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


@app.after_request
def apply_response_headers(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline'; "
        "connect-src 'self'; "
        "img-src 'self' data:; "
        "frame-ancestors 'self'; "
        "base-uri 'self'"
    )
    if https_enabled():
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    if response.status_code >= 400:
        app.logger.info("Request from %s: %s %s", request.remote_addr, request.method, request.path)
    return response


@app.route("/api/products")
def api_products():
    conn = None
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT name FROM sections ORDER BY name")
            sections = [r[0] for r in cur.fetchall()] or ["General"]
            cur.execute("""
                SELECT sku, name, brand, stock, min_stock, status, COALESCE(group_name, 'General')
                FROM products ORDER BY COALESCE(group_name, 'General'), name
            """)
            products = [
                {"sku": r[0], "name": r[1], "brand": r[2], "stock": r[3],
                 "min_stock": r[4], "status": r[5], "group_name": r[6]}
                for r in cur.fetchall()
            ]
        return jsonify({"products": products, "sections": sections})
    except Exception as e:
        return _api_error("api_products", e)
    finally:
        _close_conn(conn)


@app.route("/api/next-sku")
def api_next_sku():
    conn = None
    try:
        conn = get_conn()
        return jsonify({"sku": generate_next_sku(conn)})
    except Exception as e:
        return _api_error("api_next_sku", e)
    finally:
        _close_conn(conn)


@app.route("/api/products", methods=["POST"])
def api_add_product():
    data = request.get_json(silent=True) or {}
    sku = (data.get("sku") or "").strip().upper()
    if not sku:
        return jsonify({"error": "SKU required"}), 400
    group_name = (data.get("group_name") or "General").strip() or "General"
    try:
        stock = int(data.get("stock", 0))
        min_stock = int(data.get("min_stock", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "Stock values must be numbers"}), 400

    conn = None
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT sku FROM products WHERE sku=%s", (sku,))
            if cur.fetchone():
                return jsonify({"error": "SKU already exists"}), 400
            cur.execute(
                "INSERT INTO sections(name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (group_name,)
            )
            cur.execute(
                "INSERT INTO products VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (sku, data.get("name", ""), data.get("brand", ""), stock, min_stock, "Active", group_name),
            )
        conn.commit()
        return jsonify({"ok": True, "sku": sku})
    except Exception as e:
        return _api_error("api_add_product", e)
    finally:
        _close_conn(conn)


@app.route("/api/products/<sku>", methods=["PUT"])
def api_update_product(sku):
    data = request.get_json(silent=True) or {}
    try:
        stock = int(data.get("stock", 0))
        min_stock = int(data.get("min_stock", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "Stock values must be numbers"}), 400

    group_name = (data.get("group_name") or "General").strip() or "General"
    conn = None
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO sections(name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (group_name,)
            )
            cur.execute("""
                UPDATE products SET name=%s, brand=%s, stock=%s, min_stock=%s, status=%s, group_name=%s
                WHERE sku=%s
            """, (data.get("name", ""), data.get("brand", ""), stock, min_stock,
                  data.get("status", "Active"), group_name, sku))
            if cur.rowcount == 0:
                return jsonify({"error": "Product not found"}), 404
        conn.commit()
        return jsonify({"ok": True})
    except Exception as e:
        return _api_error("api_update_product", e)
    finally:
        _close_conn(conn)


@app.route("/api/products/<sku>", methods=["DELETE"])
def api_delete_product(sku):
    conn = None
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM products WHERE sku=%s", (sku,))
            if cur.rowcount == 0:
                return jsonify({"error": "Product not found"}), 404
        conn.commit()
        return jsonify({"ok": True})
    except Exception as e:
        return _api_error("api_delete_product", e)
    finally:
        _close_conn(conn)


@app.route("/api/products/<sku>/section", methods=["POST"])
def api_move_section(sku):
    data = request.get_json(silent=True) or {}
    group_name = (data.get("group_name") or "").strip()
    if not group_name:
        return jsonify({"error": "Section name required"}), 400
    sku = sku.strip().upper()
    conn = None
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO sections(name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (group_name,)
            )
            cur.execute("UPDATE products SET group_name=%s WHERE sku=%s", (group_name, sku))
            if cur.rowcount == 0:
                return jsonify({"error": "Product not found"}), 404
        conn.commit()
        return jsonify({"ok": True, "sku": sku, "group_name": group_name})
    except Exception as e:
        return _api_error("api_move_section", e)
    finally:
        _close_conn(conn)


@app.route("/api/products/<sku>/status", methods=["POST"])
def api_set_status(sku):
    data = request.get_json(silent=True) or {}
    status = (data.get("status") or "").strip()
    if status.lower() == "old":
        status = "Old"
    elif status.lower() == "active":
        status = "Active"
    else:
        return jsonify({"error": "Status must be Active or Old"}), 400
    sku = sku.strip().upper()
    conn = None
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute("UPDATE products SET status=%s WHERE sku=%s", (status, sku))
            if cur.rowcount == 0:
                return jsonify({"error": "Product not found"}), 404
        conn.commit()
        return jsonify({"ok": True, "sku": sku, "status": status})
    except Exception as e:
        return _api_error("api_set_status", e)
    finally:
        _close_conn(conn)


@app.route("/api/products/<sku>/damage", methods=["POST"])
def api_damage(sku):
    conn = None
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT stock FROM products WHERE sku=%s", (sku,))
            if not cur.fetchone():
                return jsonify({"error": "Product not found"}), 404
            cur.execute("UPDATE products SET stock = stock - 1 WHERE sku=%s", (sku,))
            cur.execute(
                "INSERT INTO movements (sku, change, reason, customer, date) VALUES (%s,%s,%s,%s,%s)",
                (sku, -1, "Damaged", "", datetime.now()),
            )
        conn.commit()
        return jsonify({"ok": True})
    except Exception as e:
        return _api_error("api_damage", e)
    finally:
        _close_conn(conn)


@app.route("/api/stock", methods=["POST"])
def api_stock():
    data = request.get_json(silent=True) or {}
    sku = (data.get("sku") or "").strip().upper()
    try:
        change = int(data.get("change"))
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid change value"}), 400

    conn = None
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT stock FROM products WHERE sku=%s", (sku,))
            if not cur.fetchone():
                return jsonify({"error": "Product not found"}), 404
            cur.execute(
                "UPDATE products SET stock = stock + %s WHERE sku=%s RETURNING stock",
                (change, sku),
            )
            new_stock = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO movements (sku, change, reason, customer, date) VALUES (%s,%s,%s,%s,%s)",
                (sku, change, "Mobile Web", "", datetime.now()),
            )
        conn.commit()
        return jsonify({"sku": sku, "stock": new_stock})
    except Exception as e:
        return _api_error("api_stock", e)
    finally:
        _close_conn(conn)


@app.route("/api/sections")
def api_sections():
    force = request.args.get("force") == "1"
    cached = _get_cache("sections", force=force)
    if cached is not None:
        return jsonify(cached)

    conn = None
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT name FROM sections ORDER BY name")
            sections = [r[0] for r in cur.fetchall()] or ["General"]
        payload = {"sections": sections}
        _set_cache("sections", "default", payload)
        return jsonify(payload)
    except Exception as e:
        return _api_error("api_sections", e)
    finally:
        _close_conn(conn)


@app.route("/api/sections", methods=["POST"])
def api_add_section():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "Section name required"}), 400
    conn = None
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO sections(name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (name,)
            )
        conn.commit()
        return jsonify({"ok": True})
    except Exception as e:
        return _api_error("api_add_section", e)
    finally:
        _close_conn(conn)


@app.route("/api/sections/<name>", methods=["DELETE"])
def api_delete_section(name):
    if name == "General":
        return jsonify({"error": "General section cannot be deleted"}), 400
    conn = None
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE products SET group_name='General' WHERE COALESCE(group_name,'General')=%s", (name,)
            )
            cur.execute("DELETE FROM sections WHERE name=%s", (name,))
        conn.commit()
        return jsonify({"ok": True})
    except Exception as e:
        return _api_error("api_delete_section", e)
    finally:
        _close_conn(conn)


@app.route("/api/jit-forecast")
def api_jit_forecast():
    now = time.time()
    force = request.args.get("force") == "1"
    if (
        not force
        and _jit_forecast_cache["payload"] is not None
        and now - _jit_forecast_cache["ts"] < JIT_CACHE_TTL_SECONDS
    ):
        return jsonify(_jit_forecast_cache["payload"])

    conn = None
    try:
        conn = get_conn()
        rows = calculate_jit_forecast(conn)
        conf_values = [r["confidence_pct"] for r in rows if r.get("confidence_pct") is not None]
        acc_values = [
            r["rolling_accuracy_pct"] for r in rows
            if r.get("rolling_accuracy_pct") is not None
        ]
        payload = {
            "rows": rows,
            "summary": {
                "avg_confidence_pct": (
                    round(sum(conf_values) / len(conf_values), 1) if conf_values else None
                ),
                "avg_rolling_accuracy_pct": (
                    round(sum(acc_values) / len(acc_values), 1) if acc_values else None
                ),
            },
            "cached_at": datetime.now().isoformat(),
            "horizon_days": 30,
            "service_level_z": 1.65,
        }
        _jit_forecast_cache["payload"] = payload
        _jit_forecast_cache["ts"] = now
        return jsonify(payload)
    except Exception as e:
        return _api_error("api_jit_forecast", e)
    finally:
        _close_conn(conn)


@app.route("/api/forecast")
def api_forecast():
    conn = None
    try:
        conn = get_conn()
        rows = calculate_forecast_recommendations(conn)
        return jsonify({
            "rows": rows,
            "lead_time": DEFAULT_LEAD_TIME_DAYS,
            "service_z": DEFAULT_SERVICE_Z,
        })
    except Exception as e:
        return _api_error("api_forecast", e)
    finally:
        _close_conn(conn)


@app.route("/api/analytics")
def api_analytics():
    year = request.args.get("year", type=int) or datetime.now().year
    force = request.args.get("force") == "1"
    cache_key = str(year)
    cached = _get_cache("analytics", cache_key, force=force)
    if cached is not None:
        return jsonify(cached)

    conn = None
    try:
        conn = get_conn()
        payload = get_analytics_data(conn, year, include_activity=False, include_jit=False)
        _set_cache("analytics", cache_key, payload)
        return jsonify(payload)
    except Exception as e:
        return _api_error("api_analytics", e)
    finally:
        _close_conn(conn)


@app.route("/api/analytics/activity")
def api_analytics_activity():
    year = request.args.get("year", type=int)
    month = request.args.get("month", type=int)
    force = request.args.get("force") == "1"
    cache_key = f"{year or 'rolling'}:{month or '30d'}"
    cached = _get_cache("activity", cache_key, force=force)
    if cached is not None:
        return jsonify(cached)

    conn = None
    try:
        conn = get_conn()
        if year and month:
            payload = get_movement_activity_summary(conn, year=year, month=month)
        else:
            payload = get_movement_activity_summary(conn, days=30)

        year_opts = _get_cache("year_options")
        if year_opts is None:
            year_opts = get_activity_year_options(conn)
            _set_cache("year_options", "default", year_opts)
        payload["years"] = year_opts

        _set_cache("activity", cache_key, payload)
        return jsonify(payload)
    except Exception as e:
        return _api_error("api_analytics_activity", e)
    finally:
        _close_conn(conn)


@app.route("/api/analytics/day/<day>")
def api_analytics_day(day):
    if len(day) != 10 or day[4] != "-" or day[7] != "-":
        return jsonify({"error": "Invalid date"}), 400
    conn = None
    try:
        conn = get_conn()
        return jsonify(get_daily_movement_detail(conn, day))
    except Exception as e:
        return _api_error("api_analytics_day", e)
    finally:
        _close_conn(conn)


if __name__ == "__main__":
    ip = lan_ip()
    scheme = url_scheme()
    ssl_ctx = build_ssl_context()
    print("Momentum Inventory Web Server")
    print(f"  On this Mac:   {scheme}://127.0.0.1:{PORT}")
    print(f"  On your phone: {scheme}://{ip}:{PORT}")
    if ssl_ctx:
        print("  Security: HTTPS enabled (LAN traffic encrypted)")
    else:
        print("  Security: HTTP only — run ./setup_lan_https.sh to enable HTTPS")
    print("  Supabase: unchanged (separate encrypted connection)")
    app.run(host="0.0.0.0", port=PORT, debug=False, ssl_context=ssl_ctx, threaded=True)
