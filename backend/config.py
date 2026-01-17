"""Configuration management for the trading bot."""

from pydantic_settings import BaseSettings
from pydantic import Field
from typing import Optional
from functools import lru_cache


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Polymarket credentials
    polymarket_private_key: str = Field(default="", description="Polymarket wallet private key")
    polymarket_funder_address: str = Field(default="", description="Polymarket funder address")

    # Trading settings
    max_position_size: float = Field(default=100.0, description="Maximum position size in USD")
    daily_loss_limit: float = Field(default=50.0, description="Maximum daily loss in USD")
    auto_trade_enabled: bool = Field(default=False, description="Enable automatic trading")
    min_confidence_threshold: float = Field(default=70.0, description="Minimum confidence for trades (0-100)")

    # Risk management
    stop_loss_percent: float = Field(default=10.0, description="Stop loss percentage")
    cooldown_seconds: int = Field(default=300, description="Cooldown between trades in seconds")
    max_daily_trades: int = Field(default=10, description="Maximum trades per day")

    # Server settings
    host: str = Field(default="127.0.0.1", description="Server host")
    port: int = Field(default=8000, description="Server port")

    # Data settings
    default_timeframe: str = Field(default="15m", description="Default chart timeframe")
    price_history_limit: int = Field(default=500, description="Number of candles to fetch")

    # ML settings
    ml_retrain_interval: int = Field(default=3600, description="ML model retrain interval in seconds")
    ml_lookback_periods: int = Field(default=100, description="Lookback periods for ML features")

    # Paper trading
    paper_trading: bool = Field(default=True, description="Enable paper trading mode")
    paper_balance: float = Field(default=1000.0, description="Paper trading starting balance")

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
        "extra": "ignore",  # Allow extra env variables
    }


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


# Timeframe mappings for ccxt
TIMEFRAME_MAP = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "1h": "1h",
    "4h": "4h",
    "1d": "1d",
}

# Indicator default parameters
INDICATOR_PARAMS = {
    "sma_fast": 10,
    "sma_slow": 30,
    "ema_fast": 12,
    "ema_slow": 26,
    "rsi_period": 14,
    "macd_fast": 12,
    "macd_slow": 26,
    "macd_signal": 9,
    "bb_period": 20,
    "bb_std": 2,
    "stoch_k": 14,
    "stoch_d": 3,
    "atr_period": 14,
    "obv_signal": 20,
}

# Signal weights for combining indicators
SIGNAL_WEIGHTS = {
    "rsi": 0.10,
    "macd": 0.10,
    "sma_cross": 0.05,
    "ema_cross": 0.05,
    "bollinger": 0.10,
    "stochastic": 0.10,
    "ml_prediction": 0.50,  # ML gets highest weight for price prediction
}
