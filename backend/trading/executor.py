"""Trade execution engine."""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from ..config import get_settings
from ..data.polymarket import get_polymarket_client
from ..analysis.signals import TradingSignal
from ..utils import get_trading_logger, TradeLogger
from .risk_manager import get_risk_manager, TradeValidation
from .portfolio import get_portfolio_manager

logger = get_trading_logger()


@dataclass
class TradeResult:
    """Result of a trade execution."""

    success: bool
    order_id: Optional[str]
    market_id: str
    token_id: str
    side: str
    size: float
    price: float
    timestamp: datetime
    paper: bool
    error: Optional[str] = None

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "success": self.success,
            "order_id": self.order_id,
            "market_id": self.market_id,
            "token_id": self.token_id,
            "side": self.side,
            "size": self.size,
            "price": self.price,
            "timestamp": self.timestamp.isoformat(),
            "paper": self.paper,
            "error": self.error,
        }


class TradeExecutor:
    """
    Executes trades on Polymarket based on signals.
    """

    def __init__(self):
        """Initialize the trade executor."""
        self.settings = get_settings()
        self.polymarket = get_polymarket_client()
        self.risk_manager = get_risk_manager()
        self.portfolio = get_portfolio_manager()
        self.trade_logger = TradeLogger()
        self._selected_market: Optional[dict] = None

    async def initialize(self):
        """Initialize the executor and Polymarket connection."""
        await self.polymarket.initialize()
        logger.info("Trade executor initialized")

    def set_market(self, market: dict):
        """
        Set the market to trade on.

        Args:
            market: Market dict from Polymarket search
        """
        self._selected_market = market
        logger.info(f"Selected market: {market.get('question', 'Unknown')}")

    def get_selected_market(self) -> Optional[dict]:
        """Get the currently selected market."""
        return self._selected_market

    async def execute_signal(
        self,
        signal: TradingSignal,
        size_usd: Optional[float] = None,
    ) -> TradeResult:
        """
        Execute a trade based on a signal.

        Args:
            signal: Trading signal to execute
            size_usd: Optional position size (uses max if not specified)

        Returns:
            TradeResult with execution details
        """
        if not self._selected_market:
            return TradeResult(
                success=False,
                order_id=None,
                market_id="",
                token_id="",
                side="",
                size=0,
                price=0,
                timestamp=datetime.now(timezone.utc),
                paper=self.settings.paper_trading,
                error="No market selected",
            )

        if signal.direction == "HOLD":
            return TradeResult(
                success=False,
                order_id=None,
                market_id=self._selected_market["id"],
                token_id="",
                side="HOLD",
                size=0,
                price=0,
                timestamp=datetime.now(timezone.utc),
                paper=self.settings.paper_trading,
                error="Signal is HOLD - no trade",
            )

        # Determine trade parameters
        size = size_usd or self.settings.max_position_size

        # Validate with risk manager
        validation = self.risk_manager.validate_trade(
            size=size,
            side=signal.direction,
            confidence=signal.confidence,
        )

        if not validation.allowed:
            return TradeResult(
                success=False,
                order_id=None,
                market_id=self._selected_market["id"],
                token_id="",
                side=signal.direction,
                size=0,
                price=0,
                timestamp=datetime.now(timezone.utc),
                paper=self.settings.paper_trading,
                error=validation.reason,
            )

        # Use validated size
        actual_size = validation.max_size

        # Determine which outcome to trade
        outcomes = self._selected_market.get("outcomes", [])
        if len(outcomes) < 2:
            return TradeResult(
                success=False,
                order_id=None,
                market_id=self._selected_market["id"],
                token_id="",
                side=signal.direction,
                size=0,
                price=0,
                timestamp=datetime.now(timezone.utc),
                paper=self.settings.paper_trading,
                error="Invalid market outcomes",
            )

        # BUY signal = buy YES tokens (betting price goes up)
        # SELL signal = buy NO tokens (betting price goes down)
        if signal.direction == "BUY":
            target_outcome = next((o for o in outcomes if "yes" in o["outcome"].lower()), outcomes[0])
            side = "YES"
        else:
            target_outcome = next((o for o in outcomes if "no" in o["outcome"].lower()), outcomes[1])
            side = "NO"

        token_id = target_outcome["token_id"]
        price = target_outcome["price"]

        # Calculate number of shares (size in USD / price per share)
        shares = actual_size / price if price > 0 else 0

        # Execute the trade
        result = await self._execute_order(
            token_id=token_id,
            side="BUY",  # We're always buying the outcome token
            shares=shares,
            price=price,
        )

        if result["success"]:
            # Record in risk manager
            self.risk_manager.record_trade(
                size=actual_size,
                side=signal.direction,
                entry_price=price,
                market_id=self._selected_market["id"],
            )

            # Add to portfolio
            self.portfolio.add_position(
                market_id=self._selected_market["id"],
                market_name=self._selected_market.get("question", "Unknown"),
                token_id=token_id,
                side=side,
                size=shares,
                entry_price=price,
            )

            # Log the trade
            self.trade_logger.log_trade(
                action="OPEN",
                market=self._selected_market.get("question", "Unknown"),
                side=side,
                size=actual_size,
                price=price,
                confidence=signal.confidence,
                paper=self.settings.paper_trading,
            )

        return TradeResult(
            success=result["success"],
            order_id=result.get("order_id"),
            market_id=self._selected_market["id"],
            token_id=token_id,
            side=side,
            size=shares,
            price=price,
            timestamp=datetime.now(timezone.utc),
            paper=self.settings.paper_trading,
            error=result.get("error"),
        )

    async def _execute_order(
        self,
        token_id: str,
        side: str,
        shares: float,
        price: float,
    ) -> dict:
        """
        Execute an order on Polymarket.

        Returns:
            Dict with success status and order details
        """
        try:
            result = await self.polymarket.place_order(
                token_id=token_id,
                side=side,
                size=shares,
                price=price,
            )

            if result:
                return {
                    "success": True,
                    "order_id": result.get("id", f"paper_{datetime.now(timezone.utc).timestamp()}"),
                }
            else:
                return {
                    "success": False,
                    "error": "Order placement failed",
                }

        except Exception as e:
            logger.error(f"Order execution error: {e}")
            return {
                "success": False,
                "error": str(e),
            }

    async def close_position(
        self,
        position_id: str,
    ) -> TradeResult:
        """
        Close an existing position.

        Args:
            position_id: Position to close

        Returns:
            TradeResult with closing details
        """
        position = self.portfolio.get_position(position_id)
        if not position:
            return TradeResult(
                success=False,
                order_id=None,
                market_id="",
                token_id="",
                side="CLOSE",
                size=0,
                price=0,
                timestamp=datetime.now(timezone.utc),
                paper=self.settings.paper_trading,
                error="Position not found",
            )

        # For paper trading, we simulate closing at current price
        exit_price = position.current_price

        # Execute the close (sell the tokens)
        result = await self._execute_order(
            token_id=position.token_id,
            side="SELL",
            shares=position.size,
            price=exit_price,
        )

        if result["success"]:
            # Close in portfolio
            close_result = self.portfolio.close_position(position_id, exit_price)

            if close_result:
                # Record P&L
                self.risk_manager.record_pnl(close_result["pnl"])

                # Log the close
                self.trade_logger.log_trade(
                    action="CLOSE",
                    market=position.market_name,
                    side=position.side,
                    size=position.cost_basis,
                    price=exit_price,
                    confidence=100,  # N/A for close
                    paper=self.settings.paper_trading,
                )

        return TradeResult(
            success=result["success"],
            order_id=result.get("order_id"),
            market_id=position.market_id,
            token_id=position.token_id,
            side="CLOSE",
            size=position.size,
            price=exit_price,
            timestamp=datetime.now(timezone.utc),
            paper=self.settings.paper_trading,
            error=result.get("error"),
        )

    async def manual_trade(
        self,
        market_id: str,
        side: str,  # "YES" or "NO"
        size_usd: float,
    ) -> TradeResult:
        """
        Execute a manual trade (not signal-based).

        Args:
            market_id: Market to trade
            side: "YES" or "NO"
            size_usd: Position size in USD

        Returns:
            TradeResult
        """
        # Find the market
        markets = self.polymarket.btc_markets
        market = next((m for m in markets if m["id"] == market_id), None)

        if not market:
            return TradeResult(
                success=False,
                order_id=None,
                market_id=market_id,
                token_id="",
                side=side,
                size=0,
                price=0,
                timestamp=datetime.now(timezone.utc),
                paper=self.settings.paper_trading,
                error="Market not found",
            )

        # Set as selected market
        self.set_market(market)

        # Create a synthetic signal
        fake_signal = TradingSignal(
            direction="BUY" if side == "YES" else "SELL",
            confidence=100,  # Manual trades bypass confidence check
            strength=1.0 if side == "YES" else -1.0,
            timestamp=datetime.now(timezone.utc),
            timeframe="manual",
            indicator_signals={},
            ml_prediction=None,
            reasons=["Manual trade"],
        )

        return await self.execute_signal(fake_signal, size_usd)


# Singleton instance
_trade_executor: Optional[TradeExecutor] = None


def get_trade_executor() -> TradeExecutor:
    """Get the global trade executor instance."""
    global _trade_executor
    if _trade_executor is None:
        _trade_executor = TradeExecutor()
    return _trade_executor
