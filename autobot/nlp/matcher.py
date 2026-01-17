"""
Market Matcher
Matches news events to Polymarket markets
"""

import re
from typing import List, Optional, Tuple
from dataclasses import dataclass

from ..monitors.base import NewsEvent, EventType
from ..trading.polymarket_client import PolymarketTrader
from ..utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class MarketMatch:
    """A matched market with trade recommendation"""
    market_id: str
    question: str
    current_yes_price: float
    current_no_price: float
    token_id_yes: str
    token_id_no: str
    fair_value: float
    edge: float
    recommended_side: str  # "YES" or "NO"
    recommended_token: str
    confidence: float
    liquidity: float


class MarketMatcher:
    """
    Matches news events to relevant Polymarket markets

    The key to speed arbitrage:
    1. Pre-load all markets
    2. Index by keywords
    3. Instant matching when news hits
    """

    def __init__(self, trader: PolymarketTrader):
        self.trader = trader
        self._markets: List[dict] = []
        self._keyword_index: dict = {}  # keyword -> [market_ids]
        self._market_by_id: dict = {}  # id -> market

    async def load_markets(self):
        """Load and index all active markets"""
        logger.info("Loading Polymarket markets...")

        self._markets = await self.trader.get_all_markets()

        # Build keyword index
        self._keyword_index = {}
        self._market_by_id = {}

        for market in self._markets:
            market_id = market.get("id", "")
            question = market.get("question", "").lower()
            description = market.get("description", "").lower()

            self._market_by_id[market_id] = market

            # Extract keywords
            text = f"{question} {description}"
            keywords = self._extract_keywords(text)

            for kw in keywords:
                if kw not in self._keyword_index:
                    self._keyword_index[kw] = []
                self._keyword_index[kw].append(market_id)

        logger.info(f"Indexed {len(self._markets)} markets with {len(self._keyword_index)} keywords")

    def _extract_keywords(self, text: str) -> List[str]:
        """Extract indexable keywords from text"""
        # Remove common words
        stop_words = {
            "the", "a", "an", "is", "are", "was", "were", "be", "been",
            "will", "would", "could", "should", "have", "has", "had",
            "do", "does", "did", "to", "of", "in", "for", "on", "with",
            "at", "by", "from", "as", "or", "and", "but", "if", "than",
            "this", "that", "these", "those", "it", "its",
        }

        # Extract words
        words = re.findall(r'\b[a-z]+\b', text.lower())

        # Filter and return unique keywords
        keywords = set()
        for word in words:
            if len(word) > 2 and word not in stop_words:
                keywords.add(word)

        return list(keywords)

    def find_matches(
        self,
        event: NewsEvent,
        min_edge: float = 0.20,
        fair_value: Optional[float] = None,
    ) -> List[MarketMatch]:
        """
        Find markets that match a news event

        Args:
            event: The news event
            min_edge: Minimum edge required to return a match
            fair_value: Pre-calculated fair value (0-1)

        Returns:
            List of matching markets with trade recommendations
        """
        matches = []

        # Get keywords from event
        search_terms = set()
        search_terms.update(event.keywords)
        search_terms.update(event.entities)

        # Add keywords from headline
        headline_keywords = self._extract_keywords(event.headline)
        search_terms.update(headline_keywords)

        # Find candidate markets
        candidate_ids = set()
        for term in search_terms:
            if term in self._keyword_index:
                candidate_ids.update(self._keyword_index[term])

        logger.debug(f"Found {len(candidate_ids)} candidate markets for event")

        # Score each candidate
        for market_id in candidate_ids:
            market = self._market_by_id.get(market_id)
            if not market:
                continue

            # Check if market is active
            if market.get("closed", False):
                continue

            # Calculate relevance score
            relevance = self._score_relevance(event, market)
            if relevance < 0.5:
                continue

            # Get current prices
            outcomes = market.get("outcomes", [])
            if len(outcomes) < 2:
                continue

            yes_outcome = None
            no_outcome = None

            for outcome in outcomes:
                name = outcome.get("name", "").lower()
                if name == "yes":
                    yes_outcome = outcome
                elif name == "no":
                    no_outcome = outcome

            if not yes_outcome or not no_outcome:
                # Try first/second outcome
                yes_outcome = outcomes[0]
                no_outcome = outcomes[1] if len(outcomes) > 1 else None

            if not no_outcome:
                continue

            current_yes = yes_outcome.get("price", 0.5)
            current_no = no_outcome.get("price", 0.5)

            # Use provided fair value or estimate
            if fair_value is not None:
                fv = fair_value
            else:
                fv = self._estimate_fair_value(event, market)

            # Calculate edge
            # If fair value > current price, buy YES
            # If fair value < current price, buy NO
            yes_edge = fv - current_yes
            no_edge = current_no - (1 - fv)

            if yes_edge > min_edge:
                match = MarketMatch(
                    market_id=market_id,
                    question=market.get("question", ""),
                    current_yes_price=current_yes,
                    current_no_price=current_no,
                    token_id_yes=yes_outcome.get("token_id", ""),
                    token_id_no=no_outcome.get("token_id", ""),
                    fair_value=fv,
                    edge=yes_edge,
                    recommended_side="YES",
                    recommended_token=yes_outcome.get("token_id", ""),
                    confidence=event.confidence * relevance,
                    liquidity=market.get("liquidity", 0),
                )
                matches.append(match)
                logger.info(f"MATCH: {market.get('question', '')[:50]} | Edge: {yes_edge:.1%}")

            elif no_edge > min_edge:
                match = MarketMatch(
                    market_id=market_id,
                    question=market.get("question", ""),
                    current_yes_price=current_yes,
                    current_no_price=current_no,
                    token_id_yes=yes_outcome.get("token_id", ""),
                    token_id_no=no_outcome.get("token_id", ""),
                    fair_value=fv,
                    edge=no_edge,
                    recommended_side="NO",
                    recommended_token=no_outcome.get("token_id", ""),
                    confidence=event.confidence * relevance,
                    liquidity=market.get("liquidity", 0),
                )
                matches.append(match)
                logger.info(f"MATCH: {market.get('question', '')[:50]} | Edge: {no_edge:.1%}")

        # Sort by edge (highest first)
        matches.sort(key=lambda m: m.edge, reverse=True)

        return matches

    def _score_relevance(self, event: NewsEvent, market: dict) -> float:
        """Score how relevant a market is to an event"""
        question = market.get("question", "").lower()
        description = market.get("description", "").lower()
        market_text = f"{question} {description}"

        score = 0.0

        # Check entity matches
        for entity in event.entities:
            if entity.lower() in market_text:
                score += 0.3

        # Check keyword matches
        for keyword in event.keywords:
            if keyword.lower() in market_text:
                score += 0.2

        # Check headline match
        headline_words = event.headline.lower().split()
        for word in headline_words:
            if len(word) > 4 and word in market_text:
                score += 0.1

        return min(1.0, score)

    def _estimate_fair_value(self, event: NewsEvent, market: dict) -> float:
        """
        Estimate fair value based on event type and outcome

        Edge targets by event type:
        - Supreme Court ruling: 50%
        - Major political news: 40%
        - Regulatory (SEC/FDA): 35%
        - Candidate announcement: 30%
        - Sports injury (severe): 45%
        - Sports injury (moderate): 30%
        - Sports trade/signing: 35%
        - Sports result: 40%
        """
        event_type = event.event_type.value if hasattr(event.event_type, 'value') else str(event.event_type)

        # Determine base fair value shift based on event type
        edge_multipliers = {
            # Political - high edge events
            'court_ruling': 0.50,
            'political_news': 0.40,
            'regulatory_decision': 0.35,
            'sec_filing': 0.35,
            'fda_approval': 0.35,
            'executive_order': 0.40,
            'legislation': 0.35,
            'candidate_announcement': 0.30,

            # Sports - edge depends on impact
            'sports_injury': 0.40,  # Will be adjusted by severity
            'sports_trade': 0.35,
            'sports_result': 0.40,
            'sports_news': 0.25,
        }

        base_edge = edge_multipliers.get(event_type, 0.30)

        # Adjust sports injury edge by severity
        if event_type == 'sports_injury':
            severity = None
            for kw in event.keywords:
                if 'severity:' in kw:
                    severity = kw.split(':')[1]
                    break

            if severity == 'severe':
                base_edge = 0.45  # Season-ending = high edge
            elif severity == 'moderate':
                base_edge = 0.30  # Week-to-week
            else:
                base_edge = 0.20  # Day-to-day = lower edge

        # Determine direction based on outcome
        if event.outcome:
            outcome_upper = event.outcome.upper()

            # Positive outcomes (YES direction)
            if outcome_upper in ["YES", "WIN", "CHAMPION", "SIGNED", "TRADED", "APPROVED", "PASSED", "AFFIRMED"]:
                fair_value = min(0.95, 0.50 + base_edge)

            # Negative outcomes (NO direction)
            elif outcome_upper in ["NO", "LOSS", "ELIMINATED", "RELEASED", "OUT_LONG_TERM", "OUT_WEEKS",
                                   "QUESTIONABLE", "DENIED", "FAILED", "REVERSED"]:
                fair_value = max(0.05, 0.50 - base_edge)

            else:
                # Unknown outcome - use confidence to determine direction
                fair_value = 0.50 + (base_edge if event.confidence > 0.5 else -base_edge)
        else:
            # No outcome parsed - use confidence direction
            fair_value = 0.50 + (base_edge * 0.5 if event.confidence > 0.5 else -base_edge * 0.5)

        # Adjust by confidence
        confidence_factor = event.confidence if event.confidence else 0.5
        fair_value = 0.50 + (fair_value - 0.50) * confidence_factor

        return max(0.05, min(0.95, fair_value))

    async def refresh_markets(self):
        """Refresh market data"""
        await self.load_markets()
