# Polymarket Speed Bot - Project Context

## Overview
This is a Polymarket trading bot that monitors news events (sports & politics) and automatically trades on prediction markets when it detects an edge.

## Architecture
- **Backend**: FastAPI server at `http://localhost:8000`
- **Autobot**: News monitoring and trading engine
- **Frontend**: Dashboard showing events, trades, positions

## Monitors
- **Sports Monitor** (45s): ESPN, CBS Sports, Yahoo, Bleacher Report - detects injuries, trades, game results
- **Political Monitor** (60s): Politico, The Hill, NPR, BBC RSS feeds
- **Supreme Court Monitor** (30s): Court rulings
- **Regulatory Monitor** (60s): SEC, FDA decisions

## Trading Settings
- Auto-trading: **ENABLED** (keep on unless user says turn off)
- Paper trading: true
- Min confidence: 70%
- Max 3 concurrent positions
- 4-hour cooldown per market

## Position Sizing (Equity-Based)
| Setting | Default | Description |
|---------|---------|-------------|
| risk_per_trade_pct | 2% | Risk 2% of equity per trade |
| max_position_pct | 20% | Max 20% of equity in single position |
| max_daily_loss_pct | 10% | Stop trading if down 10% of equity |
| stop_loss_pct | 15% | Stop loss at 15% below entry |

### How It Works
- **Risk Amount** = Equity × risk_per_trade_pct (e.g., $10,000 × 2% = $200)
- **Position Size** = Risk Amount / stop_loss_pct (e.g., $200 / 15% = $1,333)
- As equity grows, position sizes scale proportionally
- Example at different equity levels:
  - $10,000 equity → $200 risk → ~$1,333 position
  - $20,000 equity → $400 risk → ~$2,666 position
  - $50,000 equity → $1,000 risk → ~$6,666 position

## Edge Calculation

### Political Events
| Event Type | Target Edge |
|------------|-------------|
| Supreme Court ruling | 50% |
| Executive order | 40% |
| Major political news | 40% |
| Legislation | 35% |
| Regulatory (SEC/FDA) | 35% |
| Candidate announcement | 30% |

### Sports Events
| Event Type | Target Edge |
|------------|-------------|
| Injury (severe/season-ending) | 45% |
| Game result (championship) | 40% |
| Trade/signing | 35% |
| Injury (moderate/week-to-week) | 30% |
| General sports news | 25% |
| Injury (minor/day-to-day) | 20% |

### How Edge Works
- Fair value = 50% + edge (for positive outcomes like WIN, SIGNED, APPROVED)
- Fair value = 50% - edge (for negative outcomes like LOSS, OUT, DENIED)
- If fair value > current market price → BUY YES
- If fair value < current market price → BUY NO
- Only trade if edge > min_edge_to_trade (default 5%)

## Risk Management (Stop Loss / Take Profit / Trailing Stop)

### Settings (in config.py)
| Setting | Default | Description |
|---------|---------|-------------|
| stop_loss_pct | 15% | Close position if down 15% from entry |
| take_profit_pct | 30% | Close position if up 30% from entry |
| breakeven_trigger_pct | 10% | Move stop to breakeven after 10% profit |
| trailing_stop_pct | 10% | Trail by 10% below highest price |
| use_trailing_stop | true | Enable trailing stop after breakeven |

### How It Works
1. **Initial Entry**: Position opens with SL at entry - 15% and TP at entry + 30%
2. **Breakeven Trigger**: When position gains 10%, stop loss moves to entry price
3. **Trailing Stop**: After breakeven, stop trails 10% below highest price reached
4. **Close Reasons**: STOP_LOSS, TAKE_PROFIT, BREAKEVEN_STOP, TRAILING_STOP

### Position Monitor
- Runs every 30 seconds
- Checks all positions against SL/TP levels
- Auto-closes positions that hit limits
- Sends alerts for closed positions

## Key Decisions
- Sports and politics events stored separately (50 each) to prevent flooding
- Frontend fetches 100 events to capture both categories
- Star player detection boosts confidence by 15%

## Market Deduplication
Prevents trading the same market repeatedly:
- **Open Position Check**: Won't trade a market if there's already an open position
- **Market Cooldown**: 4-hour cooldown per market after trading
- **Persists Across Restarts**: Cooldown state loaded from database on startup
- Implemented in `autobot/trading/executor.py`

## Database Persistence
Trades and bot state are stored in SQLite at `autobot/data/trades.db`:
- **closed_trades** table: Full trade history with P&L, close reason, etc.
- **bot_state** table: Paper balance, daily P&L, total P&L
- Data persists across server restarts
- Database created automatically on first run

## Commands
- Start server: `python3 -m uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000`
- Dashboard: `http://localhost:8000`
- View database: `sqlite3 autobot/data/trades.db "SELECT * FROM closed_trades;"`
