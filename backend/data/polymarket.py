"""Polymarket client for trading Bitcoin prediction markets."""

from datetime import datetime, timezone
from typing import Optional
import httpx

from ..config import get_settings
from ..utils import get_data_logger

logger = get_data_logger()

# Polymarket API endpoints
POLYMARKET_API_URL = "https://clob.polymarket.com"
GAMMA_API_URL = "https://gamma-api.polymarket.com"


class PolymarketClient:
    """Client for interacting with Polymarket prediction markets."""

    def __init__(self):
        """Initialize the Polymarket client."""
        self.settings = get_settings()
        self._clob_client = None
        self._initialized = False
        self._btc_markets: list[dict] = []

    async def initialize(self):
        """Initialize the Polymarket client connection."""
        if self._initialized:
            return

        try:
            # Only import and initialize if we have credentials
            if self.settings.polymarket_private_key:
                from py_clob_client.client import ClobClient
                from py_clob_client.clob_types import ApiCreds

                self._clob_client = ClobClient(
                    host=POLYMARKET_API_URL,
                    key=self.settings.polymarket_private_key,
                    chain_id=137,  # Polygon mainnet
                    funder=self.settings.polymarket_funder_address or None,
                )
                logger.info("Polymarket CLOB client initialized")
            else:
                logger.warning("No Polymarket credentials - running in read-only mode")

            self._initialized = True

        except ImportError as e:
            logger.warning(f"py-clob-client not available: {e}")
            self._initialized = True
        except Exception as e:
            logger.error(f"Failed to initialize Polymarket client: {e}")
            raise

    async def search_btc_markets(self) -> list[dict]:
        """
        Search for Bitcoin-related prediction markets.

        Returns:
            List of market dictionaries
        """
        try:
            async with httpx.AsyncClient() as client:
                # Search for BTC/Bitcoin markets using Gamma API
                response = await client.get(
                    f"{GAMMA_API_URL}/markets",
                    params={
                        "closed": "false",
                        "limit": 500,
                    },
                    timeout=30.0,
                )
                response.raise_for_status()
                all_markets = response.json()

                # Filter for Bitcoin-related markets with price keywords
                btc_keywords = ["bitcoin", "btc", "₿"]
                price_keywords = ["100k", "150k", "200k", "50k", "price", "hit $", "reach $", "above $", "below $"]
                btc_markets = []

                for market in all_markets:
                    question = market.get("question", "").lower()
                    description = market.get("description", "").lower()

                    # Check for BTC keywords
                    is_btc = any(kw in question or kw in description for kw in btc_keywords)
                    # Check for price-related markets
                    is_price = any(kw in question for kw in price_keywords)

                    if is_btc or (is_price and "bitcoin" in description):
                        parsed = self._parse_market(market)
                        # Categorize market type
                        if any(p in question for p in ["100k", "150k", "200k", "hit $", "reach $"]):
                            parsed["market_type"] = "price_target"
                        else:
                            parsed["market_type"] = "other"
                        btc_markets.append(parsed)

                # Sort by volume (most active first)
                btc_markets.sort(key=lambda x: x.get("volume", 0), reverse=True)

                self._btc_markets = btc_markets
                logger.info(f"Found {len(btc_markets)} BTC-related markets")
                return btc_markets

        except Exception as e:
            logger.error(f"Error searching BTC markets: {e}")
            return []

    async def get_btc_sentiment(self) -> dict:
        """
        Get overall BTC sentiment from Polymarket.

        Returns:
            Dict with sentiment analysis from available markets
        """
        if not self._btc_markets:
            await self.search_btc_markets()

        sentiment = {
            "markets_analyzed": len(self._btc_markets),
            "bullish_markets": 0,
            "bearish_markets": 0,
            "market_details": [],
        }

        for market in self._btc_markets:
            outcomes = market.get("outcomes", [])
            if len(outcomes) >= 2:
                # Find YES/NO prices
                yes_price = 0
                no_price = 0
                for outcome in outcomes:
                    if outcome.get("outcome", "").lower() == "yes":
                        yes_price = outcome.get("price", 0)
                    elif outcome.get("outcome", "").lower() == "no":
                        no_price = outcome.get("price", 0)

                # Determine if market is bullish (YES > 50% for upside markets)
                question = market.get("question", "").lower()
                is_upside_market = any(k in question for k in ["hit", "reach", "above", "100k", "150k", "200k", "1m"])

                if is_upside_market:
                    if yes_price > 0.5:
                        sentiment["bullish_markets"] += 1
                    else:
                        sentiment["bearish_markets"] += 1

                sentiment["market_details"].append({
                    "question": market.get("question", "")[:80],
                    "yes_price": yes_price,
                    "no_price": no_price,
                    "volume": market.get("volume", 0),
                    "market_type": market.get("market_type", "other"),
                })

        # Calculate overall sentiment
        total = sentiment["bullish_markets"] + sentiment["bearish_markets"]
        if total > 0:
            sentiment["bullish_ratio"] = sentiment["bullish_markets"] / total
            sentiment["overall"] = "BULLISH" if sentiment["bullish_ratio"] > 0.5 else "BEARISH"
        else:
            sentiment["bullish_ratio"] = 0.5
            sentiment["overall"] = "NEUTRAL"

        return sentiment

    def _parse_market(self, raw_market: dict) -> dict:
        """Parse raw market data into a cleaner format."""
        import json as json_lib

        # Parse outcomes and prices (API returns JSON strings)
        outcome_names = raw_market.get("outcomes", [])
        outcome_prices = raw_market.get("outcomePrices", [])
        clob_token_ids = raw_market.get("clobTokenIds", [])

        # Handle JSON strings
        if isinstance(outcome_names, str):
            try:
                outcome_names = json_lib.loads(outcome_names)
            except:
                outcome_names = []
        if isinstance(outcome_prices, str):
            try:
                outcome_prices = json_lib.loads(outcome_prices)
            except:
                outcome_prices = []
        if isinstance(clob_token_ids, str):
            try:
                clob_token_ids = json_lib.loads(clob_token_ids)
            except:
                clob_token_ids = []

        outcomes = []
        for i, name in enumerate(outcome_names):
            price = 0
            token_id = ""
            if i < len(outcome_prices):
                try:
                    price = float(outcome_prices[i])
                except (ValueError, TypeError):
                    price = 0
            if i < len(clob_token_ids):
                token_id = clob_token_ids[i]

            outcomes.append({
                "outcome": name,
                "token_id": token_id,
                "price": price,
            })

        return {
            "id": raw_market.get("conditionId", raw_market.get("condition_id", "")),
            "question": raw_market.get("question", ""),
            "description": raw_market.get("description", ""),
            "end_date": raw_market.get("endDateIso", raw_market.get("end_date_iso", "")),
            "volume": float(raw_market.get("volume", 0) or 0),
            "liquidity": float(raw_market.get("liquidity", 0) or 0),
            "outcomes": outcomes,
            "active": raw_market.get("active", False),
            "closed": raw_market.get("closed", False),
        }

    async def get_market_details(self, condition_id: str) -> Optional[dict]:
        """
        Get detailed information about a specific market.

        Args:
            condition_id: The market's condition ID

        Returns:
            Market details dictionary or None
        """
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{GAMMA_API_URL}/markets/{condition_id}",
                    timeout=30.0,
                )
                response.raise_for_status()
                market = response.json()
                return self._parse_market(market)

        except Exception as e:
            logger.error(f"Error fetching market details: {e}")
            return None

    async def get_orderbook(self, token_id: str) -> Optional[dict]:
        """
        Get the orderbook for a specific token.

        Args:
            token_id: The token ID

        Returns:
            Orderbook data or None
        """
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{POLYMARKET_API_URL}/book",
                    params={"token_id": token_id},
                    timeout=30.0,
                )
                response.raise_for_status()
                return response.json()

        except Exception as e:
            logger.error(f"Error fetching orderbook: {e}")
            return None

    async def get_positions(self) -> list[dict]:
        """
        Get current positions (requires authentication).

        Returns:
            List of position dictionaries
        """
        if not self._clob_client:
            logger.warning("Cannot get positions - no authenticated client")
            return []

        try:
            # Note: This requires proper authentication setup
            # The actual implementation depends on py-clob-client version
            positions = []

            # Placeholder for paper trading positions
            if self.settings.paper_trading:
                return self._get_paper_positions()

            return positions

        except Exception as e:
            logger.error(f"Error fetching positions: {e}")
            return []

    def _get_paper_positions(self) -> list[dict]:
        """Get paper trading positions from local storage."""
        # This will be populated by the portfolio manager
        return []

    async def place_order(
        self,
        token_id: str,
        side: str,  # "BUY" or "SELL"
        size: float,
        price: float,
    ) -> Optional[dict]:
        """
        Place an order on Polymarket.

        Args:
            token_id: The token to trade
            side: "BUY" or "SELL"
            size: Order size in shares
            price: Limit price (0-1)

        Returns:
            Order confirmation or None
        """
        if self.settings.paper_trading:
            return self._paper_order(token_id, side, size, price)

        if not self._clob_client:
            logger.error("Cannot place order - no authenticated client")
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

            # Create and sign the order
            signed_order = self._clob_client.create_order(order_args)

            # Post the order
            result = self._clob_client.post_order(signed_order, OrderType.GTC)

            logger.info(f"Order placed: {side} {size} @ {price}")
            return result

        except Exception as e:
            logger.error(f"Error placing order: {e}")
            return None

    def _paper_order(
        self,
        token_id: str,
        side: str,
        size: float,
        price: float,
    ) -> dict:
        """Simulate a paper trading order."""
        order = {
            "id": f"paper_{datetime.now(timezone.utc).timestamp()}",
            "token_id": token_id,
            "side": side,
            "size": size,
            "price": price,
            "status": "FILLED",
            "filled_at": datetime.now(timezone.utc).isoformat(),
            "paper": True,
        }
        logger.info(f"Paper order: {side} {size} @ {price}")
        return order

    async def cancel_order(self, order_id: str) -> bool:
        """
        Cancel an open order.

        Args:
            order_id: The order ID to cancel

        Returns:
            True if cancelled successfully
        """
        if self.settings.paper_trading:
            logger.info(f"Paper order cancelled: {order_id}")
            return True

        if not self._clob_client:
            logger.error("Cannot cancel order - no authenticated client")
            return False

        try:
            self._clob_client.cancel(order_id)
            logger.info(f"Order cancelled: {order_id}")
            return True

        except Exception as e:
            logger.error(f"Error cancelling order: {e}")
            return False

    @property
    def btc_markets(self) -> list[dict]:
        """Get cached BTC markets."""
        return self._btc_markets

    @property
    def is_authenticated(self) -> bool:
        """Check if the client has valid credentials."""
        return self._clob_client is not None


# Singleton instance
_polymarket_client: Optional[PolymarketClient] = None


def get_polymarket_client() -> PolymarketClient:
    """Get the global Polymarket client instance."""
    global _polymarket_client
    if _polymarket_client is None:
        _polymarket_client = PolymarketClient()
    return _polymarket_client
