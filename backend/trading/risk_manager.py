"""Risk management for trading operations."""

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

from ..config import get_settings
from ..utils import get_trading_logger

logger = get_trading_logger()


@dataclass
class RiskLimits:
    """Current risk limit status."""

    max_position_size: float
    daily_loss_limit: float
    current_daily_loss: float
    trades_today: int
    max_daily_trades: int
    last_trade_time: Optional[datetime] = None
    cooldown_seconds: int = 300
    stop_loss_percent: float = 10.0

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "max_position_size": self.max_position_size,
            "daily_loss_limit": self.daily_loss_limit,
            "current_daily_loss": self.current_daily_loss,
            "remaining_daily_budget": self.daily_loss_limit - self.current_daily_loss,
            "trades_today": self.trades_today,
            "max_daily_trades": self.max_daily_trades,
            "trades_remaining": self.max_daily_trades - self.trades_today,
            "last_trade_time": self.last_trade_time.isoformat() if self.last_trade_time else None,
            "cooldown_seconds": self.cooldown_seconds,
            "stop_loss_percent": self.stop_loss_percent,
        }


@dataclass
class TradeValidation:
    """Result of trade validation check."""

    allowed: bool
    reason: str
    max_size: float
    warnings: list = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "max_size": self.max_size,
            "warnings": self.warnings,
        }


class RiskManager:
    """
    Manages trading risk limits and validates trades.
    """

    def __init__(self):
        """Initialize the risk manager."""
        self.settings = get_settings()
        self._daily_loss: float = 0.0
        self._trades_today: int = 0
        self._last_trade_time: Optional[datetime] = None
        self._last_reset_date: Optional[datetime] = None
        self._trade_history: list[dict] = []

        self._check_daily_reset()

    def _check_daily_reset(self):
        """Reset daily counters if it's a new day."""
        now = datetime.now(timezone.utc)
        today = now.date()

        if self._last_reset_date is None or self._last_reset_date != today:
            self._daily_loss = 0.0
            self._trades_today = 0
            self._last_reset_date = today
            logger.info("Daily risk counters reset")

    def validate_trade(
        self,
        size: float,
        side: str,
        confidence: float,
    ) -> TradeValidation:
        """
        Validate whether a trade should be allowed.

        Args:
            size: Proposed trade size in USD
            side: "BUY" or "SELL"
            confidence: Signal confidence (0-100)

        Returns:
            TradeValidation result
        """
        self._check_daily_reset()
        warnings = []

        # Check auto-trade enabled
        if not self.settings.auto_trade_enabled:
            return TradeValidation(
                allowed=False,
                reason="Auto-trading is disabled",
                max_size=0,
                warnings=["Enable auto-trading in settings to proceed"],
            )

        # Check confidence threshold
        if confidence < self.settings.min_confidence_threshold:
            return TradeValidation(
                allowed=False,
                reason=f"Confidence {confidence:.1f}% below threshold {self.settings.min_confidence_threshold}%",
                max_size=0,
            )

        # Check daily trade limit
        if self._trades_today >= self.settings.max_daily_trades:
            return TradeValidation(
                allowed=False,
                reason=f"Daily trade limit reached ({self.settings.max_daily_trades})",
                max_size=0,
            )

        # Check daily loss limit
        remaining_budget = self.settings.daily_loss_limit - self._daily_loss
        if remaining_budget <= 0:
            return TradeValidation(
                allowed=False,
                reason=f"Daily loss limit reached (${self.settings.daily_loss_limit:.2f})",
                max_size=0,
            )

        # Check cooldown
        if self._last_trade_time:
            elapsed = (datetime.now(timezone.utc) - self._last_trade_time).total_seconds()
            if elapsed < self.settings.cooldown_seconds:
                remaining = int(self.settings.cooldown_seconds - elapsed)
                return TradeValidation(
                    allowed=False,
                    reason=f"Cooldown active: {remaining}s remaining",
                    max_size=0,
                )

        # Calculate maximum allowed size
        max_size = min(
            self.settings.max_position_size,
            remaining_budget,
            size,
        )

        if max_size < 1:
            return TradeValidation(
                allowed=False,
                reason="Calculated max size below minimum ($1)",
                max_size=0,
            )

        # Add warnings for edge cases
        if size > max_size:
            warnings.append(f"Size reduced from ${size:.2f} to ${max_size:.2f}")

        if remaining_budget < self.settings.daily_loss_limit * 0.2:
            warnings.append("Warning: Less than 20% of daily budget remaining")

        if confidence < 75:
            warnings.append("Warning: Moderate confidence level")

        return TradeValidation(
            allowed=True,
            reason="Trade validated",
            max_size=max_size,
            warnings=warnings,
        )

    def record_trade(
        self,
        size: float,
        side: str,
        entry_price: float,
        market_id: str,
    ):
        """
        Record a trade execution for tracking.

        Args:
            size: Trade size in USD
            side: "BUY" or "SELL"
            entry_price: Entry price
            market_id: Market identifier
        """
        self._check_daily_reset()

        trade = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "size": size,
            "side": side,
            "entry_price": entry_price,
            "market_id": market_id,
            "pnl": 0.0,
            "closed": False,
        }

        self._trade_history.append(trade)
        self._trades_today += 1
        self._last_trade_time = datetime.now(timezone.utc)

        logger.info(f"Trade recorded: {side} ${size:.2f} on {market_id}")

    def record_pnl(self, pnl: float):
        """
        Record profit/loss from a closed trade.

        Args:
            pnl: Profit (positive) or loss (negative)
        """
        self._check_daily_reset()

        if pnl < 0:
            self._daily_loss += abs(pnl)

        logger.info(f"P&L recorded: ${pnl:+.2f} (Daily loss: ${self._daily_loss:.2f})")

    def check_stop_loss(
        self,
        entry_price: float,
        current_price: float,
        side: str,
    ) -> bool:
        """
        Check if stop loss has been triggered.

        Args:
            entry_price: Original entry price
            current_price: Current market price
            side: "BUY" or "SELL"

        Returns:
            True if stop loss triggered
        """
        if side == "BUY":
            # Long position: stop if price drops
            loss_percent = ((entry_price - current_price) / entry_price) * 100
        else:
            # Short position: stop if price rises
            loss_percent = ((current_price - entry_price) / entry_price) * 100

        if loss_percent >= self.settings.stop_loss_percent:
            logger.warning(f"Stop loss triggered: {loss_percent:.1f}% loss")
            return True

        return False

    def get_limits(self) -> RiskLimits:
        """Get current risk limit status."""
        self._check_daily_reset()

        return RiskLimits(
            max_position_size=self.settings.max_position_size,
            daily_loss_limit=self.settings.daily_loss_limit,
            current_daily_loss=self._daily_loss,
            trades_today=self._trades_today,
            max_daily_trades=self.settings.max_daily_trades,
            last_trade_time=self._last_trade_time,
            cooldown_seconds=self.settings.cooldown_seconds,
            stop_loss_percent=self.settings.stop_loss_percent,
        )

    def get_trade_history(self, limit: int = 50) -> list[dict]:
        """Get recent trade history."""
        return self._trade_history[-limit:]


# Singleton instance
_risk_manager: Optional[RiskManager] = None


def get_risk_manager() -> RiskManager:
    """Get the global risk manager instance."""
    global _risk_manager
    if _risk_manager is None:
        _risk_manager = RiskManager()
    return _risk_manager
