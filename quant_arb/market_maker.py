"""
Polymarket Market Maker
High-frequency market making bot that provides liquidity and profits from spreads
"""

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from enum import Enum

from dotenv import load_dotenv

logger = logging.getLogger(__name__)


class Side(Enum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass
class Quote:
    """A bid or ask quote"""
    market_id: str
    token_id: str
    side: Side
    price: float
    size: float
    order_id: Optional[str] = None
    placed_at: Optional[datetime] = None


@dataclass
class Position:
    """Current position in a market"""
    market_id: str
    token_id: str
    size: float = 0.0  # Positive = long, negative = short
    avg_entry: float = 0.0
    realized_pnl: float = 0.0

    def update(self, side: Side, price: float, size: float):
        """Update position after a fill"""
        if side == Side.BUY:
            # Buying increases position
            new_size = self.size + size
            if self.size >= 0:
                # Adding to long
                total_cost = (self.size * self.avg_entry) + (size * price)
                self.avg_entry = total_cost / new_size if new_size > 0 else 0
            else:
                # Closing short
                self.realized_pnl += (self.avg_entry - price) * min(size, abs(self.size))
            self.size = new_size
        else:
            # Selling decreases position
            new_size = self.size - size
            if self.size > 0:
                # Closing long
                self.realized_pnl += (price - self.avg_entry) * min(size, self.size)
            else:
                # Adding to short
                total_cost = (abs(self.size) * self.avg_entry) + (size * price)
                self.avg_entry = total_cost / abs(new_size) if new_size < 0 else 0
            self.size = new_size


@dataclass
class MarketState:
    """State for a single market"""
    market_id: str
    token_id: str
    question: str

    # Current orderbook
    best_bid: float = 0.0
    best_ask: float = 1.0
    bid_size: float = 0.0
    ask_size: float = 0.0

    # Our quotes
    our_bid: Optional[Quote] = None
    our_ask: Optional[Quote] = None

    # Position
    position: Position = None

    # Stats
    trades: int = 0
    volume: float = 0.0
    pnl: float = 0.0

    def __post_init__(self):
        if self.position is None:
            self.position = Position(self.market_id, self.token_id)

    @property
    def spread(self) -> float:
        return self.best_ask - self.best_bid if self.best_ask > self.best_bid else 0

    @property
    def mid_price(self) -> float:
        return (self.best_bid + self.best_ask) / 2


@dataclass
class MMConfig:
    """Market maker configuration"""
    # Mode
    paper_trading: bool = True  # Paper trading mode (no real orders)

    # Spread settings
    min_spread_pct: float = 0.02  # 2% minimum spread
    max_spread_pct: float = 0.10  # 10% maximum spread

    # Size settings
    quote_size: float = 10.0  # Size per quote in dollars
    max_position: float = 100.0  # Max position per market

    # Inventory management
    inventory_skew: float = 0.01  # Skew quotes 1% per $10 inventory

    # Quote management
    quote_refresh_ms: int = 5000  # Refresh quotes every 5 seconds
    min_edge: float = 0.005  # Minimum edge to quote (0.5%)

    # Risk
    max_daily_loss: float = 100.0  # Stop if down $100
    max_markets: int = 50  # Max simultaneous markets

    # Filters
    min_market_volume: float = 10000  # Only MM markets with $10k+ volume
    min_liquidity: float = 1000  # Minimum orderbook liquidity

    # Simulation
    fill_probability: float = 0.3  # 30% chance of fill in paper mode


class PolymarketMM:
    """
    Polymarket Market Maker

    Makes markets by placing bid/ask quotes and profiting from the spread.
    Manages inventory risk by skewing quotes based on position.
    """

    def __init__(self, config: MMConfig = None):
        self.config = config or MMConfig()

        # State
        self.markets: Dict[str, MarketState] = {}
        self.running = False
        self.start_time: Optional[datetime] = None

        # Stats
        self.total_trades = 0
        self.total_volume = 0.0
        self.total_pnl = 0.0
        self.daily_pnl = 0.0

        # Platform connector
        self.platform = None
        self._http = None
        self._api_creds = None

    async def initialize(self) -> bool:
        """Initialize the market maker"""
        load_dotenv()

        logger.info("=" * 60)
        logger.info("POLYMARKET MARKET MAKER")
        logger.info("=" * 60)

        # Get credentials
        private_key = os.environ.get("POLYMARKET_PRIVATE_KEY")
        funder_address = os.environ.get("POLYMARKET_FUNDER_ADDRESS")

        if not private_key:
            logger.error("POLYMARKET_PRIVATE_KEY not set")
            return False

        # Initialize platform
        from platforms import PolymarketConnector

        self.platform = PolymarketConnector(
            private_key=private_key,
            funder_address=funder_address,
        )

        if not await self.platform.connect():
            logger.error("Failed to connect to Polymarket")
            return False

        logger.info("Connected to Polymarket")

        # Load markets
        await self._load_markets()

        logger.info(f"Loaded {len(self.markets)} markets for market making")
        logger.info(f"Config: spread={self.config.min_spread_pct:.1%}-{self.config.max_spread_pct:.1%}, size=${self.config.quote_size}")

        return True

    async def _load_markets(self):
        """Load and filter markets for market making"""
        all_markets = await self.platform.load_markets()

        # Filter markets
        eligible = []
        for m in all_markets:
            # Check volume
            if m.volume < self.config.min_market_volume:
                continue

            # Check liquidity
            if m.liquidity < self.config.min_liquidity:
                continue

            # Get token ID (YES token)
            token_id = m.yes_token_id

            if not token_id:
                continue

            eligible.append((m, token_id))

        # Sort by volume and take top N
        eligible.sort(key=lambda x: -x[0].volume)

        for m, token_id in eligible[:self.config.max_markets]:
            self.markets[m.id] = MarketState(
                market_id=m.id,
                token_id=token_id,
                question=m.question,
            )

        logger.info(f"Selected {len(self.markets)} markets from {len(eligible)} eligible")

        # Fetch initial orderbooks
        logger.info("Fetching initial orderbooks...")
        for market_id, state in self.markets.items():
            ob = await self.platform.get_orderbook(market_id)
            if ob:
                if ob.bids:
                    state.best_bid = ob.bids[0][0]
                    state.bid_size = ob.bids[0][1]
                if ob.asks:
                    state.best_ask = ob.asks[0][0]
                    state.ask_size = ob.asks[0][1]
                logger.info(f"  {state.question[:35]}... bid={state.best_bid:.3f} ask={state.best_ask:.3f}")

    async def run(self):
        """Main market making loop"""
        self.running = True
        self.start_time = datetime.utcnow()

        mode = "PAPER TRADING" if self.config.paper_trading else "LIVE TRADING"
        logger.info("=" * 60)
        logger.info(f"MARKET MAKER RUNNING - {mode}")
        logger.info(f"Markets: {len(self.markets)} | Quote: ${self.config.quote_size} | Max pos: ${self.config.max_position}")
        logger.info(f"Spread: {self.config.min_spread_pct:.0%}-{self.config.max_spread_pct:.0%} | Refresh: {self.config.quote_refresh_ms}ms")
        logger.info("=" * 60)

        # Start tasks
        tasks = [
            asyncio.create_task(self._quote_loop()),
            asyncio.create_task(self._orderbook_loop()),
            asyncio.create_task(self._stats_loop()),
            asyncio.create_task(self._risk_loop()),
        ]

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            pass
        finally:
            await self._cancel_all_quotes()

    async def _quote_loop(self):
        """Main quoting loop - refresh quotes periodically"""
        while self.running:
            try:
                for market_id, state in self.markets.items():
                    if not self.running:
                        break

                    await self._update_quotes(state)

                    # Small delay between markets to avoid rate limits
                    await asyncio.sleep(0.05)

                # Wait before next refresh cycle
                await asyncio.sleep(self.config.quote_refresh_ms / 1000)

            except Exception as e:
                logger.error(f"Quote loop error: {e}")
                await asyncio.sleep(1)

    async def _orderbook_loop(self):
        """Fetch orderbooks and check for fills"""
        while self.running:
            try:
                for market_id, state in self.markets.items():
                    if not self.running:
                        break

                    # Get current orderbook
                    ob = await self.platform.get_orderbook(market_id)
                    if ob:
                        # Update state
                        if ob.bids:
                            state.best_bid = ob.bids[0][0]
                            state.bid_size = ob.bids[0][1]
                        if ob.asks:
                            state.best_ask = ob.asks[0][0]
                            state.ask_size = ob.asks[0][1]

                    await asyncio.sleep(0.05)

                await asyncio.sleep(1)  # Refresh orderbooks every second

            except Exception as e:
                logger.error(f"Orderbook loop error: {e}")
                await asyncio.sleep(1)

    async def _update_quotes(self, state: MarketState):
        """Update bid/ask quotes for a market"""
        try:
            # Calculate target prices
            bid_price, ask_price = self._calculate_quotes(state)

            if bid_price is None or ask_price is None:
                return

            # Check if we should update
            should_update_bid = (
                state.our_bid is None or
                abs(state.our_bid.price - bid_price) > 0.005 or
                (datetime.utcnow() - state.our_bid.placed_at).seconds > 30
            )

            should_update_ask = (
                state.our_ask is None or
                abs(state.our_ask.price - ask_price) > 0.005 or
                (datetime.utcnow() - state.our_ask.placed_at).seconds > 30
            )

            # Cancel and replace if needed
            if should_update_bid:
                if state.our_bid and state.our_bid.order_id:
                    await self._cancel_order(state.our_bid.order_id)

                # Only place bid if we're not max long
                if state.position.size < self.config.max_position:
                    state.our_bid = await self._place_quote(
                        state, Side.BUY, bid_price, self.config.quote_size
                    )

            if should_update_ask:
                if state.our_ask and state.our_ask.order_id:
                    await self._cancel_order(state.our_ask.order_id)

                # Only place ask if we're not max short
                if state.position.size > -self.config.max_position:
                    state.our_ask = await self._place_quote(
                        state, Side.SELL, ask_price, self.config.quote_size
                    )

        except Exception as e:
            logger.debug(f"Update quotes error for {state.market_id}: {e}")

    def _calculate_quotes(self, state: MarketState) -> Tuple[Optional[float], Optional[float]]:
        """Calculate bid and ask prices based on market state and inventory"""

        # Need valid orderbook
        if state.best_bid <= 0 or state.best_ask >= 1 or state.best_ask <= state.best_bid:
            return None, None

        mid = state.mid_price

        # Base spread
        half_spread = max(self.config.min_spread_pct / 2, state.spread / 2)
        half_spread = min(half_spread, self.config.max_spread_pct / 2)

        # Inventory skew - if long, lower bid and raise ask to reduce position
        inventory_adjustment = state.position.size * self.config.inventory_skew / 10

        bid_price = mid - half_spread - inventory_adjustment
        ask_price = mid + half_spread - inventory_adjustment

        # Ensure we're inside the spread (providing liquidity, not taking)
        bid_price = min(bid_price, state.best_bid + 0.001)
        ask_price = max(ask_price, state.best_ask - 0.001)

        # Ensure minimum spread
        if ask_price - bid_price < self.config.min_spread_pct:
            return None, None

        # Clamp to valid range
        bid_price = max(0.01, min(0.99, bid_price))
        ask_price = max(0.01, min(0.99, ask_price))

        return round(bid_price, 3), round(ask_price, 3)

    async def _place_quote(self, state: MarketState, side: Side, price: float, size: float) -> Optional[Quote]:
        """Place a quote order"""
        try:
            quote = Quote(
                market_id=state.market_id,
                token_id=state.token_id,
                side=side,
                price=price,
                size=size,
                order_id=f"sim_{state.market_id[:8]}_{side.value}_{int(time.time())}",
                placed_at=datetime.utcnow(),
            )

            if self.config.paper_trading:
                # Simulate potential fill
                import random
                if random.random() < self.config.fill_probability:
                    # Simulated fill
                    fill_price = price
                    fill_size = size

                    # Update position
                    state.position.update(side, fill_price, fill_size)

                    # Update stats
                    state.trades += 1
                    state.volume += fill_size * fill_price
                    self.total_trades += 1
                    self.total_volume += fill_size * fill_price

                    # Calculate P&L (spread capture)
                    if side == Side.BUY:
                        pnl = (state.mid_price - fill_price) * fill_size
                    else:
                        pnl = (fill_price - state.mid_price) * fill_size

                    state.pnl += pnl
                    self.total_pnl += pnl
                    self.daily_pnl += pnl

                    logger.info(f"FILL: {side.value} ${fill_size:.0f} @ {fill_price:.3f} | PnL: ${pnl:.2f} | {state.question[:35]}...")

            return quote

        except Exception as e:
            logger.debug(f"Place quote error: {e}")
            return None

    async def _cancel_order(self, order_id: str) -> bool:
        """Cancel an order"""
        try:
            # Simulated for now
            logger.debug(f"Cancel order: {order_id}")
            return True
        except Exception as e:
            logger.debug(f"Cancel order error: {e}")
            return False

    async def _cancel_all_quotes(self):
        """Cancel all outstanding quotes"""
        logger.info("Cancelling all quotes...")
        for state in self.markets.values():
            if state.our_bid and state.our_bid.order_id:
                await self._cancel_order(state.our_bid.order_id)
            if state.our_ask and state.our_ask.order_id:
                await self._cancel_order(state.our_ask.order_id)

    async def _stats_loop(self):
        """Log statistics periodically"""
        while self.running:
            try:
                await asyncio.sleep(30)  # Every 30 seconds

                runtime = datetime.utcnow() - self.start_time

                # Count active quotes
                active_bids = sum(1 for s in self.markets.values() if s.our_bid)
                active_asks = sum(1 for s in self.markets.values() if s.our_ask)

                # Calculate total position
                total_long = sum(s.position.size for s in self.markets.values() if s.position.size > 0)
                total_short = sum(abs(s.position.size) for s in self.markets.values() if s.position.size < 0)

                logger.info("-" * 60)
                logger.info(f"STATS | Runtime: {runtime}")
                logger.info(f"Markets: {len(self.markets)} | Quotes: {active_bids}B/{active_asks}A")
                logger.info(f"Trades: {self.total_trades} | Volume: ${self.total_volume:.2f}")
                logger.info(f"Positions: ${total_long:.2f} long / ${total_short:.2f} short")
                logger.info(f"PnL: ${self.total_pnl:.2f} total / ${self.daily_pnl:.2f} daily")
                logger.info("-" * 60)

            except Exception as e:
                logger.error(f"Stats error: {e}")

    async def _risk_loop(self):
        """Monitor risk and stop if limits breached"""
        while self.running:
            try:
                await asyncio.sleep(5)

                # Check daily loss limit
                if self.daily_pnl < -self.config.max_daily_loss:
                    logger.warning(f"DAILY LOSS LIMIT: ${self.daily_pnl:.2f}")
                    self.running = False
                    break

            except Exception as e:
                logger.error(f"Risk loop error: {e}")

    async def stop(self):
        """Stop the market maker"""
        logger.info("Stopping market maker...")
        self.running = False
        await self._cancel_all_quotes()

        if self.platform:
            await self.platform.disconnect()

        logger.info("=" * 60)
        logger.info("FINAL STATS")
        logger.info(f"Total trades: {self.total_trades}")
        logger.info(f"Total volume: ${self.total_volume:.2f}")
        logger.info(f"Total PnL: ${self.total_pnl:.2f}")
        logger.info("=" * 60)


async def main():
    """Entry point"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    # Reduce noise
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)

    config = MMConfig(
        # Mode - set to False for live trading (requires API credentials)
        paper_trading=True,

        # Spread settings
        min_spread_pct=0.01,  # 1% minimum spread
        max_spread_pct=0.05,  # 5% max spread

        # Size settings
        quote_size=10.0,  # $10 per quote
        max_position=100.0,  # $100 max position per market

        # Markets
        max_markets=20,  # Top 20 markets by volume
        min_market_volume=10000,  # $10k minimum volume
        min_liquidity=5000,  # $5k minimum liquidity

        # Timing
        quote_refresh_ms=2000,  # Refresh every 2 seconds

        # Paper trading simulation
        fill_probability=0.3,  # 30% fill rate in paper mode
    )

    mm = PolymarketMM(config)

    # Handle shutdown
    import signal
    def shutdown(sig, frame):
        logger.info("Shutdown signal received")
        asyncio.create_task(mm.stop())

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    if await mm.initialize():
        await mm.run()


if __name__ == "__main__":
    asyncio.run(main())
