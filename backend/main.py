"""FastAPI server for the Polymarket BTC Trading Bot."""

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .config import get_settings, TIMEFRAME_MAP
from .data import PriceFetcher, get_price_stream, get_polymarket_client
from .analysis import (
    TechnicalIndicators,
    get_ml_predictor,
    get_signal_generator,
    get_mtf_aggregator,
)
from .trading import get_risk_manager, get_portfolio_manager, get_trade_executor
from .utils import get_api_logger

logger = get_api_logger()

# Global state
price_fetcher: Optional[PriceFetcher] = None
background_tasks: list[asyncio.Task] = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    global price_fetcher

    logger.info("Starting Polymarket News/Events Trading Bot...")

    # Initialize the autobot (news/events trading)
    try:
        from autobot.main import get_bot
        autobot = get_bot()
        autobot_task = asyncio.create_task(autobot.start())
        background_tasks.append(autobot_task)
        logger.info("Autobot (news/events monitor) started")
    except Exception as e:
        logger.error(f"Failed to start autobot: {e}")

    # Initialize BTC price components (for dashboard display only)
    price_fetcher = PriceFetcher()
    await price_fetcher.initialize()

    polymarket = get_polymarket_client()
    await polymarket.initialize()

    executor = get_trade_executor()
    await executor.initialize()

    # Start price stream in background (for dashboard)
    price_stream = get_price_stream()
    stream_task = asyncio.create_task(price_stream.start(interval=2.0))
    background_tasks.append(stream_task)

    logger.info("Bot initialized successfully")

    yield

    # Cleanup
    logger.info("Shutting down...")
    for task in background_tasks:
        task.cancel()

    await price_stream.stop()
    await price_fetcher.close()


async def auto_trading_loop():
    """
    Background task that automatically executes trades based on signals.
    Runs every 30 seconds to check for trading opportunities.
    """
    logger.info("Auto-trading loop started")
    settings = get_settings()

    while True:
        try:
            await asyncio.sleep(30)  # Check every 30 seconds

            # Check if auto-trading is enabled
            if not settings.auto_trade_enabled:
                continue

            executor = get_trade_executor()
            portfolio = get_portfolio_manager()
            risk_manager = get_risk_manager()

            # Skip if no market selected
            if not executor.get_selected_market():
                logger.debug("No market selected for auto-trading")
                continue

            # Get price data and generate signal
            signal_gen = get_signal_generator()
            df = await price_fetcher.fetch_ohlcv("15m", limit=100)
            if df is None or df.empty:
                logger.debug("No price data available")
                continue

            signal = signal_gen.generate_signal(df, "15m")

            if not signal:
                continue

            # Check if we should trade
            if signal.direction == "HOLD":
                continue

            # Check confidence threshold
            if signal.confidence < settings.min_confidence_threshold:
                logger.debug(f"Signal confidence {signal.confidence:.1f}% below threshold")
                continue

            # Check if we already have an open position in this market
            market = executor.get_selected_market()
            existing_positions = portfolio.get_positions_for_market(market["id"])
            if existing_positions:
                # Monitor existing position for stop-loss/take-profit
                await monitor_positions(existing_positions, executor)
                continue

            # Execute trade
            logger.info(f"Auto-trade signal: {signal.direction} with {signal.confidence:.1f}% confidence")
            result = await executor.execute_signal(signal, settings.max_position_size)

            if result.success:
                logger.info(f"Auto-trade executed: {result.side} @ {result.price:.4f}")
            else:
                logger.warning(f"Auto-trade failed: {result.error}")

        except asyncio.CancelledError:
            logger.info("Auto-trading loop cancelled")
            break
        except Exception as e:
            logger.error(f"Auto-trading loop error: {e}")
            await asyncio.sleep(10)


async def monitor_positions(positions, executor):
    """Monitor positions and close when stop-loss or take-profit is hit."""
    settings = get_settings()
    portfolio = get_portfolio_manager()

    for position in positions:
        # Calculate P&L percentage
        if position.entry_price > 0:
            pnl_pct = ((position.current_price - position.entry_price) / position.entry_price) * 100
        else:
            pnl_pct = 0

        # Check stop-loss (default 10% loss)
        if pnl_pct <= -settings.stop_loss_percent:
            logger.info(f"Stop-loss triggered for {position.market_name}: {pnl_pct:.1f}%")
            from .trading.portfolio import CloseReason
            result = portfolio.close_position(position.id, position.current_price, CloseReason.STOP_LOSS)
            if result:
                logger.info(f"Position closed: P&L ${result['pnl']:.2f}")
            continue

        # Check take-profit (20% gain)
        if pnl_pct >= 20:
            logger.info(f"Take-profit triggered for {position.market_name}: {pnl_pct:.1f}%")
            from .trading.portfolio import CloseReason
            result = portfolio.close_position(position.id, position.current_price, CloseReason.TAKE_PROFIT)
            if result:
                logger.info(f"Position closed: P&L ${result['pnl']:.2f}")
            continue

        # Check trailing stop (if position is up 10%+, set trailing stop at 5% from high)
        if pnl_pct >= 10 and position.highest_price > 0:
            trailing_stop_price = position.highest_price * 0.95
            if position.current_price <= trailing_stop_price:
                logger.info(f"Trailing stop triggered for {position.market_name}")
                from .trading.portfolio import CloseReason
                result = portfolio.close_position(position.id, position.current_price, CloseReason.TRAILING_STOP)
                if result:
                    logger.info(f"Position closed: P&L ${result['pnl']:.2f}")


# Create FastAPI app
app = FastAPI(
    title="Polymarket BTC Trading Bot",
    description="AI-powered Bitcoin prediction trading bot for Polymarket",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Pydantic models for requests/responses
class TradeRequest(BaseModel):
    """Trade execution request."""
    market_id: str
    side: str  # "YES" or "NO"
    size_usd: float


class SettingsUpdate(BaseModel):
    """Settings update request."""
    auto_trade_enabled: Optional[bool] = None
    min_confidence_threshold: Optional[float] = None
    max_position_size: Optional[float] = None
    daily_loss_limit: Optional[float] = None
    paper_trading: Optional[bool] = None


# API Endpoints

@app.get("/api/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/api/price")
async def get_current_price():
    """Get current BTC price."""
    price_stream = get_price_stream()
    price_data = price_stream.current_price

    if not price_data:
        price_data = await price_fetcher.fetch_current_price()

    return price_data


@app.get("/api/price/history")
async def get_price_history(
    tf: str = Query(default="15m", description="Timeframe"),
    limit: int = Query(default=200, le=500, description="Number of candles"),
):
    """Get historical OHLCV data."""
    if tf not in TIMEFRAME_MAP:
        raise HTTPException(status_code=400, detail=f"Invalid timeframe: {tf}")

    df = await price_fetcher.fetch_ohlcv(tf, limit=limit)

    # Convert to list of dicts for JSON
    records = []
    for idx, row in df.iterrows():
        records.append({
            "time": int(idx.timestamp()),
            "open": row["open"],
            "high": row["high"],
            "low": row["low"],
            "close": row["close"],
            "volume": row["volume"],
        })

    return {"timeframe": tf, "data": records}


@app.get("/api/indicators")
async def get_indicators(
    tf: str = Query(default="15m", description="Timeframe"),
):
    """Get current indicator values."""
    df = await price_fetcher.fetch_ohlcv(tf, limit=100)

    indicators = TechnicalIndicators()
    df_with_indicators = indicators.calculate_all(df)
    values = indicators.get_latest_values(df_with_indicators)
    signals = indicators.generate_signals(df_with_indicators)

    return {
        "timeframe": tf,
        "indicators": values,
        "signals": signals,
    }


@app.get("/api/prediction")
async def get_prediction(
    tf: str = Query(default="15m", description="Timeframe"),
):
    """Get current ML prediction and combined signal."""
    df = await price_fetcher.fetch_ohlcv(tf, limit=200)

    signal_generator = get_signal_generator()
    signal = signal_generator.generate_signal(df, tf)

    return signal.to_dict()


@app.get("/api/prediction/mtf")
async def get_mtf_prediction():
    """Get multi-timeframe aggregated prediction."""
    data = await price_fetcher.fetch_multi_timeframe(
        timeframes=["5m", "15m", "1h", "4h"],
        limit=100,
    )

    aggregator = get_mtf_aggregator()
    signal = aggregator.aggregate_signals(data)

    return signal.to_dict()


@app.get("/api/prediction/15m")
async def get_15m_prediction():
    """
    Get dedicated 15-minute BTC price movement prediction.
    Returns UP or DOWN prediction with confidence and supporting data.
    """
    df = await price_fetcher.fetch_ohlcv("15m", limit=200)

    # Calculate indicators
    indicators = TechnicalIndicators()
    df_with_indicators = indicators.calculate_all(df)

    # Get ML prediction
    ml_predictor = get_ml_predictor()
    ml_pred = ml_predictor.predict(df_with_indicators)

    # Get indicator signals
    ind_signals = indicators.generate_signals(df_with_indicators)
    ind_values = indicators.get_latest_values(df_with_indicators)

    # Current price info
    current_price = float(df["close"].iloc[-1])
    prev_price = float(df["close"].iloc[-2])
    price_change = ((current_price - prev_price) / prev_price) * 100

    # Determine final prediction (ML-weighted)
    ml_direction = ml_pred.get("direction", "NEUTRAL")
    ml_confidence = ml_pred.get("confidence", 50)

    # Count bullish/bearish indicators
    bullish_count = sum(1 for v in ind_signals.values() if v > 0)
    bearish_count = sum(1 for v in ind_signals.values() if v < 0)

    return {
        "prediction": {
            "direction": ml_direction,
            "confidence": ml_confidence,
            "probabilities": ml_pred.get("probabilities", {"UP": 0.5, "DOWN": 0.5}),
        },
        "current_price": current_price,
        "price_change_pct": price_change,
        "timeframe": "15m",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "indicators": {
            "rsi": ind_values.get("rsi"),
            "macd": ind_values.get("macd"),
            "macd_histogram": ind_values.get("macd_hist"),
            "stochastic_k": ind_values.get("stoch_k"),
            "bollinger_percent": ind_values.get("bb_percent"),
        },
        "signals": {
            "bullish_count": bullish_count,
            "bearish_count": bearish_count,
            "indicator_signals": ind_signals,
        },
        "model_ready": ml_pred.get("model_ready", False),
        "polymarket": await get_polymarket_sentiment_data(),
    }


async def get_polymarket_sentiment_data():
    """Helper to get Polymarket sentiment for predictions."""
    try:
        polymarket = get_polymarket_client()
        sentiment = await polymarket.get_btc_sentiment()
        return {
            "sentiment": sentiment.get("overall", "NEUTRAL"),
            "bullish_ratio": sentiment.get("bullish_ratio", 0.5),
            "markets_count": sentiment.get("markets_analyzed", 0),
            "top_markets": sentiment.get("market_details", [])[:3],
        }
    except Exception as e:
        return {"sentiment": "UNAVAILABLE", "error": str(e)}


@app.get("/api/markets")
async def get_btc_markets():
    """Get available BTC prediction markets."""
    polymarket = get_polymarket_client()

    # Refresh markets if empty
    if not polymarket.btc_markets:
        await polymarket.search_btc_markets()

    return {"markets": polymarket.btc_markets}


@app.get("/api/markets/sentiment")
async def get_polymarket_sentiment():
    """Get Polymarket BTC sentiment from available markets."""
    polymarket = get_polymarket_client()
    sentiment = await polymarket.get_btc_sentiment()
    return sentiment


@app.get("/api/markets/{market_id}")
async def get_market_details(market_id: str):
    """Get details for a specific market."""
    polymarket = get_polymarket_client()
    market = await polymarket.get_market_details(market_id)

    if not market:
        raise HTTPException(status_code=404, detail="Market not found")

    return market


@app.post("/api/markets/{market_id}/select")
async def select_market(market_id: str):
    """Select a market for trading."""
    polymarket = get_polymarket_client()

    # Find market in cached list
    market = next(
        (m for m in polymarket.btc_markets if m["id"] == market_id),
        None
    )

    if not market:
        market = await polymarket.get_market_details(market_id)

    if not market:
        raise HTTPException(status_code=404, detail="Market not found")

    executor = get_trade_executor()
    executor.set_market(market)

    return {"message": "Market selected", "market": market}


@app.get("/api/positions")
async def get_positions():
    """Get current open positions."""
    portfolio = get_portfolio_manager()
    positions = [p.to_dict() for p in portfolio.get_positions()]
    summary = portfolio.get_summary().to_dict()

    return {
        "positions": positions,
        "summary": summary,
    }


@app.get("/api/trades/history")
async def get_trade_history(limit: int = Query(default=50, le=200)):
    """Get closed trade history with full details."""
    portfolio = get_portfolio_manager()
    trades = portfolio.get_trade_history(limit)
    return {"trades": trades, "total": len(trades)}


@app.get("/api/trades/daily")
async def get_daily_trades():
    """Get today's closed trades."""
    portfolio = get_portfolio_manager()
    trades = [t.to_dict() for t in portfolio.get_daily_trades()]
    daily_pnl = portfolio.get_daily_pnl()
    return {
        "trades": trades,
        "total": len(trades),
        "daily_pnl": daily_pnl,
    }


@app.get("/api/trades/statistics")
async def get_trade_statistics():
    """Get comprehensive trade statistics."""
    portfolio = get_portfolio_manager()
    stats = portfolio.get_trade_statistics()
    return stats


@app.get("/api/pnl")
async def get_pnl_breakdown():
    """Get detailed P&L breakdown."""
    portfolio = get_portfolio_manager()
    summary = portfolio.get_summary()

    return {
        "total_pnl": summary.total_pnl,
        "total_pnl_percent": summary.total_pnl_percent,
        "realized_pnl": summary.realized_pnl,
        "unrealized_pnl": summary.unrealized_pnl,
        "daily_pnl": summary.daily_pnl,
        "daily_realized_pnl": summary.daily_realized_pnl,
        "daily_unrealized_pnl": summary.daily_unrealized_pnl,
        "cash_balance": summary.cash_balance,
        "positions_value": summary.total_value,
        "total_equity": portfolio.get_total_equity(),
    }


@app.post("/api/trade")
async def execute_trade(request: TradeRequest):
    """Execute a manual trade."""
    executor = get_trade_executor()

    result = await executor.manual_trade(
        market_id=request.market_id,
        side=request.side,
        size_usd=request.size_usd,
    )

    if not result.success:
        raise HTTPException(status_code=400, detail=result.error)

    return result.to_dict()


@app.post("/api/trade/signal")
async def execute_signal_trade(
    size_usd: Optional[float] = Query(default=None),
):
    """Execute a trade based on current signal."""
    executor = get_trade_executor()

    if not executor.get_selected_market():
        raise HTTPException(status_code=400, detail="No market selected")

    # Get current signal
    df = await price_fetcher.fetch_ohlcv("15m", limit=200)
    signal_generator = get_signal_generator()
    signal = signal_generator.generate_signal(df, "15m")

    result = await executor.execute_signal(signal, size_usd)

    return result.to_dict()


@app.post("/api/positions/{position_id}/close")
async def close_position(position_id: str):
    """Close an open position."""
    executor = get_trade_executor()
    result = await executor.close_position(position_id)

    if not result.success:
        raise HTTPException(status_code=400, detail=result.error)

    return result.to_dict()


@app.get("/api/risk")
async def get_risk_status():
    """Get current risk limits and status."""
    risk_manager = get_risk_manager()
    limits = risk_manager.get_limits()

    return limits.to_dict()


@app.get("/api/settings")
async def get_settings_endpoint():
    """Get current bot settings."""
    settings = get_settings()

    return {
        "auto_trade_enabled": settings.auto_trade_enabled,
        "min_confidence_threshold": settings.min_confidence_threshold,
        "max_position_size": settings.max_position_size,
        "daily_loss_limit": settings.daily_loss_limit,
        "paper_trading": settings.paper_trading,
        "stop_loss_percent": settings.stop_loss_percent,
        "cooldown_seconds": settings.cooldown_seconds,
        "max_daily_trades": settings.max_daily_trades,
    }


@app.post("/api/settings")
async def update_settings(update: SettingsUpdate):
    """Update bot settings (runtime only, not persisted to .env)."""
    settings = get_settings()

    # Note: These are runtime changes only
    # For a production app, you'd want to persist these
    if update.auto_trade_enabled is not None:
        settings.auto_trade_enabled = update.auto_trade_enabled
    if update.min_confidence_threshold is not None:
        settings.min_confidence_threshold = update.min_confidence_threshold
    if update.max_position_size is not None:
        settings.max_position_size = update.max_position_size
    if update.daily_loss_limit is not None:
        settings.daily_loss_limit = update.daily_loss_limit
    if update.paper_trading is not None:
        settings.paper_trading = update.paper_trading

    return {"message": "Settings updated", "settings": await get_settings_endpoint()}


@app.post("/api/ml/train")
async def train_ml_model():
    """Train/retrain the ML model."""
    df = await price_fetcher.fetch_ohlcv("15m", limit=500)

    indicators = TechnicalIndicators()
    df_with_indicators = indicators.calculate_all(df)

    ml_predictor = get_ml_predictor()
    metrics = ml_predictor.train(df_with_indicators)

    return {"message": "Model trained", "metrics": metrics}


@app.get("/api/ml/status")
async def get_ml_status():
    """Get ML model status."""
    ml_predictor = get_ml_predictor()

    return {
        "ready": ml_predictor.is_ready,
        "should_retrain": ml_predictor.should_retrain(),
    }


# ============================================
# News Events API (from autobot)
# ============================================

@app.get("/api/events")
async def get_events(limit: int = Query(default=20, le=100)):
    """Get recent news events detected by the autobot."""
    try:
        from autobot.main import get_bot
        bot = get_bot()
        events = bot.get_recent_events(limit)
        return {"events": events, "total": len(events)}
    except Exception as e:
        logger.error(f"Failed to get events: {e}")
        return {"events": [], "total": 0, "error": str(e)}


@app.get("/api/events/matches")
async def get_event_matches(limit: int = Query(default=10, le=50)):
    """Get recent market matches from news events."""
    try:
        from autobot.main import get_bot
        bot = get_bot()
        matches = bot.get_recent_matches(limit)
        return {"matches": matches, "total": len(matches)}
    except Exception as e:
        logger.error(f"Failed to get matches: {e}")
        return {"matches": [], "total": 0, "error": str(e)}


@app.get("/api/events/monitors")
async def get_monitors_status():
    """Get status of all news monitors."""
    try:
        from autobot.main import get_bot
        bot = get_bot()
        monitors = bot.get_monitors_status()
        return {"monitors": monitors}
    except Exception as e:
        logger.error(f"Failed to get monitors: {e}")
        return {"monitors": [], "error": str(e)}


@app.get("/api/events/status")
async def get_autobot_status():
    """Get overall autobot status."""
    try:
        from autobot.main import get_bot
        bot = get_bot()
        status = bot.get_bot_status()
        return status
    except Exception as e:
        logger.error(f"Failed to get bot status: {e}")
        return {"running": False, "error": str(e)}


@app.get("/api/events/positions")
async def get_autobot_positions():
    """Get autobot open positions with unrealized P&L."""
    try:
        from autobot.main import get_bot
        from autobot.data import database as db
        bot = get_bot()
        trader = bot.trader

        positions = await trader.get_positions_with_pnl()
        total_unrealized = sum(p.get("unrealized_pnl", 0) for p in positions)
        total_value = sum(p.get("value", 0) for p in positions)

        # Get P&L from database (source of truth) to ensure consistency
        trade_stats = db.get_trade_stats()
        bot_state = db.get_bot_state()

        # Cash balance = total equity - open positions value
        total_equity = bot_state.get("paper_balance", 10000.0)
        cash_balance = total_equity - total_value

        return {
            "positions": positions,
            "summary": {
                "position_count": len(positions),
                "total_value": total_value,
                "total_unrealized_pnl": total_unrealized,
                "cash_balance": cash_balance,
                "total_equity": total_equity,
                "daily_pnl": bot_state.get("daily_pnl", 0),
                "total_pnl": bot_state.get("total_pnl", 0),
                "paper_mode": True,
            },
            "stats": trade_stats,
        }
    except Exception as e:
        logger.error(f"Failed to get autobot positions: {e}")
        return {"positions": [], "summary": {"position_count": 0, "total_unrealized_pnl": 0}}


@app.get("/api/events/trades")
async def get_autobot_trades(limit: int = Query(default=20, le=100)):
    """Get trades executed by the autobot (both open and closed)."""
    try:
        from autobot.main import get_bot
        bot = get_bot()
        trader = bot.trader

        trades = []

        # Get actually open positions first
        open_positions = trader.get_positions()
        for pos in open_positions:
            trade_data = {
                "id": pos.get("id"),
                "market_name": pos.get("market", "Unknown"),
                "side": pos.get("side"),
                "prediction": pos.get("prediction", "YES"),  # YES or NO
                "size_usd": pos.get("value", 0),
                "size_shares": pos.get("size", 0),
                "entry_price": pos.get("price", 0),
                "exit_price": None,
                "pnl": pos.get("unrealized_pnl", 0),
                "pnl_pct": pos.get("pnl_pct", 0),
                "won": None,
                "close_reason": None,
                "status": "OPEN",
                "entry_time": pos.get("entry_time", ""),
                "exit_time": None,
                "stop_loss_price": pos.get("stop_loss_price"),
                "take_profit_price": pos.get("take_profit_price"),
                "breakeven_triggered": pos.get("breakeven_triggered", False),
                "trailing_stop_active": pos.get("trailing_stop_active", False),
                "paper": pos.get("paper", True),
            }
            trades.append(trade_data)

        # Get closed trades (most recent)
        closed_trades = trader.get_closed_trades(limit)
        for trade in closed_trades:
            trade_data = {
                "id": trade.get("id"),
                "market_name": trade.get("market", "Unknown"),
                "side": trade.get("side"),
                "prediction": trade.get("prediction", "YES"),  # YES or NO
                "size_usd": trade.get("risk_amount", 0),
                "size_shares": trade.get("size", 0),
                "entry_price": trade.get("entry_price", 0),
                "exit_price": trade.get("exit_price", 0),
                "pnl": trade.get("pnl", 0),
                "pnl_pct": trade.get("pnl_pct", 0),
                "won": trade.get("won", False),
                "close_reason": trade.get("close_reason", ""),
                "status": "CLOSED",
                "entry_time": trade.get("entry_time", ""),
                "exit_time": trade.get("exit_time", ""),
                "stop_loss_price": trade.get("stop_loss_price"),
                "take_profit_price": trade.get("take_profit_price"),
                "breakeven_triggered": trade.get("breakeven_triggered", False),
                "trailing_stop_active": trade.get("trailing_stop_active", False),
                "paper": trade.get("paper", True),
            }
            trades.append(trade_data)

        # Get trade stats
        stats = trader.get_trade_stats()

        return {"trades": trades, "total": len(trades), "stats": stats}
    except Exception as e:
        logger.error(f"Failed to get autobot trades: {e}")
        return {"trades": [], "total": 0, "error": str(e)}


# WebSocket for real-time updates
@app.websocket("/ws/stream")
async def websocket_stream(websocket: WebSocket):
    """WebSocket endpoint for real-time price and signal updates."""
    await websocket.accept()
    logger.info("WebSocket client connected")

    price_stream = get_price_stream()
    queue = price_stream.subscribe()

    try:
        signal_generator = get_signal_generator()
        last_signal_time = 0

        while True:
            try:
                # Get price update (with timeout)
                price_data = await asyncio.wait_for(queue.get(), timeout=5.0)

                # Send price update
                await websocket.send_json({
                    "type": "price",
                    "data": price_data,
                })

                # Generate and send signal every 30 seconds
                now = datetime.now(timezone.utc).timestamp()
                if now - last_signal_time > 30:
                    try:
                        df = price_fetcher.get_cached_data("15m")
                        if df is not None and not df.empty:
                            indicators = TechnicalIndicators()
                            df_with_indicators = indicators.calculate_all(df)
                            signal = signal_generator.generate_signal(df_with_indicators, "15m")

                            await websocket.send_json({
                                "type": "signal",
                                "data": signal.to_dict(),
                            })
                            last_signal_time = now
                    except Exception as e:
                        logger.error(f"Signal generation error: {e}")

            except asyncio.TimeoutError:
                # Send heartbeat
                await websocket.send_json({"type": "heartbeat"})

    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        price_stream.unsubscribe(queue)


# Serve static files (frontend)
import os
frontend_path = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.exists(frontend_path):
    app.mount("/static", StaticFiles(directory=frontend_path), name="static")

    @app.get("/")
    async def serve_frontend():
        """Serve the frontend dashboard."""
        return FileResponse(os.path.join(frontend_path, "index.html"))


if __name__ == "__main__":
    import uvicorn
    settings = get_settings()
    uvicorn.run(
        "backend.main:app",
        host=settings.host,
        port=settings.port,
        reload=True,
    )
