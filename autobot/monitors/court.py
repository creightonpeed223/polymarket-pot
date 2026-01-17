"""
Supreme Court Monitor
Checks supremecourt.gov for new opinions and orders
"""

import asyncio
import re
from datetime import datetime, timezone
from typing import List
import httpx
from bs4 import BeautifulSoup

from .base import NewsMonitor, NewsEvent, EventType
from ..utils.logger import get_logger

logger = get_logger(__name__)


class SupremeCourtMonitor(NewsMonitor):
    """
    Monitors Supreme Court for new decisions
    This is one of the highest-edge opportunities
    """

    OPINIONS_URL = "https://www.supremecourt.gov/opinions/opinions.aspx"
    ORDERS_URL = "https://www.supremecourt.gov/orders/ordersofthecourt.aspx"
    SLIP_URL = "https://www.supremecourt.gov/opinions/slipopinion.aspx"

    # Keywords that indicate major decisions
    RULING_KEYWORDS = [
        "affirmed", "reversed", "remanded", "granted", "denied",
        "vacated", "overruled", "upheld", "struck down", "constitutional",
        "unconstitutional", "unanimous", "dissent", "majority opinion"
    ]

    def __init__(self, check_interval: int = 30):
        super().__init__("SupremeCourt", check_interval)
        self._headers = {
            "User-Agent": "Mozilla/5.0 (compatible; NewsBot/1.0)"
        }

    async def check(self) -> List[NewsEvent]:
        """Check for new Supreme Court decisions"""
        events = []

        try:
            async with httpx.AsyncClient() as client:
                # Check slip opinions (most recent decisions)
                events.extend(await self._check_slip_opinions(client))

                # Check orders
                events.extend(await self._check_orders(client))

        except Exception as e:
            logger.error(f"Supreme Court check failed: {e}")

        return events

    async def _check_slip_opinions(self, client: httpx.AsyncClient) -> List[NewsEvent]:
        """Check for new slip opinions (full decisions)"""
        events = []

        try:
            response = await client.get(
                self.SLIP_URL,
                headers=self._headers,
                timeout=15.0
            )
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")

            # Find opinion entries - they're typically in tables
            tables = soup.find_all("table")

            for table in tables:
                rows = table.find_all("tr")
                for row in rows:
                    cells = row.find_all("td")
                    if len(cells) >= 3:
                        # Extract case info
                        date_cell = cells[0].get_text(strip=True)
                        case_cell = cells[1].get_text(strip=True)
                        name_cell = cells[2].get_text(strip=True) if len(cells) > 2 else ""

                        # Create unique ID
                        item_id = f"sc_opinion_{date_cell}_{case_cell}"

                        if self._is_new(item_id):
                            # Get any linked PDF
                            link = row.find("a", href=True)
                            pdf_url = f"https://www.supremecourt.gov{link['href']}" if link else self.SLIP_URL

                            # Check if this looks like a major ruling
                            full_text = f"{case_cell} {name_cell}".lower()
                            is_major = any(kw in full_text for kw in self.RULING_KEYWORDS)

                            event = NewsEvent(
                                event_type=EventType.COURT_RULING,
                                headline=f"Supreme Court: {name_cell[:100]}",
                                content=f"Case: {case_cell}\nDate: {date_cell}\n{name_cell}",
                                source_url=pdf_url,
                                source_name="Supreme Court",
                                keywords=self._extract_keywords(full_text),
                                confidence=0.9 if is_major else 0.7,
                            )

                            if is_major or self._looks_tradeable(name_cell):
                                events.append(event)
                                logger.info(f"NEW COURT OPINION: {name_cell[:60]}")

        except Exception as e:
            logger.error(f"Slip opinions check failed: {e}")

        return events

    async def _check_orders(self, client: httpx.AsyncClient) -> List[NewsEvent]:
        """Check for new court orders (cert grants/denials)"""
        events = []

        try:
            response = await client.get(
                self.ORDERS_URL,
                headers=self._headers,
                timeout=15.0
            )
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")

            # Find order links
            links = soup.find_all("a", href=re.compile(r".*order.*\.pdf", re.I))

            for link in links[:10]:  # Check recent orders
                href = link.get("href", "")
                text = link.get_text(strip=True)

                item_id = f"sc_order_{href}"

                if self._is_new(item_id):
                    order_url = f"https://www.supremecourt.gov{href}" if href.startswith("/") else href

                    # Check if it mentions cert or major case
                    if "cert" in text.lower() or "granted" in text.lower():
                        event = NewsEvent(
                            event_type=EventType.COURT_RULING,
                            headline=f"Supreme Court Order: {text[:100]}",
                            content=text,
                            source_url=order_url,
                            source_name="Supreme Court Orders",
                            confidence=0.7,
                        )
                        events.append(event)
                        logger.info(f"NEW COURT ORDER: {text[:60]}")

        except Exception as e:
            logger.error(f"Orders check failed: {e}")

        return events

    def _extract_keywords(self, text: str) -> List[str]:
        """Extract relevant keywords from text"""
        keywords = []
        text_lower = text.lower()

        for kw in self.RULING_KEYWORDS:
            if kw in text_lower:
                keywords.append(kw)

        # Extract case-specific terms
        important_terms = [
            "abortion", "gun", "firearms", "election", "vote", "voting",
            "immigration", "border", "trump", "biden", "climate", "epa",
            "healthcare", "aca", "affirmative action", "student loans",
            "religious", "free speech", "first amendment", "second amendment"
        ]

        for term in important_terms:
            if term in text_lower:
                keywords.append(term)

        return keywords

    def _looks_tradeable(self, text: str) -> bool:
        """Check if this might be a tradeable event"""
        tradeable_terms = [
            "trump", "biden", "abortion", "roe", "gun", "election",
            "affirmative", "student loan", "climate", "epa", "immigration"
        ]
        text_lower = text.lower()
        return any(term in text_lower for term in tradeable_terms)
