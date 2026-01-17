"""
Bot Configuration
Easy to customize settings
"""

import os
from dataclasses import dataclass
from typing import Optional
from dotenv import load_dotenv

load_dotenv()


@dataclass
class TradingConfig:
    """Trading parameters - adjust these for your risk level"""

    # Capital and position sizing
    starting_capital: float = 10000.0
    risk_per_trade_pct: float = 0.05  # Risk 2% of equity per trade
    max_position_pct: float = 0.30  # Max 20% of equity in single position

    # Risk limits
    min_edge_to_trade: float = 0.30  # Only trade 30%+ edge
    max_daily_loss_pct: float = 0.10  # Stop trading if down 10% of equity
    max_concurrent_positions: int = 10

    # Stop loss / Take profit / Trailing stop (as percentages)
    stop_loss_pct: float = 0.15  # 15% stop loss
    take_profit_pct: float = 0.30  # 30% take profit
    trailing_stop_pct: float = 0.10  # 10% trailing stop (activates after breakeven)
    breakeven_trigger_pct: float = 0.10  # Move stop to breakeven after 10% profit
    use_trailing_stop: bool = True  # Enable trailing stop after breakeven

    # Speed settings
    execution_delay_ms: int = 100  # Delay between detection and trade

    # Auto-trading
    auto_trade_enabled: bool = True  # Set False to require manual approval
    paper_trading: bool = True  # Start with paper trading!


@dataclass
class WalletConfig:
    """Polymarket wallet configuration"""

    private_key: str = ""
    funder_address: str = ""
    chain_id: int = 137  # Polygon mainnet

    def __post_init__(self):
        self.private_key = os.getenv("POLYMARKET_PRIVATE_KEY", "")
        self.funder_address = os.getenv("POLYMARKET_FUNDER_ADDRESS", "")


@dataclass
class AlertConfig:
    """Notification settings"""

    telegram_enabled: bool = False
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    discord_enabled: bool = False
    discord_webhook_url: str = ""

    def __post_init__(self):
        self.telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        self.discord_webhook_url = os.getenv("DISCORD_WEBHOOK_URL", "")

        if self.telegram_bot_token and self.telegram_chat_id:
            self.telegram_enabled = True
        if self.discord_webhook_url:
            self.discord_enabled = True


@dataclass
class MonitorConfig:
    """News source monitoring intervals (seconds)"""

    supreme_court_interval: int = 30
    white_house_interval: int = 60
    congress_interval: int = 120
    sec_edgar_interval: int = 60
    fda_interval: int = 120
    twitter_streaming: bool = True


@dataclass
class BotConfig:
    """Master configuration"""

    trading: TradingConfig
    wallet: WalletConfig
    alerts: AlertConfig
    monitors: MonitorConfig

    # Logging
    log_level: str = "INFO"
    log_file: str = "autobot.log"

    # Dashboard
    dashboard_enabled: bool = True
    dashboard_port: int = 8080


def load_config() -> BotConfig:
    """Load configuration from environment"""
    return BotConfig(
        trading=TradingConfig(
            starting_capital=float(os.getenv("STARTING_CAPITAL", "10000")),
            risk_per_trade_pct=float(os.getenv("RISK_PER_TRADE_PCT", "0.05")),
            max_position_pct=float(os.getenv("MAX_POSITION_PCT", "0.30")),
            min_edge_to_trade=float(os.getenv("MIN_EDGE", "0.30")),
            max_daily_loss_pct=float(os.getenv("MAX_DAILY_LOSS_PCT", "0.10")),
            max_concurrent_positions=int(os.getenv("MAX_CONCURRENT", "10")),
            auto_trade_enabled=os.getenv("AUTO_TRADE", "true").lower() == "true",
            paper_trading=os.getenv("PAPER_TRADING", "true").lower() == "true",
        ),
        wallet=WalletConfig(),
        alerts=AlertConfig(),
        monitors=MonitorConfig(),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
    )


# Global config instance
config = load_config()
