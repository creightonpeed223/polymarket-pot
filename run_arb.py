#!/usr/bin/env python3
"""
Run the Quantitative Arbitrage Bot

Usage:
    python run_arb.py                    # Run with defaults
    python run_arb.py --scan-only        # Only scan, no trading
    python run_arb.py --balance 10000    # Set starting balance

Environment Variables Required:
    POLYMARKET_PRIVATE_KEY      - Polymarket wallet private key
    POLYMARKET_FUNDER_ADDRESS   - Polymarket funder address

Optional:
    KALSHI_API_KEY              - Kalshi API key
    KALSHI_PRIVATE_KEY          - Kalshi private key
    BETFAIR_APP_KEY             - Betfair app key
    BETFAIR_USERNAME            - Betfair username
    BETFAIR_PASSWORD            - Betfair password
"""

import argparse
import asyncio
import logging
import os
import sys

# Add parent to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv


async def run_scan_only():
    """Run in scan-only mode (no trading)"""
    from quant_arb.config import CONFIG
    from quant_arb.platforms import PolymarketConnector, KalshiConnector
    from quant_arb.news import NewsScanner
    from quant_arb.engine import ArbitrageDetector

    load_dotenv()

    print("=" * 60)
    print("QUANT ARBITRAGE SCANNER (SCAN ONLY MODE)")
    print("=" * 60)

    # Initialize platforms (read-only)
    platforms = {}

    poly = PolymarketConnector()
    if await poly.connect():
        platforms["polymarket"] = poly
        print("Polymarket connected")

    kalshi = KalshiConnector(
        api_key=os.environ.get("KALSHI_API_KEY"),
        private_key_path=os.environ.get("KALSHI_PRIVATE_KEY"),
    )
    if await kalshi.connect():
        platforms["kalshi"] = kalshi
        print("Kalshi connected")

    if not platforms:
        print("No platforms connected")
        return

    # Initialize detector
    detector = ArbitrageDetector(
        min_profit_pct=0.002,
        max_profit_pct=0.05,
    )

    # Initialize news scanner
    news_scanner = NewsScanner(
        keywords=CONFIG.keywords,
        scan_interval_ms=1000,
    )

    def on_news(news):
        if news.keywords_matched:
            print(f"NEWS: {news.title[:60]} | {news.keywords_matched}")

    news_scanner.on_news(on_news)

    # Load markets (no keyword filter - get all active markets)
    for name, platform in platforms.items():
        markets = await platform.load_markets()  # Load all markets
        detector.update_markets(name, markets)
        print(f"Loaded {len(markets)} markets from {name}")

        for market in markets[:20]:
            await platform.subscribe_orderbook(market.id)
            platform.on_orderbook_update(
                lambda ob, n=name: detector.update_orderbook(n, ob)
            )

    # Start news scanner in background
    news_task = asyncio.create_task(news_scanner.start())

    scan_count = 0
    total_opps = 0

    print("\nScanning... (Press Ctrl+C to stop)\n")

    try:
        while True:
            scan_count += 1
            opportunities = detector.detect_opportunities()

            if opportunities:
                total_opps += len(opportunities)
                print(f"\nScan #{scan_count} - Found {len(opportunities)} opportunities:")
                for opp in opportunities[:5]:
                    print(f"  {opp}")

            await asyncio.sleep(0.5)

    except KeyboardInterrupt:
        print(f"\n\nStopped. Scans: {scan_count}, Opportunities: {total_opps}")

    await news_scanner.stop()
    news_task.cancel()

    for platform in platforms.values():
        await platform.disconnect()


async def run_bot(balance: float):
    """Run the full trading bot"""
    from quant_arb.config import Config
    from quant_arb.bot import QuantArbitrageBot

    config = Config()
    config.account_balance = balance

    bot = QuantArbitrageBot(config)

    if await bot.initialize():
        await bot.run()


async def run_alert_only(balance: float):
    """Run in alert-only mode - send Telegram alerts but don't trade"""
    load_dotenv()  # Load .env before reading config

    from quant_arb.config import Config
    from quant_arb.bot import QuantArbitrageBot

    config = Config.from_env()
    config.account_balance = balance

    bot = QuantArbitrageBot(config, alert_only=True)

    if await bot.initialize():
        await bot.run()


def main():
    parser = argparse.ArgumentParser(
        description="Quantitative Arbitrage Bot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument(
        "--scan-only",
        action="store_true",
        help="Only scan for opportunities, don't trade",
    )
    parser.add_argument(
        "--alert-only",
        action="store_true",
        help="Scan for opportunities and send Telegram alerts, but don't execute trades",
    )
    parser.add_argument(
        "--balance",
        type=float,
        default=10000.0,
        help="Starting account balance (default: 10000)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )

    args = parser.parse_args()

    # Setup logging
    level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%H:%M:%S",
    )

    # Reduce noise
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)

    # Determine mode
    if args.alert_only:
        mode = "ALERT ONLY"
    elif args.scan_only:
        mode = "SCAN ONLY"
    else:
        mode = "LIVE TRADING"

    print()
    print("╔════════════════════════════════════════════════════════════╗")
    print("║       QUANTITATIVE ARBITRAGE BOT                           ║")
    print("║       Cross-Platform News Arbitrage                        ║")
    print("╠════════════════════════════════════════════════════════════╣")
    print(f"║  Mode: {mode:50} ║")
    print(f"║  Balance: ${args.balance:,.0f}                                       ║")
    print("╚════════════════════════════════════════════════════════════╝")
    print()

    if args.alert_only:
        asyncio.run(run_alert_only(args.balance))
    elif args.scan_only:
        asyncio.run(run_scan_only())
    else:
        asyncio.run(run_bot(args.balance))


if __name__ == "__main__":
    main()
