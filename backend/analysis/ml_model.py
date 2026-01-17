"""Machine Learning model for Bitcoin price prediction."""

from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
import joblib

from ..config import get_settings
from ..utils import get_analysis_logger

logger = get_analysis_logger()


class MLPredictor:
    """
    Machine learning model for predicting Bitcoin price direction.
    Uses Gradient Boosting with technical indicators as features.
    """

    def __init__(self, model_path: Optional[str] = None):
        """
        Initialize the ML predictor.

        Args:
            model_path: Path to load/save model. If None, uses default.
        """
        self.settings = get_settings()
        self.model_path = Path(model_path) if model_path else Path("models/btc_predictor.joblib")
        self.scaler_path = self.model_path.with_suffix(".scaler.joblib")
        self.columns_path = self.model_path.with_suffix(".columns.joblib")

        self.model: Optional[GradientBoostingClassifier] = None
        self.scaler: Optional[StandardScaler] = None
        self._last_train_time: Optional[datetime] = None
        self._feature_columns: list[str] = []

        # Try to load existing model
        self._load_model()

    def _load_model(self) -> bool:
        """Load model, scaler, and feature columns from disk if available."""
        try:
            if self.model_path.exists() and self.scaler_path.exists():
                self.model = joblib.load(self.model_path)
                self.scaler = joblib.load(self.scaler_path)
                # Load feature columns if available
                if self.columns_path.exists():
                    self._feature_columns = joblib.load(self.columns_path)
                logger.info(f"Loaded model from {self.model_path}")
                return True
        except Exception as e:
            logger.warning(f"Could not load model: {e}")
        return False

    def _save_model(self):
        """Save model, scaler, and feature columns to disk."""
        try:
            self.model_path.parent.mkdir(parents=True, exist_ok=True)
            joblib.dump(self.model, self.model_path)
            joblib.dump(self.scaler, self.scaler_path)
            joblib.dump(self._feature_columns, self.columns_path)
            logger.info(f"Saved model to {self.model_path}")
        except Exception as e:
            logger.error(f"Could not save model: {e}")

    def prepare_features(self, df: pd.DataFrame) -> Tuple[pd.DataFrame, list[str]]:
        """
        Prepare features for ML model from indicator DataFrame.

        Args:
            df: DataFrame with OHLCV and indicator data

        Returns:
            Tuple of (feature DataFrame, feature column names)
        """
        feature_df = pd.DataFrame(index=df.index)

        # Price-based features
        feature_df["returns"] = df["close"].pct_change()
        feature_df["returns_2"] = df["close"].pct_change(2)
        feature_df["returns_5"] = df["close"].pct_change(5)
        feature_df["returns_10"] = df["close"].pct_change(10)

        # Volatility features
        feature_df["volatility_5"] = feature_df["returns"].rolling(5).std()
        feature_df["volatility_10"] = feature_df["returns"].rolling(10).std()

        # Momentum features (Rate of Change)
        feature_df["roc_3"] = df["close"].pct_change(3)
        feature_df["roc_6"] = df["close"].pct_change(6)

        # Price position features
        feature_df["high_low_range"] = (df["high"] - df["low"]) / df["close"]
        feature_df["close_position"] = (df["close"] - df["low"]) / (df["high"] - df["low"] + 0.0001)

        # Candle features
        feature_df["body_size"] = abs(df["close"] - df["open"]) / df["close"]
        feature_df["upper_wick"] = (df["high"] - df[["open", "close"]].max(axis=1)) / df["close"]
        feature_df["lower_wick"] = (df[["open", "close"]].min(axis=1) - df["low"]) / df["close"]

        # Trend features
        feature_df["higher_high"] = (df["high"] > df["high"].shift(1)).astype(int)
        feature_df["lower_low"] = (df["low"] < df["low"].shift(1)).astype(int)
        feature_df["higher_close"] = (df["close"] > df["close"].shift(1)).astype(int)

        # Rolling momentum
        feature_df["up_candles_5"] = feature_df["higher_close"].rolling(5).sum()
        feature_df["price_vs_sma5"] = df["close"] / df["close"].rolling(5).mean() - 1

        # Volume features
        if "volume" in df.columns:
            feature_df["volume_change"] = df["volume"].pct_change()
            feature_df["volume_sma_ratio"] = df["volume"] / df["volume"].rolling(20).mean()
            feature_df["volume_trend"] = df["volume"].rolling(5).mean() / df["volume"].rolling(20).mean()

        # Technical indicator features (if available)
        indicator_cols = [
            "rsi", "macd", "macd_signal", "macd_hist",
            "stoch_k", "stoch_d", "bb_percent", "atr"
        ]

        for col in indicator_cols:
            if col in df.columns:
                feature_df[col] = df[col]

        # Moving average features
        if "sma_fast" in df.columns and "sma_slow" in df.columns:
            feature_df["sma_ratio"] = df["sma_fast"] / df["sma_slow"]

        if "ema_fast" in df.columns and "ema_slow" in df.columns:
            feature_df["ema_ratio"] = df["ema_fast"] / df["ema_slow"]

        # Price relative to Bollinger Bands
        if "bb_upper" in df.columns and "bb_lower" in df.columns:
            bb_range = df["bb_upper"] - df["bb_lower"]
            feature_df["bb_position"] = (df["close"] - df["bb_lower"]) / bb_range

        # Drop NaN rows
        feature_df = feature_df.dropna()

        feature_columns = list(feature_df.columns)
        return feature_df, feature_columns

    def prepare_target(
        self,
        df: pd.DataFrame,
        lookahead: int = 1,
        threshold: float = 0.001,
    ) -> pd.Series:
        """
        Prepare target variable for training.

        Args:
            df: DataFrame with close prices
            lookahead: Number of periods to look ahead
            threshold: Minimum price change to consider (filters noise)

        Returns:
            Series with 1 (UP) or 0 (DOWN) labels
        """
        future_returns = df["close"].pct_change(lookahead).shift(-lookahead)

        # Binary classification: 1 = UP, 0 = DOWN
        target = (future_returns > threshold).astype(int)

        return target

    def train(
        self,
        df: pd.DataFrame,
        lookahead: int = 1,
        test_size: float = 0.2,
    ) -> dict:
        """
        Train the model on historical data.

        Args:
            df: DataFrame with OHLCV and indicators
            lookahead: Periods to predict ahead
            test_size: Fraction of data for testing

        Returns:
            Dict with training metrics
        """
        logger.info("Training ML model...")

        # Prepare features and target
        features, self._feature_columns = self.prepare_features(df)
        target = self.prepare_target(df, lookahead)

        # Align features and target
        common_idx = features.index.intersection(target.dropna().index)
        X = features.loc[common_idx]
        y = target.loc[common_idx]

        if len(X) < 100:
            logger.warning("Not enough data for training")
            return {"error": "Insufficient data", "samples": len(X)}

        # Split data
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=test_size, shuffle=False
        )

        # Scale features
        self.scaler = StandardScaler()
        X_train_scaled = self.scaler.fit_transform(X_train)
        X_test_scaled = self.scaler.transform(X_test)

        # Train model
        self.model = GradientBoostingClassifier(
            n_estimators=100,
            max_depth=5,
            learning_rate=0.1,
            min_samples_split=20,
            min_samples_leaf=10,
            random_state=42,
        )
        self.model.fit(X_train_scaled, y_train)

        # Evaluate
        train_accuracy = self.model.score(X_train_scaled, y_train)
        test_accuracy = self.model.score(X_test_scaled, y_test)

        # Feature importance
        feature_importance = dict(zip(
            self._feature_columns,
            self.model.feature_importances_
        ))

        self._last_train_time = datetime.now()
        self._save_model()

        metrics = {
            "train_samples": len(X_train),
            "test_samples": len(X_test),
            "train_accuracy": float(train_accuracy),
            "test_accuracy": float(test_accuracy),
            "feature_importance": feature_importance,
            "trained_at": self._last_train_time.isoformat(),
        }

        logger.info(f"Model trained - Test accuracy: {test_accuracy:.2%}")
        return metrics

    def predict(self, df: pd.DataFrame) -> dict:
        """
        Make a prediction for the current market state.

        Args:
            df: DataFrame with OHLCV and indicators

        Returns:
            Dict with prediction and confidence
        """
        if self.model is None or self.scaler is None:
            # Initialize a basic model if none exists
            logger.warning("No trained model - using default prediction")
            return {
                "direction": "NEUTRAL",
                "confidence": 50.0,
                "probabilities": {"UP": 0.5, "DOWN": 0.5},
                "model_ready": False,
            }

        try:
            # Prepare features
            features, feature_cols = self.prepare_features(df)

            if features.empty:
                return {
                    "direction": "NEUTRAL",
                    "confidence": 50.0,
                    "model_ready": True,
                    "error": "No valid features",
                }

            # Get latest features
            latest = features.iloc[[-1]]

            # If no feature columns saved, use what we have (backward compatibility)
            if not self._feature_columns:
                self._feature_columns = feature_cols
                # Save for future use
                self._save_model()

            # Ensure we have all required columns
            missing_cols = set(self._feature_columns) - set(latest.columns)
            if missing_cols:
                for col in missing_cols:
                    latest[col] = 0

            # Reorder columns to match training
            if self._feature_columns:
                latest = latest[self._feature_columns]

            # Scale and predict
            X_scaled = self.scaler.transform(latest)
            prediction = self.model.predict(X_scaled)[0]
            probabilities = self.model.predict_proba(X_scaled)[0]

            # Get confidence (probability of predicted class)
            confidence = float(max(probabilities) * 100)

            direction = "UP" if prediction == 1 else "DOWN"

            return {
                "direction": direction,
                "confidence": confidence,
                "probabilities": {
                    "UP": float(probabilities[1]) if len(probabilities) > 1 else 0.5,
                    "DOWN": float(probabilities[0]) if len(probabilities) > 0 else 0.5,
                },
                "model_ready": True,
            }

        except Exception as e:
            logger.error(f"Prediction error: {e}")
            return {
                "direction": "NEUTRAL",
                "confidence": 50.0,
                "model_ready": True,
                "error": str(e),
            }

    def should_retrain(self) -> bool:
        """Check if the model should be retrained."""
        if self._last_train_time is None:
            return True

        elapsed = (datetime.now() - self._last_train_time).total_seconds()
        return elapsed > self.settings.ml_retrain_interval

    @property
    def is_ready(self) -> bool:
        """Check if the model is ready for predictions."""
        return self.model is not None and self.scaler is not None


# Singleton instance
_ml_predictor: Optional[MLPredictor] = None


def get_ml_predictor() -> MLPredictor:
    """Get the global ML predictor instance."""
    global _ml_predictor
    if _ml_predictor is None:
        _ml_predictor = MLPredictor()
    return _ml_predictor
