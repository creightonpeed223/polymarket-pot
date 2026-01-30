"""
Base platform connector interface
All platforms implement this interface
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Callable, Any
from datetime import datetime
import asyncio


@dataclass
class OrderBook:
    """Simplified orderbook snapshot"""
    market_id: str
    platform: str
    bids: List[tuple]  # [(price, size), ...]
    asks: List[tuple]  # [(price, size), ...]
    timestamp: datetime = field(default_factory=datetime.utcnow)

    @property
    def best_bid(self) -> Optional[float]:
        return self.bids[0][0] if self.bids else None

    @property
    def best_ask(self) -> Optional[float]:
        return self.asks[0][0] if self.asks else None

    @property
    def spread(self) -> Optional[float]:
        if self.best_bid and self.best_ask:
            return self.best_ask - self.best_bid
        return None

    @property
    def mid_price(self) -> Optional[float]:
        if self.best_bid and self.best_ask:
            return (self.best_bid + self.best_ask) / 2
        return None


@dataclass
class Market:
    """Unified market representation"""
    id: str
    platform: str
    question: str
    category: str
    end_date: Optional[datetime]
    yes_token_id: Optional[str] = None
    no_token_id: Optional[str] = None
    volume: float = 0.0
    liquidity: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Order:
    """Order representation"""
    market_id: str
    platform: str
    side: str  # "BUY" or "SELL"
    outcome: str  # "YES" or "NO"
    price: float
    size: float
    order_type: str = "FOK"  # FOK, GTC, IOC


@dataclass
class Fill:
    """Executed trade"""
    order_id: str
    market_id: str
    platform: str
    side: str
    outcome: str
    price: float
    size: float
    fee: float
    timestamp: datetime
    latency_ms: int


class BasePlatform(ABC):
    """Base class for all platform connectors"""

    def __init__(self, name: str):
        self.name = name
        self._connected = False
        self._orderbooks: Dict[str, OrderBook] = {}
        self._markets: Dict[str, Market] = {}
        self._callbacks: List[Callable] = []
        self._last_latency_ms: int = 0

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def latency_ms(self) -> int:
        return self._last_latency_ms

    def on_orderbook_update(self, callback: Callable[[OrderBook], None]):
        """Register callback for orderbook updates"""
        self._callbacks.append(callback)

    def _notify_orderbook_update(self, orderbook: OrderBook):
        """Notify all registered callbacks"""
        for callback in self._callbacks:
            try:
                callback(orderbook)
            except Exception:
                pass

    @abstractmethod
    async def connect(self) -> bool:
        """Connect to platform APIs"""
        pass

    @abstractmethod
    async def disconnect(self):
        """Disconnect from platform"""
        pass

    @abstractmethod
    async def load_markets(self, keywords: List[str] = None) -> List[Market]:
        """Load available markets, optionally filtered by keywords"""
        pass

    @abstractmethod
    async def subscribe_orderbook(self, market_id: str):
        """Subscribe to orderbook updates for a market"""
        pass

    @abstractmethod
    async def get_orderbook(self, market_id: str) -> Optional[OrderBook]:
        """Get current orderbook for a market"""
        pass

    @abstractmethod
    async def place_order(self, order: Order) -> Optional[Fill]:
        """Place an order, return fill if successful"""
        pass

    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an order"""
        pass

    @abstractmethod
    async def get_balance(self) -> float:
        """Get account balance"""
        pass

    @abstractmethod
    async def get_positions(self) -> List[Dict]:
        """Get open positions"""
        pass
