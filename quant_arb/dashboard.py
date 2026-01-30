"""
Market Maker Dashboard
Simple web UI to monitor the market maker
"""

import asyncio
import logging
import os
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from collections import deque

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
import uvicorn
from dotenv import load_dotenv

from market_maker import PolymarketMM, MMConfig, Side

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(message)s', datefmt='%H:%M:%S')
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('websockets').setLevel(logging.WARNING)
logging.getLogger('uvicorn.access').setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

app = FastAPI(title="Market Maker Dashboard")

# Global state
mm: Optional[PolymarketMM] = None
recent_fills: deque = deque(maxlen=50)
start_time: Optional[datetime] = None


# Store fills when they happen
original_place_quote = None

async def tracked_place_quote(self, state, side, price, size):
    """Wrapper to track fills"""
    result = await original_place_quote(self, state, side, price, size)

    # Check if a fill happened by comparing stats
    if self.config.paper_trading and result:
        # Record fill for dashboard
        recent_fills.appendleft({
            'time': datetime.now().strftime('%H:%M:%S'),
            'market': state.question[:40],
            'side': side.value,
            'price': price,
            'size': size,
            'pnl': state.pnl,
        })

    return result


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Main dashboard page"""

    # Calculate stats
    runtime = ""
    if start_time:
        delta = datetime.now() - start_time
        hours, remainder = divmod(int(delta.total_seconds()), 3600)
        minutes, seconds = divmod(remainder, 60)
        runtime = f"{hours}h {minutes}m {seconds}s"

    # Get market data
    markets_html = ""
    total_position = 0

    if mm and mm.markets:
        for mid, state in sorted(mm.markets.items(), key=lambda x: -abs(x[1].position.size)):
            pos_class = "positive" if state.position.size > 0 else "negative" if state.position.size < 0 else ""
            pnl_class = "positive" if state.pnl > 0 else "negative" if state.pnl < 0 else ""

            markets_html += f"""
            <tr>
                <td class="market-name">{state.question[:45]}...</td>
                <td>${state.best_bid:.3f}</td>
                <td>${state.best_ask:.3f}</td>
                <td class="{pos_class}">${state.position.size:.2f}</td>
                <td>{state.trades}</td>
                <td class="{pnl_class}">${state.pnl:.2f}</td>
            </tr>
            """
            total_position += abs(state.position.size)

    # Get recent fills
    fills_html = ""
    for fill in list(recent_fills)[:20]:
        side_class = "buy" if fill['side'] == 'BUY' else "sell"
        fills_html += f"""
        <tr>
            <td>{fill['time']}</td>
            <td class="{side_class}">{fill['side']}</td>
            <td>${fill['size']:.0f}</td>
            <td>${fill['price']:.3f}</td>
            <td class="market-name">{fill['market']}</td>
        </tr>
        """

    # Status
    status = "RUNNING" if mm and mm.running else "STOPPED"
    status_class = "running" if mm and mm.running else "stopped"
    mode = "PAPER" if mm and mm.config.paper_trading else "LIVE"
    mode_class = "paper" if mm and mm.config.paper_trading else "live"

    # Stats
    total_trades = mm.total_trades if mm else 0
    total_volume = mm.total_volume if mm else 0
    total_pnl = mm.total_pnl if mm else 0
    daily_pnl = mm.daily_pnl if mm else 0
    num_markets = len(mm.markets) if mm else 0

    pnl_class = "positive" if total_pnl > 0 else "negative" if total_pnl < 0 else ""

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Market Maker Dashboard</title>
        <meta http-equiv="refresh" content="3">
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            body {{
                font-family: 'Monaco', 'Menlo', monospace;
                background: #0a0a0a;
                color: #e0e0e0;
                padding: 20px;
            }}
            .header {{
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 20px;
                padding-bottom: 15px;
                border-bottom: 1px solid #333;
            }}
            .title {{ font-size: 24px; font-weight: bold; }}
            .status {{
                padding: 8px 16px;
                border-radius: 4px;
                font-weight: bold;
            }}
            .status.running {{ background: #1a472a; color: #4ade80; }}
            .status.stopped {{ background: #472a2a; color: #f87171; }}
            .mode {{
                padding: 8px 16px;
                border-radius: 4px;
                margin-left: 10px;
            }}
            .mode.paper {{ background: #3d3d00; color: #fbbf24; }}
            .mode.live {{ background: #1a472a; color: #4ade80; }}
            .stats-grid {{
                display: grid;
                grid-template-columns: repeat(6, 1fr);
                gap: 15px;
                margin-bottom: 25px;
            }}
            .stat-box {{
                background: #1a1a1a;
                border: 1px solid #333;
                border-radius: 8px;
                padding: 15px;
                text-align: center;
            }}
            .stat-label {{ color: #888; font-size: 12px; margin-bottom: 5px; }}
            .stat-value {{ font-size: 24px; font-weight: bold; }}
            .stat-value.positive {{ color: #4ade80; }}
            .stat-value.negative {{ color: #f87171; }}
            .section {{ margin-bottom: 25px; }}
            .section-title {{
                font-size: 16px;
                margin-bottom: 10px;
                color: #888;
                border-bottom: 1px solid #333;
                padding-bottom: 5px;
            }}
            table {{
                width: 100%;
                border-collapse: collapse;
                font-size: 13px;
            }}
            th {{
                text-align: left;
                padding: 10px;
                background: #1a1a1a;
                color: #888;
                font-weight: normal;
            }}
            td {{
                padding: 10px;
                border-bottom: 1px solid #222;
            }}
            tr:hover {{ background: #1a1a1a; }}
            .market-name {{ color: #888; }}
            .positive {{ color: #4ade80; }}
            .negative {{ color: #f87171; }}
            .buy {{ color: #4ade80; }}
            .sell {{ color: #f87171; }}
            .two-col {{
                display: grid;
                grid-template-columns: 2fr 1fr;
                gap: 20px;
            }}
            .fills-table {{ max-height: 400px; overflow-y: auto; }}
        </style>
    </head>
    <body>
        <div class="header">
            <div class="title">POLYMARKET MARKET MAKER</div>
            <div>
                <span class="status {status_class}">{status}</span>
                <span class="mode {mode_class}">{mode}</span>
            </div>
        </div>

        <div class="stats-grid">
            <div class="stat-box">
                <div class="stat-label">RUNTIME</div>
                <div class="stat-value">{runtime or '--'}</div>
            </div>
            <div class="stat-box">
                <div class="stat-label">MARKETS</div>
                <div class="stat-value">{num_markets}</div>
            </div>
            <div class="stat-box">
                <div class="stat-label">TRADES</div>
                <div class="stat-value">{total_trades}</div>
            </div>
            <div class="stat-box">
                <div class="stat-label">VOLUME</div>
                <div class="stat-value">${total_volume:.0f}</div>
            </div>
            <div class="stat-box">
                <div class="stat-label">POSITION</div>
                <div class="stat-value">${total_position:.0f}</div>
            </div>
            <div class="stat-box">
                <div class="stat-label">TOTAL P&L</div>
                <div class="stat-value {pnl_class}">${total_pnl:.2f}</div>
            </div>
        </div>

        <div class="two-col">
            <div class="section">
                <div class="section-title">MARKETS</div>
                <table>
                    <thead>
                        <tr>
                            <th>Market</th>
                            <th>Bid</th>
                            <th>Ask</th>
                            <th>Position</th>
                            <th>Trades</th>
                            <th>P&L</th>
                        </tr>
                    </thead>
                    <tbody>
                        {markets_html or '<tr><td colspan="6" style="text-align:center;color:#666">No markets loaded</td></tr>'}
                    </tbody>
                </table>
            </div>

            <div class="section">
                <div class="section-title">RECENT FILLS</div>
                <div class="fills-table">
                    <table>
                        <thead>
                            <tr>
                                <th>Time</th>
                                <th>Side</th>
                                <th>Size</th>
                                <th>Price</th>
                                <th>Market</th>
                            </tr>
                        </thead>
                        <tbody>
                            {fills_html or '<tr><td colspan="5" style="text-align:center;color:#666">No fills yet</td></tr>'}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>

        <div style="margin-top: 20px; color: #444; font-size: 11px; text-align: center;">
            Auto-refreshes every 3 seconds | Press Ctrl+C in terminal to stop
        </div>
    </body>
    </html>
    """

    return HTMLResponse(content=html)


@app.get("/api/stats")
async def get_stats():
    """API endpoint for stats"""
    if not mm:
        return {"status": "not_running"}

    return {
        "status": "running" if mm.running else "stopped",
        "mode": "paper" if mm.config.paper_trading else "live",
        "markets": len(mm.markets),
        "trades": mm.total_trades,
        "volume": mm.total_volume,
        "pnl": mm.total_pnl,
        "daily_pnl": mm.daily_pnl,
    }


async def run_market_maker():
    """Run the market maker in background"""
    global mm, start_time, original_place_quote

    load_dotenv()

    config = MMConfig(
        paper_trading=True,
        min_spread_pct=0.01,
        max_spread_pct=0.05,
        quote_size=10.0,
        max_position=100.0,
        max_markets=15,
        min_market_volume=20000,
        min_liquidity=5000,
        quote_refresh_ms=2000,
        fill_probability=0.3,
    )

    mm = PolymarketMM(config)

    # Patch to track fills
    original_place_quote = PolymarketMM._place_quote
    PolymarketMM._place_quote = tracked_place_quote

    if await mm.initialize():
        start_time = datetime.now()
        await mm.run()


async def main():
    """Main entry point"""
    # Start market maker in background
    mm_task = asyncio.create_task(run_market_maker())

    # Start web server
    config = uvicorn.Config(app, host="0.0.0.0", port=8080, log_level="warning")
    server = uvicorn.Server(config)

    logger.info("=" * 60)
    logger.info("DASHBOARD: http://localhost:8080")
    logger.info("=" * 60)

    await server.serve()


if __name__ == "__main__":
    asyncio.run(main())
