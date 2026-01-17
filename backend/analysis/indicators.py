"""Technical indicators for price analysis using the ta library."""

from typing import Optional
import pandas as pd
import numpy as np
from ta import trend, momentum, volatility, volume

from ..config import INDICATOR_PARAMS
from ..utils import get_analysis_logger

logger = get_analysis_logger()


class TechnicalIndicators:
    """Calculate and analyze technical indicators for price data."""

    def __init__(self, params: Optional[dict] = None):
        """
        Initialize with optional custom parameters.

        Args:
            params: Custom indicator parameters (overrides defaults)
        """
        self.params = {**INDICATOR_PARAMS, **(params or {})}

    def calculate_all(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Calculate all technical indicators for the given OHLCV data.

        Args:
            df: DataFrame with columns: open, high, low, close, volume

        Returns:
            DataFrame with indicator columns added
        """
        if df is None or df.empty:
            return df

        result = df.copy()

        # Trend indicators
        result = self._add_moving_averages(result)
        result = self._add_macd(result)

        # Momentum indicators
        result = self._add_rsi(result)
        result = self._add_stochastic(result)

        # Volatility indicators
        result = self._add_bollinger_bands(result)
        result = self._add_atr(result)

        # Volume indicators
        result = self._add_obv(result)

        return result

    def _add_moving_averages(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add SMA and EMA indicators."""
        # Simple Moving Averages
        df["sma_fast"] = trend.sma_indicator(df["close"], window=self.params["sma_fast"])
        df["sma_slow"] = trend.sma_indicator(df["close"], window=self.params["sma_slow"])

        # Exponential Moving Averages
        df["ema_fast"] = trend.ema_indicator(df["close"], window=self.params["ema_fast"])
        df["ema_slow"] = trend.ema_indicator(df["close"], window=self.params["ema_slow"])

        return df

    def _add_macd(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add MACD indicator."""
        macd_indicator = trend.MACD(
            df["close"],
            window_fast=self.params["macd_fast"],
            window_slow=self.params["macd_slow"],
            window_sign=self.params["macd_signal"],
        )
        df["macd"] = macd_indicator.macd()
        df["macd_signal"] = macd_indicator.macd_signal()
        df["macd_hist"] = macd_indicator.macd_diff()
        return df

    def _add_rsi(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add RSI indicator."""
        df["rsi"] = momentum.rsi(df["close"], window=self.params["rsi_period"])
        return df

    def _add_stochastic(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add Stochastic oscillator."""
        stoch = momentum.StochasticOscillator(
            df["high"],
            df["low"],
            df["close"],
            window=self.params["stoch_k"],
            smooth_window=self.params["stoch_d"],
        )
        df["stoch_k"] = stoch.stoch()
        df["stoch_d"] = stoch.stoch_signal()
        return df

    def _add_bollinger_bands(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add Bollinger Bands indicator."""
        bb = volatility.BollingerBands(
            df["close"],
            window=self.params["bb_period"],
            window_dev=self.params["bb_std"],
        )
        df["bb_lower"] = bb.bollinger_lband()
        df["bb_mid"] = bb.bollinger_mavg()
        df["bb_upper"] = bb.bollinger_hband()
        df["bb_bandwidth"] = bb.bollinger_wband()
        df["bb_percent"] = bb.bollinger_pband()
        return df

    def _add_atr(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add Average True Range indicator."""
        df["atr"] = volatility.average_true_range(
            df["high"],
            df["low"],
            df["close"],
            window=self.params["atr_period"],
        )
        return df

    def _add_obv(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add On-Balance Volume indicator."""
        df["obv"] = volume.on_balance_volume(df["close"], df["volume"])
        df["obv_sma"] = trend.sma_indicator(df["obv"], window=self.params["obv_signal"])
        return df

    def get_latest_values(self, df: pd.DataFrame) -> dict:
        """
        Get the latest indicator values from the DataFrame.

        Args:
            df: DataFrame with calculated indicators

        Returns:
            Dictionary of indicator names to values
        """
        if df is None or df.empty:
            return {}

        latest = df.iloc[-1]
        indicators = {}

        # List of indicator columns
        indicator_cols = [
            "close", "sma_fast", "sma_slow", "ema_fast", "ema_slow",
            "macd", "macd_signal", "macd_hist",
            "rsi",
            "stoch_k", "stoch_d",
            "bb_lower", "bb_mid", "bb_upper", "bb_percent",
            "atr", "obv", "obv_sma"
        ]

        for col in indicator_cols:
            if col in latest.index:
                value = latest[col]
                if pd.notna(value):
                    indicators[col] = float(value)

        return indicators

    def generate_signals(self, df: pd.DataFrame) -> dict:
        """
        Generate buy/sell signals from technical indicators.

        Args:
            df: DataFrame with calculated indicators

        Returns:
            Dictionary with signal for each indicator (-1 sell, 0 neutral, 1 buy)
        """
        if df is None or len(df) < 2:
            return {}

        latest = df.iloc[-1]
        prev = df.iloc[-2]
        signals = {}

        # RSI signal
        if "rsi" in latest.index and pd.notna(latest["rsi"]):
            rsi = latest["rsi"]
            if rsi < 30:
                signals["rsi"] = 1  # Oversold - buy signal
            elif rsi > 70:
                signals["rsi"] = -1  # Overbought - sell signal
            else:
                signals["rsi"] = 0

        # MACD signal (histogram direction)
        if "macd_hist" in latest.index and pd.notna(latest["macd_hist"]):
            if "macd_hist" in prev.index and pd.notna(prev["macd_hist"]):
                if latest["macd_hist"] > prev["macd_hist"] and latest["macd_hist"] > 0:
                    signals["macd"] = 1  # Bullish momentum
                elif latest["macd_hist"] < prev["macd_hist"] and latest["macd_hist"] < 0:
                    signals["macd"] = -1  # Bearish momentum
                else:
                    signals["macd"] = 0

        # SMA crossover signal
        if all(col in latest.index for col in ["sma_fast", "sma_slow"]):
            if pd.notna(latest["sma_fast"]) and pd.notna(latest["sma_slow"]):
                if latest["sma_fast"] > latest["sma_slow"]:
                    if prev["sma_fast"] <= prev["sma_slow"]:
                        signals["sma_cross"] = 1  # Golden cross
                    else:
                        signals["sma_cross"] = 0.5  # Bullish trend
                else:
                    if prev["sma_fast"] >= prev["sma_slow"]:
                        signals["sma_cross"] = -1  # Death cross
                    else:
                        signals["sma_cross"] = -0.5  # Bearish trend

        # EMA crossover signal
        if all(col in latest.index for col in ["ema_fast", "ema_slow"]):
            if pd.notna(latest["ema_fast"]) and pd.notna(latest["ema_slow"]):
                if latest["ema_fast"] > latest["ema_slow"]:
                    signals["ema_cross"] = 1 if prev["ema_fast"] <= prev["ema_slow"] else 0.5
                else:
                    signals["ema_cross"] = -1 if prev["ema_fast"] >= prev["ema_slow"] else -0.5

        # Bollinger Bands signal
        if all(col in latest.index for col in ["close", "bb_lower", "bb_upper"]):
            if pd.notna(latest["bb_lower"]) and pd.notna(latest["bb_upper"]):
                if latest["close"] < latest["bb_lower"]:
                    signals["bollinger"] = 1  # Price below lower band - oversold
                elif latest["close"] > latest["bb_upper"]:
                    signals["bollinger"] = -1  # Price above upper band - overbought
                else:
                    signals["bollinger"] = 0

        # Stochastic signal
        if all(col in latest.index for col in ["stoch_k", "stoch_d"]):
            if pd.notna(latest["stoch_k"]) and pd.notna(latest["stoch_d"]):
                if latest["stoch_k"] < 20 and latest["stoch_k"] > latest["stoch_d"]:
                    signals["stochastic"] = 1  # Oversold with bullish crossover
                elif latest["stoch_k"] > 80 and latest["stoch_k"] < latest["stoch_d"]:
                    signals["stochastic"] = -1  # Overbought with bearish crossover
                else:
                    signals["stochastic"] = 0

        return signals


def calculate_indicators(df: pd.DataFrame, params: Optional[dict] = None) -> pd.DataFrame:
    """
    Convenience function to calculate all indicators.

    Args:
        df: OHLCV DataFrame
        params: Optional custom parameters

    Returns:
        DataFrame with indicators added
    """
    calculator = TechnicalIndicators(params)
    return calculator.calculate_all(df)
