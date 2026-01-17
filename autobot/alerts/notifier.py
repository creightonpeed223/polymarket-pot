"""
Alert Notifier
Sends alerts via Telegram, Discord, and other channels
"""

import asyncio
from datetime import datetime, timezone
from typing import Optional
import httpx

from ..config import config
from ..trading.executor import TradeDecision
from ..monitors.base import NewsEvent
from ..utils.logger import get_logger

logger = get_logger(__name__)


class AlertNotifier:
    """
    Sends trading alerts to various channels

    Supported channels:
    - Telegram (recommended)
    - Discord webhook
    """

    def __init__(self):
        self.config = config.alerts
        self._enabled = self.config.telegram_enabled or self.config.discord_enabled

        if self._enabled:
            channels = []
            if self.config.telegram_enabled:
                channels.append("Telegram")
            if self.config.discord_enabled:
                channels.append("Discord")
            logger.info(f"Alerts enabled: {', '.join(channels)}")
        else:
            logger.warning("No alert channels configured")

    async def send_trade_alert(self, trade: TradeDecision):
        """Send alert for executed trade"""
        match = trade.match
        event = trade.event

        message = f"""
🚨 **TRADE EXECUTED**

**Market:** {match.question[:100]}
**Side:** {match.recommended_side}
**Size:** ${trade.size_usd:.2f} ({trade.size_shares:.2f} shares)
**Price:** ${match.current_yes_price if match.recommended_side == 'YES' else match.current_no_price:.3f}
**Edge:** {match.edge:.1%}

**News:** {event.headline[:100]}
**Source:** {event.source_name}

**Fair Value:** ${match.fair_value:.3f}
**Confidence:** {match.confidence:.1%}

⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}
"""

        await self._send(message.strip())

    async def send_opportunity_alert(self, event: NewsEvent, match):
        """Send alert for detected opportunity (before trade)"""
        message = f"""
🔔 **OPPORTUNITY DETECTED**

**News:** {event.headline[:100]}
**Source:** {event.source_name}

**Market:** {match.question[:100]}
**Current Price:** ${match.current_yes_price:.3f} YES / ${match.current_no_price:.3f} NO
**Fair Value:** ${match.fair_value:.3f}
**Edge:** {match.edge:.1%}

**Action:** BUY {match.recommended_side}

⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}
"""

        await self._send(message.strip())

    async def send_pnl_alert(self, pnl: float, trade: TradeDecision):
        """Send P&L update after trade closes"""
        emoji = "✅" if pnl >= 0 else "❌"
        pnl_pct = (pnl / trade.size_usd) * 100 if trade.size_usd > 0 else 0

        message = f"""
{emoji} **TRADE CLOSED**

**Market:** {trade.match.question[:80]}
**P&L:** ${pnl:+.2f} ({pnl_pct:+.1f}%)

**Entry:** ${trade.match.current_yes_price if trade.match.recommended_side == 'YES' else trade.match.current_no_price:.3f}
**Size:** ${trade.size_usd:.2f}
"""

        await self._send(message.strip())

    async def send_risk_alert(self, message: str):
        """Send risk warning"""
        alert = f"""
⚠️ **RISK ALERT**

{message}

⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}
"""
        await self._send(alert.strip())

    async def send_position_closed_alert(self, closed: dict):
        """Send alert when position is auto-closed by SL/TP/trailing stop"""
        reason = closed.get("close_reason", "UNKNOWN")
        pnl = closed.get("pnl", 0)
        pnl_pct = closed.get("pnl_pct", 0)
        position = closed.get("position", {})
        exit_price = closed.get("exit_price", 0)

        # Choose emoji based on close reason and P&L
        if reason == "TAKE_PROFIT":
            emoji = "🎯"
        elif reason == "TRAILING_STOP":
            emoji = "📈" if pnl >= 0 else "🛑"
        elif reason == "BREAKEVEN_STOP":
            emoji = "⚖️"
        elif reason == "STOP_LOSS":
            emoji = "🛑"
        else:
            emoji = "📊"

        market = position.get("market", "Unknown")[:80]
        entry_price = position.get("price", 0)
        size = position.get("size", 0)

        message = f"""
{emoji} **POSITION CLOSED - {reason.replace('_', ' ')}**

**Market:** {market}
**Entry:** ${entry_price:.3f}
**Exit:** ${exit_price:.3f}
**Size:** {size:.2f} shares

**P&L:** ${pnl:+.2f} ({pnl_pct:+.1f}%)

⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}
"""

        await self._send(message.strip())

    async def send_daily_summary(
        self,
        trades: int,
        pnl: float,
        balance: float,
    ):
        """Send daily summary"""
        emoji = "📈" if pnl >= 0 else "📉"

        message = f"""
{emoji} **DAILY SUMMARY**

**Trades:** {trades}
**P&L:** ${pnl:+.2f}
**Balance:** ${balance:,.2f}

🗓️ {datetime.now(timezone.utc).strftime('%Y-%m-%d')}
"""

        await self._send(message.strip())

    async def send_startup_alert(self):
        """Send bot startup notification"""
        message = f"""
🤖 **BOT STARTED**

Polymarket Speed Trading Bot is now running.

**Mode:** {'PAPER' if config.trading.paper_trading else 'LIVE'} TRADING
**Auto-Trade:** {'ENABLED' if config.trading.auto_trade_enabled else 'DISABLED'}
**Starting Balance:** ${config.trading.starting_capital:,.2f}

**Risk Limits:**
- Risk Per Trade: {config.trading.risk_per_trade_pct:.0%} of equity
- Max Position: {config.trading.max_position_pct:.0%} of equity
- Daily Loss Limit: {config.trading.max_daily_loss_pct:.0%} of equity
- Min Edge: {config.trading.min_edge_to_trade:.0%}

⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}
"""

        await self._send(message.strip())

    async def _send(self, message: str):
        """Send message to all enabled channels"""
        tasks = []

        if self.config.telegram_enabled:
            tasks.append(self._send_telegram(message))

        if self.config.discord_enabled:
            tasks.append(self._send_discord(message))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _send_telegram(self, message: str):
        """Send message via Telegram"""
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
                    logger.error(f"Telegram error: {response.text}")
                else:
                    logger.debug("Telegram alert sent")

        except Exception as e:
            logger.error(f"Telegram send failed: {e}")

    async def _send_discord(self, message: str):
        """Send message via Discord webhook"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self.config.discord_webhook_url,
                    json={"content": message},
                    timeout=10.0,
                )

                if response.status_code not in [200, 204]:
                    logger.error(f"Discord error: {response.text}")
                else:
                    logger.debug("Discord alert sent")

        except Exception as e:
            logger.error(f"Discord send failed: {e}")

    async def test(self):
        """Send test alert"""
        await self._send("🧪 **TEST ALERT**\n\nIf you see this, alerts are working!")
        logger.info("Test alert sent")
