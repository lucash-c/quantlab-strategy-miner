from __future__ import annotations

import unittest
from datetime import date, timedelta

from quantlab_robustness.walk_forward import build_walk_forward_plan
from robustness_helpers import walk_policy


def sessions(count):
    start = date(2026, 1, 1)
    return [
        {"session_id": f"S{index + 1:02d}", "trading_date": str(start + timedelta(days=index))}
        for index in range(count)
    ]


class WalkForwardTests(unittest.TestCase):
    def test_rolling_fixed_13_6_produces_three_overlapping_folds(self):
        plan = build_walk_forward_plan(
            dataset_id="dataset", logical_asset="WIN", sessions=sessions(21), policy=walk_policy()
        )
        self.assertEqual(plan["status"], "READY")
        self.assertEqual(plan["fold_count"], 3)
        self.assertEqual(
            plan["folds"][0]["discovery_session_ids"],
            [f"S{i:02d}" for i in range(1, 14)],
        )
        self.assertEqual(
            plan["folds"][0]["validation_session_ids"],
            [f"S{i:02d}" for i in range(14, 20)],
        )
        self.assertEqual(
            plan["folds"][2]["validation_session_ids"],
            [f"S{i:02d}" for i in range(16, 22)],
        )
        overlap = plan["overlap"]
        self.assertEqual(overlap["validation_session_slots"], 18)
        self.assertEqual(overlap["unique_validation_sessions"], 8)
        self.assertEqual(overlap["overlap_session_slots"], 10)
        self.assertFalse(overlap["statistically_independent"])

    def test_walk_forward_insufficient_history_never_invents_partial_fold(self):
        plan = build_walk_forward_plan(
            dataset_id="dataset", logical_asset="WIN", sessions=sessions(20), policy=walk_policy()
        )
        self.assertEqual(plan["fold_count"], 2)
        self.assertEqual(plan["status"], "INSUFFICIENT_WALK_FORWARD_HISTORY")

    def test_walk_forward_uses_only_explicit_authorized_sessions(self):
        authorized = sessions(21)
        first = build_walk_forward_plan(
            dataset_id="dataset", logical_asset="WIN", sessions=authorized, policy=walk_policy()
        )
        second = build_walk_forward_plan(
            dataset_id="dataset",
            logical_asset="WIN",
            sessions=list(authorized),
            policy=walk_policy(),
        )
        self.assertEqual(first, second)
