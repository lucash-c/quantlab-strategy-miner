from __future__ import annotations

import unittest

from helpers import strategy_record
from quantlab_backtest.engine import run_backtest
from quantlab_core.candles import build_one_minute_candles
from quantlab_core.indicators import calculate_sma_close
from quantlab_core.market_data import MarketTrade
from quantlab_core.strategy import StrategyDefinition
from quantlab_core.time import NANOSECONDS_PER_MINUTE, parse_iso8601_ns


class BacktestTests(unittest.TestCase):
    def test_signal_fills_at_boundary_and_target_uses_tick_sequence(self) -> None:
        start = parse_iso8601_ns("2026-01-02T12:00:00Z")
        ticks = [
            MarketTrade("TEST", start, 0, 100, 1),
            MarketTrade("TEST", start + NANOSECONDS_PER_MINUTE, 0, 110, 1),
            MarketTrade("TEST", start + 2 * NANOSECONDS_PER_MINUTE, 0, 111, 1),
            MarketTrade("TEST", start + 2 * NANOSECONDS_PER_MINUTE, 1, 116, 1),
        ]
        features = list(calculate_sma_close(build_one_minute_candles(ticks), 2))
        strategy = StrategyDefinition.model_validate(strategy_record())
        ledger = []
        result = run_backtest(ticks, features, strategy, price_scale=0, on_trade=ledger.append)

        self.assertEqual(len(ledger), 1)
        trade = ledger[0]
        self.assertEqual(trade.signal_available_at_ns_utc, start + 2 * NANOSECONDS_PER_MINUTE)
        self.assertEqual(trade.entry_timestamp_ns_utc, start + 2 * NANOSECONDS_PER_MINUTE)
        self.assertEqual(trade.entry_source_sequence, 0)
        self.assertEqual(trade.exit_source_sequence, 1)
        self.assertEqual(trade.exit_reason, "TAKE_PROFIT")
        self.assertEqual(trade.pnl_units, 5)
        self.assertEqual(result.metrics["net_pnl_units"], 5)
        self.assertIsNone(result.open_position)

    def test_end_of_data_policy_is_explicit(self) -> None:
        start = parse_iso8601_ns("2026-01-02T12:00:00Z")
        ticks = [
            MarketTrade("TEST", start, 0, 100, 1),
            MarketTrade("TEST", start + NANOSECONDS_PER_MINUTE, 0, 110, 1),
            MarketTrade("TEST", start + 2 * NANOSECONDS_PER_MINUTE, 0, 111, 1),
        ]
        features = list(calculate_sma_close(build_one_minute_candles(ticks), 2))
        strategy = StrategyDefinition.model_validate(strategy_record(end_of_data="LEAVE_OPEN"))
        ledger = []
        result = run_backtest(ticks, features, strategy, price_scale=0, on_trade=ledger.append)
        self.assertEqual(ledger, [])
        self.assertIsNotNone(result.open_position)
        self.assertTrue(result.metrics["has_open_position"])


if __name__ == "__main__":
    unittest.main()
