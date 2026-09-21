from __future__ import annotations

import copy
import unittest

from quantlab_core.canonical import canonical_json_bytes
from quantlab_core.numeric import CanonicalRational
from quantlab_scoring.contracts import ResearchScorePolicyV1
from quantlab_scoring.engine import finalize_score, rational, score_candidate, validate_score_record
from scoring_helpers import score_policy, scoring_evidence, strategy, undefined


class ScoringEngineTests(unittest.TestCase):
    def score(self, evidence):
        return score_candidate(policy=score_policy(), **evidence)

    def test_oracle_a_all_components_maximum_is_exactly_100(self):
        record = self.score(scoring_evidence())
        self.assertEqual(record["score_status"], "SCORED")
        self.assertEqual(rational(record["base_score"]), CanonicalRational(100))
        self.assertEqual(rational(record["penalty_total"]), CanonicalRational(0))
        self.assertEqual(record["score_units"], 1_000_000)
        self.assertEqual(
            record["score_exact_before_quantization"],
            {"numerator": "100", "denominator": "1"},
        )
        validate_score_record(record)

    def test_oracle_b_midpoints_and_known_penalties_equal_45(self):
        record = self.score(scoring_evidence(midpoint=True))
        self.assertEqual(rational(record["base_score"]), CanonicalRational(50))
        self.assertEqual(rational(record["penalty_total"]), CanonicalRational(5))
        self.assertEqual(record["score_units"], 450_000)

    def test_oracles_c_and_d_clamp_negative_and_above_100(self):
        self.assertEqual(finalize_score(CanonicalRational(-10), 10_000)[0], CanonicalRational(0))
        self.assertEqual(finalize_score(CanonicalRational(101), 10_000)[0], CanonicalRational(100))

    def test_oracle_e_half_even_is_the_only_quantization(self):
        even = finalize_score(CanonicalRational(1, 20_000), 10_000)
        odd = finalize_score(CanonicalRational(3, 20_000), 10_000)
        self.assertEqual(even[1], 0)
        self.assertEqual(odd[1], 2)
        self.assertEqual(even[2], CanonicalRational(-1, 20_000))
        self.assertEqual(odd[2], CanonicalRational(1, 20_000))

    def test_no_losses_is_diagnostic_and_no_profitable_sessions_is_not_applicable(self):
        evidence = scoring_evidence()
        evidence["validation"]["metrics"]["largest_profitable_session_share"] = undefined(
            "NO_PROFITABLE_SESSIONS"
        )
        record = self.score(evidence)
        self.assertEqual(record["score_status"], "SCORED")
        term = next(
            item
            for item in record["penalties"]
            if item["metric"] == "largest_profitable_session_share"
        )
        self.assertEqual(term["status"], "NOT_APPLICABLE")
        self.assertEqual(term["contribution"], {"numerator": "0", "denominator": "1"})
        self.assertEqual(record["diagnostic_metrics"]["profit_factor"]["reason"], "NO_LOSSES")

    def test_zero_trades_and_all_undefined_reasons_are_collected(self):
        evidence = scoring_evidence()
        metrics = evidence["validation"]["metrics"]
        metrics["trades"] = {
            "status": "DEFINED",
            "value": {"numerator": "0", "denominator": "1"},
            "reason": None,
        }
        metrics["average_trade"] = undefined("NO_TRADES")
        metrics["win_rate"] = undefined("NO_TRADES")
        metrics["largest_trade_count_session_share"] = undefined("NO_TRADES")
        evidence["comparison"]["metrics"]["average_trade_delta"] = undefined(
            "SOURCE_METRIC_UNDEFINED"
        )
        record = self.score(evidence)
        self.assertEqual(record["score_status"], "NOT_SCORABLE")
        self.assertIsNone(record["score_units"])
        codes = [item["code"] for item in record["reasons"]]
        self.assertIn("ZERO_TRADES", codes)
        self.assertIn("REQUIRED_METRIC_UNDEFINED", codes)
        self.assertIn("MISSING_COMPARISON_METRIC", codes)
        self.assertEqual(
            record["reasons"],
            sorted(record["reasons"], key=canonical_json_bytes),
        )

    def test_not_eligible_never_receives_diagnostic_score(self):
        evidence = scoring_evidence()
        evidence["status"]["validation_status"] = "VALIDATION_FAILED_GATE"
        evidence["validation_gate"]["result"] = "FAIL"
        record = self.score(evidence)
        self.assertEqual(record["score_status"], "NOT_ELIGIBLE")
        self.assertIsNone(record["score_units"])
        self.assertEqual(record["terms"], [])
        self.assertEqual(record["reasons"][0]["code"], "VALIDATION_GATE_FAILED")

    def test_score_is_absolute_when_an_unrelated_candidate_is_added(self):
        evidence = scoring_evidence("A")
        before = self.score(evidence)
        self.score(scoring_evidence("D", midpoint=True))
        after = self.score(copy.deepcopy(evidence))
        self.assertEqual(before, after)

    def test_risk_unit_is_exact_strategy_stop_and_part_of_identity(self):
        first = scoring_evidence()
        second = copy.deepcopy(first)
        second["strategy"] = second["strategy"].model_copy(
            update={"stop_loss": second["strategy"].stop_loss.model_copy(update={"value": "200"})}
        )
        a, b = self.score(first), self.score(second)
        self.assertNotEqual(a["strategy_score_id"], b["strategy_score_id"])
        self.assertEqual(a["risk_unit"]["value"], {"numerator": "100", "denominator": "1"})

    def test_score_policy_change_invalidates_score_without_other_candidates(self):
        evidence = scoring_evidence()
        original = score_policy()
        record = original.model_dump(mode="json")
        term = next(
            item
            for item in record["positive_terms"]
            if item["term_id"] == "performance.average_trade_risk"
        )
        term["transform"]["minimum"] = {"numerator": "-1", "denominator": "5"}
        changed = ResearchScorePolicyV1.model_validate(record)
        first = score_candidate(policy=original, **evidence)
        second = score_candidate(policy=changed, **evidence)
        self.assertNotEqual(first["score_policy_id"], second["score_policy_id"])
        self.assertNotEqual(first["strategy_score_id"], second["strategy_score_id"])

    def test_rolling_evidence_change_recalculates_score_but_not_semantic_group(self):
        first_evidence = scoring_evidence()
        second_evidence = copy.deepcopy(first_evidence)
        second_evidence["validation"]["result_fingerprint"] = "sha256:rolling-window-2-20"
        first = self.score(first_evidence)
        second = self.score(second_evidence)
        self.assertEqual(first["score_units"], second["score_units"])
        self.assertEqual(first["semantic_group_id"], second["semantic_group_id"])
        self.assertNotEqual(first["strategy_score_id"], second["strategy_score_id"])

    def test_all_four_timeframes_are_scored_independently(self):
        ids = set()
        groups = set()
        for timeframe in ("1m", "2m", "5m", "15m"):
            evidence = scoring_evidence("candidate-" + timeframe)
            evidence["strategy"] = strategy(timeframe=timeframe)
            record = self.score(evidence)
            self.assertEqual(record["score_status"], "SCORED")
            ids.add(record["strategy_score_id"])
            groups.add(record["semantic_group_id"])
        self.assertEqual(len(ids), 4)
        self.assertEqual(len(groups), 4)


if __name__ == "__main__":
    unittest.main()
