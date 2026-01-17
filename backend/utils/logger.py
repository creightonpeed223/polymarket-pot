"""Logging utilities for the trading bot."""

import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional


def setup_logger(
    name: str = "trading_bot",
    level: int = logging.INFO,
    log_file: Optional[str] = None,
) -> logging.Logger:
    """
    Set up a logger with console and optional file output.

    Args:
        name: Logger name
        level: Logging level
        log_file: Optional file path for logging

    Returns:
        Configured logger instance
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)

    # Prevent duplicate handlers
    if logger.handlers:
        return logger

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_format = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    console_handler.setFormatter(console_format)
    logger.addHandler(console_handler)

    # File handler (if specified)
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(level)
        file_format = logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | %(funcName)s:%(lineno)d | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        file_handler.setFormatter(file_format)
        logger.addHandler(file_handler)

    return logger


# Pre-configured loggers for different modules
def get_data_logger() -> logging.Logger:
    """Get logger for data fetching operations."""
    return setup_logger("bot.data", logging.INFO)


def get_analysis_logger() -> logging.Logger:
    """Get logger for analysis operations."""
    return setup_logger("bot.analysis", logging.INFO)


def get_trading_logger() -> logging.Logger:
    """Get logger for trading operations."""
    return setup_logger("bot.trading", logging.INFO)


def get_api_logger() -> logging.Logger:
    """Get logger for API operations."""
    return setup_logger("bot.api", logging.INFO)


class TradeLogger:
    """Specialized logger for trade events."""

    def __init__(self, log_dir: str = "logs/trades"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.logger = setup_logger(
            "bot.trades",
            logging.INFO,
            str(self.log_dir / f"trades_{datetime.now().strftime('%Y%m%d')}.log")
        )

    def log_trade(
        self,
        action: str,
        market: str,
        side: str,
        size: float,
        price: float,
        confidence: float,
        paper: bool = True,
    ):
        """Log a trade execution."""
        mode = "PAPER" if paper else "LIVE"
        self.logger.info(
            f"[{mode}] {action} | Market: {market} | Side: {side} | "
            f"Size: ${size:.2f} | Price: {price:.4f} | Confidence: {confidence:.1f}%"
        )

    def log_signal(
        self,
        signal: str,
        confidence: float,
        indicators: dict,
    ):
        """Log a trading signal."""
        self.logger.info(
            f"SIGNAL: {signal} | Confidence: {confidence:.1f}% | "
            f"Indicators: {indicators}"
        )

    def log_position_update(
        self,
        market: str,
        position_size: float,
        pnl: float,
        paper: bool = True,
    ):
        """Log a position update."""
        mode = "PAPER" if paper else "LIVE"
        self.logger.info(
            f"[{mode}] POSITION | Market: {market} | "
            f"Size: ${position_size:.2f} | P&L: ${pnl:+.2f}"
        )
