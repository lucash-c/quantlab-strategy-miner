from __future__ import annotations

import json
import unittest
from pathlib import Path

from quantlab_core.errors import ContractError
from quantlab_robustness.candidate_set import build_candidate_set
from quantlab_robustness.contracts import CandidateSelectionV1
from quantlab_robustness.evidence import ScoreEvidence
from quantlab_robustness.ranking import (
    diversify_qualified,
    qualified_ranking,
    qualified_set,
    robustness_top_n,
)
from scoring_helpers import diversity_policy


def selection(method, **arguments):
    return CandidateSelectionV1.model_validate(
        {
            "schema_version": "robustness-candidate-selection/v1",
            "method": method,
            **arguments,
        }
    )


def score_evidence():
    scores = {
        candidate: {
            "candidate_id": candidate,
            "strategy_score_id": "score-" + candidate,
            "score_status": "SCORED",
            "score_units": units,
            "score_scale": 4,
            "semantic_group_id": group,
        }
        for candidate, units, group in (("A", 100, "G1"), ("B", 90, "G1"), ("C", 80, "G2"))
    }
    scores["X"] = {
        "candidate_id": "X",
        "strategy_score_id": "score-X",
        "score_status": "NOT_ELIGIBLE",
        "score_units": None,
        "score_scale": 4,
        "semantic_group_id": "GX",
    }
    raw = tuple(
        {
            "raw_rank": rank,
            "candidate_id": candidate,
            "strategy_score_id": "score-" + candidate,
            "score_units": units,
            "score_scale": 4,
            "semantic_group_id": group,
        }
        for rank, (candidate, units, group) in enumerate(
            (("A", 100, "G1"), ("B", 90, "G1"), ("C", 80, "G2")), 1
        )
    )
    diversified = (
        {**raw[0], "diversified_rank": 1},
        {**raw[1], "diversified_rank": 3},
        {**raw[2], "diversified_rank": 2},
    )
    return ScoreEvidence(
        Path("score-evidence"),
        {
            "score_export_id": "export",
            "score_set_id": "set",
            "raw_ranking_id": "raw",
            "diversified_ranking_id": "diversified",
            "top_n_output_id": "top",
        },
        {"score_set_id": "set"},
        scores,
        raw,
        diversified,
        {"emitted_candidate_ids": ["A", "C"]},
    )


class RobustnessSelectionRankingTests(unittest.TestCase):
    def test_published_seventh_increment_fingerprints_remain_unchanged(self):
        evidence = json.loads(
            Path("docs/evidence/seventh-increment-fixture.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            evidence["identities"],
            {
                "score_input_id": "sha256:"
                "d8c73763e1e5161e90dcee01055583d48f7f10b36612fe17658a6dc64278f800",
                "score_set_id": "sha256:"
                "f1efb2e38164f2bd098647226ea838be639b69bd77aca15584b00544c56d8193",
                "raw_ranking_id": "sha256:"
                "25971e38449b124c7465bce59d71f66e0bc444c757a4d37c23f86a8c53d1b902",
                "diversified_ranking_id": "sha256:"
                "0725e6206726a060690fe460c58c8c21fcf36e2360f6df7b68b6eef35d521d2b",
                "top_n_output_id": "sha256:"
                "bcbab0516e87f83bb95d73fbc750c053d1d705a232368de53d0a997fe637b555",
                "score_export_id": "sha256:"
                "d6bd74f036bccb355afb1dc09519b0e99704dd1a2b42e022b2a1fc40f89449d4",
            },
        )
        self.assertEqual(
            [item["strategy_score_id"] for item in evidence["scored_candidates"]],
            [
                "sha256:266ccd25081ac616c39924e456b6bdb24a4e427dfb4a2bd1d9d2cc1c1a1053b6",
                "sha256:b75771bae07bc2e621b52aa006d47275f2ed20dfdbf388c9165e7ab884e2d592",
                "sha256:b20e2d52c263aab44414f5fc4575d2f7794fd3c9e652f17c3f6993b23d04e2ca",
                "sha256:5e0d24b8ec87774197eb64a825c1e278c358163f47a39bccc078329265166402",
            ],
        )

    def test_all_five_candidate_selectors_and_invalid_reference(self):
        evidence = score_evidence()
        cases = (
            (selection("ALL_SCORED"), ["A", "B", "C"]),
            (selection("RAW_RANKING_TOP_N", top_n=2), ["A", "B"]),
            (selection("DIVERSIFIED_RANKING_TOP_N", top_n=2), ["A", "C"]),
            (selection("TOP_N_OUTPUT"), ["A", "C"]),
            (selection("EXPLICIT_CANDIDATE_IDS", candidate_ids=["C", "A"]), ["C", "A"]),
        )
        for policy, expected_source_order in cases:
            with self.subTest(method=policy.method):
                result = build_candidate_set(evidence, policy)
                by_ordinal = sorted(result["candidates"], key=lambda item: item["source_ordinal"])
                self.assertEqual(
                    [item["candidate_id"] for item in by_ordinal], expected_source_order
                )
        with self.assertRaisesRegex(ContractError, "not SCORED"):
            build_candidate_set(
                evidence,
                selection("EXPLICIT_CANDIDATE_IDS", candidate_ids=["X"]),
            )
        with self.assertRaisesRegex(ContractError, "unknown"):
            build_candidate_set(
                evidence,
                selection("EXPLICIT_CANDIDATE_IDS", candidate_ids=["MISSING"]),
            )

    def test_qualified_flow_filters_original_raw_then_diversifies_without_padding(self):
        assessments = [
            {
                "candidate_id": candidate,
                "strategy_score_id": "score-" + candidate,
                "robustness_assessment_id": "assessment-" + candidate,
                "status": status,
            }
            for candidate, status in (
                ("A", "ROBUSTNESS_PASSED"),
                ("B", "ROBUSTNESS_FAILED"),
                ("C", "ROBUSTNESS_PASSED"),
            )
        ]
        qualified = qualified_set(assessments, "gate")
        ranking = qualified_ranking(list(score_evidence().raw), qualified)
        self.assertEqual([item["candidate_id"] for item in ranking["records"]], ["A", "C"])
        self.assertEqual([item["original_raw_rank"] for item in ranking["records"]], [1, 3])
        self.assertFalse(ranking["research_score_recomputed"])
        diversified = diversify_qualified(ranking, diversity_policy())
        shortlist = robustness_top_n(diversified, 5)
        self.assertEqual(shortlist["emitted_candidate_ids"], ["A", "C"])
        self.assertEqual(shortlist["emitted_count"], 2)


if __name__ == "__main__":
    unittest.main()
