"""Chronological tick-driven backtest engine."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator

from quantlab_core.errors import ChronologyError, ContractError
from quantlab_core.evaluator import evaluate_condition
from quantlab_core.market_data import FeatureRow, MarketTrade
from quantlab_core.price import decimal_to_units
from quantlab_core.strategy import StrategyDefinition

from quantlab_backtest.metrics import MetricsAccumulator
from quantlab_backtest.models import BacktestSummary, ClosedTrade, EntrySignal, OpenPosition

BACKTEST_ENGINE_VERSION = "1.0.0"


def generate_entry_signals(
    features: Iterable[FeatureRow],
    strategy: StrategyDefinition,
    *,
    price_scale: int,
) -> Iterator[EntrySignal]:
    previous_available_at: int | None = None
    for feature in features:
        candle = feature.candle
        if candle.symbol != strategy.symbol or candle.timeframe != strategy.timeframe:
            raise ContractError("feature stream does not match strategy symbol/timeframe")
        if feature.available_at_ns_utc != candle.close_time_ns_utc:
            raise ContractError("ON_CLOSE feature availability must equal candle close")
        if (
            previous_available_at is not None
            and feature.available_at_ns_utc <= previous_available_at
        ):
            raise ChronologyError("features must have strictly increasing availability")
        previous_available_at = feature.available_at_ns_utc
        if evaluate_condition(strategy.entry_conditions, feature, price_scale):
            yield EntrySignal(
                available_at_ns_utc=feature.available_at_ns_utc,
                candle_open_time_ns_utc=candle.open_time_ns_utc,
            )


def _close_trade(
    position: OpenPosition,
    tick: MarketTrade,
    reason: str,
    trade_number: int,
) -> ClosedTrade:
    pnl = (
        tick.price_units - position.entry_price_units
        if position.direction == "BUY"
        else position.entry_price_units - tick.price_units
    )
    return ClosedTrade(
        trade_number=trade_number,
        direction=position.direction,
        signal_available_at_ns_utc=position.signal_available_at_ns_utc,
        entry_timestamp_ns_utc=position.entry_timestamp_ns_utc,
        entry_source_sequence=position.entry_source_sequence,
        entry_price_units=position.entry_price_units,
        exit_timestamp_ns_utc=tick.timestamp_ns_utc,
        exit_source_sequence=tick.source_sequence,
        exit_price_units=tick.price_units,
        exit_reason=reason,
        pnl_units=pnl,
    )


def run_backtest(
    ticks: Iterable[MarketTrade],
    features: Iterable[FeatureRow],
    strategy: StrategyDefinition,
    *,
    price_scale: int,
    on_trade: Callable[[ClosedTrade], None],
) -> BacktestSummary:
    """Run one explicit single-position strategy over an ordered tick stream."""

    target_distance = decimal_to_units(strategy.take_profit.value, price_scale)
    stop_distance = decimal_to_units(strategy.stop_loss.value, price_scale)
    signals = iter(generate_entry_signals(features, strategy, price_scale=price_scale))
    next_signal = next(signals, None)
    metrics = MetricsAccumulator()
    position: OpenPosition | None = None
    previous_tick_key: tuple[int, int] | None = None
    last_tick: MarketTrade | None = None
    trade_number = 0

    def emit(trade: ClosedTrade) -> None:
        nonlocal trade_number
        trade_number += 1
        numbered = ClosedTrade(
            trade_number=trade_number,
            direction=trade.direction,
            signal_available_at_ns_utc=trade.signal_available_at_ns_utc,
            entry_timestamp_ns_utc=trade.entry_timestamp_ns_utc,
            entry_source_sequence=trade.entry_source_sequence,
            entry_price_units=trade.entry_price_units,
            exit_timestamp_ns_utc=trade.exit_timestamp_ns_utc,
            exit_source_sequence=trade.exit_source_sequence,
            exit_price_units=trade.exit_price_units,
            exit_reason=trade.exit_reason,
            pnl_units=trade.pnl_units,
        )
        metrics.add(numbered)
        on_trade(numbered)

    for tick in ticks:
        if tick.symbol != strategy.symbol:
            raise ContractError("tick stream symbol does not match strategy")
        if previous_tick_key is not None and tick.order_key <= previous_tick_key:
            raise ChronologyError("ticks must have a strictly increasing order key")
        previous_tick_key = tick.order_key
        last_tick = tick

        if position is not None:
            while (
                next_signal is not None and next_signal.available_at_ns_utc <= tick.timestamp_ns_utc
            ):
                next_signal = next(signals, None)
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
                emit(_close_trade(position, tick, reason, trade_number + 1))
                position = None
            continue

        selected_signal: EntrySignal | None = None
        while next_signal is not None and next_signal.available_at_ns_utc <= tick.timestamp_ns_utc:
            if selected_signal is None:
                selected_signal = next_signal
            next_signal = next(signals, None)
        if selected_signal is None:
            continue

        if strategy.direction == "BUY":
            target_price = tick.price_units + target_distance
            stop_price = tick.price_units - stop_distance
        else:
            target_price = tick.price_units - target_distance
            stop_price = tick.price_units + stop_distance
        position = OpenPosition(
            direction=strategy.direction,
            signal_available_at_ns_utc=selected_signal.available_at_ns_utc,
            entry_timestamp_ns_utc=tick.timestamp_ns_utc,
            entry_source_sequence=tick.source_sequence,
            entry_price_units=tick.price_units,
            target_price_units=target_price,
            stop_price_units=stop_price,
        )

    if (
        position is not None
        and last_tick is not None
        and strategy.execution.end_of_data == "CLOSE_AT_LAST_TRADE"
    ):
        emit(_close_trade(position, last_tick, "END_OF_DATA", trade_number + 1))
        position = None

    return BacktestSummary(
        metrics=metrics.to_record(price_scale=price_scale, open_position=position is not None),
        open_position=position,
    )
