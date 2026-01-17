"""Signal combination and aggregation for trading decisions."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

from ..config import SIGNAL_WEIGHTS, get_settings
from ..utils import get_analysis_logger
from .indicators import TechnicalIndicators
from .ml_model import get_ml_predictor

logger = get_analysis_logger()


@dataclass
class TradingSignal:
    """Represents a combined trading signal."""

    direction: str  # "BUY", "SELL", or "HOLD"
    confidence: float  # 0-100
    strength: float  # -1 to 1 (negative = bearish, positive = bullish)
    timestamp: datetime
    timeframe: str
    indicator_signals: dict = field(default_factory=dict)
    ml_prediction: Optional[dict] = None
    reasons: list = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert signal to dictionary."""
        return {
            "direction": self.direction,
            "confidence": self.confidence,
            "strength": self.strength,
            "timestamp": self.timestamp.isoformat(),
            "timeframe": self.timeframe,
            "indicator_signals": self.indicator_signals,
            "ml_prediction": self.ml_prediction,
            "reasons": self.reasons,
        }


class SignalGenerator:
    """
    Combines technical indicators and ML predictions into trading signals.
    """

    def __init__(self, weights: Optional[dict] = None):
        """
        Initialize the signal generator.

        Args:
            weights: Custom signal weights (overrides defaults)
        """
        self.weights = weights or SIGNAL_WEIGHTS
        self.settings = get_settings()
        self.indicators = TechnicalIndicators()
        self.ml_predictor = get_ml_predictor()

    def generate_signal(
        self,
        df: pd.DataFrame,
        timeframe: str = "15m",
        include_ml: bool = True,
    ) -> TradingSignal:
        """
        Generate a combined trading signal from price data.

        Args:
            df: OHLCV DataFrame
            timeframe: The timeframe of the data
            include_ml: Whether to include ML predictions

        Returns:
            TradingSignal with combined analysis
        """
        # Calculate indicators if not already present
        if "rsi" not in df.columns:
            df = self.indicators.calculate_all(df)

        # Get indicator signals
        indicator_signals = self.indicators.generate_signals(df)

        # Get ML prediction
        ml_prediction = None
        if include_ml and self.ml_predictor.is_ready:
            ml_prediction = self.ml_predictor.predict(df)

        # Combine signals
        combined = self._combine_signals(indicator_signals, ml_prediction)

        # Determine direction and confidence
        direction = self._determine_direction(combined["strength"])
        confidence = combined["confidence"]

        # Generate reasons
        reasons = self._generate_reasons(indicator_signals, ml_prediction)

        signal = TradingSignal(
            direction=direction,
            confidence=confidence,
            strength=combined["strength"],
            timestamp=datetime.now(timezone.utc),
            timeframe=timeframe,
            indicator_signals=indicator_signals,
            ml_prediction=ml_prediction,
            reasons=reasons,
        )

        logger.debug(f"Generated signal: {direction} @ {confidence:.1f}% confidence")
        return signal

    def _combine_signals(
        self,
        indicator_signals: dict,
        ml_prediction: Optional[dict],
    ) -> dict:
        """
        Combine indicator and ML signals using weighted average.

        Returns:
            Dict with combined strength and confidence
        """
        total_weight = 0
        weighted_sum = 0

        # Combine indicator signals
        for indicator, signal_value in indicator_signals.items():
            weight = self.weights.get(indicator, 0.1)
            weighted_sum += signal_value * weight
            total_weight += weight

        # Add ML prediction
        if ml_prediction and ml_prediction.get("model_ready"):
            ml_weight = self.weights.get("ml_prediction", 0.3)

            # Convert ML direction to signal value
            ml_direction = ml_prediction.get("direction", "NEUTRAL")
            ml_confidence = ml_prediction.get("confidence", 50) / 100

            if ml_direction == "UP":
                ml_signal = ml_confidence
            elif ml_direction == "DOWN":
                ml_signal = -ml_confidence
            else:
                ml_signal = 0

            weighted_sum += ml_signal * ml_weight
            total_weight += ml_weight

        # Calculate combined strength (-1 to 1)
        if total_weight > 0:
            strength = weighted_sum / total_weight
        else:
            strength = 0

        # Calculate confidence based on signal agreement
        confidence = self._calculate_confidence(
            indicator_signals, ml_prediction, strength
        )

        return {"strength": strength, "confidence": confidence}

    def _calculate_confidence(
        self,
        indicator_signals: dict,
        ml_prediction: Optional[dict],
        strength: float,
    ) -> float:
        """
        Calculate confidence based on signal agreement and strength.

        Returns:
            Confidence percentage (0-100)
        """
        if not indicator_signals:
            return 50.0

        # Base confidence from strength
        base_confidence = abs(strength) * 50  # 0-50 based on strength

        # Agreement bonus: how many indicators agree
        signal_values = list(indicator_signals.values())
        if len(signal_values) > 0:
            positive_count = sum(1 for s in signal_values if s > 0)
            negative_count = sum(1 for s in signal_values if s < 0)

            total = len(signal_values)
            agreement_ratio = max(positive_count, negative_count) / total
            agreement_bonus = agreement_ratio * 30  # 0-30 bonus
        else:
            agreement_bonus = 0

        # ML agreement bonus
        ml_bonus = 0
        if ml_prediction and ml_prediction.get("model_ready"):
            ml_direction = ml_prediction.get("direction", "NEUTRAL")
            ml_confidence = ml_prediction.get("confidence", 50)

            # Check if ML agrees with overall signal direction
            if (strength > 0 and ml_direction == "UP") or \
               (strength < 0 and ml_direction == "DOWN"):
                ml_bonus = (ml_confidence - 50) / 50 * 20  # 0-20 bonus

        confidence = min(100, base_confidence + agreement_bonus + ml_bonus)
        return max(0, confidence)

    def _determine_direction(self, strength: float) -> str:
        """Determine trading direction from signal strength."""
        if strength > 0.2:
            return "BUY"
        elif strength < -0.2:
            return "SELL"
        else:
            return "HOLD"

    def _generate_reasons(
        self,
        indicator_signals: dict,
        ml_prediction: Optional[dict],
    ) -> list[str]:
        """Generate human-readable reasons for the signal."""
        reasons = []

        # RSI reasons
        if "rsi" in indicator_signals:
            if indicator_signals["rsi"] == 1:
                reasons.append("RSI indicates oversold conditions")
            elif indicator_signals["rsi"] == -1:
                reasons.append("RSI indicates overbought conditions")

        # MACD reasons
        if "macd" in indicator_signals:
            if indicator_signals["macd"] == 1:
                reasons.append("MACD showing bullish momentum")
            elif indicator_signals["macd"] == -1:
                reasons.append("MACD showing bearish momentum")

        # Moving average reasons
        if "sma_cross" in indicator_signals:
            if indicator_signals["sma_cross"] == 1:
                reasons.append("SMA golden cross detected")
            elif indicator_signals["sma_cross"] == -1:
                reasons.append("SMA death cross detected")

        # Bollinger Band reasons
        if "bollinger" in indicator_signals:
            if indicator_signals["bollinger"] == 1:
                reasons.append("Price below lower Bollinger Band")
            elif indicator_signals["bollinger"] == -1:
                reasons.append("Price above upper Bollinger Band")

        # ML prediction reasons
        if ml_prediction and ml_prediction.get("model_ready"):
            direction = ml_prediction.get("direction", "NEUTRAL")
            confidence = ml_prediction.get("confidence", 50)
            if direction != "NEUTRAL":
                reasons.append(f"ML model predicts {direction} with {confidence:.0f}% confidence")

        return reasons


class MultiTimeframeSignalAggregator:
    """
    Aggregates signals across multiple timeframes for more robust decisions.
    """

    def __init__(self):
        self.generator = SignalGenerator()
        self.timeframe_weights = {
            "1m": 0.05,
            "5m": 0.10,
            "15m": 0.25,
            "1h": 0.30,
            "4h": 0.20,
            "1d": 0.10,
        }

    def aggregate_signals(
        self,
        data: dict[str, pd.DataFrame],
    ) -> TradingSignal:
        """
        Aggregate signals from multiple timeframes.

        Args:
            data: Dict mapping timeframe to DataFrame

        Returns:
            Aggregated TradingSignal
        """
        signals: dict[str, TradingSignal] = {}
        total_weight = 0
        weighted_strength = 0

        for tf, df in data.items():
            if df is not None and not df.empty:
                signal = self.generator.generate_signal(df, tf)
                signals[tf] = signal

                weight = self.timeframe_weights.get(tf, 0.1)
                weighted_strength += signal.strength * weight
                total_weight += weight

        if total_weight > 0:
            combined_strength = weighted_strength / total_weight
        else:
            combined_strength = 0

        # Calculate agreement across timeframes
        direction = self._determine_direction(combined_strength)
        confidence = self._calculate_mtf_confidence(signals, combined_strength)

        # Get primary timeframe signal details
        primary_tf = "15m"
        primary_signal = signals.get(primary_tf)

        return TradingSignal(
            direction=direction,
            confidence=confidence,
            strength=combined_strength,
            timestamp=datetime.now(timezone.utc),
            timeframe="MTF",
            indicator_signals=primary_signal.indicator_signals if primary_signal else {},
            ml_prediction=primary_signal.ml_prediction if primary_signal else None,
            reasons=self._generate_mtf_reasons(signals, direction),
        )

    def _determine_direction(self, strength: float) -> str:
        """Determine direction from combined strength."""
        if strength > 0.15:
            return "BUY"
        elif strength < -0.15:
            return "SELL"
        else:
            return "HOLD"

    def _calculate_mtf_confidence(
        self,
        signals: dict[str, TradingSignal],
        combined_strength: float,
    ) -> float:
        """Calculate confidence based on timeframe agreement."""
        if not signals:
            return 50.0

        # Count agreeing timeframes
        buy_count = sum(1 for s in signals.values() if s.direction == "BUY")
        sell_count = sum(1 for s in signals.values() if s.direction == "SELL")

        total = len(signals)
        max_agreement = max(buy_count, sell_count)
        agreement_ratio = max_agreement / total

        base_confidence = abs(combined_strength) * 50
        agreement_bonus = agreement_ratio * 40

        return min(100, base_confidence + agreement_bonus)

    def _generate_mtf_reasons(
        self,
        signals: dict[str, TradingSignal],
        direction: str,
    ) -> list[str]:
        """Generate reasons from multi-timeframe analysis."""
        reasons = []

        agreeing = [tf for tf, s in signals.items() if s.direction == direction]
        if agreeing:
            reasons.append(f"{direction} signal on timeframes: {', '.join(agreeing)}")

        # Add specific reasons from primary timeframes
        for tf in ["15m", "1h"]:
            if tf in signals:
                reasons.extend(signals[tf].reasons[:2])

        return reasons[:5]  # Limit to 5 reasons


# Singleton instances
_signal_generator: Optional[SignalGenerator] = None
_mtf_aggregator: Optional[MultiTimeframeSignalAggregator] = None


def get_signal_generator() -> SignalGenerator:
    """Get the global signal generator instance."""
    global _signal_generator
    if _signal_generator is None:
        _signal_generator = SignalGenerator()
    return _signal_generator


def get_mtf_aggregator() -> MultiTimeframeSignalAggregator:
    """Get the global MTF aggregator instance."""
    global _mtf_aggregator
    if _mtf_aggregator is None:
        _mtf_aggregator = MultiTimeframeSignalAggregator()
    return _mtf_aggregator
