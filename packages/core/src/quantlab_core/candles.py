"""Deterministic sparse candle construction."""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from quantlab_core.errors import ChronologyError, ContractError
from quantlab_core.market_data import Candle, MarketTrade
from quantlab_core.time import NANOSECONDS_PER_MINUTE, floor_minute_ns

CANDLE_ENGINE_VERSION = "1.0.0"


def build_one_minute_candles(trades: Iterable[MarketTrade]) -> Iterator[Candle]:
    """Build non-filled `[open, close)` one-minute candles from ordered trades."""

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
            raise ContractError("one candle stream can contain only one symbol")

        bucket = floor_minute_ns(trade.timestamp_ns_utc)
        if current_bucket is None or bucket != current_bucket:
            if current_bucket is not None:
                yield Candle(
                    symbol=symbol,
                    timeframe="1m",
                    open_time_ns_utc=current_bucket,
                    close_time_ns_utc=current_bucket + NANOSECONDS_PER_MINUTE,
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
            timeframe="1m",
            open_time_ns_utc=current_bucket,
            close_time_ns_utc=current_bucket + NANOSECONDS_PER_MINUTE,
            open_units=open_units,
            high_units=high_units,
            low_units=low_units,
            close_units=close_units,
            volume=volume,
            trade_count=trade_count,
        )
