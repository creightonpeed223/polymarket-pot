"""
Simple Dashboard Server
Provides a web UI for monitoring the bot
"""

import asyncio
import json
from datetime import datetime, timezone
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
import threading

from ..trading.polymarket_client import get_trader
from ..config import config
from ..utils.logger import get_logger

logger = get_logger(__name__)

DASHBOARD_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Polymarket Trading Bot</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #0a0a0a;
            color: #fff;
            padding: 20px;
        }
        .container { max-width: 1200px; margin: 0 auto; }
        h1 { color: #00ff88; margin-bottom: 20px; }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 20px; }
        .card {
            background: #1a1a1a;
            border-radius: 12px;
            padding: 20px;
            border: 1px solid #333;
        }
        .card h3 { color: #888; font-size: 12px; text-transform: uppercase; margin-bottom: 10px; }
        .big-number { font-size: 32px; font-weight: bold; }
        .green { color: #00ff88; }
        .red { color: #ff4444; }
        .yellow { color: #ffaa00; }
        .status-dot {
            display: inline-block;
            width: 10px;
            height: 10px;
            border-radius: 50%;
            margin-right: 8px;
        }
        .status-dot.active { background: #00ff88; }
        .status-dot.inactive { background: #ff4444; }
        .log-box {
            background: #111;
            border-radius: 8px;
            padding: 15px;
            font-family: monospace;
            font-size: 12px;
            max-height: 300px;
            overflow-y: auto;
            margin-top: 20px;
        }
        .log-entry { padding: 4px 0; border-bottom: 1px solid #222; }
        .refresh-btn {
            background: #00ff88;
            color: #000;
            border: none;
            padding: 10px 20px;
            border-radius: 8px;
            cursor: pointer;
            font-weight: bold;
            margin-bottom: 20px;
        }
        .refresh-btn:hover { background: #00cc66; }
    </style>
</head>
<body>
    <div class="container">
        <h1>🤖 Polymarket Speed Trading Bot</h1>

        <button class="refresh-btn" onclick="refresh()">Refresh</button>

        <div class="grid">
            <div class="card">
                <h3>Status</h3>
                <div>
                    <span class="status-dot active" id="status-dot"></span>
                    <span id="status-text">Running</span>
                </div>
                <div style="margin-top: 10px; color: #888;">
                    Mode: <span id="mode">PAPER</span>
                </div>
            </div>

            <div class="card">
                <h3>Balance</h3>
                <div class="big-number green" id="balance">$10,000.00</div>
            </div>

            <div class="card">
                <h3>Daily P&L</h3>
                <div class="big-number" id="daily-pnl">$0.00</div>
            </div>

            <div class="card">
                <h3>Total P&L</h3>
                <div class="big-number" id="total-pnl">$0.00</div>
            </div>

            <div class="card">
                <h3>Trades Today</h3>
                <div class="big-number" id="trades">0</div>
            </div>

            <div class="card">
                <h3>Open Positions</h3>
                <div class="big-number" id="positions">0</div>
            </div>
        </div>

        <div class="card" style="margin-top: 20px;">
            <h3>Recent Activity</h3>
            <div class="log-box" id="log-box">
                <div class="log-entry">Bot started...</div>
            </div>
        </div>

        <div style="margin-top: 20px; color: #666; text-align: center;">
            Last updated: <span id="last-update">-</span>
        </div>
    </div>

    <script>
        function refresh() {
            fetch('/api/status')
                .then(r => r.json())
                .then(data => {
                    document.getElementById('balance').textContent = '$' + data.balance.toLocaleString();
                    document.getElementById('daily-pnl').textContent = (data.daily_pnl >= 0 ? '+$' : '-$') + Math.abs(data.daily_pnl).toLocaleString();
                    document.getElementById('daily-pnl').className = 'big-number ' + (data.daily_pnl >= 0 ? 'green' : 'red');
                    document.getElementById('total-pnl').textContent = (data.total_pnl >= 0 ? '+$' : '-$') + Math.abs(data.total_pnl).toLocaleString();
                    document.getElementById('total-pnl').className = 'big-number ' + (data.total_pnl >= 0 ? 'green' : 'red');
                    document.getElementById('trades').textContent = data.trades;
                    document.getElementById('positions').textContent = data.positions;
                    document.getElementById('mode').textContent = data.paper ? 'PAPER' : 'LIVE';
                    document.getElementById('last-update').textContent = new Date().toLocaleTimeString();
                })
                .catch(e => console.error(e));
        }

        // Auto-refresh every 30 seconds
        setInterval(refresh, 30000);
        refresh();
    </script>
</body>
</html>
"""


class DashboardHandler(SimpleHTTPRequestHandler):
    """HTTP handler for dashboard"""

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(DASHBOARD_HTML.encode())

        elif self.path == "/api/status":
            trader = get_trader()
            data = {
                "balance": trader.get_balance(),
                "daily_pnl": trader.get_daily_pnl(),
                "total_pnl": trader.get_total_pnl(),
                "trades": len(trader.get_positions()),
                "positions": len(trader.get_positions()),
                "paper": config.trading.paper_trading,
            }
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(data).encode())

        else:
            self.send_error(404)

    def log_message(self, format, *args):
        pass  # Suppress HTTP logs


def start_dashboard(port: int = 8080):
    """Start dashboard server in background"""
    server = HTTPServer(("0.0.0.0", port), DashboardHandler)

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    logger.info(f"Dashboard running at http://localhost:{port}")
    return server
