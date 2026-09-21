from __future__ import annotations

import unittest

from quantlab_robustness.assessment import assess_candidate
from quantlab_robustness.contracts import RobustnessGatePolicyV1
from quantlab_robustness.exact import defined
from robustness_helpers import robustness_gate


def families(*, insufficient=None, failed_metric=None):
    result = {}
    for name in ("WALK_FORWARD", "MONTE_CARLO", "SENSITIVITY", "STRESS"):
        result[name] = {
            "status": "INSUFFICIENT_EVIDENCE" if name == insufficient else "COMPLETED",
            "reason": "NO_HISTORY" if name == insufficient else None,
            "metrics": {"ok": defined(0 if name == failed_metric else 1)},
            {
                "WALK_FORWARD": "walk_forward_result_id",
                "MONTE_CARLO": "monte_carlo_result_id",
                "SENSITIVITY": "sensitivity_result_id",
                "STRESS": "stress_result_id",
            }[name]: "id-" + name,
        }
    return result


def policy():
    return robustness_gate(
        [
            {
                "metric": f"{name}.ok",
                "operator": "GTE",
                "threshold": {"numerator": "1", "denominator": "1"},
            }
            for name in ("walk_forward", "monte_carlo", "sensitivity", "stress")
        ]
    )


class RobustnessGateTests(unittest.TestCase):
    def test_cases_a_through_f_and_insufficient_precedence_without_short_circuit(self):
        cases = {
            "A": (families(), "ROBUSTNESS_PASSED"),
            "B": (families(failed_metric="WALK_FORWARD"), "ROBUSTNESS_FAILED"),
            "C": (families(failed_metric="MONTE_CARLO"), "ROBUSTNESS_FAILED"),
            "D": (families(failed_metric="SENSITIVITY"), "ROBUSTNESS_FAILED"),
            "E": (families(failed_metric="STRESS"), "ROBUSTNESS_FAILED"),
        }
        for candidate, (facts, expected) in cases.items():
            with self.subTest(candidate=candidate):
                result = assess_candidate(
                    candidate_id=candidate,
                    strategy_score_id="score-" + candidate,
                    robustness_protocol_id="protocol",
                    family_results=facts,
                    gate_policy=policy(),
                )
                self.assertEqual(result["status"], expected)
        insufficient_facts = families(insufficient="MONTE_CARLO", failed_metric="STRESS")
        insufficient_facts["MONTE_CARLO"]["metrics"] = {}
        insufficient = assess_candidate(
            candidate_id="F",
            strategy_score_id="score-F",
            robustness_protocol_id="protocol",
            family_results=insufficient_facts,
            gate_policy=policy(),
        )
        self.assertEqual(insufficient["status"], "ROBUSTNESS_INSUFFICIENT_EVIDENCE")
        self.assertEqual(len(insufficient["gate"]["criteria"]), 4)
        self.assertTrue(any(item["result"] == "FAIL" for item in insufficient["gate"]["criteria"]))

    def test_optional_executed_family_may_have_criterion_without_insufficient_precedence(self):
        gate = RobustnessGatePolicyV1.model_validate(
            {
                "schema_version": "robustness-gate-policy/v1",
                "required_families": ["WALK_FORWARD"],
                "criteria": [
                    {
                        "metric": "stress.ok",
                        "operator": "GTE",
                        "threshold": {"numerator": "1", "denominator": "1"},
                    }
                ],
                "semantics": "all-criteria-insufficient-precedence/v1",
            }
        )
        facts = families(insufficient="STRESS")
        facts["STRESS"]["metrics"] = {}
        result = assess_candidate(
            candidate_id="optional",
            strategy_score_id="score",
            robustness_protocol_id="protocol",
            family_results=facts,
            gate_policy=gate,
        )
        self.assertEqual(result["status"], "ROBUSTNESS_FAILED")
        self.assertEqual(result["gate"]["criteria"][0]["undefined_reason"], "INSUFFICIENT_EVIDENCE")

    def test_absent_gate_produces_facts_only(self):
        result = assess_candidate(
            candidate_id="A",
            strategy_score_id="score-A",
            robustness_protocol_id="protocol",
            family_results=families(),
            gate_policy=None,
        )
        self.assertEqual(result["status"], "ROBUSTNESS_FACTS_ONLY")
        self.assertIsNone(result["robustness_score"])
