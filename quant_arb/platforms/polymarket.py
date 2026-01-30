"""
Polymarket Platform Connector
WebSocket-based real-time orderbook streaming with direct CLOB order placement
"""

import asyncio
import json
import time
import logging
import secrets
from datetime import datetime
from typing import Dict, List, Optional, Any
import httpx
import websockets
from websockets.exceptions import ConnectionClosed

from .base import BasePlatform, OrderBook, Market, Order, Fill

logger = logging.getLogger(__name__)

# EIP-712 domains and types for Polymarket CLOB

# Domain for CLOB authentication
CLOB_AUTH_DOMAIN = {
    "name": "ClobAuthDomain",
    "version": "1",
    "chainId": 137,
}

CLOB_AUTH_TYPES = {
    "ClobAuth": [
        {"name": "address", "type": "address"},
        {"name": "timestamp", "type": "string"},
        {"name": "nonce", "type": "uint256"},
        {"name": "message", "type": "string"},
    ]
}

# Domain for order signing
ORDER_DOMAIN = {
    "name": "Polymarket CTF Exchange",
    "version": "1",
    "chainId": 137,
}

ORDER_TYPES = {
    "Order": [
        {"name": "salt", "type": "uint256"},
        {"name": "maker", "type": "address"},
        {"name": "signer", "type": "address"},
        {"name": "taker", "type": "address"},
        {"name": "tokenId", "type": "uint256"},
        {"name": "makerAmount", "type": "uint256"},
        {"name": "takerAmount", "type": "uint256"},
        {"name": "expiration", "type": "uint256"},
        {"name": "nonce", "type": "uint256"},
        {"name": "feeRateBps", "type": "uint256"},
        {"name": "side", "type": "uint8"},
        {"name": "signatureType", "type": "uint8"},
    ]
}

CLOB_AUTH_MSG = "This message attests that I control the given wallet"


class PolymarketConnector(BasePlatform):
    """
    Polymarket connector with WebSocket orderbook streaming and direct CLOB trading
    """

    def __init__(
        self,
        private_key: Optional[str] = None,
        funder_address: Optional[str] = None,
    ):
        super().__init__("polymarket")
        self.private_key = private_key
        self.funder_address = funder_address

        self.api_url = "https://clob.polymarket.com"
        self.ws_url = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
        self.gamma_url = "https://gamma-api.polymarket.com"

        self._http: Optional[httpx.AsyncClient] = None
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._ws_task: Optional[asyncio.Task] = None
        self._subscriptions: set = set()

        # Signing
        self._account = None
        self._api_key = None
        self._api_secret = None
        self._api_passphrase = None
        self._nonce = 0

    async def connect(self) -> bool:
        """Connect to Polymarket APIs"""
        try:
            self._http = httpx.AsyncClient(timeout=10.0)

            # Initialize account for signing if credentials provided
            if self.private_key:
                try:
                    from eth_account import Account

                    # Handle key with or without 0x prefix
                    key = self.private_key
                    if not key.startswith("0x"):
                        key = "0x" + key

                    self._account = Account.from_key(key)

                    # Derive API credentials
                    await self._derive_api_credentials()

                    logger.info(f"Polymarket initialized for address: {self._account.address}")
                except Exception as e:
                    logger.warning(f"Account init failed (read-only mode): {e}")
                    self._account = None

            # Start WebSocket connection
            self._ws_task = asyncio.create_task(self._ws_loop())

            self._connected = True
            logger.info("Polymarket connected")
            return True

        except Exception as e:
            logger.error(f"Polymarket connect failed: {e}")
            return False

    async def _derive_api_credentials(self):
        """Derive API credentials using EIP-712 Level 1 authentication"""
        if not self._account:
            return

        try:
            from eth_account.messages import encode_typed_data

            # Generate timestamp and nonce for auth
            timestamp = str(int(time.time()))
            nonce = 0

            # Create EIP-712 auth message
            auth_message = {
                "address": self._account.address,
                "timestamp": timestamp,
                "nonce": nonce,
                "message": CLOB_AUTH_MSG,
            }

            typed_data = {
                "types": CLOB_AUTH_TYPES,
                "primaryType": "ClobAuth",
                "domain": CLOB_AUTH_DOMAIN,
                "message": auth_message,
            }

            # Sign the auth message
            signed = self._account.sign_message(encode_typed_data(full_message=typed_data))
            signature = "0x" + signed.signature.hex()

            # Level 1 headers
            headers = {
                "POLY_ADDRESS": self._account.address,
                "POLY_SIGNATURE": signature,
                "POLY_TIMESTAMP": timestamp,
                "POLY_NONCE": str(nonce),
            }

            # Try to create or derive API key
            resp = await self._http.get(
                f"{self.api_url}/auth/api-key",
                headers=headers,
            )

            if resp.status_code == 200:
                creds = resp.json()
                self._api_key = creds.get("apiKey")
                self._api_secret = creds.get("secret")
                self._api_passphrase = creds.get("passphrase")
                logger.info("Polymarket API credentials derived successfully")
            elif resp.status_code == 401:
                # Need to create new API key
                resp = await self._http.post(
                    f"{self.api_url}/auth/api-key",
                    headers=headers,
                )
                if resp.status_code == 200:
                    creds = resp.json()
                    self._api_key = creds.get("apiKey")
                    self._api_secret = creds.get("secret")
                    self._api_passphrase = creds.get("passphrase")
                    logger.info("Polymarket API credentials created successfully")
                else:
                    logger.warning(f"Failed to create API creds: {resp.status_code} {resp.text[:100]}")
            else:
                logger.debug(f"API creds not available (paper trading mode): {resp.status_code}")

        except Exception as e:
            logger.debug(f"API credential derivation skipped: {e}")

    async def disconnect(self):
        """Disconnect from Polymarket"""
        self._connected = False

        if self._ws_task:
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass

        if self._ws:
            await self._ws.close()

        if self._http:
            await self._http.aclose()

        logger.info("Polymarket disconnected")

    async def _ws_loop(self):
        """WebSocket message loop"""
        while self._connected:
            try:
                async with websockets.connect(self.ws_url) as ws:
                    self._ws = ws
                    logger.info("Polymarket WebSocket connected")

                    # Resubscribe to all markets
                    for market_id in self._subscriptions:
                        await self._send_subscribe(market_id)

                    async for message in ws:
                        await self._handle_ws_message(message)

            except ConnectionClosed:
                logger.warning("Polymarket WebSocket disconnected, reconnecting...")
                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"Polymarket WebSocket error: {e}")
                await asyncio.sleep(1)

    async def _send_subscribe(self, asset_id: str):
        """Send subscription message"""
        if self._ws:
            msg = {
                "type": "subscribe",
                "channel": "book",
                "asset_id": asset_id,
            }
            await self._ws.send(json.dumps(msg))

    async def _handle_ws_message(self, raw_message: str):
        """Handle incoming WebSocket message"""
        try:
            start = time.time()
            data = json.loads(raw_message)

            if data.get("type") == "book":
                asset_id = data.get("asset_id")
                if asset_id:
                    bids = [(float(b["price"]), float(b["size"])) for b in data.get("bids", [])]
                    asks = [(float(a["price"]), float(a["size"])) for a in data.get("asks", [])]

                    orderbook = OrderBook(
                        market_id=asset_id,
                        platform=self.name,
                        bids=sorted(bids, key=lambda x: -x[0]),
                        asks=sorted(asks, key=lambda x: x[0]),
                    )

                    self._orderbooks[asset_id] = orderbook
                    self._last_latency_ms = int((time.time() - start) * 1000)
                    self._notify_orderbook_update(orderbook)

        except Exception as e:
            logger.debug(f"WS message parse error: {e}")

    async def load_markets(self, keywords: List[str] = None) -> List[Market]:
        """Load active markets from Gamma API (more reliable than CLOB)"""
        markets = []

        try:
            # Use Gamma API for market discovery - has better active market data
            resp = await self._http.get(
                f"{self.gamma_url}/markets",
                params={"closed": "false", "limit": 200},
            )
            data = resp.json()

            for m in data:
                # clobTokenIds is a JSON string, needs parsing
                tokens_raw = m.get("clobTokenIds", "[]")
                try:
                    tokens = json.loads(tokens_raw) if isinstance(tokens_raw, str) else tokens_raw
                except:
                    tokens = []

                # Must have token IDs for trading
                if not tokens or len(tokens) < 2:
                    continue

                # Check liquidity - skip illiquid markets
                liquidity = float(m.get("liquidity", 0) or 0)
                if liquidity < 100:  # At least $100 liquidity
                    continue

                question = m.get("question", "").lower()

                # Filter by keywords if provided
                if keywords:
                    if not any(kw.lower() in question for kw in keywords):
                        continue

                market = Market(
                    id=m.get("conditionId", ""),
                    platform=self.name,
                    question=m.get("question", ""),
                    category=m.get("groupItemTitle", "") or m.get("category", ""),
                    end_date=None,
                    yes_token_id=tokens[0] if len(tokens) > 0 else None,
                    no_token_id=tokens[1] if len(tokens) > 1 else None,
                    volume=float(m.get("volume", 0) or 0),
                    liquidity=liquidity,
                    metadata=m,
                )
                markets.append(market)
                self._markets[market.id] = market

            # Sort by liquidity descending
            markets.sort(key=lambda x: x.liquidity, reverse=True)
            logger.info(f"Polymarket: loaded {len(markets)} active markets")

        except Exception as e:
            logger.error(f"Polymarket load_markets error: {e}")

        return markets

    async def subscribe_orderbook(self, market_id: str):
        """Subscribe to orderbook updates"""
        market = self._markets.get(market_id)
        if not market:
            return

        # Subscribe to both YES and NO tokens
        if market.yes_token_id:
            self._subscriptions.add(market.yes_token_id)
            await self._send_subscribe(market.yes_token_id)

        if market.no_token_id:
            self._subscriptions.add(market.no_token_id)
            await self._send_subscribe(market.no_token_id)

    async def get_orderbook(self, market_id: str) -> Optional[OrderBook]:
        """Get orderbook via REST (fallback)"""
        market = self._markets.get(market_id)
        if not market:
            return None

        try:
            start = time.time()
            resp = await self._http.get(
                f"{self.api_url}/book",
                params={"token_id": market.yes_token_id},
            )
            data = resp.json()

            bids = [(float(b["price"]), float(b["size"])) for b in data.get("bids", [])]
            asks = [(float(a["price"]), float(a["size"])) for a in data.get("asks", [])]

            self._last_latency_ms = int((time.time() - start) * 1000)

            return OrderBook(
                market_id=market_id,
                platform=self.name,
                bids=sorted(bids, key=lambda x: -x[0]),
                asks=sorted(asks, key=lambda x: x[0]),
            )

        except Exception as e:
            logger.error(f"get_orderbook error: {e}")
            return None

    async def place_order(self, order: Order) -> Optional[Fill]:
        """Place order via CLOB API with EIP-712 signature"""
        if not self._account or not self._api_key:
            logger.error("Not authenticated - cannot place orders")
            return None

        market = self._markets.get(order.market_id)
        if not market:
            logger.error(f"Market not found: {order.market_id}")
            return None

        try:
            token_id = market.yes_token_id if order.outcome == "YES" else market.no_token_id
            side = 0 if order.side == "BUY" else 1  # 0=BUY, 1=SELL

            start = time.time()

            # Build order
            self._nonce += 1
            salt = secrets.randbits(128)

            # Convert price/size to contract amounts (6 decimals for USDC)
            price_decimal = order.price
            size_decimal = order.size

            if side == 0:  # BUY
                maker_amount = int(size_decimal * price_decimal * 1e6)  # USDC to pay
                taker_amount = int(size_decimal * 1e6)  # Tokens to receive
            else:  # SELL
                maker_amount = int(size_decimal * 1e6)  # Tokens to sell
                taker_amount = int(size_decimal * price_decimal * 1e6)  # USDC to receive

            order_data = {
                "salt": salt,
                "maker": self.funder_address or self._account.address,
                "signer": self._account.address,
                "taker": "0x0000000000000000000000000000000000000000",
                "tokenId": int(token_id),
                "makerAmount": maker_amount,
                "takerAmount": taker_amount,
                "expiration": 0,  # No expiration
                "nonce": self._nonce,
                "feeRateBps": 0,
                "side": side,
                "signatureType": 0,
            }

            # Sign the order using EIP-712
            signature = self._sign_order(order_data)

            # Build API request
            order_payload = {
                "order": {
                    "salt": str(salt),
                    "maker": order_data["maker"],
                    "signer": order_data["signer"],
                    "taker": order_data["taker"],
                    "tokenId": str(token_id),
                    "makerAmount": str(maker_amount),
                    "takerAmount": str(taker_amount),
                    "expiration": "0",
                    "nonce": str(self._nonce),
                    "feeRateBps": "0",
                    "side": "BUY" if side == 0 else "SELL",
                    "signatureType": 0,
                    "signature": signature,
                },
                "orderType": "FOK" if order.order_type == "FOK" else "GTC",
            }

            # Get auth headers
            headers = self._get_auth_headers("POST", "/order")
            headers["Content-Type"] = "application/json"

            resp = await self._http.post(
                f"{self.api_url}/order",
                json=order_payload,
                headers=headers,
            )

            latency_ms = int((time.time() - start) * 1000)
            self._last_latency_ms = latency_ms

            if resp.status_code == 200:
                result = resp.json()
                return Fill(
                    order_id=result.get("orderID", ""),
                    market_id=order.market_id,
                    platform=self.name,
                    side=order.side,
                    outcome=order.outcome,
                    price=order.price,
                    size=order.size,
                    fee=order.size * order.price * 0.01,
                    timestamp=datetime.utcnow(),
                    latency_ms=latency_ms,
                )
            else:
                logger.error(f"Order failed: {resp.status_code} {resp.text}")

        except Exception as e:
            logger.error(f"place_order error: {e}")

        return None

    def _sign_order(self, order_data: dict) -> str:
        """Sign order using EIP-712"""
        from eth_account.messages import encode_typed_data

        typed_data = {
            "types": ORDER_TYPES,
            "primaryType": "Order",
            "domain": ORDER_DOMAIN,
            "message": order_data,
        }

        signed = self._account.sign_message(encode_typed_data(full_message=typed_data))
        return "0x" + signed.signature.hex()

    def _get_auth_headers(self, method: str, path: str) -> dict:
        """Get authenticated headers for CLOB API"""
        if not self._api_key:
            return {}

        import hmac
        import hashlib
        import base64

        timestamp = str(int(time.time() * 1000))
        message = f"{timestamp}{method}{path}"

        signature = hmac.new(
            base64.b64decode(self._api_secret),
            message.encode(),
            hashlib.sha256
        ).digest()

        return {
            "POLY_ADDRESS": self._account.address,
            "POLY_SIGNATURE": base64.b64encode(signature).decode(),
            "POLY_TIMESTAMP": timestamp,
            "POLY_API_KEY": self._api_key,
            "POLY_PASSPHRASE": self._api_passphrase,
        }

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an order"""
        if not self._api_key:
            return False

        try:
            headers = self._get_auth_headers("DELETE", f"/order/{order_id}")
            resp = await self._http.delete(
                f"{self.api_url}/order/{order_id}",
                headers=headers,
            )
            return resp.status_code == 200
        except Exception as e:
            logger.error(f"cancel_order error: {e}")
            return False

    async def get_balance(self) -> float:
        """Get USDC balance from wallet"""
        if not self.funder_address and not self._account:
            return 0.0

        try:
            address = self.funder_address or self._account.address
            # Query balance from Polymarket data API
            resp = await self._http.get(
                f"https://data-api.polymarket.com/balance",
                params={"address": address},
            )
            if resp.status_code == 200:
                data = resp.json()
                return float(data.get("balance", 0))
        except Exception:
            pass
        return 0.0

    async def get_positions(self) -> List[Dict]:
        """Get open positions"""
        if not self.funder_address:
            return []

        try:
            resp = await self._http.get(
                f"https://data-api.polymarket.com/positions",
                params={"user": self.funder_address},
            )
            return resp.json()
        except Exception:
            return []
