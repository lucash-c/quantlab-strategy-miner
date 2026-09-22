from __future__ import annotations

import json
import unittest
from copy import deepcopy
from pathlib import Path

from quantlab_core.numeric import CanonicalRational
from quantlab_robustness.contracts import (
    RobustnessQuantitativePolicyV1,
    WalkForwardPolicyV1,
)
from quantlab_robustness.exact import defined, undefined
from quantlab_robustness.quantitative import (
    assess_quantitative_candidate,
    diversify_quantitative_qualified,
    quantitative_qualified_ranking,
    quantitative_qualified_set,
    quantitative_top_n,
)
from quantlab_robustness.walk_forward import build_walk_forward_plan
from scoring_helpers import diversity_policy


def policy() -> RobustnessQuantitativePolicyV1:
    return RobustnessQuantitativePolicyV1.model_validate(
        json.loads(
            Path("examples/acceptance/ninth-increment/quantitative-gate-v1.json").read_text(
                encoding="utf-8"
            )
        )
    )


def metric(numerator: int, denominator: int = 1) -> dict:
    return defined(CanonicalRational(numerator, denominator))


def evidence() -> dict:
    return {
        "candidate_id": "A",
        "quantitative_evidence_id": "evidence-A",
        "walk_forward_metrics": {
            "evaluable_folds": metric(3),
            "positive_validation_fold_rate": metric(2, 3),
            "median_validation_net_pnl_per_session": metric(1),
            "median_validation_average_trade": metric(1),
        },
        "monte_carlo_metrics": {
            "median_total_net": metric(1),
            "probability_net_le_zero": metric(1, 4),
            "p95_max_drawdown": metric(5),
            "p99_max_drawdown": metric(6),
            "median_max_consecutive_losses": metric(2),
        },
        "sensitivity": {
            "baseline_partition_evaluation_id": "sensitivity-baseline",
            "baseline_metrics": {"net_pnl": metric(4)},
            "scenarios": [
                {
                    "sensitivity_scenario_id": "sensitivity-1",
                    "status": "VALID",
                    "reason": None,
                    "metrics": {"net_pnl": metric(2)},
                }
            ],
        },
        "stress": {
            "scenarios": [
                {
                    "stress_scenario_id": "stress-1",
                    "status": "VALID",
                    "reason": None,
                    "metrics": {
                        "net_pnl": metric(1),
                        "average_trade": metric(1, 2),
                        "win_rate": metric(3, 5),
                    },
                }
            ]
        },
    }


def technical(status: str = "ROBUSTNESS_PASSED") -> dict:
    return {
        "candidate_id": "A",
        "robustness_assessment_id": "technical-A",
        "status": status,
    }


def assess(source: dict, *, technical_status: str = "ROBUSTNESS_PASSED") -> dict:
    return assess_quantitative_candidate(
        candidate_id="A",
        strategy_score_id="score-A",
        robustness_protocol_id="protocol",
        technical_assessment=technical(technical_status),
        evidence=source,
        policy=policy(),
    )


class QuantitativeRobustnessV1Tests(unittest.TestCase):
    def test_frozen_policy_identity_and_legacy_gate_is_unchanged(self):
        self.assertEqual(
            policy().policy_id,
            "sha256:a1196bb8333baad0ed7d29fbcd3a094d27a2c68e5f25c7b782597845ac093e1f",
        )
        legacy = json.loads(Path("examples/robustness/fixture-gate.json").read_text())
        additive = json.loads(
            Path("examples/acceptance/ninth-increment/technical-gate-v1.json").read_text()
        )
        self.assertEqual(legacy, additive)

    def test_pass_and_exact_walk_forward_fold_requirement(self):
        result = assess(evidence())
        self.assertEqual(result["status"], "PASSED")
        folds = next(
            item
            for item in result["criteria"]
            if item["metric"] == "positive_validation_fold_rate"
        )
        self.assertEqual(folds["evaluable_folds"], 3)
        self.assertEqual(folds["minimum_positive_folds"], 2)
        self.assertFalse(result["research_score_recomputed"])
        self.assertFalse(result["candidate_changed"])
        self.assertFalse(result["technical_gate_changed"])
        self.assertEqual(
            result["diagnostics"]["stress.win_rate"][0]["win_rate"], metric(3, 5)
        )

    def test_real_twenty_session_policy_derives_two_folds_and_requires_both_positive(self):
        old = WalkForwardPolicyV1.model_validate(
            json.loads(
                Path("examples/acceptance/ninth-increment/walk-forward-policy-v1.json")
                .read_text(encoding="utf-8")
            )
        )
        real = WalkForwardPolicyV1.model_validate(
            json.loads(
                Path(
                    "examples/acceptance/ninth-increment/"
                    "walk-forward-policy-real-20-v1.json"
                ).read_text(encoding="utf-8")
            )
        )
        self.assertEqual(old.min_folds, 3)
        self.assertEqual(real.min_folds, 2)
        self.assertNotEqual(old.policy_id, real.policy_id)
        whitelist = json.loads(
            Path("examples/acceptance/ninth-increment/session-whitelist-v2.json")
            .read_text(encoding="utf-8")
        )
        sessions = [
            {"session_id": f"session-{date}", "trading_date": date}
            for date in whitelist["sessions"]
        ]
        plan = build_walk_forward_plan(
            dataset_id="dataset", logical_asset="WIN", sessions=sessions, policy=real
        )
        self.assertEqual(plan["status"], "READY")
        self.assertEqual(plan["fold_count"], 2)
        self.assertEqual(
            [
                (
                    fold["discovery_first_date"],
                    fold["discovery_last_date"],
                    fold["validation_first_date"],
                    fold["validation_last_date"],
                )
                for fold in plan["folds"]
            ],
            [
                ("2026-08-24", "2026-09-10", "2026-09-11", "2026-09-18"),
                ("2026-08-25", "2026-09-11", "2026-09-14", "2026-09-21"),
            ],
        )
        self.assertEqual(plan["unused_tail_session_ids"], [])

        two_fold_evidence = evidence()
        two_fold_evidence["walk_forward_metrics"]["evaluable_folds"] = metric(2)
        two_fold_evidence["walk_forward_metrics"][
            "positive_validation_fold_rate"
        ] = metric(1, 2)
        result = assess(two_fold_evidence)
        criterion = next(
            item
            for item in result["criteria"]
            if item["metric"] == "positive_validation_fold_rate"
        )
        self.assertEqual(criterion["minimum_positive_folds"], 2)
        self.assertEqual(criterion["result"], "FAILED")

    def test_failure_is_not_hidden_and_ratio_is_exact(self):
        source = evidence()
        source["monte_carlo_metrics"]["median_total_net"] = metric(0)
        source["sensitivity"]["baseline_metrics"]["net_pnl"] = metric(3)
        source["sensitivity"]["scenarios"][0]["metrics"]["net_pnl"] = metric(1)
        result = assess(source)
        self.assertEqual(result["status"], "FAILED")
        ratio = next(
            item
            for item in result["criteria"]
            if item["metric"] == "median_variant_to_baseline_net_ratio"
        )
        self.assertEqual(ratio["observed_value"]["value"], {"numerator": "1", "denominator": "3"})
        self.assertEqual(ratio["result"], "FAILED")

    def test_insufficient_evidence_precedes_failure(self):
        source = evidence()
        source["monte_carlo_metrics"]["median_total_net"] = metric(0)
        source["stress"]["scenarios"][0]["metrics"]["average_trade"] = undefined(
            "NO_TRADES"
        )
        result = assess(source)
        self.assertEqual(result["status"], "INSUFFICIENT_EVIDENCE")
        self.assertTrue(result["failures"])
        self.assertTrue(result["insufficient_evidence"])

    def test_zero_baseline_is_insufficient_and_technical_failure_stays_separate(self):
        source = evidence()
        source["sensitivity"]["baseline_metrics"]["net_pnl"] = metric(0)
        result = assess(source)
        self.assertEqual(result["status"], "INSUFFICIENT_EVIDENCE")
        self.assertIn(
            "ZERO_BASELINE_NET",
            {item["reason"] for item in result["insufficient_evidence"]},
        )
        failed = assess(evidence(), technical_status="ROBUSTNESS_FAILED")
        self.assertEqual(failed["status"], "FAILED")
        self.assertEqual(failed["technical_robustness_status"], "ROBUSTNESS_FAILED")

    def test_quantitative_ranking_filters_without_rescore_or_padding(self):
        template = assess(evidence())
        assessments = []
        for candidate, status in (("A", "PASSED"), ("B", "FAILED"), ("C", "PASSED")):
            assessments.append(
                {
                    **template,
                    "candidate_id": candidate,
                    "strategy_score_id": "score-" + candidate,
                    "quantitative_assessment_id": "assessment-" + candidate,
                    "status": status,
                }
            )
        raw = [
            {
                "raw_rank": rank,
                "candidate_id": candidate,
                "strategy_score_id": "score-" + candidate,
                "score_units": 100 - rank,
                "score_scale": 4,
                "semantic_group_id": group,
            }
            for rank, (candidate, group) in enumerate(
                (("A", "G1"), ("B", "G1"), ("C", "G2")), 1
            )
        ]
        qualified = quantitative_qualified_set(assessments, policy().policy_id)
        ranking = quantitative_qualified_ranking(raw, qualified)
        self.assertEqual([row["candidate_id"] for row in ranking["records"]], ["A", "C"])
        self.assertFalse(ranking["research_score_recomputed"])
        diversified = diversify_quantitative_qualified(ranking, diversity_policy())
        shortlist = quantitative_top_n(diversified, 10)
        self.assertEqual(shortlist["emitted_count"], 2)
        self.assertEqual(shortlist["emitted_candidate_ids"], ["A", "C"])

    def test_invalid_required_scenarios_are_insufficient(self):
        source = deepcopy(evidence())
        source["sensitivity"]["scenarios"][0] = {
            "sensitivity_scenario_id": "sensitivity-1",
            "status": "INVALID_SENSITIVITY_VARIANT",
            "reason": "TARGET_NOT_RESOLVED",
        }
        source["stress"]["scenarios"][0] = {
            "stress_scenario_id": "stress-1",
            "status": "INVALID_STRESS_SCENARIO",
            "reason": "NON_STRESS_SCENARIO",
        }
        result = assess(source)
        self.assertEqual(result["status"], "INSUFFICIENT_EVIDENCE")
        reasons = {item["reason"] for item in result["insufficient_evidence"]}
        self.assertIn("TARGET_NOT_RESOLVED", reasons)
        self.assertIn("NON_STRESS_SCENARIO", reasons)


if __name__ == "__main__":
    unittest.main()
