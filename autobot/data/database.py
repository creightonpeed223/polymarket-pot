"""
SQLite Database for Trade Persistence
Stores closed trades and bot state across restarts
"""

import sqlite3
import json
import os
import shutil
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from contextlib import contextmanager

from ..utils.logger import get_logger

logger = get_logger(__name__)

# Database file location
DB_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(DB_DIR, "trades.db")
BACKUP_DIR = os.path.join(DB_DIR, "backups")


def backup_database() -> Optional[str]:
    """Create a timestamped backup of the database. Returns backup path or None on failure."""
    try:
        if not os.path.exists(DB_PATH):
            logger.warning("No database to backup")
            return None

        # Create backup directory
        os.makedirs(BACKUP_DIR, exist_ok=True)

        # Create timestamped backup filename
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_filename = f"trades_backup_{timestamp}.db"
        backup_path = os.path.join(BACKUP_DIR, backup_filename)

        # Copy the database
        shutil.copy2(DB_PATH, backup_path)

        logger.info(f"Database backed up to {backup_path}")

        # Keep only last 10 backups
        backups = sorted([f for f in os.listdir(BACKUP_DIR) if f.endswith('.db')])
        while len(backups) > 10:
            old_backup = backups.pop(0)
            os.remove(os.path.join(BACKUP_DIR, old_backup))
            logger.debug(f"Removed old backup: {old_backup}")

        return backup_path
    except Exception as e:
        logger.error(f"Failed to backup database: {e}")
        return None


def restore_from_backup(backup_path: str) -> bool:
    """Restore database from a backup file."""
    try:
        if not os.path.exists(backup_path):
            logger.error(f"Backup file not found: {backup_path}")
            return False

        # Create backup of current before restoring
        if os.path.exists(DB_PATH):
            pre_restore_backup = DB_PATH + ".pre_restore"
            shutil.copy2(DB_PATH, pre_restore_backup)

        shutil.copy2(backup_path, DB_PATH)
        logger.info(f"Database restored from {backup_path}")
        return True
    except Exception as e:
        logger.error(f"Failed to restore database: {e}")
        return False


def list_backups() -> List[str]:
    """List available database backups."""
    try:
        if not os.path.exists(BACKUP_DIR):
            return []
        return sorted([f for f in os.listdir(BACKUP_DIR) if f.endswith('.db')], reverse=True)
    except Exception:
        return []


@contextmanager
def get_connection():
    """Get database connection with context manager"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()


def init_database():
    """Initialize database tables"""
    # Backup existing database before any changes
    if os.path.exists(DB_PATH):
        backup_database()

    with get_connection() as conn:
        cursor = conn.cursor()

        # Closed trades table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS closed_trades (
                id TEXT PRIMARY KEY,
                market TEXT,
                token_id TEXT,
                side TEXT,
                size REAL,
                entry_price REAL,
                exit_price REAL,
                risk_amount REAL,
                pnl REAL,
                pnl_pct REAL,
                won INTEGER,
                close_reason TEXT,
                entry_time TEXT,
                exit_time TEXT,
                stop_loss_price REAL,
                take_profit_price REAL,
                breakeven_triggered INTEGER,
                trailing_stop_active INTEGER,
                highest_price REAL,
                paper INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Bot state table (single row for current state)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS bot_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                paper_balance REAL DEFAULT 10000.0,
                daily_pnl REAL DEFAULT 0.0,
                total_pnl REAL DEFAULT 0.0,
                last_daily_reset TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Initialize bot state if not exists
        cursor.execute("""
            INSERT OR IGNORE INTO bot_state (id, paper_balance, daily_pnl, total_pnl)
            VALUES (1, 10000.0, 0.0, 0.0)
        """)

        # Open positions table (for persistence across restarts)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS open_positions (
                id TEXT PRIMARY KEY,
                token_id TEXT,
                market TEXT,
                side TEXT,
                size REAL,
                price REAL,
                value REAL,
                risk_amount REAL,
                stop_loss_price REAL,
                take_profit_price REAL,
                breakeven_trigger_price REAL,
                highest_price REAL,
                breakeven_triggered INTEGER DEFAULT 0,
                trailing_stop_active INTEGER DEFAULT 0,
                entry_time TEXT,
                paper INTEGER DEFAULT 1
            )
        """)

        logger.info(f"Database initialized at {DB_PATH}")


def save_closed_trade(trade: Dict[str, Any]) -> bool:
    """Save a closed trade to database"""
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO closed_trades (
                    id, market, token_id, side, size, entry_price, exit_price,
                    risk_amount, pnl, pnl_pct, won, close_reason, entry_time,
                    exit_time, stop_loss_price, take_profit_price,
                    breakeven_triggered, trailing_stop_active, highest_price, paper
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                trade.get("id"),
                trade.get("market"),
                trade.get("token_id"),
                trade.get("side"),
                trade.get("size"),
                trade.get("entry_price"),
                trade.get("exit_price"),
                trade.get("risk_amount"),
                trade.get("pnl"),
                trade.get("pnl_pct"),
                1 if trade.get("won") else 0,
                trade.get("close_reason"),
                trade.get("entry_time"),
                trade.get("exit_time"),
                trade.get("stop_loss_price"),
                trade.get("take_profit_price"),
                1 if trade.get("breakeven_triggered") else 0,
                1 if trade.get("trailing_stop_active") else 0,
                trade.get("highest_price"),
                1 if trade.get("paper") else 0,
            ))
            logger.debug(f"Saved trade {trade.get('id')} to database")
            return True
    except Exception as e:
        logger.error(f"Failed to save trade: {e}")
        return False


def get_closed_trades(limit: int = 100) -> List[Dict[str, Any]]:
    """Get closed trades from database (most recent first)"""
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM closed_trades
                ORDER BY exit_time DESC
                LIMIT ?
            """, (limit,))

            trades = []
            for row in cursor.fetchall():
                trades.append({
                    "id": row["id"],
                    "market": row["market"],
                    "token_id": row["token_id"],
                    "side": row["side"],
                    "size": row["size"],
                    "entry_price": row["entry_price"],
                    "exit_price": row["exit_price"],
                    "risk_amount": row["risk_amount"],
                    "pnl": row["pnl"],
                    "pnl_pct": row["pnl_pct"],
                    "won": bool(row["won"]),
                    "close_reason": row["close_reason"],
                    "entry_time": row["entry_time"],
                    "exit_time": row["exit_time"],
                    "stop_loss_price": row["stop_loss_price"],
                    "take_profit_price": row["take_profit_price"],
                    "breakeven_triggered": bool(row["breakeven_triggered"]),
                    "trailing_stop_active": bool(row["trailing_stop_active"]),
                    "highest_price": row["highest_price"],
                    "paper": bool(row["paper"]),
                })
            return trades
    except Exception as e:
        logger.error(f"Failed to get trades: {e}")
        return []


def get_trade_stats() -> Dict[str, Any]:
    """Get aggregate trade statistics from database"""
    try:
        with get_connection() as conn:
            cursor = conn.cursor()

            # Get totals
            cursor.execute("""
                SELECT
                    COUNT(*) as total_trades,
                    SUM(CASE WHEN won = 1 THEN 1 ELSE 0 END) as wins,
                    SUM(CASE WHEN won = 0 THEN 1 ELSE 0 END) as losses,
                    SUM(pnl) as total_pnl,
                    AVG(CASE WHEN won = 1 THEN pnl ELSE NULL END) as avg_win,
                    AVG(CASE WHEN won = 0 THEN pnl ELSE NULL END) as avg_loss,
                    MAX(pnl) as best_trade,
                    MIN(pnl) as worst_trade
                FROM closed_trades
            """)

            row = cursor.fetchone()
            total_trades = row["total_trades"] or 0
            wins = row["wins"] or 0

            return {
                "total_trades": total_trades,
                "wins": wins,
                "losses": row["losses"] or 0,
                "win_rate": (wins / total_trades * 100) if total_trades > 0 else 0,
                "total_pnl": row["total_pnl"] or 0,
                "avg_win": row["avg_win"] or 0,
                "avg_loss": row["avg_loss"] or 0,
                "best_trade": row["best_trade"] or 0,
                "worst_trade": row["worst_trade"] or 0,
            }
    except Exception as e:
        logger.error(f"Failed to get stats: {e}")
        return {
            "total_trades": 0, "wins": 0, "losses": 0, "win_rate": 0,
            "total_pnl": 0, "avg_win": 0, "avg_loss": 0,
            "best_trade": 0, "worst_trade": 0,
        }


def save_bot_state(paper_balance: float, daily_pnl: float, total_pnl: float) -> bool:
    """Save current bot state"""
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE bot_state SET
                    paper_balance = ?,
                    daily_pnl = ?,
                    total_pnl = ?,
                    updated_at = ?
                WHERE id = 1
            """, (paper_balance, daily_pnl, total_pnl, datetime.now(timezone.utc).isoformat()))
            return True
    except Exception as e:
        logger.error(f"Failed to save bot state: {e}")
        return False


def get_bot_state() -> Dict[str, Any]:
    """Get current bot state, synced from actual trade data"""
    try:
        with get_connection() as conn:
            cursor = conn.cursor()

            # Calculate actual totals from trades to ensure consistency
            # Use timezone offset for daily PnL (default: US Central = -6 hours from UTC)
            # This ensures "today" matches the user's local date, not UTC
            timezone_offset_hours = -6  # Central Time (adjust as needed)
            cursor.execute("""
                SELECT
                    COALESCE(SUM(pnl), 0) as total_pnl,
                    COALESCE(SUM(CASE WHEN date(exit_time, ? || ' hours') = date('now', ? || ' hours') THEN pnl ELSE 0 END), 0) as daily_pnl
                FROM closed_trades
            """, (str(timezone_offset_hours), str(timezone_offset_hours)))
            trade_row = cursor.fetchone()
            total_pnl = trade_row["total_pnl"] if trade_row else 0.0
            daily_pnl = trade_row["daily_pnl"] if trade_row else 0.0

            # Calculate balance from starting capital + total P&L
            starting_capital = 10000.0
            paper_balance = starting_capital + total_pnl

            # Update bot_state to stay in sync
            cursor.execute("""
                UPDATE bot_state SET
                    paper_balance = ?,
                    daily_pnl = ?,
                    total_pnl = ?,
                    updated_at = ?
                WHERE id = 1
            """, (paper_balance, daily_pnl, total_pnl, datetime.now(timezone.utc).isoformat()))

            return {
                "paper_balance": paper_balance,
                "daily_pnl": daily_pnl,
                "total_pnl": total_pnl,
                "last_daily_reset": None,
            }
    except Exception as e:
        logger.error(f"Failed to get bot state: {e}")
        return {
            "paper_balance": 10000.0,
            "daily_pnl": 0.0,
            "total_pnl": 0.0,
            "last_daily_reset": None,
        }


def reset_daily_pnl() -> bool:
    """Reset daily P&L (call at midnight)"""
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE bot_state SET
                    daily_pnl = 0.0,
                    last_daily_reset = ?,
                    updated_at = ?
                WHERE id = 1
            """, (
                datetime.now(timezone.utc).isoformat(),
                datetime.now(timezone.utc).isoformat()
            ))
            return True
    except Exception as e:
        logger.error(f"Failed to reset daily P&L: {e}")
        return False


def save_open_position(position: Dict[str, Any]) -> bool:
    """Save an open position to database"""
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO open_positions (
                    id, token_id, market, side, size, price, value, risk_amount,
                    stop_loss_price, take_profit_price, breakeven_trigger_price,
                    highest_price, breakeven_triggered, trailing_stop_active, entry_time, paper
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                position.get("id"),
                position.get("token_id"),
                position.get("market"),
                position.get("side"),
                position.get("size"),
                position.get("price"),
                position.get("value"),
                position.get("risk_amount", 0),
                position.get("stop_loss_price"),
                position.get("take_profit_price"),
                position.get("breakeven_trigger_price"),
                position.get("highest_price"),
                1 if position.get("breakeven_triggered") else 0,
                1 if position.get("trailing_stop_active") else 0,
                position.get("timestamp"),
                1 if position.get("paper") else 0,
            ))
            return True
    except Exception as e:
        logger.error(f"Failed to save open position: {e}")
        return False


def get_open_positions() -> List[Dict[str, Any]]:
    """Get all open positions from database"""
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM open_positions")
            positions = []
            for row in cursor.fetchall():
                positions.append({
                    "id": row["id"],
                    "token_id": row["token_id"],
                    "market": row["market"],
                    "side": row["side"],
                    "size": row["size"],
                    "price": row["price"],
                    "value": row["value"],
                    "risk_amount": row["risk_amount"],
                    "stop_loss_price": row["stop_loss_price"],
                    "take_profit_price": row["take_profit_price"],
                    "breakeven_trigger_price": row["breakeven_trigger_price"],
                    "highest_price": row["highest_price"],
                    "breakeven_triggered": bool(row["breakeven_triggered"]),
                    "trailing_stop_active": bool(row["trailing_stop_active"]),
                    "timestamp": row["entry_time"],
                    "paper": bool(row["paper"]),
                    "status": "FILLED",
                })
            return positions
    except Exception as e:
        logger.error(f"Failed to get open positions: {e}")
        return []


def delete_open_position(position_id: str) -> bool:
    """Delete an open position from database (when closed)"""
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM open_positions WHERE id = ?", (position_id,))
            return True
    except Exception as e:
        logger.error(f"Failed to delete open position: {e}")
        return False


def update_open_position(position: Dict[str, Any]) -> bool:
    """Update an open position (e.g., highest_price, trailing stop state)"""
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE open_positions SET
                    highest_price = ?,
                    breakeven_triggered = ?,
                    trailing_stop_active = ?,
                    stop_loss_price = ?
                WHERE id = ?
            """, (
                position.get("highest_price"),
                1 if position.get("breakeven_triggered") else 0,
                1 if position.get("trailing_stop_active") else 0,
                position.get("stop_loss_price"),
                position.get("id"),
            ))
            return True
    except Exception as e:
        logger.error(f"Failed to update open position: {e}")
        return False


# Initialize database on import
init_database()
