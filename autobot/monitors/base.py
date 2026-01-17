"""
Base classes for news monitors
"""

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, List, Callable, Any
from enum import Enum

from ..utils.logger import get_logger

logger = get_logger(__name__)


class EventType(Enum):
    """Types of news events"""
    COURT_RULING = "court_ruling"
    POLITICAL_NEWS = "political_news"
    REGULATORY_DECISION = "regulatory_decision"
    CANDIDATE_ANNOUNCEMENT = "candidate_announcement"
    EXECUTIVE_ORDER = "executive_order"
    LEGISLATION = "legislation"
    SEC_FILING = "sec_filing"
    FDA_APPROVAL = "fda_approval"
    TWITTER_ANNOUNCEMENT = "twitter_announcement"
    GENERAL_NEWS = "general_news"
    SPORTS_NEWS = "sports_news"
    SPORTS_INJURY = "sports_injury"
    SPORTS_TRADE = "sports_trade"
    SPORTS_RESULT = "sports_result"


@dataclass
class NewsEvent:
    """Represents a detected news event"""

    event_type: EventType
    headline: str
    content: str
    source_url: str
    source_name: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # Extracted data
    entities: List[str] = field(default_factory=list)  # People, orgs mentioned
    keywords: List[str] = field(default_factory=list)  # Key terms
    outcome: Optional[str] = None  # Detected outcome (e.g., "YES", "NO", "APPROVED")
    confidence: float = 0.0  # How confident we are in parsing

    # Market matching
    matched_market_id: Optional[str] = None
    matched_market_question: Optional[str] = None
    calculated_fair_value: Optional[float] = None
    current_market_price: Optional[float] = None
    edge: Optional[float] = None

    def __str__(self):
        return f"[{self.event_type.value}] {self.headline[:80]}"

    def to_dict(self) -> dict:
        return {
            "event_type": self.event_type.value,
            "headline": self.headline,
            "content": self.content,
            "source_url": self.source_url,
            "source_name": self.source_name,
            "timestamp": self.timestamp.isoformat(),
            "entities": self.entities,
            "keywords": self.keywords,
            "outcome": self.outcome,
            "confidence": self.confidence,
            "matched_market_id": self.matched_market_id,
            "matched_market_question": self.matched_market_question,
            "calculated_fair_value": self.calculated_fair_value,
            "current_market_price": self.current_market_price,
            "edge": self.edge,
        }


class NewsMonitor(ABC):
    """Base class for news source monitors"""

    def __init__(self, name: str, check_interval: int = 60):
        self.name = name
        self.check_interval = check_interval
        self._running = False
        self._last_check: Optional[datetime] = None
        self._seen_items: set = set()  # Track seen news to avoid duplicates
        self._callbacks: List[Callable[[NewsEvent], Any]] = []

    def on_event(self, callback: Callable[[NewsEvent], Any]):
        """Register callback for new events"""
        self._callbacks.append(callback)

    async def _notify(self, event: NewsEvent):
        """Notify all callbacks of new event"""
        for callback in self._callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(event)
                else:
                    callback(event)
            except Exception as e:
                logger.error(f"Callback error: {e}")

    @abstractmethod
    async def check(self) -> List[NewsEvent]:
        """
        Check source for new events
        Returns list of new events since last check
        """
        pass

    async def start(self):
        """Start monitoring loop"""
        self._running = True
        logger.info(f"Starting {self.name} monitor (interval: {self.check_interval}s)")

        while self._running:
            try:
                events = await self.check()
                for event in events:
                    await self._notify(event)

                self._last_check = datetime.now(timezone.utc)

            except Exception as e:
                logger.error(f"{self.name} check failed: {e}")

            await asyncio.sleep(self.check_interval)

    def stop(self):
        """Stop monitoring"""
        self._running = False
        logger.info(f"Stopped {self.name} monitor")

    def _is_new(self, item_id: str) -> bool:
        """Check if we've seen this item before"""
        if item_id in self._seen_items:
            return False
        self._seen_items.add(item_id)
        # Keep set from growing too large
        if len(self._seen_items) > 10000:
            self._seen_items = set(list(self._seen_items)[-5000:])
        return True
