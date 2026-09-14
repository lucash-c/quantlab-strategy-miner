"""Shared deterministic QuantLab domain package."""

from quantlab_core.candles import CANDLE_ENGINE_VERSION, build_one_minute_candles
from quantlab_core.indicators import INDICATOR_ENGINE_VERSION, calculate_sma_close
from quantlab_core.market_data import Candle, FeatureRow, MarketTrade
from quantlab_core.strategy import STRATEGY_SCHEMA_VERSION, StrategyDefinition, strategy_json_schema

CORE_VERSION = "0.1.0"

__all__ = [
    "CANDLE_ENGINE_VERSION",
    "CORE_VERSION",
    "INDICATOR_ENGINE_VERSION",
    "Candle",
    "FeatureRow",
    "MarketTrade",
    "STRATEGY_SCHEMA_VERSION",
    "StrategyDefinition",
    "build_one_minute_candles",
    "calculate_sma_close",
    "strategy_json_schema",
]
