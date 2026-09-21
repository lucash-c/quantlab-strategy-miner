from __future__ import annotations

import copy
import unittest

from quantlab_core.strategy_v3 import StrategyDefinitionV3
from quantlab_scoring.contracts import ResearchRankingPolicyV1
from quantlab_scoring.engine import score_candidate
from quantlab_scoring.ranking import diversify, raw_ranking, score_distribution, top_n
from quantlab_scoring.semantic import semantic_group_id, semantic_group_record
from scoring_helpers import (
    diversity_policy,
    ranking_policy,
    score_policy,
    scoring_evidence,
    strategy,
)


def scored(candidate_id, *, midpoint=False):
    evidence = scoring_evidence(candidate_id, midpoint=midpoint)
    return score_candidate(policy=score_policy(), **evidence)


class RankingTests(unittest.TestCase):
    def test_oracle_f_exact_unquantized_score_is_never_hidden_tie_breaker(self):
        left, right = scored("A"), scored("B")
        left["score_units"] = right["score_units"] = 500_000
        left["score_exact_before_quantization"] = {"numerator": "5000001", "denominator": "100000"}
        right["score_exact_before_quantization"] = {"numerator": "4999999", "denominator": "100000"}
        left["ranking_values"]["validation.net_pnl_per_session"] = {
            "numerator": "1",
            "denominator": "1",
        }
        right["ranking_values"]["validation.net_pnl_per_session"] = {
            "numerator": "2",
            "denominator": "1",
        }
        result = raw_ranking([left, right], ranking_policy())
        self.assertEqual([row["candidate_id"] for row in result["records"]], ["B", "A"])
        self.assertNotIn("score_exact_before_quantization", result["records"][0])

    def test_all_tie_values_are_persisted_and_candidate_id_is_absolute_final(self):
        left, right = scored("A"), scored("B")
        result = raw_ranking([right, left], ranking_policy())
        self.assertEqual([row["candidate_id"] for row in result["records"]], ["A", "B"])
        self.assertEqual(
            set(result["records"][0]["tie_breaker_values"]),
            {
                "validation.net_pnl_per_session",
                "validation.max_drawdown",
                "validation.positive_session_rate",
            },
        )

    def test_ranking_policy_change_changes_ranking_not_score_identity(self):
        original = ranking_policy()
        record = original.model_dump(mode="json")
        record["tie_breakers"][1], record["tie_breakers"][2] = (
            record["tie_breakers"][2],
            record["tie_breakers"][1],
        )
        changed = ResearchRankingPolicyV1.model_validate(record)
        values = [scored("A"), scored("B")]
        score_ids = [item["strategy_score_id"] for item in values]
        self.assertNotEqual(original.ranking_policy_id, changed.ranking_policy_id)
        self.assertEqual(score_ids, [item["strategy_score_id"] for item in values])
        self.assertNotEqual(
            raw_ranking(values, original)["raw_ranking_id"],
            raw_ranking(values, changed)["raw_ranking_id"],
        )

    def test_non_scorable_and_not_eligible_never_enter_raw_ranking(self):
        eligible = scored("A")
        excluded = copy.deepcopy(eligible)
        excluded["candidate_id"] = "B"
        excluded["score_status"] = "NOT_SCORABLE"
        excluded["score_units"] = None
        result = raw_ranking([excluded, eligible], ranking_policy())
        self.assertEqual([row["candidate_id"] for row in result["records"]], ["A"])


class DiversityTests(unittest.TestCase):
    def _raw(self):
        rows = [
            {"raw_rank": 1, "candidate_id": "A1", "semantic_group_id": "A"},
            {"raw_rank": 2, "candidate_id": "A2", "semantic_group_id": "A"},
            {"raw_rank": 3, "candidate_id": "A3", "semantic_group_id": "A"},
            {"raw_rank": 4, "candidate_id": "B1", "semantic_group_id": "B"},
        ]
        for row in rows:
            row.update(
                strategy_score_id="score-" + row["candidate_id"],
                score_units=100,
                score_scale=10_000,
                tie_breaker_values={},
            )
        return {"raw_ranking_id": "raw", "records": rows}

    def test_required_a1_a2_a3_b1_round_robin(self):
        result = diversify(self._raw(), diversity_policy())
        selected = sorted(
            (row for row in result["records"] if row["diversified_rank"] is not None),
            key=lambda row: row["diversified_rank"],
        )
        self.assertEqual([row["candidate_id"] for row in selected], ["A1", "B1", "A2"])
        a3 = next(row for row in result["records"] if row["candidate_id"] == "A3")
        self.assertEqual(a3["raw_rank"], 3)
        self.assertEqual(a3["selection_status"], "DIVERSITY_GROUP_LIMIT")

    def test_diversity_is_derivation_and_top_n_is_explicit_without_padding(self):
        diversified = diversify(self._raw(), diversity_policy())
        result = top_n(diversified, 5)
        self.assertEqual(result["emitted_count"], 3)
        self.assertEqual(result["emitted_candidate_ids"], ["A1", "B1", "A2"])
        with self.assertRaisesRegex(ValueError, "explicit positive"):
            top_n(diversified, 0)

    def test_diversity_policy_change_only_changes_diversified_identity(self):
        policy = diversity_policy()
        changed = policy.model_copy(update={"max_per_semantic_group": 1})
        raw = self._raw()
        first, second = diversify(raw, policy), diversify(raw, changed)
        self.assertNotEqual(first["diversified_ranking_id"], second["diversified_ranking_id"])
        self.assertEqual(first["raw_ranking_id"], second["raw_ranking_id"])

    def test_equal_first_member_rank_uses_semantic_group_id(self):
        raw = self._raw()
        raw["records"] = [
            {
                **raw["records"][0],
                "candidate_id": "Z1",
                "semantic_group_id": "Z",
                "raw_rank": 1,
            },
            {
                **raw["records"][3],
                "candidate_id": "A1",
                "semantic_group_id": "A",
                "raw_rank": 1,
            },
        ]
        result = diversify(raw, diversity_policy())
        selected = sorted(result["records"], key=lambda item: item["diversified_rank"])
        self.assertEqual([item["semantic_group_id"] for item in selected], ["A", "Z"])


class SemanticGroupTests(unittest.TestCase):
    def test_numeric_values_are_abstracted_but_position_role_and_topology_remain(self):
        first = strategy()
        record = first.model_dump(mode="json")
        record["features"][0]["parameters"]["period"] = 10
        record["entry_conditions"]["children"][0]["right"]["value"] = "123"
        record["stop_loss"]["value"] = "250"
        record["take_profit"]["value"] = "350"
        record["entry_time_filter"]["start"] = "10:00"
        record["entry_time_filter"]["end"] = "11:00"
        second = StrategyDefinitionV3.model_validate(record)
        self.assertEqual(semantic_group_id(first), semantic_group_id(second))
        signature = semantic_group_record(first)
        self.assertEqual(
            signature["entry_conditions"]["children"][0]["right"]["value"]["dimension"],
            "PRICE",
        )

    def test_operator_topology_timeframe_and_direction_are_preserved(self):
        base = strategy()
        record = base.model_dump(mode="json")
        record["entry_conditions"]["operator"] = "OR"
        changed_ast = StrategyDefinitionV3.model_validate(record)
        self.assertNotEqual(semantic_group_id(base), semantic_group_id(changed_ast))
        self.assertNotEqual(semantic_group_id(base), semantic_group_id(strategy(timeframe="5m")))
        self.assertNotEqual(semantic_group_id(base), semantic_group_id(strategy(direction="SELL")))

    def test_fixed_buckets_include_100_in_last_bucket_and_even_median_is_exact(self):
        scores = [
            {"score_status": "SCORED", "score_units": 0},
            {"score_status": "SCORED", "score_units": 100_000},
            {"score_status": "SCORED", "score_units": 900_000},
            {"score_status": "SCORED", "score_units": 1_000_000},
        ]
        result = score_distribution(scores)
        self.assertEqual(result["buckets"][0]["count"], 1)
        self.assertEqual(result["buckets"][1]["count"], 1)
        self.assertEqual(result["buckets"][9]["count"], 2)
        self.assertEqual(
            result["median_score_units"], {"numerator": "500000", "denominator": "1"}
        )


if __name__ == "__main__":
    unittest.main()
