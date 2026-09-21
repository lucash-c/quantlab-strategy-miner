from __future__ import annotations

import unittest

from pydantic import ValidationError
from quantlab_core.errors import ContractError
from quantlab_core.numeric import CanonicalRational
from quantlab_robustness.contracts import (
    CandidateSelectionV1,
    RationalValueV1,
    RobustnessWorkloadPolicyV1,
)
from quantlab_robustness.exact import finite_decimal


class RobustnessContractTests(unittest.TestCase):
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
