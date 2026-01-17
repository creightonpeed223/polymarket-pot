"""BTC price data fetcher using ccxt for multiple exchanges."""

import asyncio
from datetime import datetime, timezone
from typing import Optional

import ccxt.async_support as ccxt
import pandas as pd
import numpy as np

from ..config import get_settings, TIMEFRAME_MAP
from ..utils import get_data_logger

logger = get_data_logger()


class PriceFetcher:
    """Fetches BTC price data from cryptocurrency exchanges."""

    def __init__(self, exchange_id: str = "kraken"):
        """
        Initialize the price fetcher.

        Args:
            exchange_id: The exchange to use (default: kraken - works globally)
        """
        self.exchange_id = exchange_id
        self.exchange: Optional[ccxt.Exchange] = None
        self.symbol = "BTC/USD" if exchange_id == "kraken" else "BTC/USDT"
        self.settings = get_settings()
        self._price_cache: dict[str, pd.DataFrame] = {}
        self._last_price: Optional[float] = None

    async def initialize(self):
        """Initialize the exchange connection."""
        exchange_class = getattr(ccxt, self.exchange_id)
        self.exchange = exchange_class({
            "enableRateLimit": True,
            "options": {"defaultType": "spot"},
        })
        logger.info(f"Initialized {self.exchange_id} connection")

    async def close(self):
        """Close the exchange connection."""
        if self.exchange:
            await self.exchange.close()
            logger.info(f"Closed {self.exchange_id} connection")

    async def fetch_ohlcv(
        self,
        timeframe: str = "15m",
        limit: int = 500,
        since: Optional[int] = None,
    ) -> pd.DataFrame:
        """
        Fetch OHLCV (candlestick) data.

        Args:
            timeframe: Candle timeframe (1m, 5m, 15m, 1h, 4h, 1d)
            limit: Number of candles to fetch
            since: Start timestamp in milliseconds

        Returns:
            DataFrame with columns: timestamp, open, high, low, close, volume
        """
        if not self.exchange:
            await self.initialize()

        tf = TIMEFRAME_MAP.get(timeframe, timeframe)

        try:
            ohlcv = await self.exchange.fetch_ohlcv(
                self.symbol, tf, since=since, limit=limit
            )

            df = pd.DataFrame(
                ohlcv,
                columns=["timestamp", "open", "high", "low", "close", "volume"]
            )
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
            df.set_index("timestamp", inplace=True)

            # Update cache
            self._price_cache[timeframe] = df
            if len(df) > 0:
                self._last_price = df["close"].iloc[-1]

            logger.debug(f"Fetched {len(df)} candles for {timeframe}")
            return df

        except Exception as e:
            logger.error(f"Error fetching OHLCV data: {e}")
            raise

    async def fetch_current_price(self) -> dict:
        """
        Fetch the current BTC price.

        Returns:
            Dict with price info: {price, timestamp, bid, ask, change_24h}
        """
        if not self.exchange:
            await self.initialize()

        try:
            ticker = await self.exchange.fetch_ticker(self.symbol)

            price_data = {
                "price": ticker.get("last", 0),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "bid": ticker.get("bid", 0),
                "ask": ticker.get("ask", 0),
                "high_24h": ticker.get("high", 0),
                "low_24h": ticker.get("low", 0),
                "change_24h": ticker.get("percentage", 0),
                "volume_24h": ticker.get("quoteVolume", 0),
            }

            self._last_price = price_data["price"]
            return price_data

        except Exception as e:
            logger.error(f"Error fetching current price: {e}")
            raise

    async def fetch_multi_timeframe(
        self,
        timeframes: list[str] = ["5m", "15m", "1h", "4h"],
        limit: int = 100,
    ) -> dict[str, pd.DataFrame]:
        """
        Fetch OHLCV data for multiple timeframes concurrently.

        Args:
            timeframes: List of timeframes to fetch
            limit: Number of candles per timeframe

        Returns:
            Dict mapping timeframe to DataFrame
        """
        tasks = [
            self.fetch_ohlcv(tf, limit=limit)
            for tf in timeframes
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        data = {}
        for tf, result in zip(timeframes, results):
            if isinstance(result, Exception):
                logger.error(f"Error fetching {tf}: {result}")
            else:
                data[tf] = result

        return data

    def get_cached_data(self, timeframe: str) -> Optional[pd.DataFrame]:
        """Get cached price data for a timeframe."""
        return self._price_cache.get(timeframe)

    @property
    def last_price(self) -> Optional[float]:
        """Get the last known price."""
        return self._last_price


class PriceStreamManager:
    """Manages WebSocket price streaming for real-time updates."""

    def __init__(self):
        self.fetcher = PriceFetcher()
        self._running = False
        self._subscribers: list[asyncio.Queue] = []
        self._current_price: Optional[dict] = None

    async def start(self, interval: float = 1.0):
        """
        Start the price streaming loop.

        Args:
            interval: Update interval in seconds
        """
        await self.fetcher.initialize()
        self._running = True
        logger.info("Price stream started")

        while self._running:
            try:
                price_data = await self.fetcher.fetch_current_price()
                self._current_price = price_data

                # Notify all subscribers
                for queue in self._subscribers:
                    try:
                        queue.put_nowait(price_data)
                    except asyncio.QueueFull:
                        pass

                await asyncio.sleep(interval)

            except Exception as e:
                logger.error(f"Price stream error: {e}")
                await asyncio.sleep(5)

    async def stop(self):
        """Stop the price streaming loop."""
        self._running = False
        await self.fetcher.close()
        logger.info("Price stream stopped")

    def subscribe(self) -> asyncio.Queue:
        """
        Subscribe to price updates.

        Returns:
            Queue that will receive price updates
        """
        queue = asyncio.Queue(maxsize=100)
        self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue):
        """Unsubscribe from price updates."""
        if queue in self._subscribers:
            self._subscribers.remove(queue)

    @property
    def current_price(self) -> Optional[dict]:
        """Get the current price data."""
        return self._current_price


# Singleton instance for the price stream
_price_stream: Optional[PriceStreamManager] = None


def get_price_stream() -> PriceStreamManager:
    """Get the global price stream manager."""
    global _price_stream
    if _price_stream is None:
        _price_stream = PriceStreamManager()
    return _price_stream
