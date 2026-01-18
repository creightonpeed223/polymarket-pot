"""
Polymarket Speed Trading Bot - Main Runner

This is the main entry point for the automated trading bot.
It coordinates all components:
- News monitoring
- Market matching
- Trade execution
- Risk management
- Alerting

Usage:
    python -m autobot.main

Or:
    python autobot/main.py
"""

import asyncio
import signal
import sys
from collections import deque
from datetime import datetime, timezone
from typing import List, Dict, Any

from .config import config, load_config
from .utils.logger import setup_logging, get_logger
from .trading.polymarket_client import PolymarketTrader, get_trader
from .trading.executor import TradeExecutor
from .monitors.base import NewsEvent
from .monitors.court import SupremeCourtMonitor
from .monitors.political import PoliticalMonitor
from .monitors.regulatory import RegulatoryMonitor
from .monitors.twitter import TwitterMonitor
from .monitors.sports import SportsMonitor
from .nlp.parser import NewsParser
from .nlp.matcher import MarketMatcher
from .risk.manager import RiskManager
from .alerts.notifier import AlertNotifier

# Setup logging
setup_logging(config.log_level)
logger = get_logger(__name__)


class SpeedTradingBot:
    """
    Main bot orchestrator

    Coordinates:
    - Multiple news monitors
    - Market matching
    - Trade execution
    - Risk management
    """

    def __init__(self):
        self.trader = get_trader()
        self.parser = NewsParser()
        self.matcher = MarketMatcher(self.trader)
        self.executor = TradeExecutor(self.trader)
        self.risk_manager = RiskManager(self.trader)
        self.notifier = AlertNotifier()

        # Monitors
        self.monitors: List = []

        # State
        self._running = False
        self._events_processed = 0
        self._trades_executed = 0

        # Recent events storage - separate by category to prevent flooding
        self._recent_sports_events: deque = deque(maxlen=50)
        self._recent_politics_events: deque = deque(maxlen=50)
        self._recent_matches: deque = deque(maxlen=50)

    async def start(self):
        """Start the trading bot"""
        logger.info("=" * 60)
        logger.info("POLYMARKET SPEED TRADING BOT")
        logger.info("=" * 60)

        # Initialize trader
        if not await self.trader.initialize():
            logger.error("Failed to initialize trader")
            return

        # Load markets
        await self.matcher.load_markets()

        # Setup monitors
        self._setup_monitors()

        # Register trade callback for alerts
        self.executor.on_trade(self._on_trade)

        # Send startup alert
        await self.notifier.send_startup_alert()

        logger.info("")
        logger.info(f"Mode: {'PAPER' if config.trading.paper_trading else 'LIVE'} TRADING")
        logger.info(f"Auto-Trade: {'ENABLED' if config.trading.auto_trade_enabled else 'DISABLED'}")
        logger.info(f"Starting Balance: ${self.trader.get_balance():,.2f}")
        logger.info(f"Min Edge: {config.trading.min_edge_to_trade:.0%}")
        logger.info("")
        logger.info("Bot is now running. Press Ctrl+C to stop.")
        logger.info("=" * 60)

        self._running = True

        # Start all monitors and risk manager
        tasks = []

        for monitor in self.monitors:
            tasks.append(asyncio.create_task(monitor.start()))

        # Risk monitoring
        tasks.append(asyncio.create_task(self.risk_manager.monitor_loop(60)))

        # Market refresh (every 5 minutes)
        tasks.append(asyncio.create_task(self._market_refresh_loop()))

        # Position monitor (every 30 seconds - checks SL/TP/trailing stop)
        tasks.append(asyncio.create_task(self._position_monitor_loop()))

        # Status update (every hour)
        tasks.append(asyncio.create_task(self._status_loop()))

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            logger.info("Bot shutting down...")

    def _setup_monitors(self):
        """Setup news monitors"""
        # Supreme Court (highest edge)
        court_monitor = SupremeCourtMonitor(check_interval=30)
        court_monitor.on_event(self._handle_event)
        self.monitors.append(court_monitor)
        logger.info("Added: Supreme Court monitor (30s)")

        # Political news
        political_monitor = PoliticalMonitor(check_interval=60)
        political_monitor.on_event(self._handle_event)
        self.monitors.append(political_monitor)
        logger.info("Added: Political monitor (60s)")

        # Regulatory (SEC, FDA)
        regulatory_monitor = RegulatoryMonitor(check_interval=60)
        regulatory_monitor.on_event(self._handle_event)
        self.monitors.append(regulatory_monitor)
        logger.info("Added: Regulatory monitor (60s)")

        # Sports (ESPN, etc.)
        sports_monitor = SportsMonitor(check_interval=45)
        sports_monitor.on_event(self._handle_event)
        self.monitors.append(sports_monitor)
        logger.info("Added: Sports monitor (45s)")

        # Twitter (if configured)
        twitter_monitor = TwitterMonitor(check_interval=30)
        if twitter_monitor._enabled:
            twitter_monitor.on_event(self._handle_event)
            self.monitors.append(twitter_monitor)
            logger.info("Added: Twitter monitor (30s)")

    async def _handle_event(self, event: NewsEvent):
        """
        Handle a detected news event

        This is the main trading pipeline:
        1. Parse news
        2. Match to markets
        3. Evaluate opportunities
        4. Execute trades
        """
        try:
            self._events_processed += 1
            logger.info(f"EVENT: {event}")

            # Store event for API access
            event_type_str = event.event_type.value if hasattr(event.event_type, 'value') else str(event.event_type)
            event_data = {
                "id": self._events_processed,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event_type": event_type_str,
                "headline": event.headline,
                "source_name": event.source_name,
                "source_url": event.source_url,
                "keywords": event.keywords[:5] if event.keywords else [],
                "outcome": None,
                "confidence": None,
                "matched_market": None,
                "edge": None,
            }
            # Store in appropriate category deque
            if event_type_str.startswith('sports'):
                self._recent_sports_events.appendleft(event_data)
            else:
                self._recent_politics_events.appendleft(event_data)

            # Check if trading is allowed
            if not self.risk_manager.can_trade():
                logger.warning("Trading disabled by risk manager")
                return

            # Parse the news
            parsed = self.parser.parse(event)
            event.outcome = parsed.outcome
            event.confidence = parsed.confidence

            # Update stored event with parsed data
            event_data["outcome"] = parsed.outcome
            event_data["confidence"] = parsed.confidence

            logger.debug(f"Parsed: {parsed.outcome} ({parsed.confidence:.0%})")

            # Find matching markets
            matches = self.matcher.find_matches(
                event,
                min_edge=config.trading.min_edge_to_trade,
                fair_value=parsed.fair_value,
            )

            if not matches:
                logger.debug("No matching markets with sufficient edge")
                return

            logger.info(f"Found {len(matches)} matching markets")

            # Process best match (highest edge)
            best_match = matches[0]

            # Update stored event with match data
            event_data["matched_market"] = best_match.market_question if hasattr(best_match, 'market_question') else str(best_match)
            event_data["edge"] = best_match.edge if hasattr(best_match, 'edge') else None

            # Store the match
            match_data = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event_headline": event.headline,
                "market_question": best_match.market_question if hasattr(best_match, 'market_question') else str(best_match),
                "market_id": best_match.market_id if hasattr(best_match, 'market_id') else None,
                "edge": best_match.edge if hasattr(best_match, 'edge') else None,
                "fair_value": best_match.fair_value if hasattr(best_match, 'fair_value') else None,
                "recommended_side": best_match.recommended_side if hasattr(best_match, 'recommended_side') else None,
            }
            self._recent_matches.appendleft(match_data)

            # Send opportunity alert
            await self.notifier.send_opportunity_alert(event, best_match)

            # Execute trade
            if config.trading.auto_trade_enabled:
                decision = await self.executor.process_opportunity(event, best_match)

                if decision and decision.executed:
                    self._trades_executed += 1

        except Exception as e:
            logger.error(f"Error handling event: {e}", exc_info=True)

    async def _on_trade(self, trade):
        """Called when a trade is executed"""
        await self.notifier.send_trade_alert(trade)

    async def _market_refresh_loop(self):
        """Periodically refresh market data"""
        while self._running:
            await asyncio.sleep(300)  # Every 5 minutes
            try:
                await self.matcher.refresh_markets()
                logger.debug("Markets refreshed")
            except Exception as e:
                logger.error(f"Market refresh failed: {e}")

    async def _position_monitor_loop(self):
        """Periodically check positions against SL/TP/trailing stop levels"""
        while self._running:
            await asyncio.sleep(30)  # Check every 30 seconds
            try:
                # Check and close positions that hit limits
                closed_positions = await self.trader.close_positions_at_limit()

                for closed in closed_positions:
                    reason = closed.get("close_reason", "UNKNOWN")
                    pnl = closed.get("pnl", 0)
                    pnl_pct = closed.get("pnl_pct", 0)
                    position = closed.get("position", {})
                    market = position.get("market", "")[:40]

                    logger.info(f"POSITION CLOSED ({reason}): {market} | P&L: ${pnl:.2f} ({pnl_pct:.1f}%)")

                    # Send alert for closed position
                    await self.notifier.send_position_closed_alert(closed)

            except Exception as e:
                logger.error(f"Position monitor error: {e}")

    async def _status_loop(self):
        """Periodically log status"""
        while self._running:
            await asyncio.sleep(3600)  # Every hour
            try:
                logger.info("")
                logger.info("=== HOURLY STATUS ===")
                logger.info(f"Events processed: {self._events_processed}")
                logger.info(f"Trades executed: {self._trades_executed}")
                logger.info(f"Balance: ${self.trader.get_balance():,.2f}")
                logger.info(f"Daily P&L: ${self.trader.get_daily_pnl():+,.2f}")
                logger.info("")
            except Exception as e:
                logger.error(f"Status update failed: {e}")

    def stop(self):
        """Stop the bot"""
        self._running = False
        for monitor in self.monitors:
            monitor.stop()
        logger.info("Bot stopped")

    def get_recent_events(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Get recent news events detected by the bot - merges sports and politics"""
        # Combine both categories
        all_events = list(self._recent_sports_events) + list(self._recent_politics_events)
        # Sort by timestamp descending (most recent first)
        all_events.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
        return all_events[:limit]

    def get_recent_matches(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get recent market matches"""
        return list(self._recent_matches)[:limit]

    def get_monitors_status(self) -> List[Dict[str, Any]]:
        """Get status of all active monitors"""
        status = []
        for monitor in self.monitors:
            monitor_info = {
                "name": monitor.__class__.__name__.replace("Monitor", ""),
                "check_interval": monitor.check_interval if hasattr(monitor, 'check_interval') else None,
                "enabled": monitor._enabled if hasattr(monitor, '_enabled') else True,
                "last_check": monitor._last_check.isoformat() if hasattr(monitor, '_last_check') and monitor._last_check else None,
            }
            status.append(monitor_info)
        return status

    def get_bot_status(self) -> Dict[str, Any]:
        """Get overall bot status"""
        return {
            "running": self._running,
            "paper_trading": config.trading.paper_trading,
            "events_processed": self._events_processed,
            "trades_executed": self._trades_executed,
            "monitors_active": len(self.monitors),
            "recent_events_count": len(self._recent_sports_events) + len(self._recent_politics_events),
            "recent_sports_count": len(self._recent_sports_events),
            "recent_politics_count": len(self._recent_politics_events),
            "recent_matches_count": len(self._recent_matches),
        }


# Global bot instance
_bot: SpeedTradingBot = None


def get_bot() -> SpeedTradingBot:
    """Get global bot instance"""
    global _bot
    if _bot is None:
        _bot = SpeedTradingBot()
    return _bot


async def main():
    """Main entry point"""
    bot = get_bot()

    # Handle shutdown signals
    def shutdown(sig, frame):
        logger.info(f"Received signal {sig}")
        bot.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    await bot.start()


if __name__ == "__main__":
    asyncio.run(main())
