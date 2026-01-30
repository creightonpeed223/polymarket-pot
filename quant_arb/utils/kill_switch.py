"""
Kill Switch System
Automatic trading halt on anomalies
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Callable, Tuple
from enum import Enum

logger = logging.getLogger(__name__)


class KillReason(Enum):
    """Reasons for triggering kill switch"""
    LATENCY = "latency"
    FILL_RATE = "fill_rate"
    SLIPPAGE = "slippage"
    CONSECUTIVE_LOSSES = "consecutive_losses"
    DAILY_LOSS_LIMIT = "daily_loss_limit"
    TOTAL_LOSS_LIMIT = "total_loss_limit"
    MANUAL = "manual"
    ERROR_RATE = "error_rate"


@dataclass
class KillSwitchState:
    """Current state of kill switch"""
    triggered: bool = False
    reason: Optional[KillReason] = None
    triggered_at: Optional[datetime] = None
    cooldown_until: Optional[datetime] = None
    details: str = ""


@dataclass
class TradeResult:
    """Result of a trade for risk tracking"""
    platform: str
    market_id: str
    timestamp: datetime
    latency_ms: int
    filled: bool
    intended_price: float
    actual_price: float
    intended_size: float
    actual_size: float
    pnl: float


class KillSwitch:
    """
    Kill switch system for automated trading halt

    Monitors:
    - Latency: Halt if execution latency exceeds threshold
    - Fill rate: Halt if fill rate drops below threshold
    - Slippage: Halt if slippage exceeds threshold
    - Consecutive losses: Halt after N consecutive losses
    - Daily loss: Halt if daily loss exceeds limit
    - Error rate: Halt if too many errors
    """

    def __init__(
        self,
        max_latency_ms: int = 500,
        min_fill_rate: float = 0.7,
        max_slippage: float = 0.01,
        max_consecutive_losses: int = 5,
        max_daily_loss_pct: float = 0.02,
        max_total_loss_pct: float = 0.05,
        cooldown_seconds: int = 60,
        error_threshold: int = 10,
    ):
        self.max_latency_ms = max_latency_ms
        self.min_fill_rate = min_fill_rate
        self.max_slippage = max_slippage
        self.max_consecutive_losses = max_consecutive_losses
        self.max_daily_loss_pct = max_daily_loss_pct
        self.max_total_loss_pct = max_total_loss_pct
        self.cooldown_seconds = cooldown_seconds
        self.error_threshold = error_threshold

        # State
        self._state = KillSwitchState()
        self._trades: List[TradeResult] = []
        self._errors: List[datetime] = []
        self._daily_pnl: Dict[str, float] = {}  # date -> pnl
        self._total_pnl: float = 0.0
        self._starting_balance: float = 0.0
        self._consecutive_losses: int = 0

        # Callbacks
        self._on_trigger: List[Callable[[KillSwitchState], None]] = []

        # Rolling windows
        self._latency_window: List[int] = []
        self._fill_window: List[bool] = []

    def set_starting_balance(self, balance: float):
        """Set starting balance for loss calculations"""
        self._starting_balance = balance

    def on_trigger(self, callback: Callable[[KillSwitchState], None]):
        """Register callback for when kill switch triggers"""
        self._on_trigger.append(callback)

    @property
    def is_active(self) -> bool:
        """Check if kill switch is currently active"""
        if not self._state.triggered:
            return False

        # Check if cooldown has passed
        if self._state.cooldown_until and datetime.utcnow() > self._state.cooldown_until:
            self._state.triggered = False
            self._state.reason = None
            self._state.cooldown_until = None
            logger.info("Kill switch cooldown expired, resuming trading")
            return False

        return True

    @property
    def state(self) -> KillSwitchState:
        """Get current state"""
        return self._state

    def record_trade(self, result: TradeResult):
        """Record a trade result for monitoring"""
        self._trades.append(result)

        # Keep last 100 trades
        if len(self._trades) > 100:
            self._trades = self._trades[-100:]

        # Update metrics
        self._latency_window.append(result.latency_ms)
        if len(self._latency_window) > 20:
            self._latency_window = self._latency_window[-20:]

        self._fill_window.append(result.filled)
        if len(self._fill_window) > 20:
            self._fill_window = self._fill_window[-20:]

        # Update P&L
        self._total_pnl += result.pnl
        today = datetime.utcnow().strftime("%Y-%m-%d")
        self._daily_pnl[today] = self._daily_pnl.get(today, 0) + result.pnl

        # Track consecutive losses
        if result.pnl < 0:
            self._consecutive_losses += 1
        else:
            self._consecutive_losses = 0

        # Check triggers
        self._check_triggers(result)

    def record_error(self, error: str):
        """Record an error for rate monitoring"""
        self._errors.append(datetime.utcnow())

        # Keep only errors from last minute
        cutoff = datetime.utcnow() - timedelta(minutes=1)
        self._errors = [e for e in self._errors if e > cutoff]

        # Check error rate
        if len(self._errors) >= self.error_threshold:
            self._trigger(KillReason.ERROR_RATE, f"Too many errors: {len(self._errors)} in last minute")

    def manual_trigger(self, reason: str = "Manual trigger"):
        """Manually trigger kill switch"""
        self._trigger(KillReason.MANUAL, reason)

    def reset(self):
        """Reset kill switch state"""
        self._state = KillSwitchState()
        self._consecutive_losses = 0
        logger.info("Kill switch reset")

    def _check_triggers(self, result: TradeResult):
        """Check all trigger conditions"""
        if self._state.triggered:
            return  # Already triggered

        # 1. Latency check
        if result.latency_ms > self.max_latency_ms:
            self._trigger(KillReason.LATENCY, f"Latency {result.latency_ms}ms > {self.max_latency_ms}ms")
            return

        # 2. Fill rate check (rolling window)
        if len(self._fill_window) >= 10:
            fill_rate = sum(self._fill_window) / len(self._fill_window)
            if fill_rate < self.min_fill_rate:
                self._trigger(KillReason.FILL_RATE, f"Fill rate {fill_rate:.0%} < {self.min_fill_rate:.0%}")
                return

        # 3. Slippage check
        if result.filled and result.intended_price > 0:
            slippage = abs(result.actual_price - result.intended_price) / result.intended_price
            if slippage > self.max_slippage:
                self._trigger(KillReason.SLIPPAGE, f"Slippage {slippage:.2%} > {self.max_slippage:.2%}")
                return

        # 4. Consecutive losses
        if self._consecutive_losses >= self.max_consecutive_losses:
            self._trigger(KillReason.CONSECUTIVE_LOSSES, f"{self._consecutive_losses} consecutive losses")
            return

        # 5. Daily loss limit
        if self._starting_balance > 0:
            today = datetime.utcnow().strftime("%Y-%m-%d")
            daily_pnl = self._daily_pnl.get(today, 0)
            daily_loss_pct = -daily_pnl / self._starting_balance

            if daily_loss_pct >= self.max_daily_loss_pct:
                self._trigger(KillReason.DAILY_LOSS_LIMIT, f"Daily loss {daily_loss_pct:.2%} >= {self.max_daily_loss_pct:.2%}")
                return

            # 6. Total loss limit
            total_loss_pct = -self._total_pnl / self._starting_balance
            if total_loss_pct >= self.max_total_loss_pct:
                self._trigger(KillReason.TOTAL_LOSS_LIMIT, f"Total loss {total_loss_pct:.2%} >= {self.max_total_loss_pct:.2%}")
                return

    def _trigger(self, reason: KillReason, details: str):
        """Trigger the kill switch"""
        self._state = KillSwitchState(
            triggered=True,
            reason=reason,
            triggered_at=datetime.utcnow(),
            cooldown_until=datetime.utcnow() + timedelta(seconds=self.cooldown_seconds),
            details=details,
        )

        logger.warning(f"KILL SWITCH TRIGGERED: {reason.value} - {details}")

        # Notify callbacks
        for callback in self._on_trigger:
            try:
                callback(self._state)
            except Exception as e:
                logger.error(f"Kill switch callback error: {e}")

    def get_stats(self) -> Dict:
        """Get current statistics"""
        today = datetime.utcnow().strftime("%Y-%m-%d")

        avg_latency = sum(self._latency_window) / max(1, len(self._latency_window))
        fill_rate = sum(self._fill_window) / max(1, len(self._fill_window)) if self._fill_window else 1.0

        return {
            "triggered": self._state.triggered,
            "reason": self._state.reason.value if self._state.reason else None,
            "details": self._state.details,
            "cooldown_remaining": (
                (self._state.cooldown_until - datetime.utcnow()).seconds
                if self._state.cooldown_until and self._state.triggered
                else 0
            ),
            "avg_latency_ms": avg_latency,
            "fill_rate": fill_rate,
            "consecutive_losses": self._consecutive_losses,
            "daily_pnl": self._daily_pnl.get(today, 0),
            "total_pnl": self._total_pnl,
            "trades_recorded": len(self._trades),
            "errors_last_minute": len(self._errors),
        }


class RiskManager:
    """
    Risk manager combining position sizing and kill switch

    Manages:
    - Position sizing based on account balance
    - Per-market risk limits
    - Overall portfolio risk
    """

    def __init__(
        self,
        account_balance: float,
        min_trade_pct: float = 0.0005,
        max_trade_pct: float = 0.0025,
        max_per_market_pct: float = 0.01,
        max_concurrent_positions: int = 20,
    ):
        self.account_balance = account_balance
        self.min_trade_pct = min_trade_pct
        self.max_trade_pct = max_trade_pct
        self.max_per_market_pct = max_per_market_pct
        self.max_concurrent_positions = max_concurrent_positions

        self._positions: Dict[str, float] = {}  # market_id -> position_value
        self._market_pnl: Dict[str, float] = {}  # market_id -> daily pnl

    def update_balance(self, balance: float):
        """Update account balance"""
        self.account_balance = balance

    def can_trade(self, market_id: str, size: float) -> Tuple[bool, str]:
        """Check if trade is allowed"""
        # Check max concurrent positions
        if len(self._positions) >= self.max_concurrent_positions:
            if market_id not in self._positions:
                return False, f"Max {self.max_concurrent_positions} concurrent positions"

        # Check position size limits
        min_size = self.account_balance * self.min_trade_pct
        max_size = self.account_balance * self.max_trade_pct

        if size < min_size:
            return False, f"Size ${size:.2f} below minimum ${min_size:.2f}"

        if size > max_size:
            return False, f"Size ${size:.2f} above maximum ${max_size:.2f}"

        # Check per-market limit
        current_position = self._positions.get(market_id, 0)
        max_market_exposure = self.account_balance * self.max_per_market_pct

        if current_position + size > max_market_exposure:
            return False, f"Would exceed market limit ${max_market_exposure:.2f}"

        return True, ""

    def calculate_position_size(
        self,
        profit_pct: float,
        confidence: float = 1.0,
    ) -> float:
        """Calculate optimal position size"""
        # Base size on profit potential
        if profit_pct >= 0.005:  # 0.5%+ profit
            size_pct = self.max_trade_pct
        elif profit_pct >= 0.002:  # 0.2%+ profit
            size_pct = (self.min_trade_pct + self.max_trade_pct) / 2
        else:
            size_pct = self.min_trade_pct

        # Adjust for confidence
        size_pct *= confidence

        return self.account_balance * size_pct

    def record_position(self, market_id: str, size: float):
        """Record a new position"""
        self._positions[market_id] = self._positions.get(market_id, 0) + size

    def close_position(self, market_id: str, pnl: float):
        """Close a position and record P&L"""
        if market_id in self._positions:
            del self._positions[market_id]
        self._market_pnl[market_id] = self._market_pnl.get(market_id, 0) + pnl

    def get_stats(self) -> Dict:
        """Get risk statistics"""
        total_exposure = sum(self._positions.values())

        return {
            "account_balance": self.account_balance,
            "total_exposure": total_exposure,
            "exposure_pct": total_exposure / max(1, self.account_balance),
            "open_positions": len(self._positions),
            "min_trade_size": self.account_balance * self.min_trade_pct,
            "max_trade_size": self.account_balance * self.max_trade_pct,
        }
