"""
Betfair Platform Connector
Streaming API for real-time orderbook updates
"""

import asyncio
import json
import time
import logging
import ssl
from datetime import datetime
from typing import Dict, List, Optional
import httpx

from .base import BasePlatform, OrderBook, Market, Order, Fill

logger = logging.getLogger(__name__)


class BetfairConnector(BasePlatform):
    """
    Betfair Exchange connector

    Note: Betfair requires:
    1. API key (app key)
    2. Username/password login
    3. SSL certificate for streaming API

    Sign up at https://developer.betfair.com
    """

    def __init__(
        self,
        app_key: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        cert_path: Optional[str] = None,
    ):
        super().__init__("betfair")
        self.app_key = app_key
        self.username = username
        self.password = password
        self.cert_path = cert_path

        self.api_url = "https://api.betfair.com/exchange/betting/rest/v1.0"
        self.auth_url = "https://identitysso-cert.betfair.com/api/certlogin"
        self.stream_url = "stream-api.betfair.com"
        self.stream_port = 443

        self._http: Optional[httpx.AsyncClient] = None
        self._session_token: Optional[str] = None
        self._stream_reader = None
        self._stream_writer = None
        self._stream_task: Optional[asyncio.Task] = None
        self._subscriptions: Dict[str, set] = {}  # market_id -> set of selection_ids

    async def connect(self) -> bool:
        """Connect to Betfair APIs"""
        try:
            self._http = httpx.AsyncClient(timeout=10.0)

            # Authenticate if credentials provided
            if self.app_key and self.username and self.password:
                await self._authenticate()

            if self._session_token:
                # Start streaming connection
                self._stream_task = asyncio.create_task(self._stream_loop())

            self._connected = True
            logger.info("Betfair connected")
            return True

        except Exception as e:
            logger.error(f"Betfair connect failed: {e}")
            return False

    async def _authenticate(self):
        """Authenticate with Betfair"""
        try:
            # Cert-based login (requires .pem certificate)
            if self.cert_path:
                resp = await self._http.post(
                    self.auth_url,
                    data={"username": self.username, "password": self.password},
                    headers={"X-Application": self.app_key},
                    cert=self.cert_path,
                )
            else:
                # Non-cert login (interactive)
                resp = await self._http.post(
                    "https://identitysso.betfair.com/api/login",
                    data={"username": self.username, "password": self.password},
                    headers={"X-Application": self.app_key, "Content-Type": "application/x-www-form-urlencoded"},
                )

            if resp.status_code == 200:
                data = resp.json()
                self._session_token = data.get("sessionToken") or data.get("token")
                if self._session_token:
                    logger.info("Betfair authenticated")
                else:
                    logger.warning(f"Betfair auth response: {data}")
            else:
                logger.warning(f"Betfair auth failed: {resp.status_code}")

        except Exception as e:
            logger.error(f"Betfair auth error: {e}")

    async def disconnect(self):
        """Disconnect from Betfair"""
        self._connected = False

        if self._stream_task:
            self._stream_task.cancel()
            try:
                await self._stream_task
            except asyncio.CancelledError:
                pass

        if self._stream_writer:
            self._stream_writer.close()
            await self._stream_writer.wait_closed()

        if self._http:
            await self._http.aclose()

        logger.info("Betfair disconnected")

    async def _stream_loop(self):
        """Streaming API loop"""
        while self._connected:
            try:
                ssl_context = ssl.create_default_context()

                reader, writer = await asyncio.open_connection(
                    self.stream_url,
                    self.stream_port,
                    ssl=ssl_context,
                )

                self._stream_reader = reader
                self._stream_writer = writer

                # Authenticate stream
                auth_msg = {
                    "op": "authentication",
                    "appKey": self.app_key,
                    "session": self._session_token,
                }
                writer.write((json.dumps(auth_msg) + "\r\n").encode())
                await writer.drain()

                logger.info("Betfair stream connected")

                # Resubscribe
                for market_id, selections in self._subscriptions.items():
                    await self._send_market_subscription(market_id, selections)

                # Read messages
                while self._connected:
                    line = await reader.readline()
                    if not line:
                        break
                    await self._handle_stream_message(line.decode().strip())

            except Exception as e:
                logger.error(f"Betfair stream error: {e}")
                await asyncio.sleep(1)

    async def _send_market_subscription(self, market_id: str, selections: set):
        """Subscribe to market updates"""
        if self._stream_writer:
            msg = {
                "op": "marketSubscription",
                "id": 1,
                "marketFilter": {"marketIds": [market_id]},
                "marketDataFilter": {
                    "ladderLevels": 3,
                    "fields": ["EX_BEST_OFFERS", "EX_TRADED"],
                },
            }
            self._stream_writer.write((json.dumps(msg) + "\r\n").encode())
            await self._stream_writer.drain()

    async def _handle_stream_message(self, raw_message: str):
        """Handle streaming message"""
        try:
            start = time.time()
            data = json.loads(raw_message)

            if data.get("op") == "mcm":
                for mc in data.get("mc", []):
                    market_id = mc.get("id")
                    for rc in mc.get("rc", []):
                        selection_id = rc.get("id")

                        # Parse ladder (back/lay prices)
                        batb = rc.get("batb", [])  # Best available to back
                        batl = rc.get("batl", [])  # Best available to lay

                        bids = [(1 / b[1], b[2]) for b in batb if len(b) >= 3 and b[1] > 1]
                        asks = [(1 / a[1], a[2]) for a in batl if len(a) >= 3 and a[1] > 1]

                        orderbook = OrderBook(
                            market_id=f"{market_id}:{selection_id}",
                            platform=self.name,
                            bids=sorted(bids, key=lambda x: -x[0]),
                            asks=sorted(asks, key=lambda x: x[0]),
                        )

                        self._orderbooks[orderbook.market_id] = orderbook
                        self._last_latency_ms = int((time.time() - start) * 1000)
                        self._notify_orderbook_update(orderbook)

        except Exception as e:
            logger.debug(f"Betfair stream parse error: {e}")

    async def load_markets(self, keywords: List[str] = None) -> List[Market]:
        """Load markets from Betfair API"""
        markets = []

        if not self._session_token:
            logger.warning("Betfair not authenticated - using demo data")
            return markets

        try:
            headers = {
                "X-Application": self.app_key,
                "X-Authentication": self._session_token,
                "Content-Type": "application/json",
            }

            # Search for prediction/politics markets
            event_types = ["7"]  # Politics event type ID

            if keywords:
                # Search by keyword
                for keyword in keywords[:5]:  # Limit keyword searches
                    body = {
                        "filter": {
                            "textQuery": keyword,
                            "marketTypeCodes": ["WIN"],
                            "inPlayOnly": False,
                        },
                        "maxResults": 100,
                        "marketProjection": ["MARKET_DESCRIPTION", "RUNNER_DESCRIPTION"],
                    }

                    resp = await self._http.post(
                        f"{self.api_url}/listMarketCatalogue/",
                        headers=headers,
                        json=body,
                    )

                    if resp.status_code == 200:
                        for m in resp.json():
                            market = self._parse_market(m)
                            if market:
                                markets.append(market)
                                self._markets[market.id] = market
            else:
                # Get all politics markets
                body = {
                    "filter": {
                        "eventTypeIds": event_types,
                        "inPlayOnly": False,
                    },
                    "maxResults": 200,
                    "marketProjection": ["MARKET_DESCRIPTION", "RUNNER_DESCRIPTION"],
                }

                resp = await self._http.post(
                    f"{self.api_url}/listMarketCatalogue/",
                    headers=headers,
                    json=body,
                )

                if resp.status_code == 200:
                    for m in resp.json():
                        market = self._parse_market(m)
                        if market:
                            markets.append(market)
                            self._markets[market.id] = market

            logger.info(f"Betfair: loaded {len(markets)} markets")

        except Exception as e:
            logger.error(f"Betfair load_markets error: {e}")

        return markets

    def _parse_market(self, data: Dict) -> Optional[Market]:
        """Parse Betfair market data"""
        try:
            runners = data.get("runners", [])
            if len(runners) < 2:
                return None

            return Market(
                id=data.get("marketId", ""),
                platform=self.name,
                question=data.get("marketName", ""),
                category=data.get("description", {}).get("marketType", ""),
                end_date=None,
                volume=0,  # Would need separate API call
                liquidity=0,
                metadata={
                    "runners": runners,
                    "description": data.get("description", {}),
                },
            )
        except Exception:
            return None

    async def subscribe_orderbook(self, market_id: str):
        """Subscribe to orderbook updates"""
        market = self._markets.get(market_id)
        if not market:
            return

        runners = market.metadata.get("runners", [])
        selection_ids = {r.get("selectionId") for r in runners}

        self._subscriptions[market_id] = selection_ids
        await self._send_market_subscription(market_id, selection_ids)

    async def get_orderbook(self, market_id: str) -> Optional[OrderBook]:
        """Get orderbook via REST"""
        if not self._session_token:
            return None

        try:
            start = time.time()
            headers = {
                "X-Application": self.app_key,
                "X-Authentication": self._session_token,
                "Content-Type": "application/json",
            }

            body = {
                "marketIds": [market_id],
                "priceProjection": {
                    "priceData": ["EX_BEST_OFFERS"],
                    "exBestOffersOverrides": {"bestPricesDepth": 3},
                },
            }

            resp = await self._http.post(
                f"{self.api_url}/listMarketBook/",
                headers=headers,
                json=body,
            )

            self._last_latency_ms = int((time.time() - start) * 1000)

            if resp.status_code == 200:
                data = resp.json()
                if data:
                    market_book = data[0]
                    runners = market_book.get("runners", [])

                    if runners:
                        # Get first runner (YES equivalent)
                        runner = runners[0]
                        ex = runner.get("ex", {})

                        back = ex.get("availableToBack", [])
                        lay = ex.get("availableToLay", [])

                        bids = [(1 / b["price"], b["size"]) for b in back if b["price"] > 1]
                        asks = [(1 / a["price"], a["size"]) for a in lay if a["price"] > 1]

                        return OrderBook(
                            market_id=market_id,
                            platform=self.name,
                            bids=sorted(bids, key=lambda x: -x[0]),
                            asks=sorted(asks, key=lambda x: x[0]),
                        )

        except Exception as e:
            logger.error(f"Betfair get_orderbook error: {e}")

        return None

    async def place_order(self, order: Order) -> Optional[Fill]:
        """Place order on Betfair"""
        if not self._session_token:
            logger.error("Betfair not authenticated")
            return None

        try:
            start = time.time()
            headers = {
                "X-Application": self.app_key,
                "X-Authentication": self._session_token,
                "Content-Type": "application/json",
            }

            market = self._markets.get(order.market_id)
            if not market:
                return None

            runners = market.metadata.get("runners", [])
            if not runners:
                return None

            selection_id = runners[0].get("selectionId")

            # Convert to Betfair odds (1/probability)
            odds = 1 / order.price if order.price > 0 else 1000

            # Betfair: BACK = buy YES, LAY = sell YES
            side = "BACK" if (order.side == "BUY" and order.outcome == "YES") or \
                           (order.side == "SELL" and order.outcome == "NO") else "LAY"

            body = {
                "marketId": order.market_id,
                "instructions": [{
                    "selectionId": selection_id,
                    "side": side,
                    "orderType": "LIMIT",
                    "limitOrder": {
                        "size": order.size,
                        "price": round(odds, 2),
                        "persistenceType": "LAPSE",
                        "timeInForce": "FILL_OR_KILL" if order.order_type == "FOK" else None,
                    },
                }],
            }

            resp = await self._http.post(
                f"{self.api_url}/placeOrders/",
                headers=headers,
                json=body,
            )

            latency_ms = int((time.time() - start) * 1000)
            self._last_latency_ms = latency_ms

            if resp.status_code == 200:
                result = resp.json()
                if result.get("status") == "SUCCESS":
                    instruction_report = result.get("instructionReports", [{}])[0]
                    return Fill(
                        order_id=instruction_report.get("betId", ""),
                        market_id=order.market_id,
                        platform=self.name,
                        side=order.side,
                        outcome=order.outcome,
                        price=order.price,
                        size=instruction_report.get("sizeMatched", order.size),
                        fee=0,  # Betfair fee is on profit
                        timestamp=datetime.utcnow(),
                        latency_ms=latency_ms,
                    )

        except Exception as e:
            logger.error(f"Betfair place_order error: {e}")

        return None

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an order"""
        # Betfair cancel requires market ID - would need to track this
        return False

    async def get_balance(self) -> float:
        """Get account balance"""
        if not self._session_token:
            return 0.0

        try:
            headers = {
                "X-Application": self.app_key,
                "X-Authentication": self._session_token,
            }

            resp = await self._http.get(
                "https://api.betfair.com/exchange/account/rest/v1.0/getAccountFunds/",
                headers=headers,
            )

            if resp.status_code == 200:
                data = resp.json()
                return float(data.get("availableToBetBalance", 0))

        except Exception:
            pass
        return 0.0

    async def get_positions(self) -> List[Dict]:
        """Get open positions (current bets)"""
        if not self._session_token:
            return []

        try:
            headers = {
                "X-Application": self.app_key,
                "X-Authentication": self._session_token,
                "Content-Type": "application/json",
            }

            body = {
                "betStatus": "UNMATCHED",
            }

            resp = await self._http.post(
                f"{self.api_url}/listCurrentOrders/",
                headers=headers,
                json=body,
            )

            if resp.status_code == 200:
                return resp.json().get("currentOrders", [])

        except Exception:
            pass
        return []
