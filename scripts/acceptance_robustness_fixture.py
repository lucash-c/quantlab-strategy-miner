"""Persistent synthetic four-timeframe Robustness v1 acceptance and benchmark."""

# ruff: noqa: E402 -- explicit source-workspace bootstrap for local acceptance.

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
for package in ("core", "data", "backtest", "mining", "research", "scoring", "robustness"):
    sys.path.insert(0, str(ROOT / "packages" / package / "src"))

import pyarrow.parquet as pq
from quantlab_core.canonical import sha256_file, write_canonical_json
from quantlab_robustness.contracts import (
    AuthorizedSessionUniverseV1,
    CandidateSelectionV1,
    ExecutionStressPolicyV1,
    MonteCarloPolicyV1,
    RobustnessWorkloadPolicyV1,
    SensitivityPolicyV1,
)
from quantlab_robustness.evidence import load_score_evidence
from quantlab_robustness.runner import ControlledRobustnessInterruption, run_robustness
from quantlab_scoring.evidence import load_research_evidence
from quantlab_scoring.runner import run_scoring
from research_helpers import execution_config, prepare, test_gate
from robustness_helpers import robustness_gate, walk_policy
from scoring_helpers import (
    diversity_policy,
    ranking_policy,
    score_policy,
    write_research_evidence,
)


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def rows(path: Path):
    records = []
    for batch in pq.ParquetFile(path).iter_batches(columns=["record_json"]):
        records.extend(json.loads(value) for value in batch.column(0).to_pylist())
    return records


def hashes(path: Path):
    return {
        item.relative_to(path).as_posix(): sha256_file(item)
        for item in sorted(path.rglob("*"))
        if item.is_file()
    }


def policies(session_ids, seed):
    source = {"source": "EXPLICIT_SESSION_IDS", "session_ids": session_ids[-8:]}
    monte_carlo = MonteCarloPolicyV1.model_validate(
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
                    "name": "one-point-each-side",
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
    return monte_carlo, sensitivity, stress


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args()
    root = args.work
    if root.exists():
        raise ValueError("acceptance work directory must be new")
    root.mkdir(parents=True)
    timeframes = ("1m", "2m", "5m", "15m")
    catalog, _ = prepare(root / "history", count=21, timeframes=timeframes)
    research = root / "research"
    write_research_evidence(research, timeframes=timeframes, all_scored=True)
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
    score_evidence = load_score_evidence(scores)
    if len(score_evidence.raw) != 4:
        raise AssertionError("four-timeframe fixture must yield four SCORED candidates")
    session_ids = [session.session_id for session in catalog.sessions]
    authorized = AuthorizedSessionUniverseV1.model_validate(
        {
            "schema_version": "robustness-authorized-sessions/v1",
            "dataset_id": catalog.manifest["dataset_id"],
            "session_ids": session_ids,
        }
    )
    candidate_selection = CandidateSelectionV1.model_validate(
        {
            "schema_version": "robustness-candidate-selection/v1",
            "method": "ALL_SCORED",
        }
    )
    walk_forward = walk_policy(
        discovery_gate=test_gate(
            criteria=[{"metric": "trades", "operator": "GTE", "threshold": 1000000}]
        ),
        validation_gate=test_gate(validation=True, empty=True),
    )
    workload = RobustnessWorkloadPolicyV1.model_validate(
        {
            "schema_version": "robustness-workload-policy/v1",
            "max_candidates": 10,
            "max_walk_forward_folds": 10,
            "max_paths": 100,
            "max_path_length_sessions": 20,
            "max_total_sampled_blocks": 1000,
            "max_sensitivity_scenarios": 10,
            "max_stress_scenarios": 10,
            "max_candidate_scenario_combinations": 100,
            "max_expected_cse_builds": 300,
        }
    )
    gate = robustness_gate(
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
    )

    def execute(name, *, seed="0" * 63 + "1", stop_after=None, cache=None):
        monte_carlo, sensitivity, stress = policies(session_ids, seed)
        return run_robustness(
            research_directory=research,
            score_directory=scores,
            catalog=catalog,
            authorized=authorized,
            baseline_evaluation=execution_config().model_copy(update={"max_sessions": 21}),
            candidate_selection=candidate_selection,
            walk_forward_policy=walk_forward,
            monte_carlo_policy=monte_carlo,
            sensitivity_policy=sensitivity,
            stress_policy=stress,
            workload_policy=workload,
            required_families=["WALK_FORWARD", "MONTE_CARLO", "SENSITIVITY", "STRESS"],
            gate_policy=gate,
            diversity_policy=diversity_policy(),
            top_n=10,
            market_cache_root=root / "history" / "cache",
            cache_root=cache or root / "robustness-cache",
            checkpoint_path=root / (name + ".sqlite"),
            output=root / name,
            stop_after=stop_after,
        )

    clean_manifest = execute("clean")
    warm_manifest = execute("warm")
    if clean_manifest != warm_manifest or hashes(root / "clean") != hashes(root / "warm"):
        raise AssertionError("clean/warm scientific outputs differ")
    resume_cache = root / "resume-cache"
    try:
        execute("resumed", stop_after=5, cache=resume_cache)
    except ControlledRobustnessInterruption:
        pass
    else:
        raise AssertionError("controlled interruption did not occur")
    execute("resumed", cache=resume_cache)
    if hashes(root / "clean") != hashes(root / "resumed"):
        raise AssertionError("clean/resume scientific outputs differ")
    execute("changed-seed", seed="0" * 63 + "2")

    clean_ids = {
        family: {
            row["candidate_id"]: row[id_name]
            for row in rows(root / "clean" / (family + "-results.parquet"))
        }
        for family, id_name in (
            ("walk-forward", "walk_forward_result_id"),
            ("monte-carlo", "monte_carlo_result_id"),
            ("sensitivity", "sensitivity_result_id"),
            ("stress", "stress_result_id"),
        )
    }
    changed_ids = {
        family: {
            row["candidate_id"]: row[id_name]
            for row in rows(root / "changed-seed" / (family + "-results.parquet"))
        }
        for family, id_name in (
            ("walk-forward", "walk_forward_result_id"),
            ("monte-carlo", "monte_carlo_result_id"),
            ("sensitivity", "sensitivity_result_id"),
            ("stress", "stress_result_id"),
        )
    }
    for family in ("walk-forward", "sensitivity", "stress"):
        if clean_ids[family] != changed_ids[family]:
            raise AssertionError(f"seed invalidated unrelated family: {family}")
    if clean_ids["monte-carlo"] == changed_ids["monte-carlo"]:
        raise AssertionError("seed did not invalidate Monte Carlo")

    research_evidence = load_research_evidence(research)
    timeframes_by_candidate = {
        candidate: research_evidence.candidates[candidate].timeframe
        for candidate in clean_ids["walk-forward"]
    }
    report = {
        "schema_version": "robustness-fixture-acceptance/v1",
        "synthetic": True,
        "real_b3_robustness_claim": False,
        "performance_thresholds": "FIXTURES_ONLY_NOT_PRODUCTION_DEFAULTS",
        "timeframes_by_candidate": timeframes_by_candidate,
        "manifest": clean_manifest,
        "protocol": load(root / "clean/robustness-protocol.json"),
        "candidate_set": load(root / "clean/robustness-candidate-set.json"),
        "workload_policy": load(root / "clean/robustness-workload-policy.json"),
        "workload_plan": load(root / "clean/robustness-workload-plan.json"),
        "walk_forward_plan": load(root / "clean/walk-forward-plan.json"),
        "monte_carlo_source_pool": load(root / "clean/monte-carlo-source-pool.json"),
        "monte_carlo_path_set": load(root / "clean/monte-carlo-path-set.json"),
        "qualified_set": load(root / "clean/robustness-qualified-set.json"),
        "shortlist": load(root / "clean/robustness-shortlist.json"),
        "family_result_ids": clean_ids,
        "seed_change_family_result_ids": changed_ids,
        "assessments": rows(root / "clean/robustness-assessments.parquet"),
        "clean_warm_resume_byte_identical": True,
        "scientific_artifact_hashes": hashes(root / "clean"),
        "operational": {
            name: load(root / (name + ".operational.json"))
            for name in ("clean", "warm", "resumed", "changed-seed")
        },
    }
    write_canonical_json(root / "acceptance-report.json", report)
    print(
        json.dumps(
            {
                "report": str(root / "acceptance-report.json"),
                "candidates": len(timeframes_by_candidate),
                "timeframes": sorted(set(timeframes_by_candidate.values())),
                "byte_identical": True,
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
