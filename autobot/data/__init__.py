"""Data storage module"""
from .database import (
    init_database,
    save_closed_trade,
    get_closed_trades,
    get_trade_stats,
    save_bot_state,
    get_bot_state,
    reset_daily_pnl,
)

__all__ = [
    "init_database",
    "save_closed_trade",
    "get_closed_trades",
    "get_trade_stats",
    "save_bot_state",
    "get_bot_state",
    "reset_daily_pnl",
]
