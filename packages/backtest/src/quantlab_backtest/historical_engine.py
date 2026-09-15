"""Deterministic multi-session backtest with explicit session boundaries."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass

from quantlab_core.errors import ChronologyError, ContractError
from quantlab_core.evaluator import evaluate_condition
from quantlab_core.historical_strategy import HistoricalStrategyDefinition
from quantlab_core.market_data import FeatureRow, MarketTrade, SessionFeatureRow, SessionTrade
from quantlab_core.price import decimal_to_units, rescale_units_exact
from quantlab_core.sessions import TradingSession

from quantlab_backtest.historical_models import (
    DiscardedSignal,
    HistoricalBacktestSummary,
    HistoricalClosedTrade,
    HistoricalEntrySignal,
    HistoricalOpenPosition,
)
from quantlab_backtest.metrics import MetricsAccumulator

HISTORICAL_BACKTEST_ENGINE_VERSION = "2.0.0"


@dataclass(frozen=True, slots=True)
class HistoricalSessionInput:
    session: TradingSession
    ticks: Iterable[SessionTrade]
    features: Iterable[SessionFeatureRow]


def _rescale_feature(
    feature: FeatureRow, source_scale: int, target_scale: int
) -> FeatureRow:
    candle = feature.candle
    from quantlab_core.market_data import Candle

    scaled_candle = Candle(
        candle.symbol,
        candle.timeframe,
        candle.open_time_ns_utc,
        candle.close_time_ns_utc,
        rescale_units_exact(candle.open_units, source_scale, target_scale),
        rescale_units_exact(candle.high_units, source_scale, target_scale),
        rescale_units_exact(candle.low_units, source_scale, target_scale),
        rescale_units_exact(candle.close_units, source_scale, target_scale),
        candle.volume,
        candle.trade_count,
    )
    sma_sum = (
        None
        if feature.sma_close_sum_units is None
        else rescale_units_exact(feature.sma_close_sum_units, source_scale, target_scale)
    )
    return FeatureRow(
        scaled_candle,
        sma_sum,
        feature.sma_close_period,
        feature.available_at_ns_utc,
    )


def _signals(
    rows: Iterable[SessionFeatureRow],
    strategy: HistoricalStrategyDefinition,
    session: TradingSession,
    common_scale: int,
) -> Iterator[HistoricalEntrySignal]:
    previous_available_at: int | None = None
    for row in rows:
        feature = row.feature
        candle = feature.candle
        if (
            row.session_id != session.session_id
            or row.trading_date != session.trading_date
            or row.logical_asset != session.logical_asset
            or row.physical_contract != session.physical_contract
        ):
            raise ContractError("feature metadata does not match TradingSession")
        if row.logical_asset != strategy.logical_asset or candle.timeframe != strategy.timeframe:
            raise ContractError("feature stream does not match historical strategy")
        if feature.available_at_ns_utc != candle.close_time_ns_utc:
            raise ContractError("ON_CLOSE feature availability must equal nominal candle close")
        if (
            previous_available_at is not None
            and feature.available_at_ns_utc <= previous_available_at
        ):
            raise ChronologyError("session features must have strictly increasing availability")
        previous_available_at = feature.available_at_ns_utc
        scaled = _rescale_feature(feature, session.price_scale, common_scale)
        if evaluate_condition(strategy.entry_conditions, scaled, common_scale):
            yield HistoricalEntrySignal(
                session.session_id,
                session.trading_date,
                session.logical_asset,
                session.physical_contract,
                candle.timeframe,
                feature.available_at_ns_utc,
                candle.open_time_ns_utc,
                row.executable_in_session,
                row.non_executable_reason,
            )


def _scaled_tick(
    trade: SessionTrade, session: TradingSession, common_scale: int
) -> MarketTrade:
    if (
        trade.session_id != session.session_id
        or trade.trading_date != session.trading_date
        or trade.logical_asset != session.logical_asset
        or trade.physical_contract != session.physical_contract
    ):
        raise ContractError("tick metadata does not match TradingSession")
    return MarketTrade(
        trade.physical_contract,
        trade.timestamp_ns_utc,
        trade.source_sequence,
        rescale_units_exact(trade.price_units, session.price_scale, common_scale),
        trade.quantity,
    )


def _closed_trade(
    position: HistoricalOpenPosition,
    tick: MarketTrade,
    reason: str,
    trade_number: int,
) -> HistoricalClosedTrade:
    pnl = (
        tick.price_units - position.entry_price_units
        if position.direction == "BUY"
        else position.entry_price_units - tick.price_units
    )
    signal = position.signal
    return HistoricalClosedTrade(
        trade_number,
        signal.session_id,
        signal.trading_date,
        signal.logical_asset,
        signal.physical_contract,
        signal.timeframe,
        position.direction,
        signal.available_at_ns_utc,
        position.entry_timestamp_ns_utc,
        position.entry_source_sequence,
        position.entry_price_units,
        tick.timestamp_ns_utc,
        tick.source_sequence,
        tick.price_units,
        reason,
        pnl,
    )


def run_historical_backtest(
    session_inputs: Iterable[HistoricalSessionInput],
    strategy: HistoricalStrategyDefinition,
    *,
    common_price_scale: int,
    on_trade: Callable[[HistoricalClosedTrade], None],
    on_discard: Callable[[DiscardedSignal], None],
) -> HistoricalBacktestSummary:
    """Run each TradingSession independently; positions and signals never cross sessions."""

    target_distance = decimal_to_units(strategy.take_profit.value, common_price_scale)
    stop_distance = decimal_to_units(strategy.stop_loss.value, common_price_scale)
    metrics = MetricsAccumulator()
    trade_number = 0
    discard_number = 0
    previous_date: str | None = None

    def discard(signal: HistoricalEntrySignal, reason: str) -> None:
        nonlocal discard_number
        discard_number += 1
        on_discard(
            DiscardedSignal(
                discard_number,
                signal.session_id,
                signal.trading_date,
                signal.logical_asset,
                signal.physical_contract,
                signal.timeframe,
                signal.available_at_ns_utc,
                signal.candle_open_time_ns_utc,
                reason,
            )
        )

    for item in session_inputs:
        session = item.session
        if previous_date is not None and session.trading_date <= previous_date:
            raise ChronologyError("TradingSessions must be strictly ordered by trading_date")
        previous_date = session.trading_date
        if session.logical_asset != strategy.logical_asset:
            raise ContractError("TradingSession logical asset does not match strategy")
        signal_iterator = iter(_signals(item.features, strategy, session, common_price_scale))
        next_signal = next(signal_iterator, None)
        tick_iterator = iter(item.ticks)
        current_raw = next(tick_iterator, None)
        following_raw = next(tick_iterator, None)
        previous_tick_key: tuple[int, int] | None = None
        position: HistoricalOpenPosition | None = None

        while current_raw is not None:
            tick = _scaled_tick(current_raw, session, common_price_scale)
            if previous_tick_key is not None and tick.order_key <= previous_tick_key:
                raise ChronologyError("session ticks must have a strictly increasing order key")
            previous_tick_key = tick.order_key
            position_was_open = position is not None
            selected_signal: HistoricalEntrySignal | None = None
            while (
                next_signal is not None
                and next_signal.available_at_ns_utc <= tick.timestamp_ns_utc
            ):
                candidate = next_signal
                next_signal = next(signal_iterator, None)
                if not candidate.executable_in_session:
                    discard(
                        candidate,
                        candidate.non_executable_reason
                        or "FEATURE_NOT_EXECUTABLE_IN_SESSION",
                    )
                elif not position_was_open and selected_signal is None:
                    selected_signal = candidate

            if position is not None:
                if position.direction == "BUY":
                    reason = (
                        "TAKE_PROFIT"
                        if tick.price_units >= position.target_price_units
                        else "STOP_LOSS"
                        if tick.price_units <= position.stop_price_units
                        else None
                    )
                else:
                    reason = (
                        "TAKE_PROFIT"
                        if tick.price_units <= position.target_price_units
                        else "STOP_LOSS"
                        if tick.price_units >= position.stop_price_units
                        else None
                    )
                if reason is not None:
                    trade_number += 1
                    closed = _closed_trade(position, tick, reason, trade_number)
                    metrics.add(closed)  # type: ignore[arg-type]
                    on_trade(closed)
                    position = None

            if not position_was_open and selected_signal is not None:
                if following_raw is None:
                    discard(selected_signal, "LAST_TICK_FILL_FORBIDDEN")
                else:
                    target = (
                        tick.price_units + target_distance
                        if strategy.direction == "BUY"
                        else tick.price_units - target_distance
                    )
                    stop = (
                        tick.price_units - stop_distance
                        if strategy.direction == "BUY"
                        else tick.price_units + stop_distance
                    )
                    position = HistoricalOpenPosition(
                        selected_signal,
                        strategy.direction,
                        tick.timestamp_ns_utc,
                        tick.source_sequence,
                        tick.price_units,
                        target,
                        stop,
                    )

            if following_raw is None and position is not None:
                trade_number += 1
                closed = _closed_trade(position, tick, "SESSION_END", trade_number)
                metrics.add(closed)  # type: ignore[arg-type]
                on_trade(closed)
                position = None

            current_raw = following_raw
            following_raw = next(tick_iterator, None)

        while next_signal is not None:
            reason = (
                next_signal.non_executable_reason
                if not next_signal.executable_in_session
                else "SIGNAL_WITHOUT_FILL_POSTERIOR"
            )
            discard(next_signal, reason or "FEATURE_NOT_EXECUTABLE_IN_SESSION")
            next_signal = next(signal_iterator, None)

    record = metrics.to_record(price_scale=common_price_scale, open_position=False)
    record["schema_version"] = "backtest-metrics/v2"
    record["historical_engine_version"] = HISTORICAL_BACKTEST_ENGINE_VERSION
    record["discarded_signals"] = discard_number
    record["has_open_position"] = False
    return HistoricalBacktestSummary(record, discard_number)
