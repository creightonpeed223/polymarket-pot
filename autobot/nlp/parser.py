"""
News Parser
Extracts structured information from news events
"""

import re
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

from ..monitors.base import NewsEvent, EventType
from ..utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class ParsedNews:
    """Structured news data"""
    subject: str  # Who/what is the news about
    action: str  # What happened
    outcome: str  # YES, NO, or specific outcome
    confidence: float  # How sure we are
    fair_value: float  # Estimated fair value (0-1)
    keywords: List[str]


class NewsParser:
    """
    Parses news events to extract tradeable information

    Key capabilities:
    - Extract subject (who/what)
    - Identify action (what happened)
    - Determine outcome (YES/NO)
    - Calculate fair value
    """

    # Patterns that indicate positive outcomes (YES)
    POSITIVE_PATTERNS = [
        r"\b(approved?|passed?|confirmed?|granted?|signed?|endorses?|endorsed)\b",
        r"\b(wins?|won|victory|succeeds?|succeeded)\b",
        r"\b(upheld|affirmed|ruled in favor)\b",
        r"\b(announces? candidacy|will run|entering race)\b",
        r"\b(yes|true|correct)\b",
    ]

    # Patterns that indicate negative outcomes (NO)
    NEGATIVE_PATTERNS = [
        r"\b(denied?|rejected?|failed?|blocked?|vetoed?)\b",
        r"\b(loses?|lost|defeat|drops? out|withdraws?|withdrawn)\b",
        r"\b(overturned?|reversed?|struck down|unconstitutional)\b",
        r"\b(resigns?|resigned|fired|removed)\b",
        r"\b(no|false|incorrect)\b",
    ]

    # Entities to extract
    POLITICAL_ENTITIES = {
        "biden": ["biden", "joe biden", "president biden"],
        "trump": ["trump", "donald trump", "president trump"],
        "harris": ["harris", "kamala harris", "vice president harris"],
        "desantis": ["desantis", "ron desantis", "governor desantis"],
        "haley": ["haley", "nikki haley"],
        "supreme court": ["supreme court", "scotus", "the court"],
        "congress": ["congress", "house", "senate"],
    }

    def __init__(self):
        # Compile regex patterns
        self._positive_re = [re.compile(p, re.IGNORECASE) for p in self.POSITIVE_PATTERNS]
        self._negative_re = [re.compile(p, re.IGNORECASE) for p in self.NEGATIVE_PATTERNS]

    def parse(self, event: NewsEvent) -> ParsedNews:
        """
        Parse a news event into structured data

        Args:
            event: The news event to parse

        Returns:
            ParsedNews with extracted information
        """
        text = f"{event.headline} {event.content}".lower()

        # Extract subject
        subject = self._extract_subject(text)

        # Extract action
        action = self._extract_action(text, event.event_type)

        # Determine outcome
        outcome, confidence = self._determine_outcome(text)

        # Calculate fair value
        fair_value = self._calculate_fair_value(outcome, confidence)

        return ParsedNews(
            subject=subject,
            action=action,
            outcome=outcome,
            confidence=confidence,
            fair_value=fair_value,
            keywords=event.keywords,
        )

    def _extract_subject(self, text: str) -> str:
        """Extract the main subject from text"""
        for entity, patterns in self.POLITICAL_ENTITIES.items():
            for pattern in patterns:
                if pattern in text:
                    return entity

        # Fallback: try to find proper nouns
        words = text.split()
        for word in words:
            if word[0].isupper() if word else False:
                return word

        return "unknown"

    def _extract_action(self, text: str, event_type: EventType) -> str:
        """Extract what happened"""
        actions = {
            EventType.COURT_RULING: ["ruled", "decided", "affirmed", "reversed", "denied", "granted"],
            EventType.POLITICAL_NEWS: ["announced", "signed", "vetoed", "endorsed", "nominated"],
            EventType.REGULATORY_DECISION: ["approved", "denied", "charged", "settled", "filed"],
            EventType.CANDIDATE_ANNOUNCEMENT: ["announced", "dropped out", "withdrew", "endorsed"],
            EventType.FDA_APPROVAL: ["approved", "denied", "granted", "rejected"],
            EventType.SEC_FILING: ["filed", "charged", "settled", "approved"],
        }

        type_actions = actions.get(event_type, actions[EventType.POLITICAL_NEWS])

        for action in type_actions:
            if action in text:
                return action

        return "unknown"

    def _determine_outcome(self, text: str) -> Tuple[str, float]:
        """
        Determine if this is a YES or NO outcome

        Returns:
            Tuple of (outcome, confidence)
        """
        positive_matches = sum(1 for p in self._positive_re if p.search(text))
        negative_matches = sum(1 for p in self._negative_re if p.search(text))

        total = positive_matches + negative_matches

        if total == 0:
            return "UNKNOWN", 0.5

        if positive_matches > negative_matches:
            confidence = positive_matches / max(total, 1)
            return "YES", min(0.95, 0.6 + confidence * 0.35)

        elif negative_matches > positive_matches:
            confidence = negative_matches / max(total, 1)
            return "NO", min(0.95, 0.6 + confidence * 0.35)

        else:
            return "UNCLEAR", 0.5

    def _calculate_fair_value(self, outcome: str, confidence: float) -> float:
        """
        Calculate fair value for YES token based on outcome

        If outcome is YES with 90% confidence: fair value = 0.90
        If outcome is NO with 90% confidence: fair value = 0.10 (for YES token)
        """
        if outcome == "YES":
            return confidence
        elif outcome == "NO":
            return 1 - confidence
        else:
            return 0.5

    def parse_court_ruling(self, text: str) -> ParsedNews:
        """Specialized parser for court rulings"""
        # Court rulings often have specific language
        affirm_patterns = [r"affirm", r"upheld", r"ruled in favor", r"constitutional"]
        reverse_patterns = [r"reverse", r"overturn", r"struck down", r"unconstitutional"]

        text_lower = text.lower()

        affirm_count = sum(1 for p in affirm_patterns if re.search(p, text_lower))
        reverse_count = sum(1 for p in reverse_patterns if re.search(p, text_lower))

        if affirm_count > reverse_count:
            outcome = "AFFIRMED"
            confidence = min(0.95, 0.7 + affirm_count * 0.1)
        elif reverse_count > affirm_count:
            outcome = "REVERSED"
            confidence = min(0.95, 0.7 + reverse_count * 0.1)
        else:
            outcome = "UNCLEAR"
            confidence = 0.5

        return ParsedNews(
            subject=self._extract_subject(text),
            action="ruled",
            outcome=outcome,
            confidence=confidence,
            fair_value=confidence if outcome == "AFFIRMED" else (1 - confidence),
            keywords=[],
        )
