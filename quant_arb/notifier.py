"""
Telegram Alert Notifier for Quant Arb Bot
Sends alerts when trading opportunities are detected
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from .engine.detector import ArbitrageOpportunity
    from .config import AlertConfig

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """
    Sends opportunity alerts via Telegram

    Usage:
        notifier = TelegramNotifier(config.alerts)
        await notifier.send_opportunity_alert(opportunity)
    """

    def __init__(self, config: "AlertConfig"):
        self.config = config
        self._enabled = config.enabled and config.telegram_bot_token and config.telegram_chat_id

        if self._enabled:
            logger.info("Telegram alerts enabled")
        else:
            logger.warning("Telegram alerts disabled - missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")

    async def send_opportunity_alert(self, opportunity: "ArbitrageOpportunity"):
        """Send formatted alert for detected opportunity"""
        if not self._enabled:
            return

        # Format opportunity type for display
        type_display = {
            "cross_platform": "Cross-Platform Arb",
            "same_market": "Same-Market Arb",
            "news_driven": "News-Driven",
        }.get(opportunity.opportunity_type, opportunity.opportunity_type)

        # Build platform arrow for cross-platform
        if opportunity.buy_platform != opportunity.sell_platform:
            platform_arrow = f"{opportunity.buy_platform.title()} → {opportunity.sell_platform.title()}"
        else:
            platform_arrow = opportunity.buy_platform.title()

        # Format message
        message = f"""🔔 **OPPORTUNITY DETECTED**

**Market:** {opportunity.question[:100]}
**Type:** {type_display} ({platform_arrow})
**Profit:** {opportunity.profit_pct:.2%}
**Confidence:** {opportunity.confidence:.0%}

**Buy:** {opportunity.buy_platform.title()} @ ${opportunity.buy_price:.3f}
**Sell:** {opportunity.sell_platform.title()} @ ${opportunity.sell_price:.3f}

**Size:** ${opportunity.max_size:.0f} (max available)
**Expected Profit:** ${opportunity.expected_profit:.2f}"""

        # Add news context if available
        if opportunity.news_item:
            message += f"""

**News:** {opportunity.news_item.title[:80]}
**Source:** {opportunity.news_item.source}"""

        message += f"""

⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"""

        await self._send(message.strip())

    async def send_startup_alert(self, platforms: list[str]):
        """Send notification when bot starts in alert-only mode"""
        if not self._enabled:
            return

        message = f"""🤖 **QUANT ARB BOT STARTED**

**Mode:** ALERT ONLY (no trading)
**Platforms:** {', '.join(p.title() for p in platforms)}

Will send alerts when opportunities are detected.

⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"""

        await self._send(message.strip())

    async def send_stats_alert(self, stats: dict):
        """Send periodic stats summary"""
        if not self._enabled:
            return

        message = f"""📊 **STATS UPDATE**

**Scans:** {stats.get('scans', 0):,}
**Opportunities Found:** {stats.get('opportunities', 0):,}
**Alerts Sent:** {stats.get('alerts_sent', 0):,}

**Breakdown:**
- Cross-platform: {stats.get('cross_platform', 0)}
- Same-market: {stats.get('same_market', 0)}
- News-driven: {stats.get('news_driven', 0)}

⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"""

        await self._send(message.strip())

    async def test(self):
        """Send test alert to verify configuration"""
        if not self._enabled:
            logger.warning("Cannot send test - alerts not enabled")
            return False

        message = """🧪 **TEST ALERT**

If you see this, Telegram alerts are working!

Quant Arb Bot is ready to send opportunity alerts."""

        return await self._send(message.strip())

    async def _send(self, message: str) -> bool:
        """Send message via Telegram API"""
        try:
            url = f"https://api.telegram.org/bot{self.config.telegram_bot_token}/sendMessage"

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    url,
                    json={
                        "chat_id": self.config.telegram_chat_id,
                        "text": message,
                        "parse_mode": "Markdown",
                    },
                    timeout=10.0,
                )

                if response.status_code != 200:
                    logger.error(f"Telegram API error: {response.text}")
                    return False
                else:
                    logger.debug("Telegram alert sent")
                    return True

        except Exception as e:
            logger.error(f"Failed to send Telegram alert: {e}")
            return False
