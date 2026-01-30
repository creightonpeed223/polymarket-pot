"""
Kalshi Platform Connector
WebSocket-based real-time orderbook streaming
Uses RSA-PSS authentication as required by Kalshi API
"""

import asyncio
import base64
import json
import time
import logging
from datetime import datetime
from typing import Dict, List, Optional
import httpx
import websockets
from websockets.exceptions import ConnectionClosed

from .base import BasePlatform, OrderBook, Market, Order, Fill

logger = logging.getLogger(__name__)


class KalshiConnector(BasePlatform):
    """
    Kalshi connector with WebSocket orderbook streaming

    Authentication uses RSA-PSS signatures.
    Generate keys at: https://kalshi.com -> Settings -> API Keys

    Required env vars:
        KALSHI_API_KEY: Your API key ID
        KALSHI_PRIVATE_KEY: Your RSA private key (PEM format)
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        private_key_path: Optional[str] = None,
    ):
        super().__init__("kalshi")
        self.api_key = api_key
        self.private_key_path = private_key_path
        self._private_key = None

        self.api_url = "https://api.elections.kalshi.com/trade-api/v2"
        self.ws_url = "wss://api.elections.kalshi.com/trade-api/ws/v2"

        self._http: Optional[httpx.AsyncClient] = None
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._ws_task: Optional[asyncio.Task] = None
        self._subscriptions: set = set()
        self._member_id: Optional[str] = None

    def _load_private_key(self):
        """Load RSA private key from file or string"""
        if self._private_key:
            return self._private_key

        if not self.private_key_path:
            return None

        try:
            from cryptography.hazmat.primitives import serialization

            # Check if it's a file path or the key itself
            if self.private_key_path.startswith("-----BEGIN"):
                key_data = self.private_key_path.encode()
            else:
                with open(self.private_key_path, "rb") as f:
                    key_data = f.read()

            self._private_key = serialization.load_pem_private_key(key_data, password=None)
            return self._private_key
        except Exception as e:
            logger.error(f"Failed to load Kalshi private key: {e}")
            return None

    def _sign_request(self, timestamp: str, method: str, path: str) -> Optional[str]:
        """Sign request using RSA-PSS"""
        private_key = self._load_private_key()
        if not private_key:
            return None

        try:
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.asymmetric import padding

            # Message format: timestamp + method + path (no query params)
            path_without_query = path.split("?")[0]
            message = f"{timestamp}{method}{path_without_query}"

            signature = private_key.sign(
                message.encode("utf-8"),
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.MAX_LENGTH
                ),
                hashes.SHA256()
            )

            return base64.b64encode(signature).decode("utf-8")
        except Exception as e:
            logger.error(f"Kalshi signature failed: {e}")
            return None

    def _get_auth_headers(self, method: str, path: str) -> Dict[str, str]:
        """Generate authentication headers with RSA-PSS signature"""
        if not self.api_key or not self.private_key_path:
            return {}

        timestamp = str(int(time.time() * 1000))
        signature = self._sign_request(timestamp, method, path)

        if not signature:
            return {}

        return {
            "KALSHI-ACCESS-KEY": self.api_key,
            "KALSHI-ACCESS-SIGNATURE": signature,
            "KALSHI-ACCESS-TIMESTAMP": timestamp,
        }

    async def connect(self) -> bool:
        """Connect to Kalshi APIs"""
        try:
            self._http = httpx.AsyncClient(timeout=10.0)

            # Verify credentials if provided
            if self.api_key and self.private_key_path:
                try:
                    headers = self._get_auth_headers("GET", "/trade-api/v2/portfolio/balance")
                    resp = await self._http.get(
                        f"{self.api_url}/portfolio/balance",
                        headers=headers,
                    )
                    if resp.status_code == 200:
                        logger.info("Kalshi authenticated")
                    else:
                        logger.warning(f"Kalshi auth check failed: {resp.status_code}")
                except Exception as e:
                    logger.warning(f"Kalshi auth failed: {e}")

            # Start WebSocket connection
            self._ws_task = asyncio.create_task(self._ws_loop())

            self._connected = True
            logger.info("Kalshi connected")
            return True

        except Exception as e:
            logger.error(f"Kalshi connect failed: {e}")
            return False

    async def disconnect(self):
        """Disconnect from Kalshi"""
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

        logger.info("Kalshi disconnected")

    async def _ws_loop(self):
        """WebSocket message loop"""
        # Skip WebSocket if not authenticated
        if not self.api_key or not self.private_key_path:
            logger.debug("Kalshi WebSocket skipped - no credentials")
            return

        while self._connected:
            try:
                # Build auth params for WebSocket using RSA-PSS
                ws_url = self.ws_url
                if self.api_key and self.private_key_path:
                    timestamp = str(int(time.time() * 1000))
                    signature = self._sign_request(timestamp, "GET", "/trade-api/ws/v2")
                    if signature:
                        ws_url = f"{self.ws_url}?api_key={self.api_key}&timestamp={timestamp}&signature={signature}"

                async with websockets.connect(ws_url) as ws:
                    self._ws = ws
                    logger.info("Kalshi WebSocket connected")

                    # Resubscribe to all markets
                    for ticker in self._subscriptions:
                        await self._send_subscribe(ticker)

                    async for message in ws:
                        await self._handle_ws_message(message)

            except ConnectionClosed:
                logger.warning("Kalshi WebSocket disconnected, reconnecting...")
                await asyncio.sleep(5)
            except Exception as e:
                if "401" in str(e):
                    logger.debug("Kalshi WebSocket auth failed - skipping WebSocket")
                    return  # Stop trying if auth fails
                logger.error(f"Kalshi WebSocket error: {e}")
                await asyncio.sleep(5)

    async def _send_subscribe(self, ticker: str):
        """Send subscription message"""
        if self._ws:
            msg = {
                "id": 1,
                "cmd": "subscribe",
                "params": {
                    "channels": ["orderbook_delta"],
                    "market_tickers": [ticker],
                }
            }
            await self._ws.send(json.dumps(msg))

    async def _handle_ws_message(self, raw_message: str):
        """Handle incoming WebSocket message"""
        try:
            start = time.time()
            data = json.loads(raw_message)

            msg_type = data.get("type")

            if msg_type == "orderbook_snapshot" or msg_type == "orderbook_delta":
                ticker = data.get("msg", {}).get("market_ticker")
                if ticker:
                    msg = data.get("msg", {})

                    # Parse orderbook
                    yes_bids = [(float(b[0]) / 100, int(b[1])) for b in msg.get("yes", []) if b[0] > 0]
                    no_bids = [(float(b[0]) / 100, int(b[1])) for b in msg.get("no", []) if b[0] > 0]

                    # Convert to standard format (Kalshi uses cents)
                    bids = sorted(yes_bids, key=lambda x: -x[0])
                    asks = [(1 - b[0], b[1]) for b in sorted(no_bids, key=lambda x: x[0])]

                    orderbook = OrderBook(
                        market_id=ticker,
                        platform=self.name,
                        bids=bids,
                        asks=asks,
                    )

                    self._orderbooks[ticker] = orderbook
                    self._last_latency_ms = int((time.time() - start) * 1000)
                    self._notify_orderbook_update(orderbook)

        except Exception as e:
            logger.debug(f"Kalshi WS parse error: {e}")

    async def load_markets(self, keywords: List[str] = None) -> List[Market]:
        """Load markets from Kalshi API - get events first, then fetch markets by event"""
        markets = []
        prediction_categories = {"Politics", "World", "Science and Technology",
                                 "Financials", "Climate and Weather", "Social", "Entertainment"}

        try:
            # Get events first (prediction market events, not sports)
            event_tickers = []
            cursor = None

            while len(event_tickers) < 200:  # Get up to 200 events
                params = {"limit": 100, "status": "open"}
                if cursor:
                    params["cursor"] = cursor

                resp = await self._http.get(f"{self.api_url}/events", params=params)
                data = resp.json()

                for e in data.get("events", []):
                    cat = e.get("category", "")
                    if cat in prediction_categories:
                        ticker = e.get("event_ticker")
                        if ticker:
                            event_tickers.append((ticker, cat))

                cursor = data.get("cursor")
                if not cursor:
                    break

            # Batch fetch markets for each event (limit concurrent requests)
            for event_ticker, category in event_tickers[:100]:  # Load up to 100 events
                try:
                    resp = await self._http.get(
                        f"{self.api_url}/markets",
                        params={"event_ticker": event_ticker, "limit": 20, "status": "open"}
                    )
                    market_data = resp.json()

                    for m in market_data.get("markets", []):
                        title = m.get("title", "")
                        question = title.lower()

                        # Filter by keywords if provided
                        if keywords:
                            if not any(kw.lower() in question for kw in keywords):
                                continue

                        market = Market(
                            id=m.get("ticker", ""),
                            platform=self.name,
                            question=title,
                            category=category,
                            end_date=None,
                            volume=float(m.get("volume", 0) or 0),
                            liquidity=float(m.get("open_interest", 0) or 0),
                            metadata=m,
                        )
                        markets.append(market)
                        self._markets[market.id] = market

                except Exception as e:
                    logger.debug(f"Error fetching markets for {event_ticker}: {e}")
                    continue

            logger.info(f"Kalshi: loaded {len(markets)} prediction markets")

        except Exception as e:
            logger.error(f"Kalshi load_markets error: {e}")

        return markets

    async def subscribe_orderbook(self, market_id: str):
        """Subscribe to orderbook updates"""
        self._subscriptions.add(market_id)
        await self._send_subscribe(market_id)

    async def get_orderbook(self, market_id: str) -> Optional[OrderBook]:
        """Get orderbook via REST"""
        try:
            start = time.time()
            resp = await self._http.get(f"{self.api_url}/markets/{market_id}/orderbook")
            data = resp.json()

            orderbook_data = data.get("orderbook", {})

            # Kalshi returns yes/no sides
            yes_bids = orderbook_data.get("yes", [])
            no_bids = orderbook_data.get("no", [])

            bids = [(float(b[0]) / 100, int(b[1])) for b in yes_bids]
            asks = [(1 - float(b[0]) / 100, int(b[1])) for b in no_bids]

            self._last_latency_ms = int((time.time() - start) * 1000)

            return OrderBook(
                market_id=market_id,
                platform=self.name,
                bids=sorted(bids, key=lambda x: -x[0]),
                asks=sorted(asks, key=lambda x: x[0]),
            )

        except Exception as e:
            logger.error(f"Kalshi get_orderbook error: {e}")
            return None

    async def place_order(self, order: Order) -> Optional[Fill]:
        """Place order on Kalshi"""
        if not self.api_key or not self.private_key_path:
            logger.error("Kalshi credentials not configured")
            return None

        try:
            start = time.time()

            # Kalshi uses cents for price
            price_cents = int(order.price * 100)

            body = {
                "ticker": order.market_id,
                "action": order.side.lower(),
                "side": "yes" if order.outcome == "YES" else "no",
                "type": "limit",
                "count": int(order.size),
                "yes_price": price_cents if order.outcome == "YES" else None,
                "no_price": price_cents if order.outcome == "NO" else None,
            }

            if order.order_type == "FOK":
                body["expiration_ts"] = int(time.time()) + 5  # 5 second expiry

            body_str = json.dumps(body)
            headers = self._get_auth_headers("POST", "/trade-api/v2/portfolio/orders", body_str)
            headers["Content-Type"] = "application/json"

            resp = await self._http.post(
                f"{self.api_url}/portfolio/orders",
                headers=headers,
                content=body_str,
            )

            latency_ms = int((time.time() - start) * 1000)
            self._last_latency_ms = latency_ms

            if resp.status_code == 200:
                result = resp.json()
                order_data = result.get("order", {})

                return Fill(
                    order_id=order_data.get("order_id", ""),
                    market_id=order.market_id,
                    platform=self.name,
                    side=order.side,
                    outcome=order.outcome,
                    price=order.price,
                    size=order.size,
                    fee=0,  # Kalshi fee is on profit, not trade
                    timestamp=datetime.utcnow(),
                    latency_ms=latency_ms,
                )
            else:
                logger.error(f"Kalshi order failed: {resp.status_code} {resp.text}")

        except Exception as e:
            logger.error(f"Kalshi place_order error: {e}")

        return None

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an order"""
        if not self.api_key or not self.private_key_path:
            return False

        try:
            headers = self._get_auth_headers("DELETE", f"/trade-api/v2/portfolio/orders/{order_id}")
            resp = await self._http.delete(
                f"{self.api_url}/portfolio/orders/{order_id}",
                headers=headers,
            )
            return resp.status_code == 200
        except Exception as e:
            logger.error(f"Kalshi cancel_order error: {e}")
            return False

    async def get_balance(self) -> float:
        """Get account balance"""
        if not self.api_key or not self.private_key_path:
            return 0.0

        try:
            headers = self._get_auth_headers("GET", "/trade-api/v2/portfolio/balance")
            resp = await self._http.get(
                f"{self.api_url}/portfolio/balance",
                headers=headers,
            )
            if resp.status_code == 200:
                data = resp.json()
                # Balance is in cents
                return float(data.get("balance", 0)) / 100
        except Exception:
            pass
        return 0.0

    async def get_positions(self) -> List[Dict]:
        """Get open positions"""
        if not self.api_key or not self.private_key_path:
            return []

        try:
            headers = self._get_auth_headers("GET", "/trade-api/v2/portfolio/positions")
            resp = await self._http.get(
                f"{self.api_url}/portfolio/positions",
                headers=headers,
            )
            if resp.status_code == 200:
                return resp.json().get("market_positions", [])
        except Exception:
            pass
        return []
