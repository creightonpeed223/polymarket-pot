"""Portfolio and position tracking."""

import json
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from typing import Optional, List
from enum import Enum
from pathlib import Path

from ..config import get_settings
from ..utils import get_trading_logger

logger = get_trading_logger()

# File path for persisting trade history
TRADE_HISTORY_FILE = Path(__file__).parent.parent.parent / "data" / "trade_history.json"


class CloseReason(Enum):
    """Reason why a trade was closed."""
    MANUAL = "Manual Close"
    TAKE_PROFIT = "Take Profit"
    STOP_LOSS = "Stop Loss"
    TRAILING_STOP = "Trailing Stop"
    BREAK_EVEN = "Break Even"
    MARKET_CLOSE = "Market Closed"
    LIQUIDATION = "Liquidation"
    PARTIAL_CLOSE = "Partial Close"


@dataclass
class Position:
    """Represents an open position."""

    id: str
    market_id: str
    market_name: str
    token_id: str
    side: str  # "YES" or "NO"
    size: float  # Number of shares
    entry_price: float
    current_price: float = 0.0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    paper: bool = True
    # Risk tracking
    risk_amount: float = 0.0  # Amount risked (position size in USD)
    stop_loss_price: float = 0.0
    take_profit_price: float = 0.0
    trailing_stop_percent: float = 0.0
    highest_price: float = 0.0  # For trailing stop
    signal_confidence: float = 0.0

    @property
    def cost_basis(self) -> float:
        """Total cost of the position."""
        return self.size * self.entry_price

    @property
    def current_value(self) -> float:
        """Current market value of the position."""
        return self.size * self.current_price

    @property
    def pnl(self) -> float:
        """Unrealized profit/loss."""
        return self.current_value - self.cost_basis

    @property
    def pnl_percent(self) -> float:
        """P&L as percentage."""
        if self.cost_basis == 0:
            return 0.0
        return (self.pnl / self.cost_basis) * 100

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "market_id": self.market_id,
            "market_name": self.market_name,
            "token_id": self.token_id,
            "side": self.side,
            "size": self.size,
            "entry_price": self.entry_price,
            "current_price": self.current_price,
            "cost_basis": self.cost_basis,
            "current_value": self.current_value,
            "pnl": self.pnl,
            "pnl_percent": self.pnl_percent,
            "created_at": self.created_at.isoformat(),
            "paper": self.paper,
            "risk_amount": self.risk_amount,
            "stop_loss_price": self.stop_loss_price,
            "take_profit_price": self.take_profit_price,
            "trailing_stop_percent": self.trailing_stop_percent,
            "signal_confidence": self.signal_confidence,
        }


@dataclass
class ClosedTrade:
    """Represents a completed trade with full history."""

    id: str
    market_id: str
    market_name: str
    token_id: str
    side: str
    size: float
    entry_price: float
    exit_price: float
    risk_amount: float
    pnl: float
    pnl_percent: float
    close_reason: CloseReason
    opened_at: datetime
    closed_at: datetime
    duration_seconds: int
    paper: bool
    signal_confidence: float = 0.0
    stop_loss_price: float = 0.0
    take_profit_price: float = 0.0

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "market_id": self.market_id,
            "market_name": self.market_name,
            "token_id": self.token_id,
            "side": self.side,
            "size": self.size,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "risk_amount": self.risk_amount,
            "pnl": self.pnl,
            "pnl_percent": self.pnl_percent,
            "close_reason": self.close_reason.value,
            "opened_at": self.opened_at.isoformat(),
            "closed_at": self.closed_at.isoformat(),
            "duration_seconds": self.duration_seconds,
            "duration_formatted": self._format_duration(),
            "paper": self.paper,
            "signal_confidence": self.signal_confidence,
            "stop_loss_price": self.stop_loss_price,
            "take_profit_price": self.take_profit_price,
        }

    def _format_duration(self) -> str:
        """Format duration as human-readable string."""
        if self.duration_seconds < 60:
            return f"{self.duration_seconds}s"
        elif self.duration_seconds < 3600:
            return f"{self.duration_seconds // 60}m {self.duration_seconds % 60}s"
        else:
            hours = self.duration_seconds // 3600
            mins = (self.duration_seconds % 3600) // 60
            return f"{hours}h {mins}m"


@dataclass
class PortfolioSummary:
    """Summary of portfolio status."""

    total_value: float
    total_cost: float
    total_pnl: float  # Combined realized + unrealized
    total_pnl_percent: float
    unrealized_pnl: float
    realized_pnl: float
    daily_pnl: float
    daily_realized_pnl: float
    daily_unrealized_pnl: float
    position_count: int
    cash_balance: float
    paper_mode: bool
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "total_value": self.total_value,
            "total_cost": self.total_cost,
            "total_pnl": self.total_pnl,
            "total_pnl_percent": self.total_pnl_percent,
            "unrealized_pnl": self.unrealized_pnl,
            "realized_pnl": self.realized_pnl,
            "daily_pnl": self.daily_pnl,
            "daily_realized_pnl": self.daily_realized_pnl,
            "daily_unrealized_pnl": self.daily_unrealized_pnl,
            "position_count": self.position_count,
            "cash_balance": self.cash_balance,
            "paper_mode": self.paper_mode,
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "win_rate": self.win_rate,
        }


class PortfolioManager:
    """
    Manages positions and portfolio tracking for paper and live trading.
    """

    def __init__(self):
        """Initialize the portfolio manager."""
        self.settings = get_settings()
        self._positions: dict[str, Position] = {}
        self._closed_trades: List[ClosedTrade] = []
        self._cash_balance: float = self.settings.paper_balance
        self._initial_balance: float = self.settings.paper_balance

        # Daily tracking
        self._last_reset_date: Optional[datetime] = None
        self._daily_realized_pnl: float = 0.0
        self._daily_trades: List[ClosedTrade] = []

        # Load persisted trade history
        self._load_trade_history()

        # Check for daily reset
        self._check_daily_reset()

    def _check_daily_reset(self):
        """Reset daily counters if it's a new day (UTC)."""
        now = datetime.now(timezone.utc)
        today = now.date()

        if self._last_reset_date is None or self._last_reset_date != today:
            self._daily_realized_pnl = 0.0
            self._daily_trades = []
            self._last_reset_date = today
            logger.info("Daily portfolio counters reset")

    def _load_trade_history(self):
        """Load trade history from file."""
        try:
            if TRADE_HISTORY_FILE.exists():
                with open(TRADE_HISTORY_FILE, 'r') as f:
                    data = json.load(f)

                for trade_data in data.get('trades', []):
                    try:
                        trade = ClosedTrade(
                            id=trade_data['id'],
                            market_id=trade_data['market_id'],
                            market_name=trade_data['market_name'],
                            token_id=trade_data['token_id'],
                            side=trade_data['side'],
                            size=trade_data['size'],
                            entry_price=trade_data['entry_price'],
                            exit_price=trade_data['exit_price'],
                            risk_amount=trade_data.get('risk_amount', 0),
                            pnl=trade_data['pnl'],
                            pnl_percent=trade_data['pnl_percent'],
                            close_reason=CloseReason(trade_data['close_reason']),
                            opened_at=datetime.fromisoformat(trade_data['opened_at']),
                            closed_at=datetime.fromisoformat(trade_data['closed_at']),
                            duration_seconds=trade_data['duration_seconds'],
                            paper=trade_data.get('paper', True),
                            signal_confidence=trade_data.get('signal_confidence', 0),
                            stop_loss_price=trade_data.get('stop_loss_price', 0),
                            take_profit_price=trade_data.get('take_profit_price', 0),
                        )
                        self._closed_trades.append(trade)
                    except Exception as e:
                        logger.warning(f"Failed to load trade: {e}")

                logger.info(f"Loaded {len(self._closed_trades)} trades from history")
        except Exception as e:
            logger.warning(f"Failed to load trade history: {e}")

    def _save_trade_history(self):
        """Save trade history to file."""
        try:
            # Ensure directory exists
            TRADE_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)

            trades_data = []
            for trade in self._closed_trades:
                trades_data.append({
                    'id': trade.id,
                    'market_id': trade.market_id,
                    'market_name': trade.market_name,
                    'token_id': trade.token_id,
                    'side': trade.side,
                    'size': trade.size,
                    'entry_price': trade.entry_price,
                    'exit_price': trade.exit_price,
                    'risk_amount': trade.risk_amount,
                    'pnl': trade.pnl,
                    'pnl_percent': trade.pnl_percent,
                    'close_reason': trade.close_reason.value,
                    'opened_at': trade.opened_at.isoformat(),
                    'closed_at': trade.closed_at.isoformat(),
                    'duration_seconds': trade.duration_seconds,
                    'paper': trade.paper,
                    'signal_confidence': trade.signal_confidence,
                    'stop_loss_price': trade.stop_loss_price,
                    'take_profit_price': trade.take_profit_price,
                })

            with open(TRADE_HISTORY_FILE, 'w') as f:
                json.dump({'trades': trades_data}, f, indent=2)

            logger.debug(f"Saved {len(trades_data)} trades to history")
        except Exception as e:
            logger.error(f"Failed to save trade history: {e}")

    def add_position(
        self,
        market_id: str,
        market_name: str,
        token_id: str,
        side: str,
        size: float,
        entry_price: float,
        risk_amount: float = 0.0,
        stop_loss_price: float = 0.0,
        take_profit_price: float = 0.0,
        trailing_stop_percent: float = 0.0,
        signal_confidence: float = 0.0,
    ) -> Position:
        """
        Add a new position to the portfolio.

        Args:
            market_id: Market identifier
            market_name: Human-readable market name
            token_id: Token being traded
            side: "YES" or "NO"
            size: Number of shares
            entry_price: Entry price per share
            risk_amount: Amount risked on this trade
            stop_loss_price: Stop loss price level
            take_profit_price: Take profit price level
            trailing_stop_percent: Trailing stop percentage
            signal_confidence: Confidence of the signal that triggered this trade

        Returns:
            The created Position
        """
        self._check_daily_reset()
        position_id = f"{market_id}_{token_id}_{datetime.now(timezone.utc).timestamp()}"

        # Calculate risk amount if not provided
        cost = size * entry_price
        if risk_amount == 0:
            risk_amount = cost

        position = Position(
            id=position_id,
            market_id=market_id,
            market_name=market_name,
            token_id=token_id,
            side=side,
            size=size,
            entry_price=entry_price,
            current_price=entry_price,
            paper=self.settings.paper_trading,
            risk_amount=risk_amount,
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price,
            trailing_stop_percent=trailing_stop_percent,
            highest_price=entry_price,
            signal_confidence=signal_confidence,
        )

        # Deduct cost from cash balance
        self._cash_balance -= cost

        self._positions[position_id] = position
        logger.info(f"Position opened: {side} {size} shares @ {entry_price} on {market_name} (Risk: ${risk_amount:.2f})")

        return position

    def close_position(
        self,
        position_id: str,
        exit_price: float,
        close_reason: CloseReason = CloseReason.MANUAL,
    ) -> Optional[dict]:
        """
        Close an existing position.

        Args:
            position_id: Position to close
            exit_price: Exit price per share
            close_reason: Why the position was closed

        Returns:
            Dict with closing details or None
        """
        self._check_daily_reset()

        if position_id not in self._positions:
            logger.warning(f"Position not found: {position_id}")
            return None

        position = self._positions[position_id]
        position.current_price = exit_price

        # Calculate final P&L
        pnl = position.pnl
        pnl_percent = position.pnl_percent
        exit_value = position.current_value

        # Determine close reason based on price if not specified
        if close_reason == CloseReason.MANUAL:
            close_reason = self._determine_close_reason(position, exit_price)

        # Calculate duration
        closed_at = datetime.now(timezone.utc)
        duration_seconds = int((closed_at - position.created_at).total_seconds())

        # Create closed trade record
        closed_trade = ClosedTrade(
            id=position_id,
            market_id=position.market_id,
            market_name=position.market_name,
            token_id=position.token_id,
            side=position.side,
            size=position.size,
            entry_price=position.entry_price,
            exit_price=exit_price,
            risk_amount=position.risk_amount,
            pnl=pnl,
            pnl_percent=pnl_percent,
            close_reason=close_reason,
            opened_at=position.created_at,
            closed_at=closed_at,
            duration_seconds=duration_seconds,
            paper=position.paper,
            signal_confidence=position.signal_confidence,
            stop_loss_price=position.stop_loss_price,
            take_profit_price=position.take_profit_price,
        )

        # Add proceeds to cash balance
        self._cash_balance += exit_value

        # Track the trade
        del self._positions[position_id]
        self._closed_trades.append(closed_trade)
        self._daily_trades.append(closed_trade)
        self._daily_realized_pnl += pnl

        # Persist trade history to file
        self._save_trade_history()

        logger.info(f"Position closed: {position.market_name} | {close_reason.value} | P&L: ${pnl:+.2f} ({pnl_percent:+.1f}%)")

        return {
            "position_id": position_id,
            "market_name": position.market_name,
            "side": position.side,
            "size": position.size,
            "entry_price": position.entry_price,
            "exit_price": exit_price,
            "pnl": pnl,
            "pnl_percent": pnl_percent,
            "close_reason": close_reason.value,
            "risk_amount": position.risk_amount,
            "duration_seconds": duration_seconds,
        }

    def _determine_close_reason(self, position: Position, exit_price: float) -> CloseReason:
        """Determine why a position was closed based on price levels."""
        pnl_percent = ((exit_price - position.entry_price) / position.entry_price) * 100

        # Check if hit take profit
        if position.take_profit_price > 0:
            if position.side == "YES" and exit_price >= position.take_profit_price:
                return CloseReason.TAKE_PROFIT
            if position.side == "NO" and exit_price <= position.take_profit_price:
                return CloseReason.TAKE_PROFIT

        # Check if hit stop loss
        if position.stop_loss_price > 0:
            if position.side == "YES" and exit_price <= position.stop_loss_price:
                return CloseReason.STOP_LOSS
            if position.side == "NO" and exit_price >= position.stop_loss_price:
                return CloseReason.STOP_LOSS

        # Check trailing stop
        if position.trailing_stop_percent > 0 and position.highest_price > position.entry_price:
            trailing_stop_price = position.highest_price * (1 - position.trailing_stop_percent / 100)
            if exit_price <= trailing_stop_price:
                return CloseReason.TRAILING_STOP

        # Check break even (within 0.5% of entry)
        if abs(pnl_percent) < 0.5:
            return CloseReason.BREAK_EVEN

        return CloseReason.MANUAL

    def update_prices(self, prices: dict[str, float]):
        """
        Update current prices for all positions.

        Args:
            prices: Dict mapping token_id to current price
        """
        for position in self._positions.values():
            if position.token_id in prices:
                new_price = prices[position.token_id]
                position.current_price = new_price
                # Update highest price for trailing stop
                if new_price > position.highest_price:
                    position.highest_price = new_price

    def get_position(self, position_id: str) -> Optional[Position]:
        """Get a specific position."""
        return self._positions.get(position_id)

    def get_positions(self) -> List[Position]:
        """Get all open positions."""
        return list(self._positions.values())

    def get_positions_for_market(self, market_id: str) -> List[Position]:
        """Get positions for a specific market."""
        return [p for p in self._positions.values() if p.market_id == market_id]

    def get_summary(self) -> PortfolioSummary:
        """Get portfolio summary with complete P&L breakdown."""
        self._check_daily_reset()
        positions = list(self._positions.values())

        # Unrealized P&L from open positions
        total_value = sum(p.current_value for p in positions)
        total_cost = sum(p.cost_basis for p in positions)
        unrealized_pnl = total_value - total_cost

        # Realized P&L from all closed trades
        realized_pnl = sum(t.pnl for t in self._closed_trades)

        # Combined total P&L
        total_pnl = realized_pnl + unrealized_pnl

        # Calculate total P&L percent based on initial balance
        if self._initial_balance > 0:
            total_pnl_percent = (total_pnl / self._initial_balance) * 100
        else:
            total_pnl_percent = 0.0

        # Daily unrealized P&L (current open positions opened today)
        today = datetime.now(timezone.utc).date()
        daily_unrealized_pnl = sum(
            p.pnl for p in positions
            if p.created_at.date() == today
        )

        # Daily combined P&L
        daily_pnl = self._daily_realized_pnl + daily_unrealized_pnl

        # Trade statistics
        total_trades = len(self._closed_trades)
        winning_trades = sum(1 for t in self._closed_trades if t.pnl > 0)
        losing_trades = sum(1 for t in self._closed_trades if t.pnl < 0)
        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0

        return PortfolioSummary(
            total_value=total_value,
            total_cost=total_cost,
            total_pnl=total_pnl,
            total_pnl_percent=total_pnl_percent,
            unrealized_pnl=unrealized_pnl,
            realized_pnl=realized_pnl,
            daily_pnl=daily_pnl,
            daily_realized_pnl=self._daily_realized_pnl,
            daily_unrealized_pnl=daily_unrealized_pnl,
            position_count=len(positions),
            cash_balance=self._cash_balance,
            paper_mode=self.settings.paper_trading,
            total_trades=total_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            win_rate=win_rate,
        )

    def get_total_equity(self) -> float:
        """Get total equity (cash + positions)."""
        positions_value = sum(p.current_value for p in self._positions.values())
        return self._cash_balance + positions_value

    def get_realized_pnl(self) -> float:
        """Get total realized P&L from closed trades."""
        return sum(t.pnl for t in self._closed_trades)

    def get_daily_pnl(self) -> float:
        """Get today's total P&L (realized + unrealized)."""
        self._check_daily_reset()
        today = datetime.now(timezone.utc).date()
        daily_unrealized = sum(
            p.pnl for p in self._positions.values()
            if p.created_at.date() == today
        )
        return self._daily_realized_pnl + daily_unrealized

    def get_closed_trades(self, limit: int = 50) -> List[ClosedTrade]:
        """Get recent closed trades."""
        return list(reversed(self._closed_trades[-limit:]))

    def get_daily_trades(self) -> List[ClosedTrade]:
        """Get today's closed trades."""
        self._check_daily_reset()
        return list(reversed(self._daily_trades))

    def get_trade_history(self, limit: int = 100) -> List[dict]:
        """Get trade history as dictionaries."""
        trades = self.get_closed_trades(limit)
        return [t.to_dict() for t in trades]

    def get_trade_statistics(self) -> dict:
        """Get comprehensive trade statistics."""
        trades = self._closed_trades
        if not trades:
            return {
                "total_trades": 0,
                "winning_trades": 0,
                "losing_trades": 0,
                "break_even_trades": 0,
                "win_rate": 0.0,
                "total_pnl": 0.0,
                "average_win": 0.0,
                "average_loss": 0.0,
                "largest_win": 0.0,
                "largest_loss": 0.0,
                "profit_factor": 0.0,
                "average_duration_seconds": 0,
                "close_reasons": {},
            }

        winning = [t for t in trades if t.pnl > 0]
        losing = [t for t in trades if t.pnl < 0]
        break_even = [t for t in trades if t.pnl == 0]

        total_wins = sum(t.pnl for t in winning)
        total_losses = abs(sum(t.pnl for t in losing))

        # Count close reasons
        close_reasons = {}
        for t in trades:
            reason = t.close_reason.value
            close_reasons[reason] = close_reasons.get(reason, 0) + 1

        return {
            "total_trades": len(trades),
            "winning_trades": len(winning),
            "losing_trades": len(losing),
            "break_even_trades": len(break_even),
            "win_rate": (len(winning) / len(trades) * 100) if trades else 0.0,
            "total_pnl": sum(t.pnl for t in trades),
            "average_win": (total_wins / len(winning)) if winning else 0.0,
            "average_loss": (total_losses / len(losing)) if losing else 0.0,
            "largest_win": max((t.pnl for t in winning), default=0.0),
            "largest_loss": min((t.pnl for t in losing), default=0.0),
            "profit_factor": (total_wins / total_losses) if total_losses > 0 else float('inf'),
            "average_duration_seconds": sum(t.duration_seconds for t in trades) // len(trades) if trades else 0,
            "close_reasons": close_reasons,
            "total_risk": sum(t.risk_amount for t in trades),
            "average_risk": sum(t.risk_amount for t in trades) / len(trades) if trades else 0.0,
        }

    def reset_paper_trading(self):
        """Reset paper trading portfolio."""
        if self.settings.paper_trading:
            self._positions.clear()
            self._closed_trades.clear()
            self._daily_trades.clear()
            self._daily_realized_pnl = 0.0
            self._cash_balance = self.settings.paper_balance
            logger.info("Paper trading portfolio reset")

    @property
    def cash_balance(self) -> float:
        """Get current cash balance."""
        return self._cash_balance


# Singleton instance
_portfolio_manager: Optional[PortfolioManager] = None


def get_portfolio_manager() -> PortfolioManager:
    """Get the global portfolio manager instance."""
    global _portfolio_manager
    if _portfolio_manager is None:
        _portfolio_manager = PortfolioManager()
    return _portfolio_manager
