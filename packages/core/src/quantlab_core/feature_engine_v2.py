"""Streaming, session-scoped Indicator/Feature Engine v2."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import Literal

from quantlab_core.errors import ChronologyError, ContractError
from quantlab_core.feature_specs import DIRECT_FEATURES, FeatureSpec
from quantlab_core.market_data import Candle, MarketTrade
from quantlab_core.numeric import (
    CALCULATION_DECIMAL_SCALE,
    MATHEMATICAL_PRECISION_POLICY_VERSION,
    CanonicalRational,
    checked_int64,
    price_units_to_fixed9,
    round_half_even,
)
from quantlab_core.sessions import TradingSession
from quantlab_core.time import NANOSECONDS_PER_MINUTE

FEATURE_ENGINE_V2_VERSION = "2.0.0"
FEATURE_SERIES_SCHEMA_VERSION = "feature-series/v3"
FeatureValueKind = Literal["NUMERIC", "BOOLEAN", "ENUM"]


@dataclass(frozen=True, slots=True)
class FeatureValue:
    kind: FeatureValueKind
    dimension: str
    numeric: CanonicalRational | None = None
    boolean: bool | None = None
    text: str | None = None

    def __post_init__(self) -> None:
        populated = sum(value is not None for value in (self.numeric, self.boolean, self.text))
        if populated != 1:
            raise ContractError("FeatureValue must contain exactly one value")
        if self.kind == "NUMERIC" and self.numeric is None:
            raise ContractError("numeric FeatureValue requires a rational")
        if self.kind == "BOOLEAN" and self.boolean is None:
            raise ContractError("boolean FeatureValue requires a boolean")
        if self.kind == "ENUM" and self.text is None:
            raise ContractError("enum FeatureValue requires text")

    @classmethod
    def number(cls, dimension: str, value: CanonicalRational) -> FeatureValue:
        return cls("NUMERIC", dimension, numeric=value)

    @classmethod
    def flag(cls, value: bool) -> FeatureValue:
        return cls("BOOLEAN", "BOOLEAN", boolean=value)

    @classmethod
    def enum(cls, dimension: str, value: str) -> FeatureValue:
        return cls("ENUM", dimension, text=value)

    def to_record(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "dimension": self.dimension,
            "numeric": self.numeric.to_record() if self.numeric is not None else None,
            "boolean": self.boolean,
            "text": self.text,
        }


@dataclass(frozen=True, slots=True)
class FeatureObservation:
    session_id: str
    trading_date: str
    logical_asset: str
    physical_contract: str
    timeframe: str
    candle_open_time_ns_utc: int
    candle_close_time_ns_utc: int
    available_at_ns_utc: int
    warmup_status: Literal["WARMING_UP", "READY"]
    executable_in_session: bool
    non_executable_reason: str | None
    value: FeatureValue | None
    undefined_reason: str | None

    def __post_init__(self) -> None:
        if self.warmup_status == "WARMING_UP" and self.value is not None:
            raise ContractError("warming-up observation cannot contain a value")
        if self.value is not None and self.undefined_reason is not None:
            raise ContractError("defined observation cannot have undefined_reason")
        if self.executable_in_session and self.non_executable_reason is not None:
            raise ContractError("executable observation cannot have non-executable reason")


class _RollingWindow:
    def __init__(self, period: int) -> None:
        self.period = period
        self.values: deque[int] = deque()
        self.total = 0
        self.maximum: deque[int] = deque()
        self.minimum: deque[int] = deque()

    def append(self, value: int) -> None:
        self.values.append(value)
        self.total += value
        while self.maximum and self.maximum[-1] < value:
            self.maximum.pop()
        self.maximum.append(value)
        while self.minimum and self.minimum[-1] > value:
            self.minimum.pop()
        self.minimum.append(value)
        if len(self.values) > self.period:
            removed = self.values.popleft()
            self.total -= removed
            if self.maximum[0] == removed:
                self.maximum.popleft()
            if self.minimum[0] == removed:
                self.minimum.popleft()

    @property
    def ready(self) -> bool:
        return len(self.values) == self.period


def _price(value: int, scale: int) -> CanonicalRational:
    return CanonicalRational(value, 10**scale)


def _enum_compare(left: CanonicalRational, right: CanonicalRational) -> str:
    comparison = left.compare(right)
    return "ABOVE" if comparison > 0 else "BELOW" if comparison < 0 else "EQUAL"


def _observation(
    session: TradingSession,
    candle: Candle,
    value: FeatureValue | None,
    *,
    warming_up: bool = False,
    undefined_reason: str | None = None,
) -> FeatureObservation:
    executable = candle.close_time_ns_utc <= session.last_event_ns_utc
    return FeatureObservation(
        session.session_id,
        session.trading_date,
        session.logical_asset,
        session.physical_contract,
        candle.timeframe,
        candle.open_time_ns_utc,
        candle.close_time_ns_utc,
        candle.close_time_ns_utc,
        "WARMING_UP" if warming_up else "READY",
        executable,
        None if executable else "AVAILABLE_AFTER_LAST_ELIGIBLE_TRADE",
        value,
        undefined_reason,
    )


def calculate_feature_series(
    session: TradingSession,
    candles: Iterable[Candle],
    spec: FeatureSpec,
    *,
    trades: Iterable[MarketTrade] | None = None,
) -> Iterator[FeatureObservation]:
    """Calculate one direct feature series without crossing its TradingSession."""

    if spec.name not in DIRECT_FEATURES:
        raise ContractError(f"derived feature requires dependencies: {spec.name}")
    period = int(spec.parameter("period")) if dict(spec.parameters).get("period") else None
    include_current = (
        bool(spec.parameter("include_current"))
        if "include_current" in dict(spec.parameters)
        else True
    )
    rolling = _RollingWindow(period) if period is not None else None
    closes: deque[int] = deque()
    ema_value: int | None = None
    atr_value: int | None = None
    previous_close: int | None = None
    cumulative_notional = 0
    cumulative_quantity = 0
    trade_iterator = iter(trades) if trades is not None else iter(())
    next_trade = next(trade_iterator, None)
    previous_candle_close: int | None = None
    anchor = session.first_event_ns_utc - (
        session.first_event_ns_utc % NANOSECONDS_PER_MINUTE
    )

    for candle in candles:
        if candle.symbol != session.physical_contract:
            raise ContractError("candle physical contract does not match TradingSession")
        if previous_candle_close is not None and candle.open_time_ns_utc < previous_candle_close:
            raise ChronologyError("candles must be ordered and non-overlapping")
        previous_candle_close = candle.close_time_ns_utc
        value: FeatureValue | None = None
        warming = False
        undefined_reason: str | None = None
        price_factor = 10**session.price_scale
        name = spec.name

        if name == "session_trade_vwap":
            if trades is None:
                raise ContractError("session_trade_vwap requires ordered session trades")
            while next_trade is not None and next_trade.timestamp_ns_utc < candle.close_time_ns_utc:
                if next_trade.symbol != session.physical_contract:
                    raise ContractError("VWAP trade contract does not match TradingSession")
                cumulative_notional += next_trade.price_units * next_trade.quantity
                cumulative_quantity += next_trade.quantity
                next_trade = next(trade_iterator, None)
            if cumulative_quantity == 0:
                warming = True
            else:
                value = FeatureValue.number(
                    "PRICE",
                    CanonicalRational(
                        cumulative_notional,
                        price_factor * cumulative_quantity,
                    ),
                )
        elif name in {"candle_range", "total_range"}:
            value = FeatureValue.number(
                "PRICE",
                _price(candle.high_units - candle.low_units, session.price_scale),
            )
        elif name == "candle_volume":
            value = FeatureValue.number("QUANTITY", CanonicalRational(candle.volume))
        elif name == "candle_body":
            value = FeatureValue.number(
                "PRICE",
                _price(candle.close_units - candle.open_units, session.price_scale),
            )
        elif name == "absolute_body":
            value = FeatureValue.number(
                "PRICE",
                _price(
                    abs(candle.close_units - candle.open_units),
                    session.price_scale,
                ),
            )
        elif name == "upper_wick":
            value = FeatureValue.number(
                "PRICE",
                _price(
                    candle.high_units - max(candle.open_units, candle.close_units),
                    session.price_scale,
                ),
            )
        elif name == "lower_wick":
            value = FeatureValue.number(
                "PRICE",
                _price(
                    min(candle.open_units, candle.close_units) - candle.low_units,
                    session.price_scale,
                ),
            )
        elif name == "close_range_position":
            candle_range = candle.high_units - candle.low_units
            if candle_range == 0:
                undefined_reason = "ZERO_RANGE"
            else:
                value = FeatureValue.number(
                    "RATIO",
                    CanonicalRational(candle.close_units - candle.low_units, candle_range),
                )
        elif name == "candle_direction":
            direction = (
                "UP"
                if candle.close_units > candle.open_units
                else "DOWN"
                if candle.close_units < candle.open_units
                else "NEUTRAL"
            )
            value = FeatureValue.enum("CANDLE_DIRECTION", direction)
        elif name == "local_time":
            local_ns = (
                candle.close_time_ns_utc - 3 * 60 * NANOSECONDS_PER_MINUTE
            ) % (24 * 60 * NANOSECONDS_PER_MINUTE)
            value = FeatureValue.number("TIME_OF_DAY_NS", CanonicalRational(local_ns))
        elif name == "minute_since_session_start":
            elapsed = (candle.close_time_ns_utc - anchor) // NANOSECONDS_PER_MINUTE
            value = FeatureValue.number("MINUTES", CanonicalRational(elapsed))
        elif name == "true_range":
            tr = candle.high_units - candle.low_units
            if previous_close is not None:
                tr = max(
                    tr,
                    abs(candle.high_units - previous_close),
                    abs(candle.low_units - previous_close),
                )
            value = FeatureValue.number("PRICE", _price(tr, session.price_scale))
            previous_close = candle.close_units
        elif name == "atr_wilder":
            tr = candle.high_units - candle.low_units
            if previous_close is not None:
                tr = max(
                    tr,
                    abs(candle.high_units - previous_close),
                    abs(candle.low_units - previous_close),
                )
            previous_close = candle.close_units
            fixed_tr = price_units_to_fixed9(tr, session.price_scale)
            if atr_value is None:
                closes.append(fixed_tr)
                if len(closes) < period:
                    warming = True
                else:
                    atr_value = checked_int64(
                        round_half_even(sum(closes), period), "ATR seed"
                    )
                    value = FeatureValue.number(
                        "PRICE",
                        CanonicalRational(atr_value, 10**CALCULATION_DECIMAL_SCALE),
                    )
            else:
                atr_value = checked_int64(
                    round_half_even((period - 1) * atr_value + fixed_tr, period),
                    "ATR",
                )
                value = FeatureValue.number(
                    "PRICE", CanonicalRational(atr_value, 10**CALCULATION_DECIMAL_SCALE)
                )
        elif name == "sma_close":
            closes.append(candle.close_units)
            if len(closes) > period:
                closes.popleft()
            if len(closes) < period:
                warming = True
            else:
                value = FeatureValue.number(
                    "PRICE", CanonicalRational(sum(closes), period * price_factor)
                )
        elif name == "ema_close":
            fixed_close = price_units_to_fixed9(candle.close_units, session.price_scale)
            if ema_value is None:
                closes.append(fixed_close)
                if len(closes) < period:
                    warming = True
                else:
                    ema_value = checked_int64(
                        round_half_even(sum(closes), period), "EMA seed"
                    )
                    value = FeatureValue.number(
                        "PRICE",
                        CanonicalRational(ema_value, 10**CALCULATION_DECIMAL_SCALE),
                    )
            else:
                ema_value = checked_int64(
                    round_half_even(
                        2 * fixed_close + (period - 1) * ema_value,
                        period + 1,
                    ),
                    "EMA",
                )
                value = FeatureValue.number(
                    "PRICE", CanonicalRational(ema_value, 10**CALCULATION_DECIMAL_SCALE)
                )
        elif name in {"point_change", "percent_change", "n_candle_return"}:
            if len(closes) < period:
                warming = True
            else:
                previous = closes[0]
                difference = candle.close_units - previous
                if name == "point_change":
                    value = FeatureValue.number(
                        "PRICE", _price(difference, session.price_scale)
                    )
                else:
                    ratio = CanonicalRational(difference, previous)
                    if name == "percent_change":
                        ratio = ratio * CanonicalRational(100)
                    value = FeatureValue.number(
                        "PERCENT" if name == "percent_change" else "RATIO", ratio
                    )
            closes.append(candle.close_units)
            if len(closes) > period:
                closes.popleft()
        elif name in {
            "rolling_mean_range",
            "rolling_mean_volume",
            "relative_volume",
            "rolling_high",
            "rolling_low",
            "distance_to_rolling_high",
            "distance_to_rolling_low",
            "breakout_above_previous_high",
            "breakout_below_previous_low",
        }:
            if rolling is None:
                raise RuntimeError("rolling feature was created without state")
            current = (
                candle.high_units - candle.low_units
                if name == "rolling_mean_range"
                else candle.volume
                if name in {"rolling_mean_volume", "relative_volume"}
                else candle.high_units
                if name in {
                    "rolling_high",
                    "distance_to_rolling_high",
                    "breakout_above_previous_high",
                }
                else candle.low_units
            )
            if include_current:
                rolling.append(current)
            if not rolling.ready:
                warming = True
            elif name == "rolling_mean_range":
                value = FeatureValue.number(
                    "PRICE", CanonicalRational(rolling.total, period * price_factor)
                )
            elif name == "rolling_mean_volume":
                value = FeatureValue.number(
                    "QUANTITY", CanonicalRational(rolling.total, period)
                )
            elif name == "relative_volume":
                value = FeatureValue.number(
                    "RATIO", CanonicalRational(candle.volume * period, rolling.total)
                )
            else:
                extreme = (
                    rolling.maximum[0]
                    if name
                    in {
                        "rolling_high",
                        "distance_to_rolling_high",
                        "breakout_above_previous_high",
                    }
                    else rolling.minimum[0]
                )
                if name == "rolling_high" or name == "rolling_low":
                    value = FeatureValue.number(
                        "PRICE", _price(extreme, session.price_scale)
                    )
                elif name == "distance_to_rolling_high":
                    value = FeatureValue.number(
                        "PRICE", _price(extreme - candle.close_units, session.price_scale)
                    )
                elif name == "distance_to_rolling_low":
                    value = FeatureValue.number(
                        "PRICE", _price(candle.close_units - extreme, session.price_scale)
                    )
                elif name == "breakout_above_previous_high":
                    value = FeatureValue.flag(candle.close_units > extreme)
                else:
                    value = FeatureValue.flag(candle.close_units < extreme)
            if not include_current:
                rolling.append(current)
        else:
            raise ContractError(f"feature implementation is missing: {name}")

        yield _observation(
            session,
            candle,
            value,
            warming_up=warming,
            undefined_reason=undefined_reason,
        )


def calculate_derived_feature_series(
    session: TradingSession,
    candles: Iterable[Candle],
    spec: FeatureSpec,
    dependencies: Sequence[Iterable[FeatureObservation]],
) -> Iterator[FeatureObservation]:
    """Calculate context features from already materialized exact dependencies."""

    if len(dependencies) != len(spec.inputs):
        raise ContractError("derived feature dependency count does not match its declaration")
    iterators = [iter(item) for item in dependencies]
    for candle in candles:
        rows = [next(iterator, None) for iterator in iterators]
        if any(row is None for row in rows):
            raise ContractError("derived feature dependency ended before candles")
        typed_rows = [row for row in rows if row is not None]
        if any(row.candle_open_time_ns_utc != candle.open_time_ns_utc for row in typed_rows):
            raise ContractError("derived feature dependencies are not candle-aligned")
        warming = any(row.warmup_status == "WARMING_UP" for row in typed_rows)
        undefined = next(
            (row.undefined_reason for row in typed_rows if row.undefined_reason is not None),
            None,
        )
        value: FeatureValue | None = None
        if not warming and undefined is None:
            numeric = [
                row.value.numeric
                for row in typed_rows
                if row.value is not None and row.value.numeric is not None
            ]
            if len(numeric) != len(typed_rows):
                raise ContractError("derived context requires numeric dependencies")
            close = _price(candle.close_units, session.price_scale)
            if spec.name in {"close_vs_sma", "close_vs_ema", "close_vs_vwap"}:
                value = FeatureValue.enum("RELATION", _enum_compare(close, numeric[0]))
            elif spec.name in {"sma_fast_vs_slow", "ema_fast_vs_slow"}:
                value = FeatureValue.enum(
                    "RELATION", _enum_compare(numeric[0], numeric[1])
                )
            elif spec.name == "distance_to_vwap":
                value = FeatureValue.number("PRICE", close - numeric[0])
            elif spec.name == "distance_between_averages":
                value = FeatureValue.number("PRICE", numeric[0] - numeric[1])
            else:
                raise ContractError(f"derived feature implementation is missing: {spec.name}")
        yield _observation(
            session,
            candle,
            value,
            warming_up=warming,
            undefined_reason=undefined,
        )
    for iterator in iterators:
        if next(iterator, None) is not None:
            raise ContractError("derived feature dependency has extra rows")


def mathematical_policy_record() -> dict[str, object]:
    return {
        "version": MATHEMATICAL_PRECISION_POLICY_VERSION,
        "ema_atr_decimal_scale": CALCULATION_DECIMAL_SCALE,
        "rounding": "ROUND_HALF_EVEN_EACH_STEP",
        "rational": {
            "denominator_positive": True,
            "reduced_by_gcd": True,
            "zero": {"numerator": "0", "denominator": "1"},
        },
        "period_semantics": "OBSERVED_CANDLES_WITHIN_TRADING_SESSION",
    }
