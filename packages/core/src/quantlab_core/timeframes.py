"""Deterministic sparse intraday timeframe construction."""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from quantlab_core.errors import ChronologyError, ContractError
from quantlab_core.market_data import Candle, MarketTrade
from quantlab_core.time import NANOSECONDS_PER_MINUTE

TIMEFRAME_ENGINE_VERSION = "2.0.0"
SUPPORTED_TIMEFRAMES: dict[str, int] = {"1m": 1, "2m": 2, "5m": 5, "15m": 15}


def timeframe_minutes(timeframe: str) -> int:
    try:
        return SUPPORTED_TIMEFRAMES[timeframe]
    except KeyError as exc:
        raise ContractError(f"unsupported timeframe: {timeframe}") from exc


def build_timeframe_candles(
    trades: Iterable[MarketTrade],
    timeframe: str,
) -> Iterator[Candle]:
    """Build sparse `[open, close)` candles anchored to local wall-clock boundaries.

    B3 DRV timestamps use fixed UTC-03 and every supported duration divides an hour,
    therefore flooring UTC epoch minutes produces the same minute boundaries as
    flooring the UTC-03 local wall clock.
    """

    duration_ns = timeframe_minutes(timeframe) * NANOSECONDS_PER_MINUTE
    current_bucket: int | None = None
    symbol: str | None = None
    open_units = high_units = low_units = close_units = 0
    volume = trade_count = 0
    previous_key: tuple[int, int] | None = None

    for trade in trades:
        if previous_key is not None and trade.order_key <= previous_key:
            raise ChronologyError("trades must have a unique, strictly increasing order key")
        previous_key = trade.order_key
        if symbol is None:
            symbol = trade.symbol
        elif trade.symbol != symbol:
            raise ContractError("one candle stream can contain only one physical contract")

        bucket = trade.timestamp_ns_utc - (trade.timestamp_ns_utc % duration_ns)
        if current_bucket is None or bucket != current_bucket:
            if current_bucket is not None:
                yield Candle(
                    symbol=symbol,
                    timeframe=timeframe,
                    open_time_ns_utc=current_bucket,
                    close_time_ns_utc=current_bucket + duration_ns,
                    open_units=open_units,
                    high_units=high_units,
                    low_units=low_units,
                    close_units=close_units,
                    volume=volume,
                    trade_count=trade_count,
                )
            current_bucket = bucket
            open_units = high_units = low_units = close_units = trade.price_units
            volume = trade.quantity
            trade_count = 1
            continue

        high_units = max(high_units, trade.price_units)
        low_units = min(low_units, trade.price_units)
        close_units = trade.price_units
        volume += trade.quantity
        trade_count += 1

    if current_bucket is not None and symbol is not None:
        yield Candle(
            symbol=symbol,
            timeframe=timeframe,
            open_time_ns_utc=current_bucket,
            close_time_ns_utc=current_bucket + duration_ns,
            open_units=open_units,
            high_units=high_units,
            low_units=low_units,
            close_units=close_units,
            volume=volume,
            trade_count=trade_count,
        )

