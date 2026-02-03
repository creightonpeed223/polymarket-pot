"""
Polymarket Whale Tracker
Monitors top traders and sends Telegram alerts when they trade
"""

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional
from dataclasses import dataclass

import httpx
from dotenv import load_dotenv

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# Reduce noise
logging.getLogger("httpx").setLevel(logging.WARNING)


# =============================================================================
# CONFIGURATION - Add trader wallet addresses here
# =============================================================================

TRADERS_TO_TRACK = {
    # High win rate, consistent traders
    "ilovecircle": "0xa9878e59934ab507f9039bcb917c1bae0451141d",      # 74% win rate AI bot - $2.2M profit
    "Axios": "0x03626d34381b0337387b0e8464c898f772009661",            # 96% win rate on mention markets
    "abeautifulmind": "0x53d2d3c78597a78402d4db455a680da7ef560c3f",   # Consistent sports bettor
    "Domer": "0x9d84ce0306f8551e02efef1680475fc0f1dc1344",            # #1 trader by volume, $448M traded
}

# How often to check for new trades (seconds)
POLL_INTERVAL = 60

# Minimum trade size to alert (USD) - filter out tiny trades
MIN_TRADE_SIZE = 100


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class Trade:
    """Represents a trade by a tracked trader"""
    trader_name: str
    wallet: str
    market: str
    side: str  # BUY or SELL
    outcome: str  # YES or NO
    size: float  # USD amount
    price: float
    timestamp: datetime
    tx_hash: str

    def __str__(self):
        return f"{self.trader_name} {self.side} ${self.size:.0f} {self.outcome} @ {self.price:.2f} - {self.market[:50]}"


# =============================================================================
# POLYMARKET API CLIENT
# =============================================================================

class PolymarketClient:
    """Client for Polymarket Data API"""

    BASE_URL = "https://data-api.polymarket.com"
    GAMMA_URL = "https://gamma-api.polymarket.com"

    def __init__(self):
        self.client = httpx.AsyncClient(timeout=30.0)

    async def get_trader_activity(self, wallet: str, since: Optional[datetime] = None) -> List[dict]:
        """Get recent trades for a wallet"""
        params = {
            "user": wallet.lower(),
            "type": "TRADE",
            "sortBy": "TIMESTAMP",
            "sortDirection": "DESC",
            "limit": 50,
        }

        if since:
            params["start"] = int(since.timestamp())

        try:
            response = await self.client.get(
                f"{self.BASE_URL}/activity",
                params=params,
            )

            if response.status_code == 200:
                return response.json()
            else:
                logger.error(f"API error: {response.status_code} - {response.text}")
                return []

        except Exception as e:
            logger.error(f"Failed to fetch activity: {e}")
            return []

    async def get_market_info(self, condition_id: str) -> Optional[dict]:
        """Get market details by condition ID"""
        try:
            response = await self.client.get(
                f"{self.GAMMA_URL}/markets",
                params={"condition_id": condition_id},
            )

            if response.status_code == 200:
                markets = response.json()
                return markets[0] if markets else None
            return None

        except Exception as e:
            logger.error(f"Failed to fetch market: {e}")
            return None

    async def close(self):
        await self.client.aclose()


# =============================================================================
# TELEGRAM NOTIFIER
# =============================================================================

class TelegramNotifier:
    """Sends alerts via Telegram"""

    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.enabled = bool(bot_token and chat_id)

        if self.enabled:
            logger.info("Telegram notifications enabled")
        else:
            logger.warning("Telegram not configured - set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")

    async def send_trade_alert(self, trade: Trade):
        """Send alert for a new trade"""
        if not self.enabled:
            return

        # Emoji based on action
        if trade.side == "BUY":
            emoji = "🟢" if trade.outcome == "YES" else "🔴"
            action = "BOUGHT"
        else:
            emoji = "🔻"
            action = "SOLD"

        message = f"""{emoji} **{trade.trader_name}** {action}

**Market:** {trade.market[:100]}
**Position:** {trade.outcome}
**Size:** ${trade.size:,.0f}
**Price:** {trade.price:.2f}

⏰ {trade.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}"""

        await self._send(message)

    async def send_startup_alert(self, traders: List[str]):
        """Send notification when tracker starts"""
        if not self.enabled:
            return

        trader_list = "\n".join(f"• {name}" for name in traders)

        message = f"""🤖 **WHALE TRACKER STARTED**

Monitoring {len(traders)} traders:
{trader_list}

You'll receive alerts when they trade.

⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"""

        await self._send(message)

    async def _send(self, message: str):
        """Send message via Telegram API"""
        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    url,
                    json={
                        "chat_id": self.chat_id,
                        "text": message,
                        "parse_mode": "Markdown",
                    },
                    timeout=10.0,
                )

                if response.status_code != 200:
                    logger.error(f"Telegram error: {response.text}")
                else:
                    logger.debug("Alert sent")

        except Exception as e:
            logger.error(f"Failed to send Telegram: {e}")


# =============================================================================
# WHALE TRACKER
# =============================================================================

class WhaleTracker:
    """Main tracker that monitors traders and sends alerts"""

    def __init__(self):
        self.polymarket = PolymarketClient()
        self.notifier: Optional[TelegramNotifier] = None

        # Track last seen trade per wallet to avoid duplicates
        self._last_seen: Dict[str, str] = {}  # wallet -> last tx_hash

        # Stats
        self.alerts_sent = 0
        self.start_time: Optional[datetime] = None

    async def initialize(self):
        """Initialize the tracker"""
        load_dotenv()

        # Set up Telegram
        self.notifier = TelegramNotifier(
            bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
        )

        # Validate traders config
        active_traders = {k: v for k, v in TRADERS_TO_TRACK.items() if v}

        if not active_traders:
            logger.error("No traders configured! Add wallet addresses to TRADERS_TO_TRACK")
            logger.info("Get wallet addresses from https://polymarket.com/@username")
            return False

        logger.info(f"Tracking {len(active_traders)} traders:")
        for name, wallet in active_traders.items():
            logger.info(f"  • {name}: {wallet[:10]}...{wallet[-6:]}")

        return True

    async def run(self):
        """Main loop - poll for new trades"""
        self.start_time = datetime.now(timezone.utc)

        active_traders = {k: v for k, v in TRADERS_TO_TRACK.items() if v}

        logger.info("=" * 60)
        logger.info("WHALE TRACKER RUNNING")
        logger.info(f"Checking every {POLL_INTERVAL} seconds")
        logger.info(f"Min trade size: ${MIN_TRADE_SIZE}")
        logger.info("=" * 60)

        # Send startup notification
        if self.notifier:
            await self.notifier.send_startup_alert(list(active_traders.keys()))

        # Initial fetch to set baseline (don't alert on old trades)
        for name, wallet in active_traders.items():
            trades = await self.polymarket.get_trader_activity(wallet)
            if trades:
                self._last_seen[wallet] = trades[0].get("transactionHash", "")

        logger.info("Baseline set. Watching for new trades...")

        # Main loop
        while True:
            try:
                for name, wallet in active_traders.items():
                    await self._check_trader(name, wallet)

                await asyncio.sleep(POLL_INTERVAL)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in main loop: {e}")
                await asyncio.sleep(10)

        await self.polymarket.close()

    async def _check_trader(self, name: str, wallet: str):
        """Check for new trades from a specific trader"""
        trades = await self.polymarket.get_trader_activity(wallet)

        if not trades:
            return

        last_seen = self._last_seen.get(wallet, "")
        new_trades = []

        for trade_data in trades:
            tx_hash = trade_data.get("transactionHash", "")

            if tx_hash == last_seen:
                break  # Reached trades we've already seen

            new_trades.append(trade_data)

        # Update last seen
        if trades:
            self._last_seen[wallet] = trades[0].get("transactionHash", "")

        # Process new trades (oldest first)
        for trade_data in reversed(new_trades):
            await self._process_trade(name, wallet, trade_data)

    async def _process_trade(self, trader_name: str, wallet: str, data: dict):
        """Process and alert on a new trade"""
        try:
            # Parse trade data - API returns usdcSize directly
            size = float(data.get("usdcSize", 0))

            # Skip small trades
            if size < MIN_TRADE_SIZE:
                return

            # Market info is included in the response
            market_question = data.get("title", "Unknown Market")

            # Parse timestamp (Unix timestamp)
            ts = data.get("timestamp", 0)
            if isinstance(ts, int):
                timestamp = datetime.fromtimestamp(ts, tz=timezone.utc)
            else:
                timestamp = datetime.now(timezone.utc)

            trade = Trade(
                trader_name=trader_name,
                wallet=wallet,
                market=market_question,
                side=data.get("side", "BUY"),
                outcome=data.get("outcome", "Yes"),
                size=size,
                price=float(data.get("price", 0)),
                timestamp=timestamp,
                tx_hash=data.get("transactionHash", ""),
            )

            logger.info(f"NEW TRADE: {trade}")

            # Send alert
            if self.notifier:
                await self.notifier.send_trade_alert(trade)
                self.alerts_sent += 1

        except Exception as e:
            logger.error(f"Failed to process trade: {e}")


# =============================================================================
# ENTRY POINT
# =============================================================================

async def main():
    """Run the whale tracker"""
    tracker = WhaleTracker()

    if not await tracker.initialize():
        logger.error("Failed to initialize. Exiting.")
        return

    try:
        await tracker.run()
    except KeyboardInterrupt:
        logger.info("Shutting down...")


if __name__ == "__main__":
    asyncio.run(main())
