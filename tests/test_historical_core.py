from __future__ import annotations

import unittest

from quantlab_core.errors import ContractError
from quantlab_core.market_data import MarketTrade
from quantlab_core.price import common_decimal_scale, rescale_units_exact
from quantlab_core.time import NANOSECONDS_PER_MINUTE, parse_iso8601_ns
from quantlab_core.timeframes import build_timeframe_candles


class HistoricalCoreTests(unittest.TestCase):
    def test_four_sparse_timeframes_have_nominal_bounds(self) -> None:
        start = parse_iso8601_ns("2026-09-10T09:00:00-03:00")
        trades = [
            MarketTrade("WINV26", start + minute * NANOSECONDS_PER_MINUTE, 0, 100 + minute, 1)
            for minute in range(5)
        ]
        expected = {"1m": 5, "2m": 3, "5m": 1, "15m": 1}
        for timeframe, count in expected.items():
            with self.subTest(timeframe=timeframe):
                candles = list(build_timeframe_candles(trades, timeframe))
                self.assertEqual(len(candles), count)
        last = list(build_timeframe_candles(trades, "15m"))[-1]
        self.assertEqual(last.open_time_ns_utc, start)
        self.assertEqual(
            last.close_time_ns_utc,
            start + 15 * NANOSECONDS_PER_MINUTE,
        )

    def test_historical_scale_is_exact_and_multiplicative(self) -> None:
        self.assertEqual(common_decimal_scale([0, 2], ["5", "0.125"]), 3)
        self.assertEqual(rescale_units_exact(101_000_25, 2, 3), 1_010_002_50)
        with self.assertRaisesRegex(ContractError, "rounding"):
            rescale_units_exact(123, 2, 1)


if __name__ == "__main__":
    unittest.main()

