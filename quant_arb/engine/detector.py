"""
Arbitrage Detection Engine
Identifies cross-platform arbitrage and news-driven opportunities
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set, Tuple
from difflib import SequenceMatcher

from ..platforms.base import Market, OrderBook
from ..news.scanner import NewsItem

logger = logging.getLogger(__name__)


@dataclass
class ArbitrageOpportunity:
    """Detected arbitrage opportunity"""
    id: str
    opportunity_type: str  # "cross_platform", "same_market", "news_driven"

    # Markets involved
    buy_platform: str
    buy_market_id: str
    buy_price: float
    buy_size: float

    sell_platform: str
    sell_market_id: str
    sell_price: float
    sell_size: float

    # Calculated values
    spread: float
    profit_pct: float
    max_size: float
    expected_profit: float

    # Metadata
    question: str
    detected_at: datetime = field(default_factory=datetime.utcnow)
    news_item: Optional[NewsItem] = None
    confidence: float = 1.0

    def __str__(self) -> str:
        return f"[{self.opportunity_type}] {self.question[:40]} | Buy@{self.buy_price:.3f} ({self.buy_platform}) Sell@{self.sell_price:.3f} ({self.sell_platform}) | Profit: {self.profit_pct:.2%}"


@dataclass
class MarketPair:
    """Matched markets across platforms"""
    markets: Dict[str, Market]  # platform -> market
    similarity: float
    last_updated: datetime = field(default_factory=datetime.utcnow)


class ArbitrageDetector:
    """
    Detects arbitrage opportunities across platforms

    Types of arbitrage detected:
    1. Cross-platform: Same event, different prices across platforms
    2. Same-market: YES + NO < 1.00 within same market
    3. News-driven: Price hasn't adjusted to new information
    """

    def __init__(
        self,
        min_profit_pct: float = 0.002,  # 0.2% minimum profit
        max_profit_pct: float = 0.05,   # 5% max (avoid obvious errors)
        min_liquidity: float = 100.0,   # Minimum $100 liquidity
        max_spread_pct: float = 0.1,    # 10% max spread for matching
    ):
        self.min_profit_pct = min_profit_pct
        self.max_profit_pct = max_profit_pct
        self.min_liquidity = min_liquidity
        self.max_spread_pct = max_spread_pct

        # Market data
        self._markets: Dict[str, Dict[str, Market]] = {}  # platform -> {market_id: market}
        self._orderbooks: Dict[str, Dict[str, OrderBook]] = {}  # platform -> {market_id: orderbook}

        # Cross-platform matching
        self._market_pairs: Dict[str, MarketPair] = {}  # pair_id -> MarketPair

        # News tracking
        self._recent_news: List[NewsItem] = []
        self._news_market_links: Dict[str, List[str]] = {}  # news_id -> [market_ids]

        # Stats
        self.opportunities_found = 0
        self.cross_platform_found = 0
        self.same_market_found = 0
        self.news_driven_found = 0

    def update_markets(self, platform: str, markets: List[Market]):
        """Update markets for a platform"""
        if platform not in self._markets:
            self._markets[platform] = {}

        for market in markets:
            self._markets[platform][market.id] = market

        # Rebuild market pairs after update
        self._match_markets()

    def update_orderbook(self, platform: str, orderbook: OrderBook):
        """Update orderbook for a market"""
        if platform not in self._orderbooks:
            self._orderbooks[platform] = {}
        self._orderbooks[platform][orderbook.market_id] = orderbook

    def add_news(self, news: NewsItem):
        """Add news item for opportunity detection"""
        self._recent_news.append(news)

        # Keep only last 100 items
        if len(self._recent_news) > 100:
            self._recent_news = self._recent_news[-100:]

        # Link news to relevant markets
        self._link_news_to_markets(news)

    def _match_markets(self):
        """Match similar markets across platforms"""
        platforms = list(self._markets.keys())
        if len(platforms) < 2:
            return

        # Compare markets between each pair of platforms
        for i, p1 in enumerate(platforms):
            for p2 in platforms[i + 1:]:
                for m1 in self._markets[p1].values():
                    for m2 in self._markets[p2].values():
                        similarity = self._calculate_similarity(m1.question, m2.question)
                        if similarity > 0.7:  # 70% similarity threshold
                            pair_id = f"{m1.id}:{m2.id}"
                            self._market_pairs[pair_id] = MarketPair(
                                markets={p1: m1, p2: m2},
                                similarity=similarity,
                            )

        logger.debug(f"Matched {len(self._market_pairs)} market pairs")

    def _calculate_similarity(self, s1: str, s2: str) -> float:
        """Calculate text similarity between two strings"""
        s1 = s1.lower().strip()
        s2 = s2.lower().strip()
        return SequenceMatcher(None, s1, s2).ratio()

    def _link_news_to_markets(self, news: NewsItem):
        """Link news item to relevant markets"""
        linked = []
        news_text = f"{news.title} {news.summary}".lower()

        for platform, markets in self._markets.items():
            for market in markets.values():
                question = market.question.lower()

                # Check for keyword overlap
                words = set(question.split())
                news_words = set(news_text.split())
                overlap = len(words & news_words) / max(len(words), 1)

                if overlap > 0.2:  # 20% word overlap (catch more matches)
                    linked.append(f"{platform}:{market.id}")

        self._news_market_links[news.id] = linked

    def detect_opportunities(self) -> List[ArbitrageOpportunity]:
        """Detect all arbitrage opportunities"""
        opportunities = []

        # Only detect news-driven opportunities (fast news trading)
        news_opps = self._detect_news_driven()
        opportunities.extend(news_opps)
        self.news_driven_found += len(news_opps)

        # Filter and sort
        valid_opps = [
            o for o in opportunities
            if self.min_profit_pct <= o.profit_pct <= self.max_profit_pct
            and o.max_size >= self.min_liquidity
        ]

        valid_opps.sort(key=lambda x: x.expected_profit, reverse=True)
        self.opportunities_found += len(valid_opps)

        return valid_opps

    def _detect_cross_platform(self) -> List[ArbitrageOpportunity]:
        """Detect cross-platform arbitrage"""
        opportunities = []

        for pair_id, pair in self._market_pairs.items():
            platforms = list(pair.markets.keys())
            if len(platforms) < 2:
                continue

            p1, p2 = platforms[0], platforms[1]
            m1, m2 = pair.markets[p1], pair.markets[p2]

            # Get orderbooks
            ob1 = self._orderbooks.get(p1, {}).get(m1.id)
            ob2 = self._orderbooks.get(p2, {}).get(m2.id)

            if not ob1 or not ob2:
                continue

            # Check for arbitrage: buy low on one, sell high on other
            # Scenario 1: Buy on p1, sell on p2
            if ob1.best_ask and ob2.best_bid and ob2.best_bid > ob1.best_ask:
                spread = ob2.best_bid - ob1.best_ask
                profit_pct = spread / ob1.best_ask

                ask_size = ob1.asks[0][1] if ob1.asks else 0
                bid_size = ob2.bids[0][1] if ob2.bids else 0
                max_size = min(ask_size, bid_size)

                if max_size > 0 and profit_pct > 0:
                    opp = ArbitrageOpportunity(
                        id=f"cross_{pair_id}_{int(time.time())}",
                        opportunity_type="cross_platform",
                        buy_platform=p1,
                        buy_market_id=m1.id,
                        buy_price=ob1.best_ask,
                        buy_size=ask_size,
                        sell_platform=p2,
                        sell_market_id=m2.id,
                        sell_price=ob2.best_bid,
                        sell_size=bid_size,
                        spread=spread,
                        profit_pct=profit_pct,
                        max_size=max_size,
                        expected_profit=max_size * spread,
                        question=m1.question,
                        confidence=pair.similarity,
                    )
                    opportunities.append(opp)

            # Scenario 2: Buy on p2, sell on p1
            if ob2.best_ask and ob1.best_bid and ob1.best_bid > ob2.best_ask:
                spread = ob1.best_bid - ob2.best_ask
                profit_pct = spread / ob2.best_ask

                ask_size = ob2.asks[0][1] if ob2.asks else 0
                bid_size = ob1.bids[0][1] if ob1.bids else 0
                max_size = min(ask_size, bid_size)

                if max_size > 0 and profit_pct > 0:
                    opp = ArbitrageOpportunity(
                        id=f"cross_{pair_id}_{int(time.time())}_rev",
                        opportunity_type="cross_platform",
                        buy_platform=p2,
                        buy_market_id=m2.id,
                        buy_price=ob2.best_ask,
                        buy_size=ask_size,
                        sell_platform=p1,
                        sell_market_id=m1.id,
                        sell_price=ob1.best_bid,
                        sell_size=bid_size,
                        spread=spread,
                        profit_pct=profit_pct,
                        max_size=max_size,
                        expected_profit=max_size * spread,
                        question=m2.question,
                        confidence=pair.similarity,
                    )
                    opportunities.append(opp)

        return opportunities

    def _detect_same_market(self) -> List[ArbitrageOpportunity]:
        """Detect same-market arbitrage (YES + NO < 1)"""
        opportunities = []

        for platform, markets in self._markets.items():
            orderbooks = self._orderbooks.get(platform, {})

            for market in markets.values():
                if not market.yes_token_id or not market.no_token_id:
                    continue

                yes_ob = orderbooks.get(market.yes_token_id)
                no_ob = orderbooks.get(market.no_token_id)

                if not yes_ob or not no_ob:
                    continue

                if not yes_ob.best_ask or not no_ob.best_ask:
                    continue

                total_cost = yes_ob.best_ask + no_ob.best_ask
                if total_cost >= 1.0:
                    continue  # No arbitrage

                # Calculate profit (payout is always $1)
                profit = 1.0 - total_cost
                profit_pct = profit / total_cost

                yes_size = yes_ob.asks[0][1] if yes_ob.asks else 0
                no_size = no_ob.asks[0][1] if no_ob.asks else 0
                max_size = min(yes_size, no_size)

                if max_size > 0:
                    opp = ArbitrageOpportunity(
                        id=f"same_{market.id}_{int(time.time())}",
                        opportunity_type="same_market",
                        buy_platform=platform,
                        buy_market_id=market.yes_token_id,
                        buy_price=yes_ob.best_ask,
                        buy_size=yes_size,
                        sell_platform=platform,
                        sell_market_id=market.no_token_id,
                        sell_price=no_ob.best_ask,
                        sell_size=no_size,
                        spread=profit,
                        profit_pct=profit_pct,
                        max_size=max_size,
                        expected_profit=max_size * profit,
                        question=market.question,
                    )
                    opportunities.append(opp)

        return opportunities

    def _detect_news_driven(self) -> List[ArbitrageOpportunity]:
        """Detect news-driven opportunities"""
        opportunities = []

        # Check news from last 15 minutes (more opportunities)
        cutoff = datetime.utcnow() - timedelta(minutes=15)
        recent = [n for n in self._recent_news if n.published > cutoff]

        for news in recent:
            linked_markets = self._news_market_links.get(news.id, [])

            for market_ref in linked_markets:
                parts = market_ref.split(":")
                if len(parts) != 2:
                    continue

                platform, market_id = parts
                market = self._markets.get(platform, {}).get(market_id)
                orderbooks = self._orderbooks.get(platform, {})

                if not market:
                    continue

                # Get YES orderbook
                ob = orderbooks.get(market.yes_token_id or market_id)
                if not ob or not ob.best_ask:
                    continue

                # News-driven opportunity: assume price should move toward 0.65 or 0.35
                # based on sentiment (simplified heuristic)
                current_price = ob.best_ask

                # Estimate fair value based on news
                # Positive keywords suggest YES, negative suggest NO
                positive_words = [
                    "win", "pass", "approve", "confirm", "success", "rise",
                    "victory", "leading", "surge", "gains", "supports", "endorses",
                    "acquitted", "cleared", "dismissed", "elected", "signed"
                ]
                negative_words = [
                    "lose", "fail", "reject", "deny", "fall", "drop",
                    "guilty", "convicted", "indicted", "charged", "arrested",
                    "resign", "withdraw", "defeated", "loses", "collapses"
                ]

                news_text = f"{news.title} {news.summary}".lower()
                pos_score = sum(1 for w in positive_words if w in news_text)
                neg_score = sum(1 for w in negative_words if w in news_text)

                if pos_score > neg_score and current_price < 0.65:
                    # Underpriced YES - news is positive, price should go up
                    target = 0.75
                    edge = target - current_price

                    if edge > 0.02:  # At least 2% edge (more signals)
                        size = ob.asks[0][1] if ob.asks else 0
                        opp = ArbitrageOpportunity(
                            id=f"news_{news.id}_{market_id}",
                            opportunity_type="news_driven",
                            buy_platform=platform,
                            buy_market_id=market_id,
                            buy_price=current_price,
                            buy_size=size,
                            sell_platform=platform,
                            sell_market_id=market_id,
                            sell_price=target,
                            sell_size=size,
                            spread=edge,
                            profit_pct=edge / current_price,
                            max_size=size,
                            expected_profit=size * edge,
                            question=market.question,
                            news_item=news,
                            confidence=news.relevance_score,
                        )
                        opportunities.append(opp)

                elif neg_score > pos_score and current_price > 0.35:
                    # Overpriced YES (should buy NO) - news is negative
                    target = 0.25
                    edge = current_price - target

                    if edge > 0.02:  # At least 2% edge (more signals)
                        size = ob.bids[0][1] if ob.bids else 0
                        opp = ArbitrageOpportunity(
                            id=f"news_{news.id}_{market_id}_no",
                            opportunity_type="news_driven",
                            buy_platform=platform,
                            buy_market_id=market.no_token_id or market_id,
                            buy_price=1 - current_price,
                            buy_size=size,
                            sell_platform=platform,
                            sell_market_id=market.no_token_id or market_id,
                            sell_price=1 - target,
                            sell_size=size,
                            spread=edge,
                            profit_pct=edge / (1 - current_price),
                            max_size=size,
                            expected_profit=size * edge,
                            question=market.question,
                            news_item=news,
                            confidence=news.relevance_score,
                        )
                        opportunities.append(opp)

        return opportunities

    def get_stats(self) -> Dict:
        """Get detector statistics"""
        return {
            "opportunities_found": self.opportunities_found,
            "cross_platform": self.cross_platform_found,
            "same_market": self.same_market_found,
            "news_driven": self.news_driven_found,
            "market_pairs": len(self._market_pairs),
            "markets_tracked": sum(len(m) for m in self._markets.values()),
            "orderbooks_active": sum(len(o) for o in self._orderbooks.values()),
        }
