"""
Trade Executor
Handles automatic trade execution with risk checks
"""

import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict
from dataclasses import dataclass, field

from .polymarket_client import PolymarketTrader, get_trader
from ..nlp.matcher import MarketMatch
from ..monitors.base import NewsEvent
from ..config import config
from ..utils.logger import get_logger
from ..data import database as db

logger = get_logger(__name__)

# Cooldown period before trading the same market again (hours)
MARKET_COOLDOWN_HOURS = 4


def _load_recent_trades_from_db() -> Dict[str, datetime]:
    """Load recently traded markets from database to restore cooldown state"""
    traded_markets = {}
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=MARKET_COOLDOWN_HOURS)
        closed_trades = db.get_closed_trades(limit=100)
        for trade in closed_trades:
            token_id = trade.get("token_id")
            exit_time_str = trade.get("exit_time") or trade.get("entry_time")
            if token_id and exit_time_str:
                try:
                    # Parse the ISO format timestamp
                    trade_time = datetime.fromisoformat(exit_time_str.replace("Z", "+00:00"))
                    if trade_time.tzinfo is None:
                        trade_time = trade_time.replace(tzinfo=timezone.utc)
                    # Only track if within cooldown period
                    if trade_time > cutoff:
                        # Keep the most recent trade time for each market
                        if token_id not in traded_markets or trade_time > traded_markets[token_id]:
                            traded_markets[token_id] = trade_time
                except (ValueError, TypeError):
                    pass
    except Exception as e:
        logger.warning(f"Could not load recent trades from DB: {e}")
    return traded_markets


@dataclass
class TradeDecision:
    """A trade decision to be executed"""
    event: NewsEvent
    match: MarketMatch
    size_usd: float
    size_shares: float
    risk_amount: float = 0.0  # Amount at risk (equity × risk_per_trade_pct)
    approved: bool = False
    reason: str = ""
    executed: bool = False
    result: Optional[dict] = None


@dataclass
class ExecutorStats:
    """Execution statistics"""
    trades_executed: int = 0
    trades_won: int = 0
    trades_lost: int = 0
    total_profit: float = 0.0
    total_volume: float = 0.0
    best_trade: float = 0.0
    worst_trade: float = 0.0


class TradeExecutor:
    """
    Executes trades automatically based on matched opportunities

    Features:
    - Automatic position sizing
    - Risk limit enforcement
    - Trade logging
    - P&L tracking
    """

    def __init__(self, trader: Optional[PolymarketTrader] = None):
        self.trader = trader or get_trader()
        self.config = config.trading
        self._pending_trades: List[TradeDecision] = []
        self._executed_trades: List[TradeDecision] = []
        self._stats = ExecutorStats()
        self._callbacks = []
        # Track when each market was last traded (market_id -> datetime)
        # Load from database to restore cooldown state after restart
        self._traded_markets: Dict[str, datetime] = _load_recent_trades_from_db()
        if self._traded_markets:
            logger.info(f"Loaded {len(self._traded_markets)} markets with active cooldowns from database")

    def on_trade(self, callback):
        """Register callback for trade events"""
        self._callbacks.append(callback)

    async def _notify(self, trade: TradeDecision):
        """Notify callbacks of trade"""
        for callback in self._callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(trade)
                else:
                    callback(trade)
            except Exception as e:
                logger.error(f"Trade callback error: {e}")

    async def evaluate_opportunity(
        self,
        event: NewsEvent,
        match: MarketMatch,
    ) -> TradeDecision:
        """
        Evaluate a trading opportunity

        Returns:
            TradeDecision with approval status
        """
        decision = TradeDecision(
            event=event,
            match=match,
            size_usd=0,
            size_shares=0,
        )

        market_id = match.recommended_token

        # Check if we already have an open position on this market
        positions = self.trader.get_positions()
        for pos in positions:
            if pos.get("token_id") == market_id:
                decision.reason = f"Already have open position on this market"
                logger.debug(f"Skipping trade - already have position on {match.question[:40]}")
                return decision

        # Check if we recently traded this market (cooldown)
        now = datetime.now(timezone.utc)
        if market_id in self._traded_markets:
            last_trade_time = self._traded_markets[market_id]
            cooldown_end = last_trade_time + timedelta(hours=MARKET_COOLDOWN_HOURS)
            if now < cooldown_end:
                time_remaining = cooldown_end - now
                hours_remaining = time_remaining.total_seconds() / 3600
                decision.reason = f"Market on cooldown ({hours_remaining:.1f}h remaining)"
                logger.debug(f"Skipping trade - market on cooldown: {match.question[:40]}")
                return decision

        # Check minimum edge
        if match.edge < self.config.min_edge_to_trade:
            decision.reason = f"Edge {match.edge:.1%} below minimum {self.config.min_edge_to_trade:.1%}"
            return decision

        # Check confidence
        if match.confidence < 0.6:
            decision.reason = f"Confidence {match.confidence:.1%} too low"
            return decision

        # Check liquidity
        if match.liquidity < 1000:
            decision.reason = f"Liquidity ${match.liquidity} too low"
            return decision

        # Get current equity
        equity = self.trader.get_balance()

        # Check daily loss limit (percentage of equity)
        daily_pnl = self.trader.get_daily_pnl()
        max_daily_loss = equity * self.config.max_daily_loss_pct
        if daily_pnl <= -max_daily_loss:
            decision.reason = f"Daily loss limit hit: ${daily_pnl:.0f} (max: -${max_daily_loss:.0f})"
            return decision

        # Check concurrent positions
        positions = self.trader.get_positions()
        if len(positions) >= self.config.max_concurrent_positions:
            decision.reason = f"Max concurrent positions ({self.config.max_concurrent_positions}) reached"
            return decision

        # === EQUITY-BASED POSITION SIZING ===
        # Risk Amount = Equity × Risk %
        # Position Size = Risk Amount / Stop Loss %
        #
        # Example with $10,000 equity, 2% risk, 15% stop loss:
        #   Risk Amount = $10,000 × 2% = $200
        #   Position Size = $200 / 15% = $1,333
        #
        # As equity grows, positions scale proportionally

        risk_amount = equity * self.config.risk_per_trade_pct
        stop_loss_pct = self.config.stop_loss_pct

        # Calculate base position size from risk
        base_size = risk_amount / stop_loss_pct

        # Adjust for edge (higher edge = slightly larger)
        edge_multiplier = min(1.2, 0.8 + match.edge)  # Scale 0.8-1.2x based on edge
        size_usd = base_size * edge_multiplier * match.confidence

        # Cap at max_position_pct of equity
        max_position = equity * self.config.max_position_pct
        size_usd = min(size_usd, max_position)

        logger.info(f"Position sizing: Equity=${equity:.0f}, Risk={risk_amount:.0f} ({self.config.risk_per_trade_pct:.0%}), Size=${size_usd:.0f}")

        # Calculate shares
        if match.recommended_side == "YES":
            price = match.current_yes_price
        else:
            price = match.current_no_price

        if price <= 0:
            decision.reason = "Invalid price"
            return decision

        size_shares = size_usd / price

        decision.size_usd = size_usd
        decision.size_shares = size_shares
        decision.risk_amount = risk_amount
        decision.approved = True
        decision.reason = f"Edge: {match.edge:.1%}, Risk: ${risk_amount:.0f}, Size: ${size_usd:.0f}"

        logger.info(f"OPPORTUNITY APPROVED: {match.question[:50]} | {decision.reason}")

        return decision

    async def execute_trade(self, decision: TradeDecision) -> bool:
        """
        Execute an approved trade

        Returns:
            True if successful
        """
        if not decision.approved:
            logger.warning("Attempted to execute unapproved trade")
            return False

        if not self.config.auto_trade_enabled:
            logger.info("Auto-trade disabled - trade not executed")
            decision.reason = "Auto-trade disabled"
            return False

        match = decision.match

        try:
            # Place order
            result = await self.trader.place_order(
                token_id=match.recommended_token,
                side="BUY",
                size=decision.size_shares,
                price=match.current_yes_price if match.recommended_side == "YES" else match.current_no_price,
                market_question=match.question,
                risk_amount=decision.risk_amount,
            )

            if result:
                decision.executed = True
                decision.result = result

                # Update stats
                self._stats.trades_executed += 1
                self._stats.total_volume += decision.size_usd

                self._executed_trades.append(decision)

                # Record this market as traded (for cooldown deduplication)
                self._traded_markets[match.recommended_token] = datetime.now(timezone.utc)

                # Notify callbacks
                await self._notify(decision)

                logger.info(f"TRADE EXECUTED: {match.recommended_side} {match.question[:40]} @ ${decision.size_usd:.0f}")
                return True
            else:
                decision.reason = "Order placement failed"
                return False

        except Exception as e:
            logger.error(f"Trade execution failed: {e}")
            decision.reason = f"Error: {e}"
            return False

    async def process_opportunity(
        self,
        event: NewsEvent,
        match: MarketMatch,
    ) -> Optional[TradeDecision]:
        """
        Full pipeline: evaluate and execute if approved

        Returns:
            TradeDecision with result
        """
        # Evaluate
        decision = await self.evaluate_opportunity(event, match)

        if not decision.approved:
            logger.debug(f"Trade not approved: {decision.reason}")
            return decision

        # Execute
        success = await self.execute_trade(decision)

        if not success:
            logger.warning(f"Trade execution failed: {decision.reason}")

        return decision

    def get_stats(self) -> ExecutorStats:
        """Get execution statistics"""
        return self._stats

    def get_executed_trades(self) -> List[TradeDecision]:
        """Get list of executed trades"""
        return self._executed_trades

    def reset_daily_stats(self):
        """Reset daily statistics (call at midnight)"""
        self.trader.reset_daily_pnl()
