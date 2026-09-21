from __future__ import annotations

import unittest

from pydantic import ValidationError
from quantlab_robustness.sensitivity import build_sensitivity_scenarios
from robustness_helpers import candidate_strategy, sensitivity_policy


class SensitivityTests(unittest.TestCase):
    def test_zero_perturbation_is_rejected_in_preflight(self):
        with self.assertRaisesRegex(ValidationError, "NO_OP_PERTURBATION"):
            sensitivity_policy(
                [
                    {
                        "name": "zero",
                        "target": {"kind": "STOP_LOSS", "path": "/stop_loss/value"},
                        "method": "DECIMAL_ABSOLUTE_DELTA",
                        "rational_delta": {"numerator": "0", "denominator": "1"},
                    }
                ]
            )

    def test_oat_variant_gets_semantic_identity_and_never_mutates_baseline(self):
        candidate_id, baseline = candidate_strategy()
        policy = sensitivity_policy(
            [
                {
                    "name": "stop-plus-one",
                    "target": {"kind": "STOP_LOSS", "path": "/stop_loss/value"},
                    "method": "DECIMAL_ABSOLUTE_DELTA",
                    "rational_delta": {"numerator": "1", "denominator": "1"},
                }
            ]
        )
        scenarios, variants = build_sensitivity_scenarios(candidate_id, baseline, policy)
        self.assertEqual(scenarios[0]["status"], "VALID")
        self.assertNotEqual(scenarios[0]["variant_candidate_id"], candidate_id)
        self.assertEqual(baseline.stop_loss.value, "100")
        self.assertEqual(next(iter(variants.values())).stop_loss.value, "101")

    def test_two_scenarios_can_share_one_variant_work_item(self):
        candidate_id, baseline = candidate_strategy()
        policy = sensitivity_policy(
            [
                {
                    "name": "absolute",
                    "target": {"kind": "STOP_LOSS", "path": "/stop_loss/value"},
                    "method": "DECIMAL_ABSOLUTE_DELTA",
                    "rational_delta": {"numerator": "10", "denominator": "1"},
                },
                {
                    "name": "relative",
                    "target": {"kind": "STOP_LOSS", "path": "/stop_loss/value"},
                    "method": "DECIMAL_RELATIVE_DELTA",
                    "rational_delta": {"numerator": "1", "denominator": "10"},
                },
            ]
        )
        scenarios, variants = build_sensitivity_scenarios(candidate_id, baseline, policy)
        self.assertEqual(len(scenarios), 2)
        self.assertNotEqual(
            scenarios[0]["sensitivity_scenario_id"],
            scenarios[1]["sensitivity_scenario_id"],
        )
        self.assertEqual(scenarios[0]["variant_candidate_id"], scenarios[1]["variant_candidate_id"])
        self.assertEqual(len(variants), 1)

    def test_nonterminating_relative_variant_is_invalid_without_rounding(self):
        candidate_id, baseline = candidate_strategy()
        policy = sensitivity_policy(
            [
                {
                    "name": "third",
                    "target": {"kind": "STOP_LOSS", "path": "/stop_loss/value"},
                    "method": "DECIMAL_RELATIVE_DELTA",
                    "rational_delta": {"numerator": "1", "denominator": "3"},
                }
            ]
        )
        scenarios, variants = build_sensitivity_scenarios(candidate_id, baseline, policy)
        self.assertEqual(scenarios[0]["reason"], "NON_TERMINATING_DECIMAL_RESULT")
        self.assertEqual(variants, {})
