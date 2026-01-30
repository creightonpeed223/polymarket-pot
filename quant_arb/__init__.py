"""
Quantitative Arbitrage Bot
Cross-platform news arbitrage for prediction markets
"""

from .bot import QuantArbitrageBot
from .config import Config, CONFIG

__all__ = ["QuantArbitrageBot", "Config", "CONFIG"]
__version__ = "1.0.0"
