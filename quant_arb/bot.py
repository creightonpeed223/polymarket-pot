"""
Quantitative Arbitrage Bot
Main runner that orchestrates all components
"""

import asyncio
import logging
import os
import signal
from datetime import datetime, timedelta
from typing import Dict, Optional

from dotenv import load_dotenv

from .config import Config, CONFIG
from .platforms import PolymarketConnector, KalshiConnector, BetfairConnector, BasePlatform
from .news import NewsScanner
from .engine import ArbitrageDetector
from .execution import SmartExecutor
from .utils import KillSwitch, RiskManager
from .notifier import TelegramNotifier

logger = logging.getLogger(__name__)


class QuantArbitrageBot:
    """
    High-Frequency Quantitative Arbitrage Bot

    Monitors multiple prediction markets and news sources
    for arbitrage opportunities and executes trades automatically.
    """

    def __init__(self, config: Config = None, alert_only: bool = False):
        self.config = config or CONFIG
        self.alert_only = alert_only

        # Components
        self.platforms: Dict[str, BasePlatform] = {}
        self.news_scanner: Optional[NewsScanner] = None
        self.detector: Optional[ArbitrageDetector] = None
        self.executor: Optional[SmartExecutor] = None
        self.kill_switch: Optional[KillSwitch] = None
        self.risk_manager: Optional[RiskManager] = None
        self.notifier: Optional[TelegramNotifier] = None

        # State
        self._running = False
        self._tasks: list = []

        # Stats
        self.start_time: Optional[datetime] = None
        self.scans = 0
        self.opportunities_found = 0
        self.trades_executed = 0
        self.alerts_sent = 0

    async def initialize(self) -> bool:
        """Initialize all components"""
        load_dotenv()

        logger.info("=" * 60)
        logger.info("QUANT ARBITRAGE BOT INITIALIZING")
        logger.info("=" * 60)

        # Initialize kill switch
        self.kill_switch = KillSwitch(
            max_latency_ms=self.config.kill_switch.max_latency_ms,
            min_fill_rate=self.config.kill_switch.min_fill_rate_pct,
            max_slippage=self.config.kill_switch.max_fill_slippage_pct,
            max_consecutive_losses=self.config.kill_switch.max_consecutive_losses,
            max_daily_loss_pct=self.config.risk.max_daily_loss_pct,
            max_total_loss_pct=self.config.risk.max_total_daily_loss_pct,
            cooldown_seconds=self.config.kill_switch.cooldown_seconds,
        )

        # Initialize risk manager
        self.risk_manager = RiskManager(
            account_balance=self.config.account_balance,
            min_trade_pct=self.config.risk.min_trade_pct,
            max_trade_pct=self.config.risk.max_trade_pct,
            max_per_market_pct=self.config.risk.max_position_per_market,
            max_concurrent_positions=self.config.risk.max_concurrent_positions,
        )

        self.kill_switch.set_starting_balance(self.config.account_balance)

        # Initialize platforms
        await self._init_platforms()

        # Initialize detector
        self.detector = ArbitrageDetector(
            min_profit_pct=self.config.risk.min_profit_pct,
            max_profit_pct=self.config.risk.max_profit_pct,
        )

        # Initialize executor
        self.executor = SmartExecutor(
            platforms=self.platforms,
            kill_switch=self.kill_switch,
            risk_manager=self.risk_manager,
        )

        # Initialize news scanner
        self.news_scanner = NewsScanner(
            keywords=self.config.keywords,
            scan_interval_ms=self.config.news_scan_interval_ms,
        )

        # Set up news callback
        self.news_scanner.on_news(self._on_news)

        # Set up kill switch callback
        self.kill_switch.on_trigger(self._on_kill_switch)

        # Initialize notifier for alert-only mode
        if self.alert_only:
            self.notifier = TelegramNotifier(self.config.alerts)
            logger.info("Alert-only mode: trades will NOT be executed")

        logger.info("Initialization complete")
        return True

    async def _init_platforms(self):
        """Initialize platform connectors"""
        # Polymarket
        if self.config.polymarket.enabled:
            poly = PolymarketConnector(
                private_key=os.environ.get("POLYMARKET_PRIVATE_KEY"),
                funder_address=os.environ.get("POLYMARKET_FUNDER_ADDRESS"),
            )
            if await poly.connect():
                self.platforms["polymarket"] = poly
                logger.info("Polymarket connected")

        # Kalshi
        if self.config.kalshi.enabled:
            kalshi = KalshiConnector(
                api_key=os.environ.get("KALSHI_API_KEY"),
                private_key_path=os.environ.get("KALSHI_PRIVATE_KEY"),  # Path to PEM file or PEM string
            )
            if await kalshi.connect():
                self.platforms["kalshi"] = kalshi
                logger.info("Kalshi connected")

        # Betfair
        if self.config.betfair.enabled:
            betfair = BetfairConnector(
                app_key=os.environ.get("BETFAIR_APP_KEY"),
                username=os.environ.get("BETFAIR_USERNAME"),
                password=os.environ.get("BETFAIR_PASSWORD"),
                cert_path=os.environ.get("BETFAIR_CERT_PATH"),
            )
            if await betfair.connect():
                self.platforms["betfair"] = betfair
                logger.info("Betfair connected")

        logger.info(f"Connected to {len(self.platforms)} platforms")

    async def run(self):
        """Run the bot"""
        if not self.platforms:
            logger.error("No platforms connected")
            return

        self._running = True
        self.start_time = datetime.utcnow()

        mode = "ALERT ONLY" if self.alert_only else "LIVE TRADING"
        logger.info("=" * 60)
        logger.info(f"QUANT ARBITRAGE BOT RUNNING ({mode})")
        logger.info(f"Platforms: {list(self.platforms.keys())}")
        logger.info(f"Scan interval: {self.config.scan_interval_ms}ms")
        logger.info(f"Min profit: {self.config.risk.min_profit_pct:.2%}")
        logger.info(f"Max trade size: {self.config.risk.max_trade_pct:.2%}")
        logger.info("=" * 60)

        # Send startup alert in alert-only mode
        if self.alert_only and self.notifier:
            await self.notifier.send_startup_alert(list(self.platforms.keys()))

        # Start tasks
        self._tasks = [
            asyncio.create_task(self._market_loader_loop()),
            asyncio.create_task(self._scan_loop()),
            asyncio.create_task(self._stats_loop()),
        ]

        # Start news scanner in background
        news_task = asyncio.create_task(self.news_scanner.start())

        try:
            await asyncio.gather(*self._tasks)
        except asyncio.CancelledError:
            pass
        finally:
            await self.news_scanner.stop()
            news_task.cancel()

    async def _market_loader_loop(self):
        """Periodically reload markets"""
        while self._running:
            try:
                for name, platform in self.platforms.items():
                    markets = await platform.load_markets(self.config.keywords)
                    self.detector.update_markets(name, markets)

                    # Subscribe to orderbooks for top markets
                    for market in markets[:50]:
                        await platform.subscribe_orderbook(market.id)
                        platform.on_orderbook_update(
                            lambda ob, n=name: self.detector.update_orderbook(n, ob)
                        )

                # Reload every 5 minutes
                await asyncio.sleep(300)

            except Exception as e:
                logger.error(f"Market loader error: {e}")
                await asyncio.sleep(60)

    async def _scan_loop(self):
        """Main scanning and execution loop"""
        while self._running:
            try:
                self.scans += 1

                # Check kill switch
                if self.kill_switch.is_active:
                    await asyncio.sleep(1)
                    continue

                # Detect opportunities
                opportunities = self.detector.detect_opportunities()
                self.opportunities_found += len(opportunities)

                # Handle opportunities based on mode
                for opp in opportunities[:3]:  # Process top 3
                    if self.kill_switch.is_active:
                        break

                    if self.alert_only:
                        # Alert-only mode: send Telegram alert instead of executing
                        if self.notifier:
                            await self.notifier.send_opportunity_alert(opp)
                            self.alerts_sent += 1
                            logger.info(f"ALERT SENT: {opp}")
                    else:
                        # Live trading mode: execute the trade
                        result = await self.executor.execute(opp)

                        if result.success:
                            self.trades_executed += 1
                            logger.info(f"EXECUTED: {opp} | Profit: ${result.realized_profit:.4f}")
                        elif self.config.log_opportunities:
                            logger.debug(f"FAILED: {opp} | {result.error}")

                # Wait for next scan
                await asyncio.sleep(self.config.scan_interval_ms / 1000)

            except Exception as e:
                logger.error(f"Scan error: {e}")
                self.kill_switch.record_error(str(e))
                await asyncio.sleep(1)

    async def _stats_loop(self):
        """Periodically log stats"""
        while self._running:
            try:
                await asyncio.sleep(60)  # Every minute

                runtime = datetime.utcnow() - self.start_time
                hours = runtime.total_seconds() / 3600

                executor_stats = self.executor.get_stats()
                detector_stats = self.detector.get_stats()
                kill_stats = self.kill_switch.get_stats()
                news_stats = self.news_scanner.get_stats()

                logger.info("-" * 60)
                logger.info(f"STATS | Runtime: {runtime}")
                logger.info(f"Scans: {self.scans} | Opportunities: {self.opportunities_found}")

                if self.alert_only:
                    logger.info(f"Alerts Sent: {self.alerts_sent}")
                else:
                    logger.info(f"Executions: {executor_stats['executions']} | Success: {executor_stats['success_rate']:.0%}")
                    logger.info(f"Total Profit: ${executor_stats['total_profit']:.2f} | Volume: ${executor_stats['total_volume']:.2f}")

                logger.info(f"News items: {news_stats['items_matched']} matched | Latency: {kill_stats['avg_latency_ms']:.0f}ms")

                if self.kill_switch.is_active:
                    logger.warning(f"KILL SWITCH: {kill_stats['reason']} - {kill_stats['details']}")

                logger.info("-" * 60)

            except Exception as e:
                logger.error(f"Stats error: {e}")

    def _on_news(self, news):
        """Handle new news item"""
        self.detector.add_news(news)
        if news.keywords_matched:
            logger.info(f"NEWS: {news.title[:60]} | Keywords: {news.keywords_matched}")

    def _on_kill_switch(self, state):
        """Handle kill switch trigger"""
        logger.warning(f"KILL SWITCH TRIGGERED: {state.reason} - {state.details}")

    async def stop(self):
        """Stop the bot"""
        logger.info("Stopping bot...")
        self._running = False

        for task in self._tasks:
            task.cancel()

        for platform in self.platforms.values():
            await platform.disconnect()

        # Final stats
        logger.info("=" * 60)
        logger.info("FINAL STATS")

        if self.alert_only:
            logger.info(f"Total scans: {self.scans}")
            logger.info(f"Opportunities found: {self.opportunities_found}")
            logger.info(f"Alerts sent: {self.alerts_sent}")
        elif self.executor:
            stats = self.executor.get_stats()
            logger.info(f"Total executions: {stats['executions']}")
            logger.info(f"Success rate: {stats['success_rate']:.1%}")
            logger.info(f"Total profit: ${stats['total_profit']:.2f}")
            logger.info(f"Total volume: ${stats['total_volume']:.2f}")

        logger.info("=" * 60)


async def main():
    """Entry point"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%H:%M:%S",
    )

    # Reduce noise from libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)

    bot = QuantArbitrageBot()

    # Handle shutdown
    def shutdown(sig, frame):
        logger.info("Shutdown signal received")
        asyncio.create_task(bot.stop())

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    if await bot.initialize():
        await bot.run()


if __name__ == "__main__":
    asyncio.run(main())
