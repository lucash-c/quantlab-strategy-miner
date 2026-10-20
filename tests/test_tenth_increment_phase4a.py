from __future__ import annotations

import unittest
from pathlib import Path

from quantlab_robustness.contracts import (
    ExecutionStressPolicyV1,
    MonteCarloPolicyV1,
    RobustnessGatePolicyV1,
    SensitivityPolicyV1,
    WalkForwardPolicyV1,
)
from quantlab_robustness.contracts import (
    load_policy as load_robustness_policy,
)
from quantlab_robustness.protocol import protocol_record
from quantlab_scoring.contracts import (
    ResearchDiversityPolicyV1,
    ResearchRankingPolicyV1,
    ResearchScorePolicyV1,
)
from quantlab_scoring.contracts import (
    load_policy as load_scoring_policy,
)

from scripts.validate_tenth_increment_phase4a import PRECOMMITMENT, load_json, validate_static

ROOT = Path(__file__).resolve().parents[1]


class TenthIncrementPhase4APrecommitmentTests(unittest.TestCase):
    def test_static_precommitment_and_all_frozen_policy_identities(self) -> None:
        report = validate_static()
        self.assertEqual(report["status"], "STATIC_PRECOMMITMENT_VALID")
        self.assertEqual(report["candidate_count"], 48)
        self.assertEqual(report["workload"]["status"], "READY")
        self.assertEqual(
            report["research_protocol_id"],
            "sha256:6712a6b80b1fc0df798ffd0192efd8c928bfbd17923a4002eaac0cf7b292ff85",
        )
        precommitment = load_json(PRECOMMITMENT)
        self.assertFalse(
            precommitment["precommitment_integrity"]["real_b3_backtest_executed"]
        )
        self.assertFalse(precommitment["precommitment_integrity"]["real_results_observed"])
        self.assertEqual(
            precommitment["research"]["outside_research_window_but_authorized_downstream"],
            ["2026-08-24"],
        )

    def test_research_v2_score_and_robustness_lineage_is_structurally_compatible(self) -> None:
        frozen = load_json(PRECOMMITMENT)
        self.assertEqual(len(frozen["auto_discovery"]["candidate_ids"]), 48)

        score_policy = load_scoring_policy(
            ROOT / "examples/scoring/research-score-v1.json", ResearchScorePolicyV1
        )
        ranking_policy = load_scoring_policy(
            ROOT / "examples/scoring/research-ranking-v1.json", ResearchRankingPolicyV1
        )
        diversity_policy = load_scoring_policy(
            ROOT / "examples/scoring/research-diversity-v1.json",
            ResearchDiversityPolicyV1,
        )
        self.assertEqual(score_policy.score_policy_id, frozen["score"]["score_policy_id"])
        self.assertEqual(
            ranking_policy.ranking_policy_id, frozen["score"]["ranking_policy_id"]
        )
        self.assertEqual(
            diversity_policy.diversity_policy_id,
            frozen["score"]["diversity_policy_id"],
        )

        acceptance = ROOT / "examples" / "acceptance" / "ninth-increment"
        walk_forward = load_robustness_policy(
            acceptance / "walk-forward-policy-v1.json", WalkForwardPolicyV1
        )
        monte_carlo = load_robustness_policy(
            acceptance / "monte-carlo-policy-v1.json", MonteCarloPolicyV1
        )
        sensitivity = load_robustness_policy(
            acceptance / "sensitivity-policy-v1.json", SensitivityPolicyV1
        )
        stress = load_robustness_policy(
            acceptance / "stress-policy-v2.json", ExecutionStressPolicyV1
        )
        legacy_gate = load_robustness_policy(
            acceptance / "technical-gate-v1.json", RobustnessGatePolicyV1
        )
        candidate_set_id = "sha256:" + "1" * 64
        robustness_protocol = protocol_record(
            candidate_set={"robustness_candidate_set_id": candidate_set_id},
            research_export_id="sha256:" + "2" * 64,
            score_export_id="sha256:" + "3" * 64,
            dataset_id=frozen["dataset"]["dataset_id"],
            authorized_session_ids=[
                item["session_id"] for item in frozen["dataset"]["sessions"]
            ],
            walk_forward=walk_forward,
            monte_carlo=monte_carlo,
            sensitivity=sensitivity,
            stress=stress,
            gate=legacy_gate,
            required_families=frozen["robustness"]["required_families"],
        )
        self.assertEqual(robustness_protocol["schema_version"], "robustness-protocol/v1")
        self.assertEqual(
            robustness_protocol["robustness_candidate_set_id"], candidate_set_id
        )
        self.assertEqual(
            robustness_protocol["stress_policy_id"],
            frozen["robustness"]["stress_v2"]["policy_id"],
        )


if __name__ == "__main__":
    unittest.main()
