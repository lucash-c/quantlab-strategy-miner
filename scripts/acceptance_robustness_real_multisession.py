"""Opt-in official B3 twenty-session Ninth Increment acceptance."""

# ruff: noqa: E402 -- explicit source-workspace bootstrap for local acceptance.

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for package in ("core", "data", "backtest", "mining", "research", "scoring", "robustness"):
    sys.path.insert(0, str(ROOT / "packages" / package / "src"))

import pyarrow.parquet as pq
from acquire_b3_real_sessions import load_whitelist, source_filename, verify_sources
from quantlab_core.canonical import sha256_file, write_canonical_json
from quantlab_data.historical import SessionSource, build_market_history
from quantlab_data.session_catalog import SessionCatalog
from quantlab_mining.contracts import (
    EvaluationConfigV1,
    GenerationPolicyV1,
    MiningSearchSpaceV1,
    load_contract,
)
from quantlab_research.contracts import GatePolicyV1, SplitPolicyV1
from quantlab_research.runner import run_research
from quantlab_robustness.contracts import (
    AuthorizedSessionUniverseV1,
    CandidateSelectionV1,
    ExecutionStressPolicyV1,
    MonteCarloPolicyV1,
    RobustnessGatePolicyV1,
    RobustnessQuantitativePolicyV1,
    RobustnessWorkloadPolicyV1,
    SensitivityPolicyV1,
    WalkForwardPolicyV1,
)
from quantlab_robustness.contracts import (
    load_policy as load_robustness_policy,
)
from quantlab_robustness.runner import ControlledRobustnessInterruption, run_robustness
from quantlab_scoring.contracts import (
    ResearchDiversityPolicyV1,
    ResearchRankingPolicyV1,
    ResearchScorePolicyV1,
)
from quantlab_scoring.contracts import (
    load_policy as load_scoring_policy,
)
from quantlab_scoring.evidence import load_research_evidence
from quantlab_scoring.runner import run_scoring

ACCEPTANCE = ROOT / "examples" / "acceptance" / "ninth-increment"
REQUIRED_FAMILIES = ["WALK_FORWARD", "MONTE_CARLO", "SENSITIVITY", "STRESS"]


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def parquet_records(path: Path) -> list[dict[str, Any]]:
    records = []
    for batch in pq.ParquetFile(path).iter_batches(columns=["record_json"]):
        records.extend(json.loads(value) for value in batch.column(0).to_pylist())
    return records


def tree_hashes(path: Path) -> dict[str, str]:
    return {
        item.relative_to(path).as_posix(): sha256_file(item)
        for item in sorted(path.rglob("*"))
        if item.is_file()
    }


def status_counts(rows: list[dict[str, Any]], field: str = "status") -> dict[str, int]:
    return dict(sorted(Counter(row[field] for row in rows).items()))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args()
    if args.work.exists():
        raise ValueError("acceptance work directory must be new")

    whitelist_path = ACCEPTANCE / "session-whitelist-v2.json"
    source_manifest_path = ROOT / "docs/evidence/ninth-increment-official-sources-v1.json"
    whitelist = load_whitelist(whitelist_path)
    source_manifest = load_json(source_manifest_path)
    expected = {row["trading_date"]: row for row in source_manifest["sources"]}
    sources = verify_sources(args.input_dir, whitelist, expected)
    if [row["trading_date"] for row in sources] != whitelist["sessions"]:
        raise AssertionError("verified sources differ from the frozen chronological whitelist")

    args.work.mkdir(parents=True)
    write_canonical_json(
        args.work / "input-freeze.json",
        {
            "schema_version": "ninth-increment-real-input-freeze/v1",
            "whitelist_file_sha256": sha256_file(whitelist_path),
            "source_manifest_file_sha256": sha256_file(source_manifest_path),
            "sources": sources,
            "real_quantitative_results_observed_before_freeze": False,
        },
    )
    started = time.perf_counter()
    source_objects = [
        SessionSource(args.input_dir / source_filename(trading_date), "WINV26")
        for trading_date in whitelist["sessions"]
    ]
    market = build_market_history(
        source_objects,
        logical_asset="WIN",
        cache_root=args.cache,
        timeframes=("1m", "2m", "5m", "15m"),
        max_sessions=20,
    )
    catalog = SessionCatalog(market.manifest)
    observed_dates = [session.trading_date for session in catalog.sessions]
    if observed_dates != whitelist["sessions"]:
        raise AssertionError("materialized market sessions differ from frozen whitelist")
    if any(session.physical_contract != "WINV26" for session in catalog.sessions):
        raise AssertionError("materialized market session changed physical contract")
    write_canonical_json(args.work / "market-history.json", catalog.manifest)

    search_space = load_contract(
        ACCEPTANCE / "real-multisession-acceptance-v1.json", MiningSearchSpaceV1
    )
    generation = load_contract(ROOT / "examples/mining/policy-160.json", GenerationPolicyV1)
    evaluation = load_contract(ROOT / "examples/mining/evaluation.json", EvaluationConfigV1)
    split = SplitPolicyV1.model_validate(load_json(ROOT / "examples/research/split-13-6.json"))
    discovery_gate = GatePolicyV1.model_validate(
        load_json(ROOT / "examples/research/discovery-no-criteria.json")
    )
    validation_gate = GatePolicyV1.model_validate(
        load_json(ROOT / "examples/research/validation-no-criteria.json")
    )
    research_events: list[dict[str, Any]] = []
    research_output = args.work / "research"
    run_research(
        search_space,
        generation,
        evaluation,
        catalog,
        split,
        discovery_gate,
        validation_gate,
        cache_root=args.cache,
        checkpoint=args.work / "research.sqlite",
        output=research_output,
        observer=research_events.append,
    )
    freeze_position = next(
        index
        for index, event in enumerate(research_events)
        if event["phase"] == "DISCOVERY_FREEZE_VALIDATED"
    )
    validation_reads_before_freeze = sum(
        event.get("stage") == "VALIDATION" for event in research_events[:freeze_position]
    )
    if validation_reads_before_freeze:
        raise AssertionError("Validation was accessed before Discovery Freeze")

    score_output = args.work / "scores"
    score_policy = load_scoring_policy(
        ROOT / "examples/scoring/research-score-v1.json", ResearchScorePolicyV1
    )
    ranking_policy = load_scoring_policy(
        ROOT / "examples/scoring/research-ranking-v1.json", ResearchRankingPolicyV1
    )
    diversity_policy = load_scoring_policy(
        ROOT / "examples/scoring/research-diversity-v1.json", ResearchDiversityPolicyV1
    )
    run_scoring(
        research_output,
        score_policy,
        ranking_policy,
        diversity_policy,
        top_n_value=160,
        cache_root=args.cache,
        output=score_output,
    )

    session_ids = [session.session_id for session in catalog.sessions]
    authorized = AuthorizedSessionUniverseV1.model_validate(
        {
            "schema_version": "robustness-authorized-sessions/v1",
            "dataset_id": catalog.manifest["dataset_id"],
            "session_ids": session_ids,
        }
    )
    candidate_selection = load_robustness_policy(
        ACCEPTANCE / "candidate-selection-v1.json", CandidateSelectionV1
    )
    walk_forward = load_robustness_policy(
        ACCEPTANCE / "walk-forward-policy-real-20-v1.json", WalkForwardPolicyV1
    )
    monte_carlo = load_robustness_policy(
        ACCEPTANCE / "monte-carlo-policy-v1.json", MonteCarloPolicyV1
    )
    sensitivity = load_robustness_policy(
        ACCEPTANCE / "sensitivity-policy-v1.json", SensitivityPolicyV1
    )
    stress = load_robustness_policy(
        ACCEPTANCE / "stress-policy-v1.json", ExecutionStressPolicyV1
    )
    workload = load_robustness_policy(
        ACCEPTANCE / "workload-policy-v1.json", RobustnessWorkloadPolicyV1
    )
    technical_gate = load_robustness_policy(
        ACCEPTANCE / "technical-gate-v1.json", RobustnessGatePolicyV1
    )
    quantitative_gate = load_robustness_policy(
        ACCEPTANCE / "quantitative-gate-v1.json", RobustnessQuantitativePolicyV1
    )
    robustness_evaluation = evaluation.model_copy(update={"max_sessions": 20})

    def execute(
        name: str,
        *,
        checkpoint: Path | None = None,
        policy: MonteCarloPolicyV1 = monte_carlo,
        stop_after: int | None = None,
    ) -> dict[str, Any]:
        return run_robustness(
            research_directory=research_output,
            score_directory=score_output,
            catalog=catalog,
            authorized=authorized,
            baseline_evaluation=robustness_evaluation,
            candidate_selection=candidate_selection,
            walk_forward_policy=walk_forward,
            monte_carlo_policy=policy,
            sensitivity_policy=sensitivity,
            stress_policy=stress,
            workload_policy=workload,
            required_families=REQUIRED_FAMILIES,
            gate_policy=technical_gate,
            diversity_policy=diversity_policy,
            top_n=160,
            market_cache_root=args.cache,
            cache_root=args.cache,
            checkpoint_path=checkpoint or args.work / f"{name}.sqlite",
            output=args.work / name,
            quantitative_policy=quantitative_gate,
            stop_after=stop_after,
            observer=lambda event: print(json.dumps({"run": name, **event}), flush=True),
        )

    clean_manifest = execute("robustness-clean")
    warm_manifest = execute("robustness-warm")
    resume_checkpoint = args.work / "robustness-resumed.sqlite"
    try:
        execute("robustness-resumed", checkpoint=resume_checkpoint, stop_after=5)
    except ControlledRobustnessInterruption:
        pass
    else:
        raise AssertionError("controlled robustness interruption did not occur")
    resumed_manifest = execute("robustness-resumed", checkpoint=resume_checkpoint)
    clean_hashes = tree_hashes(args.work / "robustness-clean")
    if not (
        clean_manifest == warm_manifest == resumed_manifest
        and clean_hashes == tree_hashes(args.work / "robustness-warm")
        and clean_hashes == tree_hashes(args.work / "robustness-resumed")
    ):
        raise AssertionError("clean/warm/resume scientific robustness outputs differ")

    changed_seed_record = monte_carlo.model_dump(mode="json")
    changed_seed_record["seed"] = "0" * 63 + "2"
    changed_seed = MonteCarloPolicyV1.model_validate(changed_seed_record)
    execute("robustness-changed-seed", policy=changed_seed)

    clean = args.work / "robustness-clean"
    plan = load_json(clean / "walk-forward-plan.json")
    if plan["fold_count"] != 2 or plan["required_min_folds"] != 2:
        raise AssertionError("real Walk-Forward did not derive exactly two complete folds")
    technical = parquet_records(clean / "robustness-assessments.parquet")
    quantitative = parquet_records(clean / "robustness-quantitative-assessments.parquet")
    if any(row["status"] == "PASSED" for row in quantitative):
        raise AssertionError("legacy technical gate cannot pass with only two evaluable folds")

    family_specs = (
        ("walk-forward", "walk_forward_result_id"),
        ("monte-carlo", "monte_carlo_result_id"),
        ("sensitivity", "sensitivity_result_id"),
        ("stress", "stress_result_id"),
    )
    family_ids = {
        family: {
            row["candidate_id"]: row[id_field]
            for row in parquet_records(clean / f"{family}-results.parquet")
        }
        for family, id_field in family_specs
    }
    changed_family_ids = {
        family: {
            row["candidate_id"]: row[id_field]
            for row in parquet_records(
                args.work / "robustness-changed-seed" / f"{family}-results.parquet"
            )
        }
        for family, id_field in family_specs
    }
    for family in ("walk-forward", "sensitivity", "stress"):
        if family_ids[family] != changed_family_ids[family]:
            raise AssertionError(f"Monte Carlo seed invalidated unrelated family: {family}")
    if family_ids["monte-carlo"] == changed_family_ids["monte-carlo"]:
        raise AssertionError("Monte Carlo seed did not invalidate Monte Carlo evidence")

    final_sources = verify_sources(args.input_dir, whitelist, expected)
    if final_sources != sources:
        raise AssertionError("official source changed during acceptance")
    research = load_research_evidence(research_output)
    timeframes = Counter(strategy.timeframe for strategy in research.candidates.values())
    report = {
        "schema_version": "ninth-increment-real-multisession-acceptance/v1",
        "status": "EXECUTED",
        "official_b3_sessions": 20,
        "logical_asset": "WIN",
        "physical_contract": "WINV26",
        "source_manifest": source_manifest,
        "dataset_id": catalog.manifest["dataset_id"],
        "window_fingerprint": catalog.manifest["window_fingerprint"],
        "session_ids": session_ids,
        "timeframes": dict(sorted(timeframes.items())),
        "research": {
            "manifest": load_json(research_output / "validation-manifest.json"),
            "split_plan": load_json(research_output / "split-plan.json"),
            "discovery_freeze": load_json(research_output / "discovery-freeze.json"),
            "discovery_pass_set": load_json(research_output / "discovery-pass-set.json"),
            "validation_experiment": load_json(
                research_output / "validation-experiment.json"
            ),
            "validation_reads_before_discovery_freeze": validation_reads_before_freeze,
        },
        "score": {
            "manifest": load_json(score_output / "score-manifest.json"),
            "score_v1_recomputed_by_robustness": False,
            "validation_is_untouched_final_holdout": False,
        },
        "walk_forward": {
            "policy_id": walk_forward.policy_id,
            "fold_count": plan["fold_count"],
            "folds": plan["folds"],
            "two_fold_generalization_evidence_is_limited": True,
        },
        "technical_gate": {
            "policy_id": technical_gate.policy_id,
            "legacy_semantics_unchanged": True,
            "status_counts": status_counts(technical),
            "minimum_evaluable_folds_remains_three": True,
        },
        "quantitative_gate": {
            "policy_id": quantitative_gate.policy_id,
            "status_counts": status_counts(quantitative),
            "qualified_set": load_json(
                clean / "robustness-quantitative-qualified-set.json"
            ),
            "technical_gate_required": True,
            "thresholds_changed_after_results": False,
        },
        "family_result_ids": family_ids,
        "changed_seed_family_result_ids": changed_family_ids,
        "clean_warm_resume_byte_identical": True,
        "scientific_artifact_hashes": clean_hashes,
        "robustness_manifest": clean_manifest,
        "operational": {
            name: load_json(args.work / f"{name}.operational.json")
            for name in (
                "research",
                "scores",
                "robustness-clean",
                "robustness-warm",
                "robustness-resumed",
                "robustness-changed-seed",
            )
        },
        "source_immutability_reverified": True,
        "automatic_rollover_used": False,
        "candidate_reoptimization_during_robustness": False,
        "scientific_limitation": (
            "Two folds provide more limited temporal evidence than three or more folds. "
            "This acceptance primarily demonstrates real multisession execution, OOS "
            "execution, absence of leakage and reoptimization, execution of the four "
            "robustness families, and application of frozen quantitative criteria; it is "
            "not strong evidence of future generalization."
        ),
        "elapsed_seconds": str(time.perf_counter() - started),
    }
    write_canonical_json(args.work / "acceptance-report.json", report)
    print(
        json.dumps(
            {
                "report": str(args.work / "acceptance-report.json"),
                "dataset_id": report["dataset_id"],
                "folds": 2,
                "technical": report["technical_gate"]["status_counts"],
                "quantitative": report["quantitative_gate"]["status_counts"],
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
