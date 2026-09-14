from __future__ import annotations

import unittest

from quantlab_core.candles import build_one_minute_candles
from quantlab_core.errors import ContractError
from quantlab_core.indicators import calculate_sma_close
from quantlab_core.market_data import MarketTrade
from quantlab_core.price import decimal_to_units, normalize_decimal_text, units_to_decimal
from quantlab_core.time import NANOSECONDS_PER_MINUTE, format_utc_ns, parse_iso8601_ns


class TimeAndPriceTests(unittest.TestCase):
    def test_offsets_resolve_to_the_same_nanosecond(self) -> None:
        local = parse_iso8601_ns("2026-01-02T09:00:00.123456789-03:00")
        utc = parse_iso8601_ns("2026-01-02T12:00:00.123456789Z")
        self.assertEqual(local, utc)
        self.assertEqual(format_utc_ns(utc), "2026-01-02T12:00:00.123456789Z")

    def test_timestamp_requires_explicit_offset(self) -> None:
        with self.assertRaises(ContractError):
            parse_iso8601_ns("2026-01-02T09:00:00")

    def test_price_is_exact_fixed_point(self) -> None:
        self.assertEqual(normalize_decimal_text("100.0500"), ("100.05", 2))
        self.assertEqual(decimal_to_units("100.05", 2), 10_005)
        self.assertEqual(units_to_decimal(10_005, 2), "100.05")


class CandleAndIndicatorTests(unittest.TestCase):
    def test_boundary_and_gap_semantics(self) -> None:
        start = parse_iso8601_ns("2026-01-02T12:00:00Z")
        trades = [
            MarketTrade("TEST", start, 0, 1000, 1),
            MarketTrade("TEST", start + NANOSECONDS_PER_MINUTE - 1, 0, 1010, 2),
            MarketTrade("TEST", start + 2 * NANOSECONDS_PER_MINUTE, 0, 990, 3),
        ]
        candles = list(build_one_minute_candles(trades))
        self.assertEqual(len(candles), 2)
        self.assertEqual(candles[0].open_time_ns_utc, start)
        self.assertEqual(candles[0].close_units, 1010)
        self.assertEqual(candles[0].volume, 3)
        self.assertEqual(candles[1].open_time_ns_utc, start + 2 * NANOSECONDS_PER_MINUTE)

    def test_sma_has_explicit_warmup_and_exact_rational_value(self) -> None:
        start = parse_iso8601_ns("2026-01-02T12:00:00Z")
        trades = [
            MarketTrade("TEST", start + minute * NANOSECONDS_PER_MINUTE, 0, price, 1)
            for minute, price in enumerate((100, 101, 105))
        ]
        rows = list(calculate_sma_close(build_one_minute_candles(trades), 2))
        self.assertIsNone(rows[0].sma_close_sum_units)
        self.assertEqual(rows[1].sma_close_sum_units, 201)
        self.assertEqual(rows[1].sma_close_period, 2)
        self.assertEqual(rows[2].sma_close_sum_units, 206)


if __name__ == "__main__":
    unittest.main()
