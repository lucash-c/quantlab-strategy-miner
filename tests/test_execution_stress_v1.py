from __future__ import annotations

import unittest

from quantlab_backtest.engine_v3 import FeatureSessionInputV3, run_backtest_v3
from quantlab_core.market_data import Candle, SessionCandle, SessionTrade
from quantlab_core.sessions import TradingSession
from quantlab_core.strategy_v3 import StrategyDefinitionV3
from quantlab_core.time import NANOSECONDS_PER_MINUTE, parse_iso8601_ns
from quantlab_robustness.stress import resolve_stress_scenarios, summarize_stress
from robustness_helpers import fixed_friction, stress_policy
from test_backtest_v3 import strategy


def change(method, numerator, denominator="1"):
    return {"method": method, "value": {"numerator": numerator, "denominator": denominator}}


class ExecutionStressTests(unittest.TestCase):
    @staticmethod
    def path_fixture(prices=(99, 100, 105, 101)):
        start = parse_iso8601_ns("2026-09-10T09:00:00-03:00")
        last = start + 3 * NANOSECONDS_PER_MINUTE
        session = TradingSession(
            "session",
            "2026-09-10",
            "WIN",
            "WINV26",
            start,
            last,
            4,
            0,
            "source",
            "import",
            "normalized",
        )
        candle = Candle(
            "WINV26", "1m", start, start + NANOSECONDS_PER_MINUTE, 100, 100, 100, 100, 1, 1
        )
        ticks = tuple(
            SessionTrade("session", "2026-09-10", "WIN", "WINV26", timestamp, sequence, price, 1)
            for timestamp, sequence, price in (
                (start, 1, prices[0]),
                (start + NANOSECONDS_PER_MINUTE, 2, prices[1]),
                (start + 2 * NANOSECONDS_PER_MINUTE, 3, prices[2]),
                (last, 4, prices[3]),
            )
        )
        return FeatureSessionInputV3(
            session,
            ticks,
            (SessionCandle("session", "2026-09-10", "WIN", "WINV26", candle),),
            {},
        )

    @staticmethod
    def execute(record, prices=(99, 100, 105, 101)):
        trades = []
        summary = run_backtest_v3(
            (ExecutionStressTests.path_fixture(prices),),
            StrategyDefinitionV3.model_validate(record),
            common_price_scale=0,
            on_trade=trades.append,
            on_signal=lambda _: None,
        )
        return trades[0], summary.metrics

    def test_multiplier_one_is_not_a_stress_scenario(self):
        policy = stress_policy(
            [{"name": "same", "cost": change("MULTIPLIER", "1"), "slippage": None}]
        )
        result = resolve_stress_scenarios(fixed_friction(), policy)[0]
        self.assertEqual(result["status"], "INVALID_STRESS_SCENARIO")
        self.assertEqual(result["reason"], "NON_STRESS_SCENARIO")

    def test_absolute_friction_cannot_be_lower_than_baseline(self):
        policy = stress_policy(
            [
                {
                    "name": "lower",
                    "cost": change("ABSOLUTE_POINTS", "1", "2"),
                    "slippage": None,
                }
            ]
        )
        result = resolve_stress_scenarios(fixed_friction(), policy)[0]
        self.assertEqual(result["reason"], "NON_STRESS_SCENARIO")

    def test_exact_worse_stress_resolves_evaluation_config(self):
        policy = stress_policy(
            [
                {
                    "name": "double",
                    "cost": change("MULTIPLIER", "2"),
                    "slippage": change("ABSOLUTE_POINTS", "3", "2"),
                }
            ]
        )
        result = resolve_stress_scenarios(fixed_friction(), policy)[0]
        self.assertEqual(result["status"], "VALID")
        self.assertEqual(result["resolved_cost"], {"numerator": "2", "denominator": "1"})
        self.assertEqual(result["resolved_slippage"], {"numerator": "3", "denominator": "2"})

    def test_nonterminating_multiplier_is_invalid_without_rounding(self):
        policy = stress_policy(
            [
                {
                    "name": "four-thirds",
                    "cost": change("MULTIPLIER", "4", "3"),
                    "slippage": None,
                }
            ]
        )
        result = resolve_stress_scenarios(fixed_friction(), policy)[0]
        self.assertEqual(result["status"], "INVALID_STRESS_SCENARIO")
        self.assertEqual(result["reason"], "NON_TERMINATING_DECIMAL_RESULT")

    def test_cost_only_keeps_path_and_degrades_net(self):
        baseline = strategy("BUY", friction=False).model_dump(mode="json")
        stressed = {
            **baseline,
            "cost_model": {
                "type": "FIXED_PER_SIDE",
                "version": "1.0.0",
                "points_per_side": "1",
            },
        }
        base_trade, _ = self.execute(baseline)
        stress_trade, _ = self.execute(stressed)
        self.assertEqual(base_trade.exit_reason, stress_trade.exit_reason)
        self.assertEqual(base_trade.exit_timestamp_ns_utc, stress_trade.exit_timestamp_ns_utc)
        self.assertEqual(base_trade.gross_pnl_units, stress_trade.gross_pnl_units)
        self.assertGreater(stress_trade.costs_units, base_trade.costs_units)
        self.assertLess(stress_trade.net_pnl_units, base_trade.net_pnl_units)

    def test_slippage_stress_rebacktest_changes_execution_path(self):
        for direction, prices in (
            ("BUY", (99, 100, 105, 101)),
            ("SELL", (101, 100, 95, 99)),
        ):
            with self.subTest(direction=direction):
                baseline = strategy(direction, friction=False).model_dump(mode="json")
                stressed = {
                    **baseline,
                    "slippage_model": {
                        "type": "FIXED_POINTS",
                        "version": "1.0.0",
                        "points_per_side": "1",
                    },
                }
                base_trade, _ = self.execute(baseline, prices)
                stress_trade, _ = self.execute(stressed, prices)
                self.assertEqual(base_trade.exit_reason, "TAKE_PROFIT")
                self.assertEqual(stress_trade.exit_reason, "SESSION_END")
                self.assertNotEqual(
                    base_trade.exit_timestamp_ns_utc, stress_trade.exit_timestamp_ns_utc
                )
                self.assertNotEqual(
                    base_trade.target_execution_price_units,
                    stress_trade.target_execution_price_units,
                )
                self.assertNotEqual(base_trade.net_pnl_units, stress_trade.net_pnl_units)

    def test_all_gate_transitions_are_preserved_including_fail_to_pass(self):
        scenarios = [
            {"status": "VALID", "stress_scenario_id": name} for name in ("pp", "pf", "ff", "fp")
        ]
        results = [
            {"stress_scenario_id": name, "gate_transition": transition}
            for name, transition in (
                ("pp", "PASS_TO_PASS"),
                ("pf", "PASS_TO_FAIL"),
                ("ff", "FAIL_TO_FAIL"),
                ("fp", "FAIL_TO_PASS"),
            )
        ]
        summary = summarize_stress(results, scenarios)
        for metric in ("pass_to_pass", "pass_to_fail", "fail_to_fail", "fail_to_pass"):
            self.assertEqual(summary["metrics"][metric]["value"]["numerator"], "1")
