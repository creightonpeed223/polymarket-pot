"""
Risk Manager
Monitors and enforces risk limits
"""

import asyncio
from datetime import datetime, timezone, timedelta
from typing import List, Optional
from dataclasses import dataclass, field

from ..trading.polymarket_client import PolymarketTrader
from ..config import config
from ..utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class RiskStatus:
    """Current risk status"""
    is_trading_allowed: bool = True
    reason: str = ""
    daily_pnl: float = 0.0
    daily_pnl_pct: float = 0.0
    open_positions: int = 0
    total_exposure: float = 0.0
    exposure_pct: float = 0.0
    balance: float = 0.0
    warnings: List[str] = field(default_factory=list)


class RiskManager:
    """
    Monitors portfolio risk and enforces limits

    Features:
    - Daily loss limit enforcement
    - Position limit enforcement
    - Exposure monitoring
    - Automatic cooldown on losses
    """

    def __init__(self, trader: PolymarketTrader):
        self.trader = trader
        self.config = config.trading
        self._trading_paused = False
        self._pause_reason = ""
        self._pause_until: Optional[datetime] = None
        self._daily_reset_time: Optional[datetime] = None

    def check_status(self) -> RiskStatus:
        """
        Check current risk status

        Returns:
            RiskStatus with trading allowed flag
        """
        status = RiskStatus()

        # Get current state
        balance = self.trader.get_balance()
        daily_pnl = self.trader.get_daily_pnl()
        positions = self.trader.get_positions()

        status.balance = balance
        status.daily_pnl = daily_pnl
        status.daily_pnl_pct = (daily_pnl / self.config.starting_capital) * 100
        status.open_positions = len(positions)

        # Calculate total exposure
        total_exposure = sum(p.get("value", 0) for p in positions)
        status.total_exposure = total_exposure
        status.exposure_pct = (total_exposure / balance * 100) if balance > 0 else 0

        # Check if paused
        if self._trading_paused:
            if self._pause_until and datetime.now(timezone.utc) > self._pause_until:
                self._trading_paused = False
                self._pause_reason = ""
                logger.info("Trading pause expired - resuming")
            else:
                status.is_trading_allowed = False
                status.reason = self._pause_reason
                return status

        # Check daily loss limit (percentage of equity)
        max_daily_loss = status.balance * self.config.max_daily_loss_pct
        if daily_pnl <= -max_daily_loss:
            status.is_trading_allowed = False
            status.reason = f"Daily loss limit hit: ${daily_pnl:.2f} (max: -${max_daily_loss:.0f})"
            status.warnings.append(status.reason)
            self._pause_trading(status.reason, hours=4)
            return status

        # Check position limits
        if status.open_positions >= self.config.max_concurrent_positions:
            status.is_trading_allowed = False
            status.reason = f"Max positions ({self.config.max_concurrent_positions}) reached"
            return status

        # Check exposure limits
        max_exposure_pct = 80  # Don't have more than 80% of capital in positions
        if status.exposure_pct > max_exposure_pct:
            status.is_trading_allowed = False
            status.reason = f"Exposure too high: {status.exposure_pct:.1f}%"
            status.warnings.append(status.reason)
            return status

        # Warnings (trading still allowed)
        if daily_pnl <= -max_daily_loss * 0.5:
            status.warnings.append(f"Approaching daily loss limit: ${daily_pnl:.2f}")

        if status.exposure_pct > 60:
            status.warnings.append(f"High exposure: {status.exposure_pct:.1f}%")

        status.is_trading_allowed = True
        return status

    def _pause_trading(self, reason: str, hours: float = 1):
        """Pause trading for specified hours"""
        self._trading_paused = True
        self._pause_reason = reason
        self._pause_until = datetime.now(timezone.utc) + timedelta(hours=hours)
        logger.warning(f"Trading PAUSED: {reason} (until {self._pause_until})")

    def can_trade(self) -> bool:
        """Quick check if trading is allowed"""
        status = self.check_status()
        return status.is_trading_allowed

    def validate_trade(
        self,
        size_usd: float,
        current_price: float,
    ) -> tuple[bool, str]:
        """
        Validate a specific trade

        Returns:
            Tuple of (allowed, reason)
        """
        status = self.check_status()

        if not status.is_trading_allowed:
            return False, status.reason

        # Check position size against max_position_pct of equity
        max_position = status.balance * self.config.max_position_pct
        if size_usd > max_position:
            return False, f"Size ${size_usd:.0f} exceeds max ${max_position:.0f} ({self.config.max_position_pct:.0%} of equity)"

        # Check if this would breach exposure limit
        new_exposure = status.total_exposure + size_usd
        new_exposure_pct = (new_exposure / status.balance) * 100
        if new_exposure_pct > 80:
            return False, f"Would exceed exposure limit: {new_exposure_pct:.1f}%"

        return True, "OK"

    def suggest_position_size(self, edge: float, confidence: float) -> float:
        """
        Suggest position size based on equity and risk parameters

        Formula: Position Size = Risk Amount / Stop Loss %
        Risk Amount = Equity × risk_per_trade_pct
        """
        status = self.check_status()
        equity = status.balance

        # Calculate risk amount and base position size
        risk_amount = equity * self.config.risk_per_trade_pct
        base_size = risk_amount / self.config.stop_loss_pct

        # Adjust for edge and confidence
        edge_multiplier = min(1.2, 0.8 + edge)
        suggested = base_size * edge_multiplier * confidence

        # Cap at max_position_pct of equity
        max_position = equity * self.config.max_position_pct
        suggested = min(suggested, max_position)

        return max(0, suggested)

    def get_report(self) -> str:
        """Get formatted risk report"""
        status = self.check_status()

        max_daily_loss = status.balance * self.config.max_daily_loss_pct
        max_position = status.balance * self.config.max_position_pct
        risk_per_trade = status.balance * self.config.risk_per_trade_pct

        report = f"""
=== RISK STATUS ===
Trading Allowed: {'YES' if status.is_trading_allowed else 'NO'}
{f'Reason: {status.reason}' if status.reason else ''}

Equity: ${status.balance:,.2f}
Daily P&L: ${status.daily_pnl:+,.2f} ({status.daily_pnl_pct:+.1f}%)

Open Positions: {status.open_positions}/{self.config.max_concurrent_positions}
Total Exposure: ${status.total_exposure:,.2f} ({status.exposure_pct:.1f}%)

Limits (% of equity):
  Risk Per Trade: ${risk_per_trade:,.0f} ({self.config.risk_per_trade_pct:.0%})
  Max Position: ${max_position:,.0f} ({self.config.max_position_pct:.0%})
  Daily Loss Limit: ${max_daily_loss:,.0f} ({self.config.max_daily_loss_pct:.0%})
  Max Concurrent: {self.config.max_concurrent_positions}
"""

        if status.warnings:
            report += "\nWarnings:\n"
            for w in status.warnings:
                report += f"  - {w}\n"

        return report.strip()

    async def monitor_loop(self, interval: int = 60):
        """Background monitoring loop"""
        logger.info("Risk monitoring started")

        while True:
            try:
                status = self.check_status()

                if status.warnings:
                    for w in status.warnings:
                        logger.warning(f"RISK WARNING: {w}")

                if not status.is_trading_allowed:
                    logger.warning(f"Trading disabled: {status.reason}")

            except Exception as e:
                logger.error(f"Risk monitoring error: {e}")

            await asyncio.sleep(interval)
