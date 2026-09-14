"""Canonical market-data ingestion package."""

from quantlab_data.materialize import (
    materialize_one_minute_candles,
    materialize_sma_features,
)
from quantlab_data.normalizer import CSV_CONTRACT_VERSION, NORMALIZER_VERSION, normalize_csv

DATA_ENGINE_VERSION = "0.1.0"

__all__ = [
    "CSV_CONTRACT_VERSION",
    "DATA_ENGINE_VERSION",
    "NORMALIZER_VERSION",
    "materialize_one_minute_candles",
    "materialize_sma_features",
    "normalize_csv",
]
