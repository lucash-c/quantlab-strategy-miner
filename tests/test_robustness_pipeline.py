from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pyarrow.parquet as pq
from quantlab_core.canonical import sha256_file
from quantlab_robustness.contracts import (
    AuthorizedSessionUniverseV1,
    CandidateSelectionV1,
    ExecutionStressPolicyV1,
    MonteCarloPolicyV1,
    RobustnessWorkloadPolicyV1,
    SensitivityPolicyV1,
)
from quantlab_robustness.runner import ControlledRobustnessInterruption, run_robustness
from quantlab_scoring.runner import run_scoring
from research_helpers import execution_config, prepare, test_gate
from robustness_helpers import robustness_gate, walk_policy
from scoring_helpers import (
    diversity_policy,
    ranking_policy,
    score_policy,
    write_research_evidence,
)


def tree_hashes(path):
    return {
        str(item.relative_to(path)).replace("\\", "/"): sha256_file(item)
        for item in sorted(path.rglob("*"))
        if item.is_file()
    }


def parquet_records(path):
    rows = []
    for batch in pq.ParquetFile(path).iter_batches(columns=["record_json"]):
        rows.extend(json.loads(value) for value in batch.column(0).to_pylist())
    return rows


def json_record(path):
    return json.loads(path.read_text(encoding="utf-8"))


class RobustnessPipelineTests(unittest.TestCase):
    def policies(self, session_ids, seed="0" * 63 + "1"):
        tail = session_ids[-8:]
        source = {"source": "EXPLICIT_SESSION_IDS", "session_ids": tail}
        monte = MonteCarloPolicyV1.model_validate(
            {
                "schema_version": "monte-carlo-policy/v1",
                "sampling_method": "SESSION_BOOTSTRAP_WITH_REPLACEMENT",
                "source_pool": source,
                "number_of_paths": 12,
                "path_length_sessions": 6,
                "seed": seed,
                "sampler_version": "SHA256_COUNTER_REJECTION_V1",
                "quantile_policy": "NEAREST_RANK_V1",
                "minimum_source_sessions": 6,
                "drawdown_thresholds": [
                    {
                        "threshold": {"numerator": "10", "denominator": "1"},
                        "operator": "GT",
                    }
                ],
            }
        )
        sensitivity = SensitivityPolicyV1.model_validate(
            {
                "schema_version": "sensitivity-policy/v1",
                "method": "ONE_AT_A_TIME",
                "source_pool": source,
                "perturbations": [
                    {
                        "name": "stop-plus-one",
                        "target": {"kind": "STOP_LOSS", "path": "/stop_loss/value"},
                        "method": "DECIMAL_ABSOLUTE_DELTA",
                        "rational_delta": {"numerator": "1", "denominator": "1"},
                    }
                ],
                "tolerance_criteria": [],
                "validation_gate": None,
            }
        )
        stress = ExecutionStressPolicyV1.model_validate(
            {
                "schema_version": "execution-stress-policy/v1",
                "source_pool": source,
                "scenarios": [
                    {
                        "name": "one-point",
                        "cost": {
                            "method": "ABSOLUTE_POINTS",
                            "value": {"numerator": "1", "denominator": "1"},
                        },
                        "slippage": {
                            "method": "ABSOLUTE_POINTS",
                            "value": {"numerator": "1", "denominator": "1"},
                        },
                    }
                ],
                "validation_gate": None,
            }
        )
        return monte, sensitivity, stress

    def run_once(
        self,
        *,
        root,
        research,
        scores,
        catalog,
        cache,
        checkpoint,
        output,
        seed="0" * 63 + "1",
        stop_after=None,
    ):
        session_ids = [session.session_id for session in catalog.sessions]
        monte, sensitivity, stress = self.policies(session_ids, seed)
        return run_robustness(
            research_directory=research,
            score_directory=scores,
            catalog=catalog,
            authorized=AuthorizedSessionUniverseV1.model_validate(
                {
                    "schema_version": "robustness-authorized-sessions/v1",
                    "dataset_id": catalog.manifest["dataset_id"],
                    "session_ids": session_ids,
                }
            ),
            baseline_evaluation=execution_config().model_copy(update={"max_sessions": 21}),
            candidate_selection=CandidateSelectionV1.model_validate(
                {
                    "schema_version": "robustness-candidate-selection/v1",
                    "method": "RAW_RANKING_TOP_N",
                    "top_n": 1,
                }
            ),
            walk_forward_policy=walk_policy(
                discovery_gate=test_gate(
                    criteria=[
                        {"metric": "trades", "operator": "GTE", "threshold": 1000000}
                    ]
                ),
                validation_gate=test_gate(validation=True, empty=True),
            ),
            monte_carlo_policy=monte,
            sensitivity_policy=sensitivity,
            stress_policy=stress,
            workload_policy=RobustnessWorkloadPolicyV1.model_validate(
                {
                    "schema_version": "robustness-workload-policy/v1",
                    "max_candidates": 2,
                    "max_walk_forward_folds": 10,
                    "max_paths": 100,
                    "max_path_length_sessions": 20,
                    "max_total_sampled_blocks": 1000,
                    "max_sensitivity_scenarios": 10,
                    "max_stress_scenarios": 10,
                    "max_candidate_scenario_combinations": 100,
                    "max_expected_cse_builds": 100,
                }
            ),
            required_families=["WALK_FORWARD", "MONTE_CARLO", "SENSITIVITY", "STRESS"],
            gate_policy=robustness_gate(
                [
                    {
                        "metric": "walk_forward.evaluable_folds",
                        "operator": "GTE",
                        "threshold": {"numerator": "3", "denominator": "1"},
                    },
                    {
                        "metric": "monte_carlo.path_count",
                        "operator": "GTE",
                        "threshold": {"numerator": "1", "denominator": "1"},
                    },
                    {
                        "metric": "sensitivity.valid_scenarios",
                        "operator": "GTE",
                        "threshold": {"numerator": "1", "denominator": "1"},
                    },
                    {
                        "metric": "stress.valid_scenarios",
                        "operator": "GTE",
                        "threshold": {"numerator": "1", "denominator": "1"},
                    },
                ]
            ),
            diversity_policy=diversity_policy(),
            top_n=5,
            market_cache_root=root / "history" / "cache",
            cache_root=cache,
            checkpoint_path=checkpoint,
            output=output,
            stop_after=stop_after,
        )

    def test_clean_warm_resume_and_seed_invalidation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            catalog, _ = prepare(root / "history", count=21)
            research = root / "research"
            write_research_evidence(research)
            scores = root / "scores"
            run_scoring(
                research,
                score_policy(),
                ranking_policy(),
                diversity_policy(),
                top_n_value=4,
                cache_root=root / "score-cache",
                output=scores,
            )
            clean = root / "clean"
            clean_manifest = self.run_once(
                root=root,
                research=research,
                scores=scores,
                catalog=catalog,
                cache=root / "robust-cache",
                checkpoint=root / "clean.sqlite",
                output=clean,
            )
            warm = root / "warm"
            warm_manifest = self.run_once(
                root=root,
                research=research,
                scores=scores,
                catalog=catalog,
                cache=root / "robust-cache",
                checkpoint=root / "warm.sqlite",
                output=warm,
            )
            self.assertEqual(clean_manifest, warm_manifest)
            self.assertEqual(tree_hashes(clean), tree_hashes(warm))
            plan = json_record(clean / "walk-forward-plan.json")
            self.assertEqual(plan["fold_count"], 3)
            self.assertEqual(plan["overlap"]["validation_session_slots"], 18)
            self.assertEqual(plan["overlap"]["unique_validation_sessions"], 8)
            self.assertEqual(plan["overlap"]["overlap_session_slots"], 10)
            walk_result = parquet_records(clean / "walk-forward-results.parquet")[0]
            self.assertTrue(
                all(
                    fold["discovery_gate_result"] == "FAIL"
                    and fold["validation_evaluated_regardless_of_discovery_gate"]
                    and fold["validation"]["partition_evaluation_id"]
                    for fold in walk_result["folds"]
                )
            )
            monte_result = parquet_records(clean / "monte-carlo-results.parquet")[0]
            self.assertFalse(monte_result["market_replayed"])
            self.assertEqual(monte_result["metrics"]["path_count"]["value"]["numerator"], "12")
            sensitivity_result = parquet_records(clean / "sensitivity-results.parquet")[0]
            self.assertTrue(
                all(
                    not item["promoted"]
                    and not item["original_candidate_changed"]
                    and not item["research_score_changed"]
                    for item in sensitivity_result["results"]
                )
            )
            stress_result = parquet_records(clean / "stress-results.parquet")[0]
            self.assertTrue(all(item["rebacktested"] for item in stress_result["results"]))
            assessment = parquet_records(clean / "robustness-assessments.parquet")[0]
            self.assertIsNone(assessment["robustness_score"])
            self.assertIsNone(assessment["robustness_rating"])
            clean_ops = json_record(root / "clean.operational.json")
            warm_ops = json_record(root / "warm.operational.json")
            self.assertGreater(clean_ops["session_evaluations_built"], 0)
            self.assertEqual(warm_ops["session_evaluations_built"], 0)
            self.assertGreater(warm_ops["session_evaluations_reused"], 0)

            resume_cache = root / "resume-cache"
            resume_checkpoint = root / "resume.sqlite"
            with self.assertRaises(ControlledRobustnessInterruption):
                self.run_once(
                    root=root,
                    research=research,
                    scores=scores,
                    catalog=catalog,
                    cache=resume_cache,
                    checkpoint=resume_checkpoint,
                    output=root / "interrupted",
                    stop_after=2,
                )
            resumed = root / "resumed"
            self.run_once(
                root=root,
                research=research,
                scores=scores,
                catalog=catalog,
                cache=resume_cache,
                checkpoint=resume_checkpoint,
                output=resumed,
            )
            self.assertEqual(tree_hashes(clean), tree_hashes(resumed))
            resumed_ops = json_record(root / "resumed.operational.json")
            self.assertGreater(resumed_ops["family_results_reused"], 0)

            changed = root / "changed-seed"
            changed_manifest = self.run_once(
                root=root,
                research=research,
                scores=scores,
                catalog=catalog,
                cache=root / "robust-cache",
                checkpoint=root / "changed.sqlite",
                output=changed,
                seed="0" * 63 + "2",
            )
            self.assertNotEqual(
                clean_manifest["robustness_protocol_id"],
                changed_manifest["robustness_protocol_id"],
            )
            for name, id_field in (
                ("walk-forward-results.parquet", "walk_forward_result_id"),
                ("sensitivity-results.parquet", "sensitivity_result_id"),
                ("stress-results.parquet", "stress_result_id"),
            ):
                self.assertEqual(
                    parquet_records(clean / name)[0][id_field],
                    parquet_records(changed / name)[0][id_field],
                )
            self.assertNotEqual(
                parquet_records(clean / "monte-carlo-results.parquet")[0]["monte_carlo_result_id"],
                parquet_records(changed / "monte-carlo-results.parquet")[0][
                    "monte_carlo_result_id"
                ],
            )
