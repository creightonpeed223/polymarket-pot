"""
Regulatory Monitor
Monitors SEC, FDA, FTC for announcements and decisions
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


class RegulatoryMonitor(NewsMonitor):
    """
    Monitors regulatory agencies for tradeable events
    SEC, FDA, FTC decisions can move markets quickly
    """

    SOURCES = {
        "sec_press": {
            "url": "https://www.sec.gov/news/pressreleases",
            "name": "SEC Press Releases",
            "event_type": EventType.SEC_FILING,
            "priority": 1,
        },
        "sec_edgar": {
            "url": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=&company=&dateb=&owner=include&count=40&output=atom",
            "name": "SEC EDGAR",
            "event_type": EventType.SEC_FILING,
            "priority": 1,
            "is_feed": True,
        },
        "fda_press": {
            "url": "https://www.fda.gov/news-events/fda-newsroom/press-announcements",
            "name": "FDA Press",
            "event_type": EventType.FDA_APPROVAL,
            "priority": 2,
        },
        "ftc_press": {
            "url": "https://www.ftc.gov/news-events/news/press-releases",
            "name": "FTC Press",
            "event_type": EventType.REGULATORY_DECISION,
            "priority": 3,
        },
    }

    # Regulatory keywords
    REGULATORY_KEYWORDS = [
        "approved", "approval", "denied", "rejected", "granted",
        "filed", "charged", "settled", "investigation",
        "clearance", "authorization", "recalled", "warning",
        "enforcement", "violation", "fine", "penalty",
        "etf", "bitcoin", "crypto", "merger", "acquisition",
    ]

    # High-value companies/topics
    KEY_TOPICS = [
        "bitcoin", "crypto", "etf", "spot",
        "nvidia", "tesla", "apple", "google", "meta",
        "pfizer", "moderna", "merck", "johnson",
        "merger", "acquisition", "ipo",
    ]

    def __init__(self, check_interval: int = 60):
        super().__init__("Regulatory", check_interval)
        self._headers = {
            "User-Agent": "Mozilla/5.0 (compatible; NewsBot/1.0)"
        }

    async def check(self) -> List[NewsEvent]:
        """Check all regulatory sources"""
        events = []

        async with httpx.AsyncClient() as client:
            for source_id, source_info in self.SOURCES.items():
                try:
                    if source_info.get("is_feed"):
                        source_events = await self._check_feed(
                            client,
                            source_info["url"],
                            source_info["name"],
                            source_info["event_type"],
                        )
                    else:
                        source_events = await self._check_html(
                            client,
                            source_info["url"],
                            source_info["name"],
                            source_info["event_type"],
                        )
                    events.extend(source_events)

                except Exception as e:
                    logger.error(f"Failed to check {source_id}: {e}")

        return events

    async def _check_html(
        self,
        client: httpx.AsyncClient,
        url: str,
        source_name: str,
        event_type: EventType,
    ) -> List[NewsEvent]:
        """Check HTML page for news"""
        events = []

        try:
            response = await client.get(
                url,
                headers=self._headers,
                timeout=15.0,
                follow_redirects=True,
            )
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")

            # Find news items
            articles = soup.find_all(["article", "div", "li"], class_=re.compile(r"news|press|item|release|announcement", re.I))

            if not articles:
                # Fallback: look for links
                articles = soup.find_all("a", href=re.compile(r"/news|/press|/release", re.I))

            for article in articles[:15]:
                try:
                    # Get title
                    title_elem = article.find(["h1", "h2", "h3", "h4", "a", "span"])
                    if not title_elem:
                        continue

                    title = title_elem.get_text(strip=True)
                    if len(title) < 15:
                        continue

                    # Get link
                    link = article.find("a", href=True)
                    article_url = ""
                    if link:
                        href = link.get("href", "")
                        if href.startswith("/"):
                            base_url = "/".join(url.split("/")[:3])
                            article_url = base_url + href
                        elif href.startswith("http"):
                            article_url = href

                    item_id = f"reg_{source_name}_{hash(title)}"

                    if not self._is_new(item_id):
                        continue

                    # Check relevance
                    title_lower = title.lower()
                    keywords = self._extract_keywords(title)
                    has_topic = any(topic in title_lower for topic in self.KEY_TOPICS)

                    if keywords or has_topic:
                        event = NewsEvent(
                            event_type=event_type,
                            headline=title[:200],
                            content=title,
                            source_url=article_url or url,
                            source_name=source_name,
                            keywords=keywords,
                            confidence=0.8 if has_topic else 0.6,
                        )

                        events.append(event)
                        logger.info(f"REGULATORY NEWS: {title[:60]}")

                except Exception as e:
                    continue

        except Exception as e:
            logger.error(f"HTML check failed for {source_name}: {e}")

        return events

    async def _check_feed(
        self,
        client: httpx.AsyncClient,
        url: str,
        source_name: str,
        event_type: EventType,
    ) -> List[NewsEvent]:
        """Check Atom/RSS feed for news"""
        events = []

        try:
            response = await client.get(
                url,
                headers=self._headers,
                timeout=15.0,
            )
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "xml")

            entries = soup.find_all("entry")

            for entry in entries[:20]:
                try:
                    title = entry.find("title")
                    if not title:
                        continue

                    title_text = title.get_text(strip=True)

                    link_elem = entry.find("link")
                    entry_url = link_elem.get("href", "") if link_elem else url

                    item_id = f"feed_{source_name}_{hash(title_text)}"

                    if not self._is_new(item_id):
                        continue

                    title_lower = title_text.lower()
                    keywords = self._extract_keywords(title_text)
                    has_topic = any(topic in title_lower for topic in self.KEY_TOPICS)

                    if keywords or has_topic:
                        # Get summary if available
                        summary = entry.find("summary")
                        content = summary.get_text(strip=True) if summary else title_text

                        event = NewsEvent(
                            event_type=event_type,
                            headline=title_text[:200],
                            content=content[:500],
                            source_url=entry_url,
                            source_name=source_name,
                            keywords=keywords,
                            confidence=0.8 if has_topic else 0.6,
                        )

                        events.append(event)
                        logger.info(f"SEC FILING: {title_text[:60]}")

                except Exception as e:
                    continue

        except Exception as e:
            logger.error(f"Feed check failed for {source_name}: {e}")

        return events

    def _extract_keywords(self, text: str) -> List[str]:
        """Extract regulatory keywords"""
        keywords = []
        text_lower = text.lower()

        for kw in self.REGULATORY_KEYWORDS:
            if kw in text_lower:
                keywords.append(kw)

        return keywords
