from __future__ import annotations

import unittest

from pydantic import ValidationError
from quantlab_core.strategy_v3 import StrategyDefinitionV3
from quantlab_mining.canonicalization import named_candidate
from quantlab_robustness.sensitivity import build_sensitivity_scenarios
from robustness_helpers import candidate_strategy, sensitivity_policy


class SensitivityTests(unittest.TestCase):
    def test_v1_targets_period_threshold_bounds_stop_and_target_but_not_time(self):
        candidate_id, baseline = candidate_strategy()
        specs = [
            {
                "name": "period",
                "target": {
                    "kind": "FEATURE_PARAMETER",
                    "path": "/features/0/parameters/period",
                },
                "method": "INTEGER_ABSOLUTE_DELTA",
                "integer_delta": 1,
            },
            {
                "name": "threshold",
                "target": {
                    "kind": "CONDITION_CONSTANT",
                    "path": "/entry_conditions/children/0/right/value",
                },
                "method": "DECIMAL_ABSOLUTE_DELTA",
                "rational_delta": {"numerator": "1", "denominator": "1"},
            },
            {
                "name": "stop",
                "target": {"kind": "STOP_LOSS", "path": "/stop_loss/value"},
                "method": "DECIMAL_ABSOLUTE_DELTA",
                "rational_delta": {"numerator": "1", "denominator": "1"},
            },
            {
                "name": "target",
                "target": {"kind": "TAKE_PROFIT", "path": "/take_profit/value"},
                "method": "DECIMAL_ABSOLUTE_DELTA",
                "rational_delta": {"numerator": "1", "denominator": "1"},
            },
        ]
        scenarios, variants = build_sensitivity_scenarios(
            candidate_id, baseline, sensitivity_policy(specs)
        )
        self.assertEqual([item["status"] for item in scenarios], ["VALID"] * 4)
        self.assertEqual(len(variants), 4)

        record = baseline.model_dump(mode="json")
        record["features"] = []
        record["entry_conditions"] = {
            "type": "range",
            "operator": "BETWEEN",
            "value": {"type": "candle_field", "name": "close"},
            "lower": {"type": "constant", "dimension": "PRICE", "value": "90"},
            "upper": {"type": "constant", "dimension": "PRICE", "value": "110"},
            "lower_inclusive": True,
            "upper_inclusive": False,
        }
        range_id, range_strategy = named_candidate(StrategyDefinitionV3.model_validate(record))
        bounds, _ = build_sensitivity_scenarios(
            range_id,
            range_strategy,
            sensitivity_policy(
                [
                    {
                        "name": "lower-bound",
                        "target": {
                            "kind": "CONDITION_CONSTANT",
                            "path": "/entry_conditions/lower/value",
                        },
                        "method": "DECIMAL_ABSOLUTE_DELTA",
                        "rational_delta": {"numerator": "1", "denominator": "1"},
                    },
                    {
                        "name": "upper-bound",
                        "target": {
                            "kind": "CONDITION_CONSTANT",
                            "path": "/entry_conditions/upper/value",
                        },
                        "method": "DECIMAL_ABSOLUTE_DELTA",
                        "rational_delta": {"numerator": "-1", "denominator": "1"},
                    },
                ]
            ),
        )
        self.assertEqual([item["status"] for item in bounds], ["VALID", "VALID"])

        time_scenario, _ = build_sensitivity_scenarios(
            candidate_id,
            baseline,
            sensitivity_policy(
                [
                    {
                        "name": "time-outside-v1",
                        "target": {
                            "kind": "CONDITION_CONSTANT",
                            "path": "/entry_time_filter/start",
                        },
                        "method": "DECIMAL_ABSOLUTE_DELTA",
                        "rational_delta": {"numerator": "1", "denominator": "1"},
                    }
                ]
            ),
        )
        self.assertEqual(time_scenario[0]["reason"], "TARGET_KIND_PATH_MISMATCH")

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
