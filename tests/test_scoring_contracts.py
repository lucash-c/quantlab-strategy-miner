from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError
from quantlab_core.errors import ContractError
from quantlab_core.numeric import CanonicalRational
from quantlab_scoring.contracts import RationalV1, ResearchScorePolicyV1, load_policy
from quantlab_scoring.transforms import degradation, inverse, linear, plateau
from scoring_helpers import diversity_policy, ranking_policy, score_policy


class ScoringContractTests(unittest.TestCase):
    def test_reference_policy_is_explicit_dimension_checked_and_exact(self):
        policy = score_policy()
        self.assertEqual(
            sum(
                (term.max_points.value() for term in policy.positive_terms),
                start=CanonicalRational(0),
            ),
            CanonicalRational(100),
        )
        self.assertEqual(
            sum(
                (term.max_points.value() for term in policy.penalty_terms),
                start=CanonicalRational(0),
            ),
            CanonicalRational(10),
        )
        self.assertTrue(policy.score_policy_id.startswith("sha256:"))
        self.assertTrue(ranking_policy().ranking_policy_id.startswith("sha256:"))
        self.assertTrue(diversity_policy().diversity_policy_id.startswith("sha256:"))

    def test_rational_rejects_noncanonical_equivalents_and_zero(self):
        for record in (
            {"numerator": "2", "denominator": "4"},
            {"numerator": "-2", "denominator": "4"},
            {"numerator": "0", "denominator": "500"},
        ):
            with self.assertRaises(ValidationError):
                RationalV1.model_validate(record)
        self.assertEqual(
            RationalV1(numerator="-1", denominator="2").value(), CanonicalRational(-1, 2)
        )

    def test_float_input_is_rejected_before_policy_execution(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "policy.json"
            path.write_text('{"score_scale": 10000.0}', encoding="utf-8")
            with self.assertRaisesRegex(ContractError, "floating-point"):
                load_policy(path, ResearchScorePolicyV1)

    def test_dimension_mismatch_fails_policy_validation(self):
        record = score_policy().model_dump(mode="json")
        record["positive_terms"][0]["transform"]["dimension"] = "COUNT"
        with self.assertRaisesRegex(ValidationError, "transform dimension mismatch"):
            ResearchScorePolicyV1.model_validate(record)

    def test_weight_sum_and_penalty_max_are_closed(self):
        record = score_policy().model_dump(mode="json")
        record["positive_terms"][0]["max_points"] = {
            "numerator": "13",
            "denominator": "1",
        }
        with self.assertRaisesRegex(ValidationError, "component maxima"):
            ResearchScorePolicyV1.model_validate(record)

    def test_policy_identity_ignores_input_term_order(self):
        first = score_policy()
        record = first.model_dump(mode="json")
        record["positive_terms"].reverse()
        record["penalty_terms"].reverse()
        second = ResearchScorePolicyV1.model_validate(record)
        self.assertEqual(first.score_policy_id, second.score_policy_id)


class ExactTransformTests(unittest.TestCase):
    def _term(self, term_id):
        return next(term for term in score_policy().positive_terms if term.term_id == term_id)

    def test_linear_boundaries_and_midpoint(self):
        spec = self._term("performance.win_rate").transform
        self.assertEqual(linear(CanonicalRational(1, 5), spec), CanonicalRational(0))
        self.assertEqual(linear(CanonicalRational(1, 2), spec), CanonicalRational(1, 2))
        self.assertEqual(linear(CanonicalRational(4, 5), spec), CanonicalRational(1))

    def test_inverse_boundaries_and_midpoint(self):
        spec = self._term("risk.max_drawdown_risk").transform
        self.assertEqual(inverse(CanonicalRational(1), spec), CanonicalRational(1))
        self.assertEqual(inverse(CanonicalRational(6), spec), CanonicalRational(1, 2))
        self.assertEqual(inverse(CanonicalRational(12), spec), CanonicalRational(0))

    def test_degradation_is_monotonic_with_approved_sign(self):
        spec = self._term("stability.average_trade_delta_risk").transform
        self.assertEqual(degradation(CanonicalRational(1), spec), CanonicalRational(1))
        self.assertEqual(degradation(CanonicalRational(-1, 20), spec), CanonicalRational(1))
        self.assertEqual(degradation(CanonicalRational(-11, 40), spec), CanonicalRational(1, 2))
        self.assertEqual(degradation(CanonicalRational(-1, 2), spec), CanonicalRational(0))

    def test_plateau_is_continuous_piecewise(self):
        spec = self._term("activity.trades_per_session").transform
        expected = {
            CanonicalRational(1, 2): CanonicalRational(0),
            CanonicalRational(3, 4): CanonicalRational(1, 2),
            CanonicalRational(1): CanonicalRational(1),
            CanonicalRational(10): CanonicalRational(1),
            CanonicalRational(20): CanonicalRational(1, 2),
            CanonicalRational(30): CanonicalRational(0),
        }
        for value, result in expected.items():
            self.assertEqual(plateau(value, spec), result)

    def test_invalid_degradation_and_plateau_anchors_fail_preflight(self):
        record = json.loads(Path("examples/scoring/research-score-v1.json").read_text())
        term = next(
            item
            for item in record["positive_terms"]
            if item["term_id"] == "stability.win_rate_delta"
        )
        term["transform"]["failure"] = term["transform"]["tolerance"]
        with self.assertRaisesRegex(ValidationError, "failure > tolerance"):
            ResearchScorePolicyV1.model_validate(record)


if __name__ == "__main__":
    unittest.main()
