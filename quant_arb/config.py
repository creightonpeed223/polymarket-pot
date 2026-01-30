"""
Quantitative Arbitrage Bot Configuration
Cross-platform news arbitrage: Polymarket + Kalshi + Betfair
"""

from dataclasses import dataclass, field
from typing import Dict, List
import os


@dataclass
class AlertConfig:
    """Alert/notification settings"""
    enabled: bool = False
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    @classmethod
    def from_env(cls) -> "AlertConfig":
        """Load alert config from environment"""
        return cls(
            enabled=bool(os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID")),
            telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID", ""),
        )


@dataclass
class RiskConfig:
    """Risk management parameters"""
    min_trade_pct: float = 0.0005      # 0.05% min per trade
    max_trade_pct: float = 0.0025      # 0.25% max per trade
    min_profit_pct: float = 0.002      # 0.2% min profit target
    max_profit_pct: float = 0.005      # 0.5% max profit target
    max_daily_loss_pct: float = 0.02   # 2% max daily loss per market
    max_total_daily_loss_pct: float = 0.05  # 5% total daily loss limit
    max_concurrent_positions: int = 20
    max_position_per_market: float = 0.01  # 1% max in single market


@dataclass
class KillSwitchConfig:
    """Kill switch thresholds"""
    max_latency_ms: int = 500          # Kill if latency > 500ms
    max_fill_slippage_pct: float = 0.01  # Kill if slippage > 1%
    min_fill_rate_pct: float = 0.7     # Kill if fill rate < 70%
    max_consecutive_losses: int = 5    # Kill after 5 consecutive losses
    cooldown_seconds: int = 60         # Cooldown after kill switch


@dataclass
class PlatformConfig:
    """Platform-specific configuration"""
    enabled: bool = True
    api_url: str = ""
    ws_url: str = ""
    fee_pct: float = 0.0
    min_order_size: float = 1.0
    max_order_size: float = 1000.0


@dataclass
class Config:
    """Main configuration"""

    # Risk management
    risk: RiskConfig = field(default_factory=RiskConfig)
    kill_switch: KillSwitchConfig = field(default_factory=KillSwitchConfig)
    alerts: AlertConfig = field(default_factory=AlertConfig)

    # Account balance (will be updated at runtime)
    account_balance: float = 10000.0

    # Scanning - fast for news trading
    scan_interval_ms: int = 100        # 100ms between scans
    news_scan_interval_ms: int = 200   # 200ms for news (faster)
    orderbook_depth: int = 5           # Levels to track

    # Platform configs
    polymarket: PlatformConfig = field(default_factory=lambda: PlatformConfig(
        enabled=True,
        api_url="https://clob.polymarket.com",
        ws_url="wss://ws-subscriptions-clob.polymarket.com/ws/market",
        fee_pct=0.01,
        min_order_size=1.0,
        max_order_size=10000.0,
    ))

    kalshi: PlatformConfig = field(default_factory=lambda: PlatformConfig(
        enabled=True,
        api_url="https://trading-api.kalshi.com/trade-api/v2",
        ws_url="wss://trading-api.kalshi.com/trade-api/ws/v2",
        fee_pct=0.07,  # 7% of profit
        min_order_size=1.0,
        max_order_size=25000.0,
    ))

    betfair: PlatformConfig = field(default_factory=lambda: PlatformConfig(
        enabled=False,  # Disabled - not available in US
        api_url="https://api.betfair.com/exchange/betting",
        ws_url="",  # Betfair uses streaming API differently
        fee_pct=0.05,  # 5% commission
        min_order_size=2.0,  # £2 minimum
        max_order_size=10000.0,
    ))

    # News sources (RSS/API endpoints) - focused on fast political news
    news_sources: List[str] = field(default_factory=lambda: [
        # Breaking News - Fast Updates
        "https://feeds.reuters.com/reuters/topNews",
        "https://feeds.reuters.com/Reuters/PoliticsNews",
        "https://feeds.bbci.co.uk/news/rss.xml",
        "https://feeds.bbci.co.uk/news/politics/rss.xml",
        # Politics - Primary
        "https://www.politico.com/rss/politicopicks.xml",
        "https://www.politico.com/rss/congress.xml",
        "https://thehill.com/feed/",
        "https://feeds.feedburner.com/realclearpolitics/qlMj",
        "https://feeds.washingtonpost.com/rss/politics",
        "https://rss.nytimes.com/services/xml/rss/nyt/Politics.xml",
        # Legal/Courts
        "https://www.supremecourt.gov/rss/cases/opinions.xml",
        # AP Wire - Very Fast
        "https://rsshub.app/apnews/topics/apf-politics",
        # Crypto (for Polymarket relevance)
        "https://cointelegraph.com/rss",
    ])

    # Keywords to track (for news relevance) - focused on politics
    keywords: List[str] = field(default_factory=lambda: [
        # Politics - Primary Focus
        "trump", "biden", "election", "congress", "senate", "house",
        "supreme court", "president", "vote", "poll", "campaign",
        "republican", "democrat", "gop", "maga", "impeach",
        "indictment", "guilty", "verdict", "trial", "charges",
        "cabinet", "nominee", "confirmation", "veto", "executive order",
        "desantis", "haley", "vance", "harris", "pelosi", "mcconnell",
        # Regulatory/Legal
        "sec", "doj", "fbi", "ruling", "lawsuit", "settlement",
        # Crypto (still relevant for Polymarket)
        "bitcoin", "ethereum", "crypto", "etf",
    ])

    # Logging
    log_level: str = "INFO"
    log_trades: bool = True
    log_opportunities: bool = False  # Too verbose at high frequency

    @classmethod
    def from_env(cls) -> "Config":
        """Load config with environment overrides"""
        config = cls()

        # Load alert config from environment
        config.alerts = AlertConfig.from_env()

        # Override from environment
        if os.environ.get("ACCOUNT_BALANCE"):
            config.account_balance = float(os.environ["ACCOUNT_BALANCE"])
        if os.environ.get("SCAN_INTERVAL_MS"):
            config.scan_interval_ms = int(os.environ["SCAN_INTERVAL_MS"])
        if os.environ.get("MAX_LATENCY_MS"):
            config.kill_switch.max_latency_ms = int(os.environ["MAX_LATENCY_MS"])

        # Disable platforms via env
        if os.environ.get("DISABLE_POLYMARKET"):
            config.polymarket.enabled = False
        if os.environ.get("DISABLE_KALSHI"):
            config.kalshi.enabled = False
        if os.environ.get("DISABLE_BETFAIR"):
            config.betfair.enabled = False

        return config


# Global config instance
CONFIG = Config.from_env()
