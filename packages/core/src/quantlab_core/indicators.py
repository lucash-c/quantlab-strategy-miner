"""Versioned deterministic indicator calculations."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Iterator

from quantlab_core.errors import ChronologyError, ContractError
from quantlab_core.market_data import Candle, FeatureRow

INDICATOR_ENGINE_VERSION = "1.0.0"
SMA_CLOSE_VERSION = "1.0.0"


def calculate_sma_close(candles: Iterable[Candle], period: int) -> Iterator[FeatureRow]:
    """Attach an exact SMA represented by numerator and denominator."""

    if period <= 0:
        raise ContractError("SMA period must be positive")
    window: deque[int] = deque()
    rolling_sum = 0
    previous_close_time: int | None = None

    for candle in candles:
        if previous_close_time is not None and candle.open_time_ns_utc < previous_close_time:
            raise ChronologyError("candles must be ordered and non-overlapping")
        previous_close_time = candle.close_time_ns_utc
        window.append(candle.close_units)
        rolling_sum += candle.close_units
        if len(window) > period:
            rolling_sum -= window.popleft()
        ready = len(window) == period
        yield FeatureRow(
            candle=candle,
            sma_close_sum_units=rolling_sum if ready else None,
            sma_close_period=period if ready else None,
            available_at_ns_utc=candle.close_time_ns_utc,
        )
