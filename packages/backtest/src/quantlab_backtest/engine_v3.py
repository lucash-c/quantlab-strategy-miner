"""Exact session-scoped backtest for Strategy Definition v3."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass

from quantlab_core.canonical import canonical_json_bytes, sha256_bytes
from quantlab_core.condition_engine_v2 import (
    ConditionEvaluatorV2,
    FeatureFrame,
    time_range_contains,
)
from quantlab_core.errors import ChronologyError, ContractError
from quantlab_core.feature_engine_v2 import FeatureObservation
from quantlab_core.market_data import MarketTrade, SessionCandle, SessionTrade
from quantlab_core.price import decimal_to_units, rescale_units_exact
from quantlab_core.sessions import TradingSession
from quantlab_core.strategy_v3 import (
    FixedPerSideCostModelV3,
    FixedPointsSlippageModelV3,
    StrategyDefinitionV3,
)

from quantlab_backtest.metrics_v3 import MetricsAccumulatorV3
from quantlab_backtest.models_v3 import (
    BacktestSummaryV3,
    ClosedTradeV3,
    EntrySignalV3,
    OpenPositionV3,
    SignalJournalRecordV3,
)

BACKTEST_ENGINE_V3_VERSION = "3.0.0"


@dataclass(frozen=True, slots=True)
class FeatureSessionInputV3:
    session: TradingSession
    ticks: Iterable[SessionTrade]
    candles: Iterable[SessionCandle]
    features: dict[str, Iterable[FeatureObservation]]


def _feature_snapshot(observation: FeatureObservation) -> dict[str, object]:
    return {
        "warmup_status": observation.warmup_status,
        "executable_in_session": observation.executable_in_session,
        "non_executable_reason": observation.non_executable_reason,
        "undefined_reason": observation.undefined_reason,
        "value": None if observation.value is None else observation.value.to_record(),
    }


def _frames(item: FeatureSessionInputV3, strategy: StrategyDefinitionV3) -> Iterator[FeatureFrame]:
    iterators = {key: iter(value) for key, value in item.features.items()}
    previous_close: int | None = None
    for session_candle in item.candles:
        candle = session_candle.candle
        if (
            session_candle.session_id != item.session.session_id
            or session_candle.trading_date != item.session.trading_date
            or session_candle.logical_asset != item.session.logical_asset
            or session_candle.physical_contract != item.session.physical_contract
        ):
            raise ContractError("candle metadata does not match TradingSession")
        if candle.timeframe != strategy.timeframe:
            raise ContractError("candle timeframe does not match strategy")
        if previous_close is not None and candle.close_time_ns_utc <= previous_close:
            raise ChronologyError("session candles must have increasing nominal closes")
        previous_close = candle.close_time_ns_utc
        observations: dict[str, FeatureObservation] = {}
        for feature_id, iterator in iterators.items():
            observation = next(iterator, None)
            if observation is None:
                raise ContractError(f"feature ended before candles: {feature_id}")
            if (
                observation.session_id != item.session.session_id
                or observation.timeframe != strategy.timeframe
                or observation.candle_open_time_ns_utc != candle.open_time_ns_utc
                or observation.candle_close_time_ns_utc != candle.close_time_ns_utc
                or observation.available_at_ns_utc != candle.close_time_ns_utc
            ):
                raise ContractError(f"feature is not aligned with candle: {feature_id}")
            observations[feature_id] = observation
        yield FeatureFrame(item.session, candle, observations)
    for feature_id, iterator in iterators.items():
        if next(iterator, None) is not None:
            raise ContractError(f"feature has rows after candles: {feature_id}")


def _signals(
    item: FeatureSessionInputV3, strategy: StrategyDefinitionV3
) -> Iterator[EntrySignalV3]:
    evaluator = ConditionEvaluatorV2()
    for frame in _frames(item, strategy):
        evaluation = evaluator.evaluate(strategy.entry_conditions, frame)
        if not evaluation.result:
            continue
        identity = {
            "strategy_sha256": strategy.semantic_sha256(),
            "session_id": frame.session.session_id,
            "timeframe": frame.candle.timeframe,
            "candle_open_time_ns_utc": frame.candle.open_time_ns_utc,
            "available_at_ns_utc": frame.available_at_ns_utc,
        }
        signal_id = "sha256:" + sha256_bytes(canonical_json_bytes(identity))
        snapshot = {
            "condition": evaluation.snapshot,
            "feature_values": {
                feature_id: _feature_snapshot(observation)
                for feature_id, observation in sorted(frame.features.items())
            },
        }
        yield EntrySignalV3(
            signal_id,
            frame.session.session_id,
            frame.session.trading_date,
            frame.session.logical_asset,
            frame.session.physical_contract,
            frame.candle.timeframe,
            frame.available_at_ns_utc,
            frame.candle.open_time_ns_utc,
            frame.executable_in_session,
            None
            if frame.executable_in_session
            else "AVAILABLE_AFTER_LAST_ELIGIBLE_TRADE",
            snapshot,
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


def _entry_execution_price(direction: str, market: int, slippage: int) -> int:
    result = market + slippage if direction == "BUY" else market - slippage
    if result <= 0:
        raise ContractError("slippage produced a non-positive entry execution price")
    return result


def _exit_execution_price(direction: str, market: int, slippage: int) -> int:
    result = market - slippage if direction == "BUY" else market + slippage
    if result <= 0:
        raise ContractError("slippage produced a non-positive exit execution price")
    return result


def _closed_trade(
    position: OpenPositionV3,
    tick: MarketTrade,
    reason: str,
    trade_number: int,
    slippage_units: int,
    costs_units: int,
) -> ClosedTradeV3:
    direction_factor = 1 if position.direction == "BUY" else -1
    execution_exit = _exit_execution_price(
        position.direction, tick.price_units, slippage_units
    )
    gross = direction_factor * (
        tick.price_units - position.entry_market_price_units
    )
    execution_pnl = direction_factor * (
        execution_exit - position.entry_execution_price_units
    )
    impact = gross - execution_pnl
    signal = position.signal
    return ClosedTradeV3(
        trade_number,
        signal.signal_id,
        signal.session_id,
        signal.trading_date,
        signal.logical_asset,
        signal.physical_contract,
        signal.timeframe,
        position.direction,
        signal.available_at_ns_utc,
        position.entry_timestamp_ns_utc,
        position.entry_source_sequence,
        position.entry_market_price_units,
        position.entry_execution_price_units,
        position.target_execution_price_units,
        position.stop_execution_price_units,
        tick.timestamp_ns_utc,
        tick.source_sequence,
        tick.price_units,
        execution_exit,
        reason,
        gross,
        impact,
        costs_units,
        gross - impact - costs_units,
    )


def run_backtest_v3(
    session_inputs: Iterable[FeatureSessionInputV3],
    strategy: StrategyDefinitionV3,
    *,
    common_price_scale: int,
    on_trade: Callable[[ClosedTradeV3], None],
    on_signal: Callable[[SignalJournalRecordV3], None],
) -> BacktestSummaryV3:
    """Run chronological fills with exact deterministic costs and adverse slippage."""

    target_distance = decimal_to_units(strategy.take_profit.value, common_price_scale)
    stop_distance = decimal_to_units(strategy.stop_loss.value, common_price_scale)
    cost_per_side = (
        decimal_to_units(strategy.cost_model.points_per_side, common_price_scale)
        if isinstance(strategy.cost_model, FixedPerSideCostModelV3)
        else 0
    )
    slippage_per_side = (
        decimal_to_units(strategy.slippage_model.points_per_side, common_price_scale)
        if isinstance(strategy.slippage_model, FixedPointsSlippageModelV3)
        else 0
    )
    round_trip_cost = 2 * cost_per_side
    metrics = MetricsAccumulatorV3()
    trade_number = 0
    journal_number = 0
    previous_date: str | None = None

    def journal(signal: EntrySignalV3, outcome: str, reason: str | None) -> None:
        nonlocal journal_number
        journal_number += 1
        on_signal(
            SignalJournalRecordV3(
                journal_number,
                signal.signal_id,
                signal.session_id,
                signal.trading_date,
                signal.logical_asset,
                signal.physical_contract,
                signal.timeframe,
                signal.available_at_ns_utc,
                signal.candle_open_time_ns_utc,
                outcome,
                reason,
                signal.condition_snapshot,
            )
        )

    for item in session_inputs:
        session = item.session
        if previous_date is not None and session.trading_date <= previous_date:
            raise ChronologyError("TradingSessions must be strictly ordered by trading_date")
        previous_date = session.trading_date
        if session.logical_asset != strategy.logical_asset:
            raise ContractError("TradingSession logical asset does not match strategy")
        signal_iterator = iter(_signals(item, strategy))
        next_signal = next(signal_iterator, None)
        tick_iterator = iter(item.ticks)
        current_raw = next(tick_iterator, None)
        following_raw = next(tick_iterator, None)
        previous_tick_key: tuple[int, int] | None = None
        position: OpenPositionV3 | None = None

        while current_raw is not None:
            tick = _scaled_tick(current_raw, session, common_price_scale)
            if previous_tick_key is not None and tick.order_key <= previous_tick_key:
                raise ChronologyError("session ticks must have a strictly increasing order key")
            previous_tick_key = tick.order_key
            position_was_open = position is not None
            selected_signal: EntrySignalV3 | None = None
            while (
                next_signal is not None
                and next_signal.available_at_ns_utc <= tick.timestamp_ns_utc
            ):
                candidate = next_signal
                next_signal = next(signal_iterator, None)
                if not candidate.executable_in_session:
                    journal(
                        candidate,
                        "DISCARDED",
                        candidate.non_executable_reason
                        or "FEATURE_NOT_EXECUTABLE_IN_SESSION",
                    )
                elif strategy.entry_time_filter is not None and not time_range_contains(
                    strategy.entry_time_filter, candidate.available_at_ns_utc
                ):
                    journal(candidate, "DISCARDED", "SIGNAL_OUTSIDE_TIME_RANGE")
                elif position_was_open:
                    journal(candidate, "DISCARDED", "POSITION_ALREADY_OPEN")
                elif selected_signal is None:
                    selected_signal = candidate
                else:
                    journal(candidate, "DISCARDED", "SIGNAL_SUPERSEDED_SAME_FILL_EVENT")

            if position is not None:
                if position.direction == "BUY":
                    exit_reason = (
                        "TAKE_PROFIT"
                        if tick.price_units >= position.target_execution_price_units
                        else "STOP_LOSS"
                        if tick.price_units <= position.stop_execution_price_units
                        else None
                    )
                else:
                    exit_reason = (
                        "TAKE_PROFIT"
                        if tick.price_units <= position.target_execution_price_units
                        else "STOP_LOSS"
                        if tick.price_units >= position.stop_execution_price_units
                        else None
                    )
                if exit_reason is not None:
                    trade_number += 1
                    closed = _closed_trade(
                        position,
                        tick,
                        exit_reason,
                        trade_number,
                        slippage_per_side,
                        round_trip_cost,
                    )
                    metrics.add(closed)
                    on_trade(closed)
                    position = None

            if not position_was_open and selected_signal is not None:
                if strategy.entry_time_filter is not None and not time_range_contains(
                    strategy.entry_time_filter, tick.timestamp_ns_utc
                ):
                    journal(selected_signal, "FILL_REJECTED", "FILL_OUTSIDE_TIME_RANGE")
                elif following_raw is None:
                    journal(selected_signal, "FILL_REJECTED", "LAST_TICK_FILL_FORBIDDEN")
                else:
                    execution_entry = _entry_execution_price(
                        strategy.direction, tick.price_units, slippage_per_side
                    )
                    target = (
                        execution_entry + target_distance
                        if strategy.direction == "BUY"
                        else execution_entry - target_distance
                    )
                    stop = (
                        execution_entry - stop_distance
                        if strategy.direction == "BUY"
                        else execution_entry + stop_distance
                    )
                    position = OpenPositionV3(
                        selected_signal,
                        strategy.direction,
                        tick.timestamp_ns_utc,
                        tick.source_sequence,
                        tick.price_units,
                        execution_entry,
                        target,
                        stop,
                    )
                    journal(selected_signal, "FILLED", None)

            if following_raw is None and position is not None:
                trade_number += 1
                closed = _closed_trade(
                    position,
                    tick,
                    "SESSION_END",
                    trade_number,
                    slippage_per_side,
                    round_trip_cost,
                )
                metrics.add(closed)
                on_trade(closed)
                position = None

            current_raw = following_raw
            following_raw = next(tick_iterator, None)

        while next_signal is not None:
            candidate = next_signal
            if not candidate.executable_in_session:
                reason = candidate.non_executable_reason or "FEATURE_NOT_EXECUTABLE_IN_SESSION"
            elif strategy.entry_time_filter is not None and not time_range_contains(
                strategy.entry_time_filter, candidate.available_at_ns_utc
            ):
                reason = "SIGNAL_OUTSIDE_TIME_RANGE"
            else:
                reason = "SIGNAL_WITHOUT_FILL_POSTERIOR"
            journal(candidate, "DISCARDED", reason)
            next_signal = next(signal_iterator, None)

    metrics_record = metrics.to_record(
        price_scale=common_price_scale,
        cost_model=strategy.cost_model.model_dump(mode="json"),
        slippage_model=strategy.slippage_model.model_dump(mode="json"),
    )
    metrics_record["backtest_engine_version"] = BACKTEST_ENGINE_V3_VERSION
    metrics_record["journal_records"] = journal_number
    return BacktestSummaryV3(metrics_record, journal_number)
