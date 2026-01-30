"""
Smart Order Executor
Executes arbitrage trades with optimal routing
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from ..platforms.base import BasePlatform, Order, Fill
from ..engine.detector import ArbitrageOpportunity
from ..utils.kill_switch import KillSwitch, RiskManager, TradeResult

logger = logging.getLogger(__name__)


@dataclass
class ExecutionResult:
    """Result of an execution attempt"""
    opportunity_id: str
    success: bool
    fills: List[Fill] = field(default_factory=list)
    error: Optional[str] = None
    latency_ms: int = 0
    realized_profit: float = 0.0


class SmartExecutor:
    """
    Smart order executor for cross-platform arbitrage

    Features:
    - Simultaneous order execution across platforms
    - FOK orders for guaranteed fills or cancellation
    - Latency monitoring and kill switch integration
    - Position sizing based on risk parameters
    """

    def __init__(
        self,
        platforms: Dict[str, BasePlatform],
        kill_switch: KillSwitch,
        risk_manager: RiskManager,
        max_concurrent_executions: int = 3,
    ):
        self.platforms = platforms
        self.kill_switch = kill_switch
        self.risk_manager = risk_manager
        self.max_concurrent = max_concurrent_executions

        self._execution_lock = asyncio.Semaphore(max_concurrent_executions)
        self._pending: Dict[str, ArbitrageOpportunity] = {}

        # Stats
        self.executions = 0
        self.successes = 0
        self.failures = 0
        self.total_profit = 0.0
        self.total_volume = 0.0

    async def execute(self, opportunity: ArbitrageOpportunity) -> ExecutionResult:
        """Execute an arbitrage opportunity"""
        # Check kill switch
        if self.kill_switch.is_active:
            return ExecutionResult(
                opportunity_id=opportunity.id,
                success=False,
                error=f"Kill switch active: {self.kill_switch.state.reason}",
            )

        # Check risk limits
        size = self.risk_manager.calculate_position_size(
            opportunity.profit_pct,
            opportunity.confidence,
        )

        size = min(size, opportunity.max_size)

        can_trade, reason = self.risk_manager.can_trade(opportunity.buy_market_id, size)
        if not can_trade:
            return ExecutionResult(
                opportunity_id=opportunity.id,
                success=False,
                error=f"Risk limit: {reason}",
            )

        # Execute based on opportunity type
        async with self._execution_lock:
            self._pending[opportunity.id] = opportunity
            self.executions += 1

            try:
                if opportunity.opportunity_type == "cross_platform":
                    result = await self._execute_cross_platform(opportunity, size)
                elif opportunity.opportunity_type == "same_market":
                    result = await self._execute_same_market(opportunity, size)
                else:
                    result = await self._execute_single_leg(opportunity, size)

                # Record result
                if result.success:
                    self.successes += 1
                    self.total_profit += result.realized_profit
                    self.total_volume += size
                    self.risk_manager.record_position(opportunity.buy_market_id, size)
                else:
                    self.failures += 1

                # Record trade for kill switch
                self._record_trade(opportunity, result, size)

                return result

            finally:
                del self._pending[opportunity.id]

    async def _execute_cross_platform(
        self,
        opp: ArbitrageOpportunity,
        size: float,
    ) -> ExecutionResult:
        """Execute cross-platform arbitrage (simultaneous buy and sell)"""
        start = time.time()

        buy_platform = self.platforms.get(opp.buy_platform)
        sell_platform = self.platforms.get(opp.sell_platform)

        if not buy_platform or not sell_platform:
            return ExecutionResult(
                opportunity_id=opp.id,
                success=False,
                error="Platform not available",
            )

        # Create orders
        buy_order = Order(
            market_id=opp.buy_market_id,
            platform=opp.buy_platform,
            side="BUY",
            outcome="YES",
            price=opp.buy_price,
            size=size,
            order_type="FOK",
        )

        sell_order = Order(
            market_id=opp.sell_market_id,
            platform=opp.sell_platform,
            side="SELL",
            outcome="YES",
            price=opp.sell_price,
            size=size,
            order_type="FOK",
        )

        # Execute simultaneously
        buy_result, sell_result = await asyncio.gather(
            buy_platform.place_order(buy_order),
            sell_platform.place_order(sell_order),
            return_exceptions=True,
        )

        latency_ms = int((time.time() - start) * 1000)

        # Check results
        buy_fill = buy_result if isinstance(buy_result, Fill) else None
        sell_fill = sell_result if isinstance(sell_result, Fill) else None

        fills = []
        if buy_fill:
            fills.append(buy_fill)
        if sell_fill:
            fills.append(sell_fill)

        # Both must succeed for profit
        if buy_fill and sell_fill:
            realized = (sell_fill.price - buy_fill.price) * min(buy_fill.size, sell_fill.size)
            realized -= buy_fill.fee + sell_fill.fee

            return ExecutionResult(
                opportunity_id=opp.id,
                success=True,
                fills=fills,
                latency_ms=latency_ms,
                realized_profit=realized,
            )
        else:
            # Partial fill - need to unwind
            error_parts = []
            if not buy_fill:
                error_parts.append(f"Buy failed on {opp.buy_platform}")
            if not sell_fill:
                error_parts.append(f"Sell failed on {opp.sell_platform}")

            return ExecutionResult(
                opportunity_id=opp.id,
                success=False,
                fills=fills,
                error=", ".join(error_parts),
                latency_ms=latency_ms,
            )

    async def _execute_same_market(
        self,
        opp: ArbitrageOpportunity,
        size: float,
    ) -> ExecutionResult:
        """Execute same-market arbitrage (buy YES and NO)"""
        start = time.time()

        platform = self.platforms.get(opp.buy_platform)
        if not platform:
            return ExecutionResult(
                opportunity_id=opp.id,
                success=False,
                error="Platform not available",
            )

        # Create orders for both YES and NO
        yes_order = Order(
            market_id=opp.buy_market_id,
            platform=opp.buy_platform,
            side="BUY",
            outcome="YES",
            price=opp.buy_price,
            size=size,
            order_type="FOK",
        )

        no_order = Order(
            market_id=opp.sell_market_id,
            platform=opp.sell_platform,
            side="BUY",
            outcome="NO",
            price=opp.sell_price,
            size=size,
            order_type="FOK",
        )

        # Execute simultaneously
        yes_result, no_result = await asyncio.gather(
            platform.place_order(yes_order),
            platform.place_order(no_order),
            return_exceptions=True,
        )

        latency_ms = int((time.time() - start) * 1000)

        yes_fill = yes_result if isinstance(yes_result, Fill) else None
        no_fill = no_result if isinstance(no_result, Fill) else None

        fills = []
        if yes_fill:
            fills.append(yes_fill)
        if no_fill:
            fills.append(no_fill)

        # Both must succeed
        if yes_fill and no_fill:
            # Profit = $1 payout - total cost
            total_cost = (yes_fill.price + no_fill.price) * min(yes_fill.size, no_fill.size)
            fees = yes_fill.fee + no_fill.fee
            realized = min(yes_fill.size, no_fill.size) - total_cost - fees

            return ExecutionResult(
                opportunity_id=opp.id,
                success=True,
                fills=fills,
                latency_ms=latency_ms,
                realized_profit=realized,
            )
        else:
            error_parts = []
            if not yes_fill:
                error_parts.append("YES order failed")
            if not no_fill:
                error_parts.append("NO order failed")

            return ExecutionResult(
                opportunity_id=opp.id,
                success=False,
                fills=fills,
                error=", ".join(error_parts),
                latency_ms=latency_ms,
            )

    async def _execute_single_leg(
        self,
        opp: ArbitrageOpportunity,
        size: float,
    ) -> ExecutionResult:
        """Execute single-leg trade (news-driven)"""
        start = time.time()

        platform = self.platforms.get(opp.buy_platform)
        if not platform:
            return ExecutionResult(
                opportunity_id=opp.id,
                success=False,
                error="Platform not available",
            )

        order = Order(
            market_id=opp.buy_market_id,
            platform=opp.buy_platform,
            side="BUY",
            outcome="YES",
            price=opp.buy_price,
            size=size,
            order_type="FOK",
        )

        result = await platform.place_order(order)
        latency_ms = int((time.time() - start) * 1000)

        if isinstance(result, Fill):
            # Expected profit (not yet realized)
            expected_profit = (opp.sell_price - result.price) * result.size - result.fee

            return ExecutionResult(
                opportunity_id=opp.id,
                success=True,
                fills=[result],
                latency_ms=latency_ms,
                realized_profit=expected_profit,
            )
        else:
            return ExecutionResult(
                opportunity_id=opp.id,
                success=False,
                error="Order failed",
                latency_ms=latency_ms,
            )

    def _record_trade(
        self,
        opp: ArbitrageOpportunity,
        result: ExecutionResult,
        intended_size: float,
    ):
        """Record trade for kill switch monitoring"""
        if not result.fills:
            # Record failed trade
            trade_result = TradeResult(
                platform=opp.buy_platform,
                market_id=opp.buy_market_id,
                timestamp=datetime.utcnow(),
                latency_ms=result.latency_ms,
                filled=False,
                intended_price=opp.buy_price,
                actual_price=0,
                intended_size=intended_size,
                actual_size=0,
                pnl=0,
            )
            self.kill_switch.record_trade(trade_result)
            return

        for fill in result.fills:
            trade_result = TradeResult(
                platform=fill.platform,
                market_id=fill.market_id,
                timestamp=fill.timestamp,
                latency_ms=fill.latency_ms,
                filled=True,
                intended_price=opp.buy_price if fill.side == "BUY" else opp.sell_price,
                actual_price=fill.price,
                intended_size=intended_size,
                actual_size=fill.size,
                pnl=result.realized_profit / len(result.fills),
            )
            self.kill_switch.record_trade(trade_result)

    def get_stats(self) -> Dict:
        """Get executor statistics"""
        return {
            "executions": self.executions,
            "successes": self.successes,
            "failures": self.failures,
            "success_rate": self.successes / max(1, self.executions),
            "total_profit": self.total_profit,
            "total_volume": self.total_volume,
            "pending": len(self._pending),
        }
