from __future__ import annotations

import unittest

from helpers import historical_strategy_record
from quantlab_backtest.historical_engine import (
    HistoricalSessionInput,
    run_historical_backtest,
)
from quantlab_core.historical_strategy import HistoricalStrategyDefinition
from quantlab_core.market_data import (
    Candle,
    FeatureRow,
    SessionFeatureRow,
    SessionTrade,
)
from quantlab_core.sessions import TradingSession
from quantlab_core.time import NANOSECONDS_PER_MINUTE, parse_iso8601_ns


def make_session(
    day: str, suffix: str, tick_count: int
) -> tuple[TradingSession, list[SessionTrade]]:
    start = parse_iso8601_ns(f"{day}T09:00:00-03:00")
    session = TradingSession(
        f"sha256:{suffix}",
        day,
        "WIN",
        "WINV26",
        start,
        start + (tick_count - 1) * NANOSECONDS_PER_MINUTE,
        tick_count,
        0,
        f"source-{suffix}",
        f"import-{suffix}",
        f"normalized-{suffix}",
    )
    prices = [100, 110, 111, 112][:tick_count]
    ticks = [
        SessionTrade(
            session.session_id,
            day,
            "WIN",
            "WINV26",
            start + index * NANOSECONDS_PER_MINUTE,
            index,
            price,
            1,
        )
        for index, price in enumerate(prices)
    ]
    return session, ticks


def signal_feature(
    session: TradingSession,
    *,
    available_minute: int = 2,
    executable: bool = True,
) -> SessionFeatureRow:
    start = session.first_event_ns_utc
    candle = Candle(
        session.physical_contract,
        "1m",
        start + (available_minute - 1) * NANOSECONDS_PER_MINUTE,
        start + available_minute * NANOSECONDS_PER_MINUTE,
        110,
        110,
        110,
        110,
        1,
        1,
    )
    return SessionFeatureRow(
        session.session_id,
        session.trading_date,
        session.logical_asset,
        session.physical_contract,
        FeatureRow(candle, 210, 2, candle.close_time_ns_utc),
        executable,
        None if executable else "AVAILABLE_AFTER_LAST_ELIGIBLE_TRADE",
    )


class HistoricalBacktestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.strategy = HistoricalStrategyDefinition.model_validate(
            historical_strategy_record(target="100", stop="100")
        )

    def test_sessions_force_session_end_and_never_hold_overnight(self) -> None:
        first, first_ticks = make_session("2026-09-10", "a", 4)
        second, second_ticks = make_session("2026-09-11", "b", 4)
        ledger = []
        discarded = []
        result = run_historical_backtest(
            [
                HistoricalSessionInput(first, first_ticks, [signal_feature(first)]),
                HistoricalSessionInput(second, second_ticks, [signal_feature(second)]),
            ],
            self.strategy,
            common_price_scale=0,
            on_trade=ledger.append,
            on_discard=discarded.append,
        )
        self.assertEqual([trade.exit_reason for trade in ledger], ["SESSION_END", "SESSION_END"])
        self.assertEqual(
            [
                (
                    trade.trading_date,
                    trade.entry_timestamp_ns_utc,
                    trade.exit_timestamp_ns_utc,
                )
                for trade in ledger
            ],
            [
                (
                    first.trading_date,
                    first_ticks[2].timestamp_ns_utc,
                    first_ticks[3].timestamp_ns_utc,
                ),
                (
                    second.trading_date,
                    second_ticks[2].timestamp_ns_utc,
                    second_ticks[3].timestamp_ns_utc,
                ),
            ],
        )
        self.assertFalse(result.metrics["has_open_position"])
        self.assertEqual(discarded, [])

    def test_fill_on_last_tick_is_forbidden_and_recorded(self) -> None:
        session, ticks = make_session("2026-09-10", "last", 3)
        ledger = []
        discarded = []
        run_historical_backtest(
            [HistoricalSessionInput(session, ticks, [signal_feature(session)])],
            self.strategy,
            common_price_scale=0,
            on_trade=ledger.append,
            on_discard=discarded.append,
        )
        self.assertEqual(ledger, [])
        self.assertEqual([item.reason for item in discarded], ["LAST_TICK_FILL_FORBIDDEN"])

    def test_feature_after_last_trade_is_non_executable_and_recorded(self) -> None:
        session, ticks = make_session("2026-09-10", "feature", 3)
        feature = signal_feature(session, available_minute=3, executable=False)
        ledger = []
        discarded = []
        run_historical_backtest(
            [HistoricalSessionInput(session, ticks, [feature])],
            self.strategy,
            common_price_scale=0,
            on_trade=ledger.append,
            on_discard=discarded.append,
        )
        self.assertEqual(ledger, [])
        self.assertEqual(
            [item.reason for item in discarded],
            ["AVAILABLE_AFTER_LAST_ELIGIBLE_TRADE"],
        )


if __name__ == "__main__":
    unittest.main()
