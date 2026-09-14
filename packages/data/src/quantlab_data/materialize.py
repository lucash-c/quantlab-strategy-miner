"""Streaming candle and feature materialization."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from quantlab_core.candles import CANDLE_ENGINE_VERSION, build_one_minute_candles
from quantlab_core.canonical import sha256_file
from quantlab_core.indicators import (
    INDICATOR_ENGINE_VERSION,
    SMA_CLOSE_VERSION,
    calculate_sma_close,
)
from quantlab_core.market_data import Candle

from quantlab_data.parquet import (
    CANDLE_SCHEMA,
    FEATURE_SCHEMA,
    iter_candles,
    iter_trades,
    new_semantic_digest,
    update_semantic_digest,
    write_record_batches,
)


def _candle_record(candle: Candle) -> dict[str, object]:
    return {
        "symbol": candle.symbol,
        "timeframe": candle.timeframe,
        "open_time_ns_utc": candle.open_time_ns_utc,
        "close_time_ns_utc": candle.close_time_ns_utc,
        "open_units": candle.open_units,
        "high_units": candle.high_units,
        "low_units": candle.low_units,
        "close_units": candle.close_units,
        "volume": candle.volume,
        "trade_count": candle.trade_count,
    }


def materialize_one_minute_candles(source: Path, destination: Path) -> dict[str, object]:
    digest = new_semantic_digest("candles-1m/v1")

    def records() -> Iterator[dict[str, object]]:
        for candle in build_one_minute_candles(iter_trades(source)):
            record = _candle_record(candle)
            update_semantic_digest(digest, record.values())
            yield record

    row_count = write_record_batches(destination, CANDLE_SCHEMA, records())
    return {
        "file": destination.name,
        "row_count": row_count,
        "byte_sha256": sha256_file(destination),
        "semantic_sha256": digest.hexdigest(),
        "engine": {"name": "candle-engine", "version": CANDLE_ENGINE_VERSION},
        "timeframe": "1m",
        "empty_interval_policy": "DO_NOT_FILL",
        "interval": "[open_time,close_time)",
    }


def materialize_sma_features(
    source: Path,
    destination: Path,
    *,
    period: int,
) -> dict[str, object]:
    digest = new_semantic_digest("features-1m-sma/v1")

    def records() -> Iterator[dict[str, object]]:
        for feature in calculate_sma_close(iter_candles(source), period):
            record = _candle_record(feature.candle)
            record.update(
                {
                    "sma_close_sum_units": feature.sma_close_sum_units,
                    "sma_close_period": feature.sma_close_period,
                    "available_at_ns_utc": feature.available_at_ns_utc,
                }
            )
            update_semantic_digest(digest, record.values())
            yield record

    row_count = write_record_batches(destination, FEATURE_SCHEMA, records())
    return {
        "file": destination.name,
        "row_count": row_count,
        "byte_sha256": sha256_file(destination),
        "semantic_sha256": digest.hexdigest(),
        "engine": {"name": "indicator-engine", "version": INDICATOR_ENGINE_VERSION},
        "indicator": {
            "name": "sma_close",
            "version": SMA_CLOSE_VERSION,
            "period": period,
            "representation": "rational_sum_over_period",
        },
    }
