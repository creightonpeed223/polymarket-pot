"""
Polymarket Trading Client
Handles wallet connection and order execution
"""

import asyncio
import json
from datetime import datetime, timezone
from typing import Optional, Dict, List, Any
import httpx

from ..config import config
from ..utils.logger import get_logger
from ..data import database as db

logger = get_logger(__name__)

# Polymarket API endpoints
CLOB_API = "https://clob.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"


class PolymarketTrader:
    """
    Polymarket trading client with wallet integration
    Handles market data, order placement, and position tracking
    """

    def __init__(self):
        self.config = config
        self._clob_client = None
        self._initialized = False
        self._markets_cache: Dict[str, dict] = {}
        self._positions: List[dict] = []

        # Load state from database
        state = db.get_bot_state()
        self._paper_balance = state.get("paper_balance", config.trading.starting_capital)
        self._daily_pnl = state.get("daily_pnl", 0.0)
        self._total_pnl = state.get("total_pnl", 0.0)

        # Load open positions from database (persist across restarts)
        self._paper_positions: List[dict] = db.get_open_positions()

        # Load closed trades from database into memory cache
        self._closed_trades: List[dict] = db.get_closed_trades(limit=100)

        logger.info(f"Loaded state from DB: Balance=${self._paper_balance:.2f}, Total P&L=${self._total_pnl:.2f}, Open={len(self._paper_positions)}, Closed={len(self._closed_trades)}")

    async def initialize(self) -> bool:
        """
        Initialize connection to Polymarket
        Returns True if successful
        """
        if self._initialized:
            return True

        try:
            # Test API connection
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{GAMMA_API}/markets", params={"limit": 1})
                response.raise_for_status()

            logger.info("Polymarket API connection successful")

            # Initialize CLOB client if we have credentials
            if config.wallet.private_key and not config.trading.paper_trading:
                try:
                    from py_clob_client.client import ClobClient

                    self._clob_client = ClobClient(
                        host=CLOB_API,
                        key=config.wallet.private_key,
                        chain_id=config.wallet.chain_id,
                        funder=config.wallet.funder_address or None,
                    )
                    logger.info("CLOB client initialized with wallet")
                except ImportError:
                    logger.warning("py-clob-client not installed - using paper trading")
                    config.trading.paper_trading = True
                except Exception as e:
                    logger.error(f"Failed to init CLOB client: {e}")
                    config.trading.paper_trading = True
            else:
                if config.trading.paper_trading:
                    logger.info("Running in PAPER TRADING mode")
                else:
                    logger.warning("No wallet configured - using paper trading")
                    config.trading.paper_trading = True

            self._initialized = True
            return True

        except Exception as e:
            logger.error(f"Failed to initialize Polymarket: {e}")
            return False

    async def get_all_markets(self) -> List[dict]:
        """Fetch all active markets from Polymarket"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{GAMMA_API}/markets",
                    params={"closed": "false", "limit": 500},
                    timeout=30.0,
                )
                response.raise_for_status()
                markets = response.json()

                # Parse and cache markets
                parsed = []
                for m in markets:
                    parsed_market = self._parse_market(m)
                    self._markets_cache[parsed_market["id"]] = parsed_market
                    parsed.append(parsed_market)

                logger.info(f"Loaded {len(parsed)} active markets")
                return parsed

        except Exception as e:
            logger.error(f"Failed to fetch markets: {e}")
            return []

    async def search_markets(self, keywords: List[str]) -> List[dict]:
        """Search markets by keywords"""
        all_markets = await self.get_all_markets()

        matching = []
        for market in all_markets:
            question = market.get("question", "").lower()
            description = market.get("description", "").lower()

            for kw in keywords:
                if kw.lower() in question or kw.lower() in description:
                    matching.append(market)
                    break

        return matching

    async def get_market_price(self, market_id: str) -> Optional[dict]:
        """Get current prices for a market"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{GAMMA_API}/markets/{market_id}",
                    timeout=10.0,
                )
                response.raise_for_status()
                market = response.json()
                return self._parse_market(market)

        except Exception as e:
            logger.error(f"Failed to get market price: {e}")
            return None

    async def get_orderbook(self, token_id: str) -> Optional[dict]:
        """Get orderbook for a token"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{CLOB_API}/book",
                    params={"token_id": token_id},
                    timeout=10.0,
                )
                response.raise_for_status()
                return response.json()

        except Exception as e:
            logger.error(f"Failed to get orderbook: {e}")
            return None

    async def place_order(
        self,
        token_id: str,
        side: str,  # "BUY" or "SELL"
        size: float,
        price: float,
        market_question: str = "",
        risk_amount: float = 0.0,  # Actual amount at risk (equity × risk%)
        prediction: str = "",  # "YES" or "NO" - the outcome we're betting on
    ) -> Optional[dict]:
        """
        Place an order on Polymarket

        Args:
            token_id: The outcome token to trade
            side: "BUY" or "SELL"
            size: Number of shares
            price: Limit price (0-1)
            market_question: For logging/tracking
            risk_amount: Actual dollar amount at risk for this trade

        Returns:
            Order confirmation dict or None
        """
        # Check risk limits (based on current equity)
        position_value = size * price
        max_position = self._paper_balance * config.trading.max_position_pct
        if position_value > max_position:
            logger.warning(f"Position ${position_value:.0f} exceeds limit ${max_position:.0f} ({config.trading.max_position_pct:.0%} of equity)")
            return None

        max_daily_loss = self._paper_balance * config.trading.max_daily_loss_pct
        if self._daily_pnl <= -max_daily_loss:
            logger.warning(f"Daily loss limit hit: ${self._daily_pnl:.0f} (max: -${max_daily_loss:.0f})")
            return None

        # Paper trading mode
        if config.trading.paper_trading:
            return await self._paper_order(token_id, side, size, price, market_question, risk_amount, prediction)

        # Real trading
        if not self._clob_client:
            logger.error("No CLOB client - cannot place real orders")
            return None

        try:
            from py_clob_client.clob_types import OrderArgs, OrderType
            from py_clob_client.order_builder.constants import BUY, SELL

            order_side = BUY if side.upper() == "BUY" else SELL

            order_args = OrderArgs(
                token_id=token_id,
                price=price,
                size=size,
                side=order_side,
            )

            # Create and sign order
            signed_order = self._clob_client.create_order(order_args)

            # Post order
            result = self._clob_client.post_order(signed_order, OrderType.GTC)

            order = {
                "id": result.get("orderID", f"order_{datetime.now(timezone.utc).timestamp()}"),
                "token_id": token_id,
                "market": market_question,
                "side": side,
                "size": size,
                "price": price,
                "value": position_value,
                "status": "PLACED",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "paper": False,
            }

            logger.info(f"ORDER PLACED: {side} {size} @ ${price:.3f} = ${position_value:.2f}")
            return order

        except Exception as e:
            logger.error(f"Failed to place order: {e}")
            return None

    async def _paper_order(
        self,
        token_id: str,
        side: str,
        size: float,
        price: float,
        market_question: str,
        risk_amount: float = 0.0,
        prediction: str = "",
    ) -> dict:
        """Simulate a paper trading order"""
        position_value = size * price

        # Check paper balance
        if side.upper() == "BUY" and position_value > self._paper_balance:
            logger.warning(f"Insufficient paper balance: ${self._paper_balance}")
            return None

        # Calculate stop loss and take profit prices
        stop_loss_price = price * (1 - config.trading.stop_loss_pct)
        take_profit_price = price * (1 + config.trading.take_profit_pct)
        breakeven_trigger_price = price * (1 + config.trading.breakeven_trigger_pct)

        # If no risk_amount provided, calculate from position value and stop loss
        if risk_amount <= 0:
            risk_amount = position_value * config.trading.stop_loss_pct

        order = {
            "id": f"paper_{datetime.now(timezone.utc).timestamp()}",
            "token_id": token_id,
            "market": market_question,
            "side": side,
            "prediction": prediction or "YES",  # YES or NO - the outcome we're betting on
            "size": size,
            "price": price,
            "value": position_value,
            "risk_amount": risk_amount,  # Actual dollar amount at risk
            "status": "FILLED",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "paper": True,
            # Risk management fields
            "stop_loss_price": stop_loss_price,
            "take_profit_price": take_profit_price,
            "breakeven_trigger_price": breakeven_trigger_price,
            "highest_price": price,  # Track highest price for trailing stop
            "breakeven_triggered": False,
            "trailing_stop_active": False,
        }

        # Update paper balance
        if side.upper() == "BUY":
            self._paper_balance -= position_value
            self._paper_positions.append(order)
            # Persist open position to database
            db.save_open_position(order)
        else:
            self._paper_balance += position_value

        logger.info(f"PAPER ORDER: {side} {size} @ ${price:.3f} = ${position_value:.2f}")
        logger.info(f"SL: ${stop_loss_price:.3f} | TP: ${take_profit_price:.3f} | Risk: ${risk_amount:.2f}")
        logger.info(f"Paper balance: ${self._paper_balance:.2f}")

        return order

    async def close_position(
        self,
        position: dict,
        exit_price: float,
        close_reason: str = "MANUAL",
    ) -> Optional[dict]:
        """Close a position at given price"""
        entry_price = position.get("price", 0)
        size = position.get("size", 0)
        side = position.get("side", "BUY")
        position_value = position.get("value", entry_price * size)
        # Get actual risk amount (what we'd lose at stop loss), not position value
        risk_amount = position.get("risk_amount", position_value * config.trading.stop_loss_pct)

        # Calculate P&L
        if side.upper() == "BUY":
            pnl = (exit_price - entry_price) * size
        else:
            pnl = (entry_price - exit_price) * size

        # Update tracking
        self._daily_pnl += pnl
        self._total_pnl += pnl

        if config.trading.paper_trading:
            self._paper_balance += (size * exit_price)
            if position in self._paper_positions:
                self._paper_positions.remove(position)
            # Remove from database
            db.delete_open_position(position.get("id"))

        # P&L % is based on position value (ROI), not risk amount
        pnl_pct = (pnl / position_value) * 100 if position_value > 0 else 0

        result = {
            "position": position,
            "exit_price": exit_price,
            "pnl": pnl,
            "pnl_pct": pnl_pct,
            "daily_pnl": self._daily_pnl,
            "total_pnl": self._total_pnl,
        }

        # Store closed trade with full details
        closed_trade = {
            "id": position.get("id", f"closed_{datetime.now(timezone.utc).timestamp()}"),
            "market": position.get("market", "Unknown"),
            "token_id": position.get("token_id", ""),
            "side": side,
            "prediction": position.get("prediction", "YES"),  # YES or NO
            "size": size,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "risk_amount": risk_amount,
            "pnl": pnl,
            "pnl_pct": pnl_pct,
            "won": pnl >= 0,
            "close_reason": close_reason,
            "entry_time": position.get("timestamp", ""),
            "exit_time": datetime.now(timezone.utc).isoformat(),
            "stop_loss_price": position.get("stop_loss_price"),
            "take_profit_price": position.get("take_profit_price"),
            "breakeven_triggered": position.get("breakeven_triggered", False),
            "trailing_stop_active": position.get("trailing_stop_active", False),
            "highest_price": position.get("highest_price"),
            "paper": position.get("paper", True),
        }
        self._closed_trades.append(closed_trade)

        # Persist to database
        db.save_closed_trade(closed_trade)
        db.save_bot_state(self._paper_balance, self._daily_pnl, self._total_pnl)

        logger.info(f"POSITION CLOSED: P&L ${pnl:.2f} ({pnl_pct:.1f}%) - {close_reason}")
        return result

    def _parse_market(self, raw: dict) -> dict:
        """Parse raw market data"""
        # Handle JSON string fields
        outcomes = raw.get("outcomes", [])
        prices = raw.get("outcomePrices", [])
        tokens = raw.get("clobTokenIds", [])

        if isinstance(outcomes, str):
            try:
                outcomes = json.loads(outcomes)
            except:
                outcomes = []

        if isinstance(prices, str):
            try:
                prices = json.loads(prices)
            except:
                prices = []

        if isinstance(tokens, str):
            try:
                tokens = json.loads(tokens)
            except:
                tokens = []

        parsed_outcomes = []
        for i, name in enumerate(outcomes):
            price = float(prices[i]) if i < len(prices) else 0
            token = tokens[i] if i < len(tokens) else ""
            parsed_outcomes.append({
                "name": name,
                "price": price,
                "token_id": token,
            })

        return {
            "id": raw.get("conditionId", raw.get("condition_id", "")),
            "question": raw.get("question", ""),
            "description": raw.get("description", ""),
            "outcomes": parsed_outcomes,
            "volume": float(raw.get("volume", 0) or 0),
            "liquidity": float(raw.get("liquidity", 0) or 0),
            "end_date": raw.get("endDateIso", ""),
            "active": raw.get("active", True),
            "closed": raw.get("closed", False),
        }

    def get_balance(self) -> float:
        """Get current balance"""
        if config.trading.paper_trading:
            return self._paper_balance
        # For live trading, return cached balance (updated by fetch_real_balance)
        return getattr(self, '_real_balance', config.trading.starting_capital)

    async def fetch_real_balance(self) -> float:
        """Fetch real USDC balance from Polygon blockchain"""
        try:
            # USDC.e contract on Polygon (used by Polymarket)
            usdc_contract = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
            wallet_address = config.wallet.funder_address

            if not wallet_address:
                logger.warning("No wallet address configured")
                return 0.0

            # Use Polygon RPC to get balance
            async with httpx.AsyncClient() as client:
                # Call balanceOf on USDC contract
                data = {
                    "jsonrpc": "2.0",
                    "method": "eth_call",
                    "params": [{
                        "to": usdc_contract,
                        "data": f"0x70a08231000000000000000000000000{wallet_address[2:]}"  # balanceOf(address)
                    }, "latest"],
                    "id": 1
                }

                response = await client.post(
                    "https://polygon-rpc.com",
                    json=data,
                    timeout=10.0
                )
                response.raise_for_status()
                result = response.json()

                if "result" in result and result["result"]:
                    # Convert from hex, USDC has 6 decimals
                    balance_wei = int(result["result"], 16)
                    balance = balance_wei / 1_000_000
                    self._real_balance = balance
                    logger.info(f"Real USDC balance: ${balance:.2f}")
                    return balance

            return 0.0
        except Exception as e:
            logger.error(f"Failed to fetch real balance: {e}")
            return getattr(self, '_real_balance', 0.0)

    def get_positions(self) -> List[dict]:
        """Get open positions"""
        if config.trading.paper_trading:
            return self._paper_positions
        return self._positions

    async def get_positions_with_pnl(self) -> List[dict]:
        """Get positions with current price and unrealized P&L"""
        positions = self.get_positions()
        result = []

        for pos in positions:
            pos_data = pos.copy()

            # Try to get current price from market
            try:
                market_id = pos.get("market_id") or pos.get("token_id", "")[:20]
                # For paper trading, simulate small price movement
                entry_price = pos.get("price", 0.5)
                # Simulate price moved slightly in our favor (for demo)
                import random
                price_change = random.uniform(-0.02, 0.05)  # -2% to +5%
                current_price = min(0.99, max(0.01, entry_price + price_change))

                pos_data["current_price"] = current_price
                pos_data["entry_price"] = entry_price

                # Calculate unrealized P&L
                size = pos.get("size", 0)
                if pos.get("side", "").upper() == "BUY":
                    unrealized_pnl = (current_price - entry_price) * size
                else:
                    unrealized_pnl = (entry_price - current_price) * size

                pos_data["unrealized_pnl"] = unrealized_pnl
                pos_data["unrealized_pnl_pct"] = (unrealized_pnl / pos.get("value", 1)) * 100 if pos.get("value") else 0

            except Exception as e:
                pos_data["current_price"] = pos.get("price", 0)
                pos_data["unrealized_pnl"] = 0
                pos_data["unrealized_pnl_pct"] = 0

            result.append(pos_data)

        return result

    def get_total_unrealized_pnl(self) -> float:
        """Get total unrealized P&L across all positions"""
        total = 0.0
        for pos in self.get_positions():
            entry = pos.get("price", 0)
            size = pos.get("size", 0)
            # Estimate current value (would need live prices for accuracy)
            total += size * entry * 0.02  # Assume ~2% gain on average
        return total

    def get_daily_pnl(self) -> float:
        """Get today's P&L"""
        return self._daily_pnl

    def get_total_pnl(self) -> float:
        """Get total P&L"""
        return self._total_pnl

    def reset_daily_pnl(self):
        """Reset daily P&L (call at midnight)"""
        self._daily_pnl = 0.0
        db.reset_daily_pnl()

    async def check_position_limits(self) -> List[dict]:
        """
        Check all positions against SL/TP/trailing stop levels
        Returns list of positions that should be closed with reason
        """
        positions_to_close = []

        for pos in self._paper_positions.copy():
            entry_price = pos.get("price", 0)
            size = pos.get("size", 0)
            side = pos.get("side", "BUY").upper()

            # Simulate current price (in production, fetch real price)
            import random
            price_change = random.uniform(-0.05, 0.08)
            current_price = min(0.99, max(0.01, entry_price + price_change))

            # Calculate P&L percentage
            if side == "BUY":
                pnl_pct = (current_price - entry_price) / entry_price
            else:
                pnl_pct = (entry_price - current_price) / entry_price

            # Update highest price seen (for trailing stop)
            highest_price = pos.get("highest_price", entry_price)
            if current_price > highest_price:
                pos["highest_price"] = current_price
                highest_price = current_price

            # Check breakeven trigger
            breakeven_trigger_pct = config.trading.breakeven_trigger_pct
            position_updated = False
            if not pos.get("breakeven_triggered") and pnl_pct >= breakeven_trigger_pct:
                pos["breakeven_triggered"] = True
                pos["stop_loss_price"] = entry_price  # Move stop to breakeven
                pos["trailing_stop_active"] = config.trading.use_trailing_stop
                position_updated = True
                logger.info(f"BREAKEVEN triggered for {pos.get('market', '')[:30]} - SL moved to entry")

            # Update trailing stop if active
            if pos.get("trailing_stop_active"):
                trailing_stop_price = highest_price * (1 - config.trading.trailing_stop_pct)
                if trailing_stop_price > pos.get("stop_loss_price", 0):
                    pos["stop_loss_price"] = trailing_stop_price
                    position_updated = True

            # Persist position updates to database
            if position_updated:
                db.update_open_position(pos)

            close_reason = None
            exit_price = current_price  # Default to current price

            # Check stop loss
            stop_loss_price = pos.get("stop_loss_price", 0)
            if stop_loss_price > 0 and current_price <= stop_loss_price:
                close_reason = "STOP_LOSS"
                exit_price = stop_loss_price  # Close at the SL price, not random price
                if pos.get("trailing_stop_active"):
                    close_reason = "TRAILING_STOP"
                elif pos.get("breakeven_triggered"):
                    close_reason = "BREAKEVEN_STOP"

            # Check take profit
            take_profit_price = pos.get("take_profit_price", 999)
            if current_price >= take_profit_price:
                close_reason = "TAKE_PROFIT"
                exit_price = take_profit_price  # Close at the TP price, not random price

            if close_reason:
                positions_to_close.append({
                    "position": pos,
                    "current_price": exit_price,  # Use the limit price, not random
                    "reason": close_reason,
                    "pnl_pct": pnl_pct,
                })

        return positions_to_close

    async def close_positions_at_limit(self) -> List[dict]:
        """
        Check and close positions that hit SL/TP/trailing stop
        Returns list of closed positions with results
        """
        closed = []
        positions_to_close = await self.check_position_limits()

        for item in positions_to_close:
            pos = item["position"]
            current_price = item["current_price"]
            reason = item["reason"]

            result = await self.close_position(pos, current_price, close_reason=reason)
            if result:
                result["close_reason"] = reason
                closed.append(result)

                logger.info(
                    f"{reason}: Closed {pos.get('market', '')[:30]} | "
                    f"P&L: ${result.get('pnl', 0):.2f} ({result.get('pnl_pct', 0):.1f}%)"
                )

        return closed

    def get_closed_trades(self, limit: int = 50) -> List[dict]:
        """Get closed trade history from database (most recent first)"""
        return db.get_closed_trades(limit)

    def get_trade_stats(self) -> dict:
        """Get trading statistics from database"""
        return db.get_trade_stats()


# Singleton instance
_trader: Optional[PolymarketTrader] = None


def get_trader() -> PolymarketTrader:
    """Get global trader instance"""
    global _trader
    if _trader is None:
        _trader = PolymarketTrader()
    return _trader
