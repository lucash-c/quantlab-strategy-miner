from __future__ import annotations

import unittest

from quantlab_core.feature_engine_v2 import (
    calculate_derived_feature_series,
    calculate_feature_series,
)
from quantlab_core.feature_specs import FeatureSpec, parse_feature_spec
from quantlab_core.market_data import Candle, MarketTrade
from quantlab_core.numeric import CanonicalRational
from quantlab_core.sessions import TradingSession
from quantlab_core.time import NANOSECONDS_PER_MINUTE, parse_iso8601_ns


def feature_spec(
    name: str,
    parameters: dict[str, object] | None = None,
    inputs: list[str] | None = None,
) -> FeatureSpec:
    return parse_feature_spec(
        {
            "feature_id": name,
            "name": name,
            "version": "1.0.0",
            "parameters": parameters or {},
            "inputs": inputs or [],
        }
    )


def make_session(
    *,
    day: str = "2026-09-10",
    first_offset_ns: int = 0,
    last_minutes: int = 30,
    scale: int = 0,
) -> TradingSession:
    start = parse_iso8601_ns(f"{day}T09:00:00-03:00") + first_offset_ns
    return TradingSession(
        f"session-{day}",
        day,
        "WIN",
        "WINV26",
        start,
        start + last_minutes * NANOSECONDS_PER_MINUTE,
        10,
        scale,
        "source",
        "import",
        "normalized",
    )


def candles_from_closes(
    closes: list[int],
    *,
    start: int | None = None,
    minute_steps: list[int] | None = None,
) -> list[Candle]:
    start = start or parse_iso8601_ns("2026-09-10T09:00:00-03:00")
    steps = minute_steps or list(range(len(closes)))
    return [
        Candle(
            "WINV26",
            "1m",
            start + minute * NANOSECONDS_PER_MINUTE,
            start + (minute + 1) * NANOSECONDS_PER_MINUTE,
            close,
            close + 1,
            close - 1,
            close,
            index + 1,
            1,
        )
        for index, (minute, close) in enumerate(zip(steps, closes, strict=True))
    ]


class FeatureEngineV2Tests(unittest.TestCase):
    def test_sma_ema_and_atr_have_formal_math(self) -> None:
        session = make_session()
        candles = candles_from_closes([10, 11, 12, 13])
        sma = list(
            calculate_feature_series(
                session, candles, feature_spec("sma_close", {"period": 3})
            )
        )
        self.assertEqual(sma[2].value.numeric, CanonicalRational(11))

        ema = list(
            calculate_feature_series(
                session, candles, feature_spec("ema_close", {"period": 3})
            )
        )
        self.assertEqual([row.warmup_status for row in ema[:2]], ["WARMING_UP"] * 2)
        self.assertEqual(ema[2].value.numeric, CanonicalRational(11))
        self.assertEqual(ema[3].value.numeric, CanonicalRational(12))

        atr_candles = [
            Candle("WINV26", "1m", i * 60, (i + 1) * 60, *ohlc, 1, 1)
            for i, ohlc in enumerate(
                [
                    (10, 11, 9, 10),
                    (12, 13, 11, 12),
                    (13, 15, 12, 13),
                    (15, 17, 13, 15),
                ]
            )
        ]
        atr = list(
            calculate_feature_series(
                session, atr_candles, feature_spec("atr_wilder", {"period": 3})
            )
        )
        self.assertEqual(
            atr[2].value.numeric, CanonicalRational(2_666_666_667, 10**9)
        )
        self.assertEqual(atr[3].value.numeric, CanonicalRational(3_111_111_111, 10**9))

    def test_vwap_uses_trades_and_excludes_close_boundary_tick(self) -> None:
        start = parse_iso8601_ns("2026-09-10T09:00:00-03:00")
        session = make_session()
        candles = candles_from_closes([10, 14], start=start)
        trades = [
            MarketTrade("WINV26", start, 0, 10, 2),
            MarketTrade("WINV26", start + 30_000_000_000, 1, 20, 1),
            MarketTrade("WINV26", start + NANOSECONDS_PER_MINUTE, 2, 14, 2),
        ]
        rows = list(
            calculate_feature_series(
                session,
                candles,
                feature_spec("session_trade_vwap"),
                trades=trades,
            )
        )
        self.assertEqual(rows[0].value.numeric, CanonicalRational(40, 3))
        self.assertEqual(rows[1].value.numeric, CanonicalRational(68, 5))

    def test_sparse_rolling_period_counts_observed_candles(self) -> None:
        session = make_session()
        candles = candles_from_closes(
            [10, 20, 31], minute_steps=[0, 5, 10]
        )
        rows = list(
            calculate_feature_series(
                session,
                candles,
                feature_spec(
                    "rolling_high", {"period": 2, "include_current": False}
                ),
            )
        )
        self.assertEqual([row.warmup_status for row in rows], [
            "WARMING_UP",
            "WARMING_UP",
            "READY",
        ])
        self.assertEqual(rows[2].value.numeric, CanonicalRational(21))

    def test_geometry_volume_momentum_and_undefined_range(self) -> None:
        session = make_session()
        start = parse_iso8601_ns("2026-09-10T09:00:00-03:00")
        candle = Candle("WINV26", "1m", start, start + 60, 10, 15, 8, 12, 7, 1)
        expected = {
            "candle_body": CanonicalRational(2),
            "absolute_body": CanonicalRational(2),
            "upper_wick": CanonicalRational(3),
            "lower_wick": CanonicalRational(2),
            "total_range": CanonicalRational(7),
        }
        for name, value in expected.items():
            row = next(calculate_feature_series(session, [candle], feature_spec(name)))
            self.assertEqual(row.value.numeric, value)
        direction = next(
            calculate_feature_series(session, [candle], feature_spec("candle_direction"))
        )
        self.assertEqual(direction.value.text, "UP")

        flat = Candle("WINV26", "1m", start, start + 60, 10, 10, 10, 10, 1, 1)
        position = next(
            calculate_feature_series(
                session, [flat], feature_spec("close_range_position")
            )
        )
        self.assertIsNone(position.value)
        self.assertEqual(position.undefined_reason, "ZERO_RANGE")

        momentum = list(
            calculate_feature_series(
                session,
                candles_from_closes([100, 110, 121]),
                feature_spec("n_candle_return", {"period": 2}),
            )
        )
        self.assertEqual(momentum[2].value.numeric, CanonicalRational(21, 100))

    def test_dependencies_and_session_anchor_are_deterministic(self) -> None:
        first_trade_offset = 3 * NANOSECONDS_PER_MINUTE + 27_000_000_000
        session = make_session(first_offset_ns=first_trade_offset)
        start = parse_iso8601_ns("2026-09-10T09:00:00-03:00")
        one_minute = Candle(
            "WINV26",
            "1m",
            start + 4 * NANOSECONDS_PER_MINUTE,
            start + 5 * NANOSECONDS_PER_MINUTE,
            10,
            10,
            10,
            10,
            1,
            1,
        )
        five_minute = Candle(
            "WINV26",
            "5m",
            start,
            start + 5 * NANOSECONDS_PER_MINUTE,
            10,
            10,
            10,
            10,
            1,
            1,
        )
        spec = feature_spec("minute_since_session_start")
        values = [
            next(calculate_feature_series(session, [candle], spec)).value.numeric
            for candle in (one_minute, five_minute)
        ]
        self.assertEqual(values, [CanonicalRational(2), CanonicalRational(2)])

        sma_spec = feature_spec("sma_close", {"period": 1})
        sma = list(calculate_feature_series(session, [one_minute], sma_spec))
        relation_spec = feature_spec("close_vs_sma", inputs=[sma_spec.feature_id])
        relation = list(
            calculate_derived_feature_series(
                session, [one_minute], relation_spec, [sma]
            )
        )
        self.assertEqual(relation[0].value.text, "EQUAL")

    def test_future_changes_do_not_change_past_feature(self) -> None:
        session = make_session()
        spec = feature_spec("sma_close", {"period": 2})
        original = list(
            calculate_feature_series(session, candles_from_closes([10, 20, 30]), spec)
        )
        changed = list(
            calculate_feature_series(session, candles_from_closes([10, 20, 999]), spec)
        )
        self.assertEqual(original[1], changed[1])

    def test_indicator_state_resets_when_called_for_next_session(self) -> None:
        spec = feature_spec("ema_close", {"period": 2})
        first = list(
            calculate_feature_series(
                make_session(day="2026-09-10"),
                candles_from_closes([10, 20]),
                spec,
            )
        )
        second = list(
            calculate_feature_series(
                make_session(day="2026-09-11"),
                candles_from_closes([30]),
                spec,
            )
        )
        self.assertEqual(first[-1].warmup_status, "READY")
        self.assertEqual(second[0].warmup_status, "WARMING_UP")


if __name__ == "__main__":
    unittest.main()
