from __future__ import annotations

import unittest
from pathlib import Path

from pydantic import ValidationError
from quantlab_core.errors import ContractError
from quantlab_core.numeric import CanonicalRational
from quantlab_robustness.contracts import (
    CandidateSelectionV1,
    ExecutionStressPolicyV1,
    MonteCarloPolicyV1,
    RationalValueV1,
    RobustnessGatePolicyV1,
    RobustnessWorkloadPolicyV1,
    SensitivityPolicyV1,
    WalkForwardPolicyV1,
    load_policy,
)
from quantlab_robustness.exact import finite_decimal
from quantlab_robustness.protocol import workload_preflight


class RobustnessContractTests(unittest.TestCase):
    def test_published_fixture_policies_match_strict_models(self):
        root = Path("examples/robustness")
        for name, model in (
            ("fixture-candidate-selection.json", CandidateSelectionV1),
            ("fixture-walk-forward.json", WalkForwardPolicyV1),
            ("fixture-monte-carlo.json", MonteCarloPolicyV1),
            ("fixture-sensitivity.json", SensitivityPolicyV1),
            ("fixture-stress.json", ExecutionStressPolicyV1),
            ("fixture-workload.json", RobustnessWorkloadPolicyV1),
            ("fixture-gate.json", RobustnessGatePolicyV1),
        ):
            with self.subTest(name=name):
                self.assertIsInstance(load_policy(root / name, model), model)

    def test_rational_and_decimal_are_canonical(self):
        self.assertEqual(
            RationalValueV1(numerator="1", denominator="2").value(),
            CanonicalRational(1, 2),
        )
        with self.assertRaisesRegex(ValidationError, "unique canonical"):
            RationalValueV1(numerator="2", denominator="4")
        self.assertEqual(finite_decimal(CanonicalRational(1, 8)), "0.125")
        with self.assertRaisesRegex(ContractError, "NON_TERMINATING"):
            finite_decimal(CanonicalRational(1, 3))

    def test_candidate_selector_requires_only_its_own_arguments(self):
        policy = CandidateSelectionV1.model_validate(
            {
                "schema_version": "robustness-candidate-selection/v1",
                "method": "RAW_RANKING_TOP_N",
                "top_n": 5,
            }
        )
        self.assertEqual(policy.top_n, 5)
        with self.assertRaises(ValidationError):
            CandidateSelectionV1.model_validate(
                {
                    "schema_version": "robustness-candidate-selection/v1",
                    "method": "ALL_SCORED",
                    "top_n": 5,
                }
            )

    def test_workload_policy_is_separate_and_content_addressed(self):
        policy = RobustnessWorkloadPolicyV1.model_validate(
            {
                "schema_version": "robustness-workload-policy/v1",
                "max_candidates": 10,
                "max_walk_forward_folds": 100,
                "max_paths": 1000,
                "max_path_length_sessions": 100,
                "max_total_sampled_blocks": 100000,
                "max_sensitivity_scenarios": 100,
                "max_stress_scenarios": 100,
                "max_candidate_scenario_combinations": 1000,
                "max_expected_cse_builds": 10000,
            }
        )
        changed = policy.model_copy(update={"max_paths": 2000})
        self.assertNotEqual(policy.policy_id, changed.policy_id)
        plan = workload_preflight(
            policy=policy,
            candidate_count=10,
            fold_count=3,
            monte_carlo_paths=100,
            path_length=20,
            sensitivity_scenarios=2,
            stress_scenarios=3,
            expected_cse_hits=5,
            expected_cse_builds=50,
            expected_feature_hits=7,
            expected_feature_builds=50,
        )
        self.assertEqual(plan["scientific_counts"]["monte_carlo_sampled_blocks"], 2000)
        self.assertEqual(plan["scientific_counts"]["candidate_scenario_combinations"], 50)
        self.assertTrue(
            plan["operational_estimates"]["excluded_from_scientific_fingerprints"]
        )
