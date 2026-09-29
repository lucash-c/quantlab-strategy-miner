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
    identity,
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
    RobustnessTechnicalCompletenessPolicyV1,
    RobustnessWorkloadPolicyV1,
    SensitivityPolicyV1,
    WalkForwardPolicyV1,
)
from quantlab_robustness.contracts import (
    load_policy as load_robustness_policy,
)
from quantlab_robustness.evidence import load_score_evidence
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


def verified_robustness_manifest(path: Path) -> dict[str, Any]:
    manifest = load_json(path / "robustness-manifest.json")
    if manifest.get("schema_version") != "robustness-manifest/v1":
        raise AssertionError(f"unsupported robustness manifest in {path}")
    for name, digest in manifest.get("artifacts", {}).items():
        if sha256_file(path / name) != digest:
            raise AssertionError(f"robustness artifact hash mismatch: {path / name}")
    expected = identity(
        "robustness-export/v1",
        {key: value for key, value in manifest.items() if key != "robustness_export_id"},
    )
    if manifest.get("robustness_export_id") != expected:
        raise AssertionError(f"robustness_export_id mismatch in {path}")
    return manifest


def family_result_ids(path: Path) -> dict[str, dict[str, str]]:
    specs = (
        ("walk-forward", "walk_forward_result_id"),
        ("ninth-increment-walk-forward", "walk_forward_result_id"),
        ("monte-carlo", "monte_carlo_result_id"),
        ("sensitivity", "sensitivity_result_id"),
        ("stress", "stress_result_id"),
    )
    return {
        family: {
            row["candidate_id"]: row[id_field]
            for row in parquet_records(path / f"{family}-results.parquet")
        }
        for family, id_field in specs
    }


def status_counts(rows: list[dict[str, Any]], field: str = "status") -> dict[str, int]:
    return dict(sorted(Counter(row[field] for row in rows).items()))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument(
        "--resume-after-scoring",
        action="store_true",
        help=(
            "Resume an interrupted acceptance after validating the already-published "
            "Research and Score evidence in the existing work directory."
        ),
    )
    parser.add_argument(
        "--stress-amendment-v2",
        action="store_true",
        help=(
            "Run the precommitted Stress V2 amendment into new output artifacts while "
            "preserving and validating the original V1 evidence."
        ),
    )
    args = parser.parse_args()
    if args.work.exists() and not args.resume_after_scoring:
        raise ValueError("acceptance work directory must be new")
    if not args.work.exists() and args.resume_after_scoring:
        raise ValueError("resume requires an existing acceptance work directory")
    if args.stress_amendment_v2 and not args.resume_after_scoring:
        raise ValueError("Stress V2 amendment requires --resume-after-scoring")

    whitelist_path = ACCEPTANCE / "session-whitelist-v2.json"
    source_manifest_path = ROOT / "docs/evidence/ninth-increment-official-sources-v1.json"
    whitelist = load_whitelist(whitelist_path)
    source_manifest = load_json(source_manifest_path)
    expected = {row["trading_date"]: row for row in source_manifest["sources"]}
    sources = verify_sources(args.input_dir, whitelist, expected)
    if [row["trading_date"] for row in sources] != whitelist["sessions"]:
        raise AssertionError("verified sources differ from the frozen chronological whitelist")

    args.work.mkdir(parents=True, exist_ok=args.resume_after_scoring)
    input_freeze = {
        "schema_version": "ninth-increment-real-input-freeze/v1",
        "whitelist_file_sha256": sha256_file(whitelist_path),
        "source_manifest_file_sha256": sha256_file(source_manifest_path),
        "legacy_technical_gate_file_sha256": sha256_file(
            ACCEPTANCE / "technical-gate-v1.json"
        ),
        "legacy_walk_forward_policy_file_sha256": sha256_file(
            ACCEPTANCE / "walk-forward-policy-v1.json"
        ),
        "ninth_increment_walk_forward_policy_file_sha256": sha256_file(
            ACCEPTANCE / "walk-forward-policy-real-20-v1.json"
        ),
        "ninth_increment_technical_completeness_policy_file_sha256": sha256_file(
            ACCEPTANCE / "technical-completeness-real-20-v1.json"
        ),
        "quantitative_policy_file_sha256": sha256_file(
            ACCEPTANCE / "quantitative-gate-real-20-v2.json"
        ),
        "sources": sources,
        "real_quantitative_results_observed_before_freeze": False,
    }
    input_freeze_path = args.work / "input-freeze.json"
    if args.resume_after_scoring:
        if load_json(input_freeze_path) != input_freeze:
            raise AssertionError("resume input freeze differs from current frozen inputs")
    else:
        write_canonical_json(input_freeze_path, input_freeze)
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
    market_history_path = args.work / "market-history.json"
    if args.resume_after_scoring:
        if load_json(market_history_path) != catalog.manifest:
            raise AssertionError("resume market history differs from published history")
    else:
        write_canonical_json(market_history_path, catalog.manifest)

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
    if args.resume_after_scoring:
        # The original process could only publish Score after this exact observer gate
        # had accepted zero Validation reads before Discovery Freeze. Validate both
        # immutable exports below instead of rebuilding either scientific stage.
        load_research_evidence(research_output)
        validation_reads_before_freeze = 0
    else:
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
    if args.resume_after_scoring:
        load_score_evidence(score_output)
    else:
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
    legacy_walk_forward = load_robustness_policy(
        ACCEPTANCE / "walk-forward-policy-v1.json", WalkForwardPolicyV1
    )
    ninth_walk_forward = load_robustness_policy(
        ACCEPTANCE / "walk-forward-policy-real-20-v1.json", WalkForwardPolicyV1
    )
    monte_carlo = load_robustness_policy(
        ACCEPTANCE / "monte-carlo-policy-v1.json", MonteCarloPolicyV1
    )
    sensitivity = load_robustness_policy(
        ACCEPTANCE / "sensitivity-policy-v1.json", SensitivityPolicyV1
    )
    original_stress = load_robustness_policy(
        ACCEPTANCE / "stress-policy-v1.json", ExecutionStressPolicyV1
    )
    amended_stress = load_robustness_policy(
        ACCEPTANCE / "stress-policy-v2.json", ExecutionStressPolicyV1
    )
    stress = amended_stress if args.stress_amendment_v2 else original_stress
    workload = load_robustness_policy(
        ACCEPTANCE / "workload-policy-v1.json", RobustnessWorkloadPolicyV1
    )
    technical_gate = load_robustness_policy(
        ACCEPTANCE / "technical-gate-v1.json", RobustnessGatePolicyV1
    )
    technical_completeness = load_robustness_policy(
        ACCEPTANCE / "technical-completeness-real-20-v1.json",
        RobustnessTechnicalCompletenessPolicyV1,
    )
    quantitative_gate = load_robustness_policy(
        ACCEPTANCE / "quantitative-gate-real-20-v2.json",
        RobustnessQuantitativePolicyV1,
    )
    robustness_evaluation = evaluation.model_copy(update={"max_sessions": 20})

    original_clean = args.work / "robustness-clean"
    original_manifest: dict[str, Any] | None = None
    original_clean_hashes: dict[str, str] | None = None
    amendment_record: dict[str, Any] | None = None
    if args.stress_amendment_v2:
        amendment_record = load_json(
            ROOT / "docs/evidence/ninth-increment-stress-policy-amendment-v3.json"
        )
        if amendment_record["original_policy"]["stress_policy_id"] != original_stress.policy_id:
            raise AssertionError("Stress V1 identity differs from amendment provenance")
        if amendment_record["amended_policy"]["stress_policy_id"] != amended_stress.policy_id:
            raise AssertionError("Stress V2 identity differs from amendment precommitment")
        original_manifest = verified_robustness_manifest(original_clean)
        original_clean_hashes = tree_hashes(original_clean)
        original_stress_rows = parquet_records(original_clean / "stress-results.parquet")
        if any(
            row["metrics"]["valid_scenarios"]["value"]["numerator"] != "0"
            or len(row["scenarios"]) != 1
            or row["scenarios"][0]["status"] != "INVALID_STRESS_SCENARIO"
            or row["scenarios"][0]["reason"] != "NON_STRESS_SCENARIO"
            for row in original_stress_rows
        ):
            raise AssertionError("preserved Stress V1 evidence does not show the structural defect")
        amendment_freeze = {
            "schema_version": "ninth-increment-stress-amendment-input-freeze/v1",
            "precommit_commit": "ed9731e",
            "amendment_record_file_sha256": sha256_file(
                ROOT / "docs/evidence/ninth-increment-stress-policy-amendment-v3.json"
            ),
            "original_stress_policy_id": original_stress.policy_id,
            "amended_stress_policy_id": amended_stress.policy_id,
            "original_robustness_export_id": original_manifest["robustness_export_id"],
            "original_robustness_manifest_file_sha256": sha256_file(
                original_clean / "robustness-manifest.json"
            ),
            "original_stress_status": "INVALID_STRESS_SCENARIO",
            "original_stress_reason": "NON_STRESS_SCENARIO",
            "amended_stress_results_observed_before_freeze": False,
        }
        amendment_freeze_path = args.work / "stress-amendment-input-freeze.json"
        if amendment_freeze_path.exists():
            if load_json(amendment_freeze_path) != amendment_freeze:
                raise AssertionError("Stress amendment input freeze changed")
        else:
            write_canonical_json(amendment_freeze_path, amendment_freeze)

    run_prefix = "robustness-stress-v2" if args.stress_amendment_v2 else "robustness"

    def execute(
        name: str,
        *,
        checkpoint: Path | None = None,
        policy: MonteCarloPolicyV1 = monte_carlo,
        stop_after: int | None = None,
    ) -> dict[str, Any]:
        destination = args.work / name
        if destination.exists():
            if stop_after is not None:
                raise AssertionError("cannot interrupt an already-published robustness export")
            return verified_robustness_manifest(destination)
        return run_robustness(
            research_directory=research_output,
            score_directory=score_output,
            catalog=catalog,
            authorized=authorized,
            baseline_evaluation=robustness_evaluation,
            candidate_selection=candidate_selection,
            walk_forward_policy=legacy_walk_forward,
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
            output=destination,
            quantitative_policy=quantitative_gate,
            quantitative_walk_forward_policy=ninth_walk_forward,
            technical_completeness_policy=technical_completeness,
            stop_after=stop_after,
            observer=lambda event: print(json.dumps({"run": name, **event}), flush=True),
        )

    clean_name = f"{run_prefix}-clean"
    warm_name = f"{run_prefix}-warm"
    resumed_name = f"{run_prefix}-resumed"
    clean_manifest = execute(clean_name)
    warm_manifest = execute(warm_name)
    resume_checkpoint = args.work / f"{resumed_name}.sqlite"
    if not (args.work / resumed_name).exists():
        try:
            execute(resumed_name, checkpoint=resume_checkpoint, stop_after=5)
        except ControlledRobustnessInterruption:
            pass
        else:
            raise AssertionError("controlled robustness interruption did not occur")
    resumed_manifest = execute(resumed_name, checkpoint=resume_checkpoint)
    clean_hashes = tree_hashes(args.work / clean_name)
    if not (
        clean_manifest == warm_manifest == resumed_manifest
        and clean_hashes == tree_hashes(args.work / warm_name)
        and clean_hashes == tree_hashes(args.work / resumed_name)
    ):
        raise AssertionError("clean/warm/resume scientific robustness outputs differ")

    if not args.stress_amendment_v2:
        changed_seed_record = monte_carlo.model_dump(mode="json")
        changed_seed_record["seed"] = "0" * 63 + "2"
        changed_seed = MonteCarloPolicyV1.model_validate(changed_seed_record)
        execute("robustness-changed-seed", policy=changed_seed)

    clean = args.work / clean_name
    legacy_plan = load_json(clean / "walk-forward-plan.json")
    ninth_plan = load_json(clean / "ninth-increment-walk-forward-plan.json")
    if (
        legacy_plan["fold_count"] != 2
        or legacy_plan["required_min_folds"] != 3
        or legacy_plan["status"] != "INSUFFICIENT_WALK_FORWARD_HISTORY"
    ):
        raise AssertionError("legacy Walk-Forward semantics changed for twenty sessions")
    if (
        ninth_plan["fold_count"] != 2
        or ninth_plan["required_min_folds"] != 2
        or ninth_plan["status"] != "READY"
    ):
        raise AssertionError("Ninth Increment Walk-Forward did not derive two complete folds")
    if [fold["validation_session_ids"] for fold in legacy_plan["folds"]] != [
        fold["validation_session_ids"] for fold in ninth_plan["folds"]
    ]:
        raise AssertionError("legacy and Ninth Increment policies derived different folds")
    legacy_technical = parquet_records(clean / "robustness-assessments.parquet")
    ninth_technical = parquet_records(
        clean / "ninth-increment-technical-assessments.parquet"
    )
    quantitative = parquet_records(clean / "robustness-quantitative-assessments.parquet")
    if any(row["status"] != "ROBUSTNESS_INSUFFICIENT_EVIDENCE" for row in legacy_technical):
        raise AssertionError("legacy technical result must preserve insufficient evidence")
    if any(
        row["status"] != "NINTH_INCREMENT_TECHNICAL_COMPLETE"
        for row in ninth_technical
    ):
        raise AssertionError("Ninth Increment mandatory families were not technically complete")
    if any(
        row.get("quantitative_dependency")
        != "NINTH_INCREMENT_TECHNICAL_COMPLETENESS"
        or not row.get("legacy_robustness_is_not_quantitative_prerequisite")
        for row in quantitative
    ):
        raise AssertionError("quantitative verdict used the wrong technical dependency")

    family_ids = family_result_ids(clean)
    original_family_ids = family_result_ids(original_clean)
    changed_family_ids = family_result_ids(args.work / "robustness-changed-seed")
    candidate_ids = set(family_ids["stress"])
    if any(set(ids) != candidate_ids for ids in family_ids.values()):
        raise AssertionError("current robustness families have different candidate universes")
    if any(set(ids) != candidate_ids for ids in original_family_ids.values()):
        raise AssertionError("original robustness families have different candidate universes")
    if any(set(ids) != candidate_ids for ids in changed_family_ids.values()):
        raise AssertionError("changed-seed families have different candidate universes")

    if args.stress_amendment_v2:
        for family in (
            "walk-forward",
            "ninth-increment-walk-forward",
            "monte-carlo",
            "sensitivity",
        ):
            if family_ids[family] != original_family_ids[family]:
                raise AssertionError(
                    f"Stress amendment invalidated unrelated family: {family}"
                )
        if any(
            family_ids["stress"][candidate_id]
            == original_family_ids["stress"][candidate_id]
            for candidate_id in sorted(candidate_ids)
        ):
            raise AssertionError("Stress V2 failed to invalidate a Stress V1 result")
        amended_stress_rows = parquet_records(clean / "stress-results.parquet")
        if len(amended_stress_rows) != len(candidate_ids) or any(
            row["status"] != "COMPLETED"
            or row["stress_policy_id"] != amended_stress.policy_id
            or row["metrics"]["valid_scenarios"]["value"]["numerator"] != "1"
            or len(row["scenarios"]) != 1
            or row["scenarios"][0]["status"] != "VALID"
            or row["scenarios"][0]["stress_policy_id"] != amended_stress.policy_id
            or row["scenarios"][0]["resolved_cost"]["numerator"] != "3"
            or row["scenarios"][0]["resolved_cost"]["denominator"] != "2"
            or row["scenarios"][0]["resolved_slippage"]["numerator"] != "3"
            or row["scenarios"][0]["resolved_slippage"]["denominator"] != "2"
            for row in amended_stress_rows
        ):
            raise AssertionError("Stress V2 evidence is not the exact valid 3/2 amendment")
        if original_clean_hashes is None or tree_hashes(original_clean) != original_clean_hashes:
            raise AssertionError("preserved Stress V1 evidence changed during amendment")

    for family in (
        "walk-forward",
        "ninth-increment-walk-forward",
        "sensitivity",
        "stress",
    ):
        comparison_ids = original_family_ids if args.stress_amendment_v2 else family_ids
        if comparison_ids[family] != changed_family_ids[family]:
            raise AssertionError(f"Monte Carlo seed invalidated unrelated family: {family}")
    comparison_ids = original_family_ids if args.stress_amendment_v2 else family_ids
    if comparison_ids["monte-carlo"] == changed_family_ids["monte-carlo"]:
        raise AssertionError("Monte Carlo seed did not invalidate Monte Carlo evidence")

    final_sources = verify_sources(args.input_dir, whitelist, expected)
    if final_sources != sources:
        raise AssertionError("official source changed during acceptance")
    research = load_research_evidence(research_output)
    timeframes = Counter(strategy.timeframe for strategy in research.candidates.values())
    operational_names = [
        "research",
        "scores",
        "robustness-clean",
        "robustness-warm",
        "robustness-resumed",
        "robustness-changed-seed",
    ]
    if args.stress_amendment_v2:
        operational_names.extend([clean_name, warm_name, resumed_name])
    report = {
        "schema_version": (
            "ninth-increment-real-multisession-acceptance/v2"
            if args.stress_amendment_v2
            else "ninth-increment-real-multisession-acceptance/v1"
        ),
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
            "legacy_policy_id": legacy_walk_forward.policy_id,
            "legacy_plan": legacy_plan,
            "ninth_increment_policy_id": ninth_walk_forward.policy_id,
            "ninth_increment_plan": ninth_plan,
            "fold_count": ninth_plan["fold_count"],
            "folds": ninth_plan["folds"],
            "two_fold_generalization_evidence_is_limited": True,
        },
        "legacy_technical_robustness": {
            "policy_id": technical_gate.policy_id,
            "legacy_semantics_unchanged": True,
            "status_counts": status_counts(legacy_technical),
            "minimum_evaluable_folds_remains_three": True,
        },
        "ninth_increment_technical_completeness": {
            "policy_id": technical_completeness.policy_id,
            "status_counts": status_counts(ninth_technical),
            "minimum_evaluable_folds": 2,
            "all_four_families_required": True,
        },
        "quantitative_gate": {
            "policy_id": quantitative_gate.policy_id,
            "status_counts": status_counts(quantitative),
            "qualified_set": load_json(
                clean / "robustness-quantitative-qualified-set.json"
            ),
            "technical_dependency": "NINTH_INCREMENT_TECHNICAL_COMPLETENESS",
            "legacy_robustness_is_not_prerequisite": True,
            "thresholds_changed_after_results": False,
        },
        "verdicts": [
            {
                "candidate_id": row["candidate_id"],
                "legacy_robustness_status": row["legacy_robustness_status"],
                "ninth_increment_technical_status": row[
                    "ninth_increment_technical_status"
                ],
                "quantitative_robustness_status": row[
                    "quantitative_robustness_status"
                ],
            }
            for row in quantitative
        ],
        "family_result_ids": family_ids,
        "original_family_result_ids": original_family_ids,
        "changed_seed_family_result_ids": changed_family_ids,
        "clean_warm_resume_byte_identical": True,
        "scientific_artifact_hashes": clean_hashes,
        "original_scientific_artifact_hashes": (
            original_clean_hashes if args.stress_amendment_v2 else clean_hashes
        ),
        "robustness_manifest": clean_manifest,
        "original_robustness_manifest": (
            original_manifest if args.stress_amendment_v2 else clean_manifest
        ),
        "stress_policy_history": (
            {
                "v1_original": {
                    "stress_policy_id": original_stress.policy_id,
                    "policy_file": "examples/acceptance/ninth-increment/stress-policy-v1.json",
                    "policy_file_sha256": amendment_record["original_policy"][
                        "file_sha256"
                    ],
                    "status": "INVALID_STRESS_SCENARIO",
                    "reason": "NON_STRESS_SCENARIO",
                    "valid_scenarios_per_candidate": 0,
                    "preserved_as_historical_evidence": True,
                    "technical_consequence": (
                        "NINTH_INCREMENT_TECHNICAL_INCOMPLETE"
                    ),
                },
                "v2_amendment": {
                    "precommit_commit": "ed9731e",
                    "stress_policy_id": amended_stress.policy_id,
                    "policy_file": "examples/acceptance/ninth-increment/stress-policy-v2.json",
                    "policy_file_sha256": amendment_record["amended_policy"][
                        "file_sha256"
                    ],
                    "cost_multiplier": {"numerator": "3", "denominator": "2"},
                    "slippage_multiplier": {
                        "numerator": "3",
                        "denominator": "2",
                    },
                    "valid_scenarios_per_candidate": 1,
                    "policy_changed_after_amended_results": False,
                    "amended_results_accepted_without_optimization": True,
                },
            }
            if args.stress_amendment_v2
            else None
        ),
        "stress_amendment_granular_invalidation": (
            {
                "reused_unchanged_families": [
                    "walk-forward",
                    "ninth-increment-walk-forward",
                    "monte-carlo",
                    "sensitivity",
                ],
                "invalidated_family": "stress",
                "invalidated_descendants": [
                    "candidate-robustness-assessment",
                    "ninth-increment-technical-completeness",
                    "quantitative-gate",
                    "qualified-set",
                    "robustness-ranking",
                    "quantitative-verdict",
                ],
                "research_reused": True,
                "score_reused": True,
                "candidate_session_evaluations_reused_when_fingerprint_compatible": True,
                "original_v1_tree_preserved": True,
            }
            if args.stress_amendment_v2
            else None
        ),
        "operational": {
            name: load_json(args.work / f"{name}.operational.json")
            for name in operational_names
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
                "legacy_technical": report["legacy_technical_robustness"][
                    "status_counts"
                ],
                "ninth_increment_technical": report[
                    "ninth_increment_technical_completeness"
                ]["status_counts"],
                "quantitative": report["quantitative_gate"]["status_counts"],
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
