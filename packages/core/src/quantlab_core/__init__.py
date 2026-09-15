"""Shared deterministic QuantLab domain package."""

from quantlab_core.candles import CANDLE_ENGINE_VERSION, build_one_minute_candles
from quantlab_core.indicators import INDICATOR_ENGINE_VERSION, calculate_sma_close
from quantlab_core.market_data import (
    Candle,
    FeatureRow,
    MarketTrade,
    SessionCandle,
    SessionFeatureRow,
    SessionTrade,
)
from quantlab_core.numeric import CanonicalRational
from quantlab_core.sessions import HistoricalDataset, TradingSession
from quantlab_core.strategy import STRATEGY_SCHEMA_VERSION, StrategyDefinition, strategy_json_schema
from quantlab_core.timeframes import TIMEFRAME_ENGINE_VERSION, build_timeframe_candles

CORE_VERSION = "0.1.0"

__all__ = [
    "CANDLE_ENGINE_VERSION",
    "CORE_VERSION",
    "INDICATOR_ENGINE_VERSION",
    "Candle",
    "CanonicalRational",
    "FeatureRow",
    "HistoricalDataset",
    "MarketTrade",
    "SessionCandle",
    "SessionFeatureRow",
    "SessionTrade",
    "STRATEGY_SCHEMA_VERSION",
    "StrategyDefinition",
    "TIMEFRAME_ENGINE_VERSION",
    "TradingSession",
    "build_one_minute_candles",
    "calculate_sma_close",
    "build_timeframe_candles",
    "strategy_json_schema",
]
