from __future__ import annotations

import unittest

from quantlab_backtest.engine_v3 import FeatureSessionInputV3, run_backtest_v3
from quantlab_backtest.metrics_v3 import MetricsAccumulatorV3
from quantlab_core.market_data import Candle, SessionCandle, SessionTrade
from quantlab_core.sessions import TradingSession
from quantlab_core.strategy_v3 import StrategyDefinitionV3
from quantlab_core.time import NANOSECONDS_PER_MINUTE, parse_iso8601_ns


def strategy(
    direction: str,
    *,
    friction: bool = True,
    time_filter: dict[str, object] | None = None,
) -> StrategyDefinitionV3:
    return StrategyDefinitionV3.model_validate(
        {
            "schema_version": "strategy-definition/v3",
            "strategy_id": f"TEST.{direction}",
            "strategy_version": 1,
            "name": "Backtest v3 fixture",
            "logical_asset": "WIN",
            "timeframe": "1m",
            "evaluation_mode": "ON_CLOSE",
            "direction": direction,
            "features": [],
            "entry_conditions": {
                "type": "comparison",
                "operator": "GT",
                "left": {"type": "candle_field", "name": "close"},
                "right": {"type": "constant", "dimension": "PRICE", "value": "0"},
            },
            "entry_time_filter": time_filter,
            "take_profit": {"unit": "POINTS", "value": "5"},
            "stop_loss": {"unit": "POINTS", "value": "5"},
            "execution": {
                "entry_fill": "NEXT_TRADE",
                "position_policy": "SINGLE_POSITION_NO_QUEUE",
                "same_tick_reentry": False,
                "session_end": "CLOSE_AT_LAST_TRADE",
                "require_post_fill_event": True,
                "position_size": 1,
            },
            "cost_model": {
                "type": "FIXED_PER_SIDE" if friction else "NONE",
                "version": "1.0.0",
                **({"points_per_side": "0.5"} if friction else {}),
            },
            "slippage_model": {
                "type": "FIXED_POINTS" if friction else "NONE",
                "version": "1.0.0",
                **({"points_per_side": "1"} if friction else {}),
            },
        }
    )


def backtest_input(
    exit_price: int,
    *,
    first: str = "2026-09-10T09:00:00-03:00",
    nominal_close_minutes: int = 1,
    fill_minutes: int = 1,
    last_minutes: int = 2,
) -> FeatureSessionInputV3:
    start = parse_iso8601_ns(first)
    last = start + last_minutes * NANOSECONDS_PER_MINUTE
    session = TradingSession(
        "session", "2026-09-10", "WIN", "WINV26", start, last, 3, 0,
        "source", "import", "normalized",
    )
    close = start + nominal_close_minutes * NANOSECONDS_PER_MINUTE
    candle = Candle("WINV26", "1m", close - NANOSECONDS_PER_MINUTE, close, 100, 100, 100, 100, 1, 1)
    session_candle = SessionCandle(
        "session", "2026-09-10", "WIN", "WINV26", candle
    )
    raw = (
        (start, 1, 99),
        (start + fill_minutes * NANOSECONDS_PER_MINUTE, 2, 100),
        (last, 3, exit_price),
    )
    ticks = tuple(
        SessionTrade("session", "2026-09-10", "WIN", "WINV26", timestamp, sequence, price, 1)
        for timestamp, sequence, price in raw
    )
    return FeatureSessionInputV3(session, ticks, (session_candle,), {})


class BacktestV3Tests(unittest.TestCase):
    def run_case(
        self, direction: str, exit_price: int
    ) -> tuple[object, dict[str, object], list[object]]:
        trades: list[object] = []
        signals: list[object] = []
        summary = run_backtest_v3(
            (backtest_input(exit_price),),
            strategy(direction),
            common_price_scale=1,
            on_trade=trades.append,
            on_signal=signals.append,
        )
        self.assertEqual(len(trades), 1)
        trade = trades[0]
        self.assertEqual(
            trade.gross_pnl_units - trade.slippage_impact_units - trade.costs_units,
            trade.net_pnl_units,
        )
        self.assertEqual(trade.slippage_impact_units, 20)
        self.assertEqual(trade.costs_units, 10)
        self.assertEqual(signals[0].outcome, "FILLED")
        self.assertIn("condition", signals[0].condition_snapshot)
        return trade, summary.metrics, signals

    def test_buy_and_sell_take_profit_with_friction(self) -> None:
        buy, _, _ = self.run_case("BUY", 106)
        sell, _, _ = self.run_case("SELL", 94)
        for trade in (buy, sell):
            self.assertEqual(trade.exit_reason, "TAKE_PROFIT")
            self.assertEqual(trade.gross_pnl_units, 60)
            self.assertEqual(trade.net_pnl_units, 30)
        self.assertEqual(buy.entry_market_price_units, 1000)
        self.assertEqual(buy.entry_execution_price_units, 1010)
        self.assertEqual(sell.entry_execution_price_units, 990)

    def test_buy_and_sell_stop_loss_with_friction(self) -> None:
        buy, _, _ = self.run_case("BUY", 96)
        sell, _, _ = self.run_case("SELL", 104)
        for trade in (buy, sell):
            self.assertEqual(trade.exit_reason, "STOP_LOSS")
            self.assertEqual(trade.gross_pnl_units, -40)
            self.assertEqual(trade.net_pnl_units, -70)

    def test_buy_and_sell_session_end_with_friction(self) -> None:
        buy, _, _ = self.run_case("BUY", 102)
        sell, metrics, _ = self.run_case("SELL", 98)
        for trade in (buy, sell):
            self.assertEqual(trade.exit_reason, "SESSION_END")
            self.assertEqual(trade.gross_pnl_units, 20)
            self.assertEqual(trade.net_pnl_units, -10)
        self.assertEqual(metrics["losses"], 1)

    def test_none_models_preserve_market_price_and_gross_pnl(self) -> None:
        trades: list[object] = []
        summary = run_backtest_v3(
            (backtest_input(106),),
            strategy("BUY", friction=False),
            common_price_scale=0,
            on_trade=trades.append,
            on_signal=lambda _: None,
        )
        trade = trades[0]
        self.assertEqual(trade.entry_market_price_units, trade.entry_execution_price_units)
        self.assertEqual(trade.exit_market_price_units, trade.exit_execution_price_units)
        self.assertEqual(trade.gross_pnl_units, trade.net_pnl_units)
        self.assertEqual(summary.metrics["costs_units"], 0)

    def test_fill_time_is_validated_separately(self) -> None:
        time_filter = {
            "start": "09:00", "end": "09:02", "start_inclusive": True, "end_inclusive": False
        }
        signals: list[object] = []
        summary = run_backtest_v3(
            (backtest_input(101, fill_minutes=2, last_minutes=3),),
            strategy("BUY", time_filter=time_filter),
            common_price_scale=1,
            on_trade=lambda _: None,
            on_signal=signals.append,
        )
        self.assertEqual(summary.metrics["trades"], 0)
        self.assertEqual(signals[0].reason, "FILL_OUTSIDE_TIME_RANGE")

    def test_partial_final_candle_is_non_executable(self) -> None:
        signals: list[object] = []
        summary = run_backtest_v3(
            (backtest_input(101, nominal_close_minutes=3, last_minutes=2),),
            strategy("BUY"),
            common_price_scale=1,
            on_trade=lambda _: None,
            on_signal=signals.append,
        )
        self.assertEqual(summary.metrics["trades"], 0)
        self.assertEqual(signals[0].reason, "AVAILABLE_AFTER_LAST_ELIGIBLE_TRADE")

    def test_last_tick_fill_is_rejected(self) -> None:
        item = backtest_input(100, last_minutes=1)
        # Collapse duplicate last timestamps into the one eligible fill tick.
        item = FeatureSessionInputV3(item.session, item.ticks[:2], item.candles, {})
        signals: list[object] = []
        run_backtest_v3(
            (item,), strategy("BUY"), common_price_scale=1,
            on_trade=lambda _: None, on_signal=signals.append,
        )
        self.assertEqual(signals[0].outcome, "FILL_REJECTED")
        self.assertEqual(signals[0].reason, "LAST_TICK_FILL_FORBIDDEN")

    def test_metrics_use_explicit_undefined_values(self) -> None:
        metrics = MetricsAccumulatorV3().to_record(
            price_scale=0,
            cost_model={"type": "NONE", "version": "1.0.0"},
            slippage_model={"type": "NONE", "version": "1.0.0"},
        )
        self.assertEqual(
            metrics["win_rate"],
            {"status": "UNDEFINED", "value": None, "reason": "NO_TRADES"},
        )
        self.assertEqual(metrics["average_win"]["reason"], "NO_WINS")
        self.assertEqual(metrics["average_loss"]["reason"], "NO_LOSSES")
        self.assertEqual(metrics["profit_factor"]["reason"], "NO_LOSSES")


if __name__ == "__main__":
    unittest.main()
