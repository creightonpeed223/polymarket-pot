"""
Twitter/X Monitor
Monitors official accounts for breaking news
Note: Requires Twitter API access ($100/month for Basic)
"""

import asyncio
import os
from datetime import datetime, timezone
from typing import List, Optional
import httpx

from .base import NewsMonitor, NewsEvent, EventType
from ..utils.logger import get_logger

logger = get_logger(__name__)


class TwitterMonitor(NewsMonitor):
    """
    Monitors Twitter/X for breaking political news

    Requires Twitter API credentials:
    - TWITTER_BEARER_TOKEN in .env

    Free tier: Very limited (not useful for trading)
    Basic tier ($100/mo): 10,000 tweets/month read
    Pro tier ($5,000/mo): Full access

    Alternative: Use Nitter or scraping (less reliable)
    """

    # Key accounts to monitor
    ACCOUNTS = [
        # Political
        "POTUS", "WhiteHouse", "VP",
        "SpeakerJohnson", "GOPLeader", "HouseDemocrats",
        "SenateMajLdr", "SenateGOP", "SenateDems",
        # News
        "AP", "Reuters", "ABC", "CBSNews", "NBCNews",
        # Regulatory
        "SECGov", "US_FDA", "FTC",
        # Campaigns (update as needed)
        "realDonaldTrump", "JoeBiden", "KamalaHarris",
    ]

    # Keywords that indicate breaking news
    BREAKING_KEYWORDS = [
        "breaking", "just in", "announces", "announced",
        "signs", "signed", "drops out", "withdraws",
        "indicted", "charged", "resigns", "fired",
        "nominates", "endorses", "executive order",
        "approves", "denies", "rules", "ruling",
    ]

    def __init__(self, check_interval: int = 30):
        super().__init__("Twitter", check_interval)
        self._bearer_token = os.getenv("TWITTER_BEARER_TOKEN", "")
        self._enabled = bool(self._bearer_token)

        if not self._enabled:
            logger.warning("Twitter monitor disabled - no TWITTER_BEARER_TOKEN")

    async def check(self) -> List[NewsEvent]:
        """Check Twitter for new tweets from key accounts"""
        if not self._enabled:
            return []

        events = []

        try:
            async with httpx.AsyncClient() as client:
                for account in self.ACCOUNTS[:10]:  # Limit to avoid rate limits
                    try:
                        account_events = await self._check_account(client, account)
                        events.extend(account_events)
                        await asyncio.sleep(0.5)  # Rate limit protection

                    except Exception as e:
                        logger.debug(f"Failed to check @{account}: {e}")

        except Exception as e:
            logger.error(f"Twitter check failed: {e}")

        return events

    async def _check_account(
        self,
        client: httpx.AsyncClient,
        username: str,
    ) -> List[NewsEvent]:
        """Check a single Twitter account for new tweets"""
        events = []

        headers = {
            "Authorization": f"Bearer {self._bearer_token}",
        }

        try:
            # First get user ID
            user_response = await client.get(
                f"https://api.twitter.com/2/users/by/username/{username}",
                headers=headers,
                timeout=10.0,
            )

            if user_response.status_code != 200:
                return []

            user_data = user_response.json()
            user_id = user_data.get("data", {}).get("id")

            if not user_id:
                return []

            # Get recent tweets
            tweets_response = await client.get(
                f"https://api.twitter.com/2/users/{user_id}/tweets",
                headers=headers,
                params={
                    "max_results": 5,
                    "tweet.fields": "created_at,text",
                },
                timeout=10.0,
            )

            if tweets_response.status_code != 200:
                return []

            tweets_data = tweets_response.json()
            tweets = tweets_data.get("data", [])

            for tweet in tweets:
                tweet_id = tweet.get("id", "")
                text = tweet.get("text", "")

                item_id = f"twitter_{username}_{tweet_id}"

                if not self._is_new(item_id):
                    continue

                # Check if breaking news
                text_lower = text.lower()
                is_breaking = any(kw in text_lower for kw in self.BREAKING_KEYWORDS)

                if is_breaking:
                    event = NewsEvent(
                        event_type=EventType.TWITTER_ANNOUNCEMENT,
                        headline=f"@{username}: {text[:100]}",
                        content=text,
                        source_url=f"https://twitter.com/{username}/status/{tweet_id}",
                        source_name=f"Twitter @{username}",
                        keywords=self._extract_keywords(text),
                        confidence=0.85,
                    )

                    events.append(event)
                    logger.info(f"TWITTER: @{username}: {text[:50]}")

        except Exception as e:
            logger.debug(f"Account check failed for @{username}: {e}")

        return events

    def _extract_keywords(self, text: str) -> List[str]:
        """Extract keywords from tweet"""
        keywords = []
        text_lower = text.lower()

        for kw in self.BREAKING_KEYWORDS:
            if kw in text_lower:
                keywords.append(kw)

        return keywords


class NitterMonitor(NewsMonitor):
    """
    Alternative Twitter monitor using Nitter (free, no API needed)
    Less reliable but works without Twitter API
    """

    NITTER_INSTANCES = [
        "https://nitter.net",
        "https://nitter.privacydev.net",
        "https://nitter.poast.org",
    ]

    ACCOUNTS = TwitterMonitor.ACCOUNTS

    def __init__(self, check_interval: int = 60):
        super().__init__("Nitter", check_interval)
        self._current_instance = 0

    async def check(self) -> List[NewsEvent]:
        """Check Nitter for tweets"""
        events = []

        # Note: Nitter instances are often unreliable
        # This is a fallback if Twitter API isn't available

        instance = self.NITTER_INSTANCES[self._current_instance]

        try:
            async with httpx.AsyncClient() as client:
                for account in self.ACCOUNTS[:5]:
                    try:
                        response = await client.get(
                            f"{instance}/{account}",
                            timeout=10.0,
                            follow_redirects=True,
                        )

                        if response.status_code == 200:
                            # Parse tweets from HTML
                            # Implementation depends on Nitter's current HTML structure
                            pass

                    except Exception as e:
                        continue

        except Exception as e:
            # Rotate instance on failure
            self._current_instance = (self._current_instance + 1) % len(self.NITTER_INSTANCES)

        return events
