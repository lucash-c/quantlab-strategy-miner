from __future__ import annotations

import unittest
from dataclasses import asdict, replace

from quantlab_backtest.engine_v3 import run_backtest_v3
from quantlab_backtest.partition_metrics import aggregate_partition
from test_backtest_v3 import backtest_input, strategy


def oracle_sessions(pnls):
    prototype = []
    run_backtest_v3(
        [backtest_input(94)],
        strategy("BUY", friction=False),
        common_price_scale=0,
        on_trade=prototype.append,
        on_signal=lambda r: None,
    )
    for day, outcomes in enumerate(pnls, 1):
        session = replace(
            backtest_input(94).session, session_id=f"s{day}", trading_date=f"2026-09-{day:02d}"
        )
        ledger = []
        for ordinal, pnl in enumerate(outcomes, 1):
            trade = replace(
                prototype[0],
                trade_number=ordinal,
                session_id=session.session_id,
                trading_date=session.trading_date,
                exit_market_price_units=100 + pnl,
                exit_execution_price_units=100 + pnl,
                gross_pnl_units=pnl,
                net_pnl_units=pnl,
                exit_reason="SESSION_END",
            )
            ledger.append(trade.to_record())
        record = {
            "session": asdict(session),
            "price_scale": 0,
            "evaluation_id": f"e{day}",
            "result_fingerprint": f"fp{day}",
            "ledger_count": len(ledger),
            "journal_count": 0,
        }
        yield record, ledger, []


class ResearchAggregationTests(unittest.TestCase):
    def aggregate(self, pnls):
        return aggregate_partition(
            "c",
            "partition",
            oracle_sessions(pnls),
            strategy("BUY", friction=False),
            0,
            lambda r: None,
            lambda r: None,
        )

    def test_drawdown_not_sum_streak_crosses_empty_session(self):
        result = self.aggregate([[10, -5], [], [-8], [20, -1]])
        self.assertEqual(result["backtest_metrics"]["max_drawdown_units"], 13)
        summed = sum(s["backtest_metrics"]["max_drawdown_units"] for s in result["sessions"])
        self.assertEqual(summed, 14)
        self.assertEqual(result["backtest_metrics"]["max_consecutive_losses"], 2)
        self.assertEqual(result["backtest_metrics"]["net_pnl_units"], 16)

    def test_breakeven_resets_and_metrics_all_sessions_exact(self):
        result = self.aggregate([[-1], [], [0], [-1]])
        self.assertEqual(result["backtest_metrics"]["max_consecutive_losses"], 1)
        values = result["metrics"]
        self.assertEqual(values["total_sessions"]["value"]["numerator"], "4")
        self.assertEqual(values["flat_sessions"]["value"]["numerator"], "2")
        self.assertEqual(
            values["trades_per_session"]["value"], {"numerator": "3", "denominator": "4"}
        )
        self.assertEqual(
            values["net_pnl_per_session"]["value"], {"numerator": "-1", "denominator": "2"}
        )
        self.assertEqual(
            values["largest_profitable_session_share"]["reason"], "NO_PROFITABLE_SESSIONS"
        )
        self.assertEqual(
            values["largest_trade_count_session_share"]["value"],
            {"numerator": "1", "denominator": "3"},
        )

    def test_concentration_single_session_and_zero_trade_denominators(self):
        result = self.aggregate([[10], [], []])
        self.assertEqual(
            result["metrics"]["largest_profitable_session_share"]["value"],
            {"numerator": "1", "denominator": "1"},
        )
        zero = self.aggregate([[], []])
        self.assertEqual(
            zero["metrics"]["largest_trade_count_session_share"]["reason"], "NO_TRADES"
        )
        self.assertEqual(
            zero["metrics"]["positive_session_rate"]["value"],
            {"numerator": "0", "denominator": "1"},
        )


if __name__ == "__main__":
    unittest.main()
