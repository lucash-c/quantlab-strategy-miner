"""Read-only validation of the Tenth Increment Phase 4A real-B3 precommitment."""

# ruff: noqa: E402 -- explicit source-workspace bootstrap for local validation.

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for source in (
    ROOT,
    ROOT / "packages" / "core" / "src",
    ROOT / "packages" / "data" / "src",
    ROOT / "packages" / "backtest" / "src",
    ROOT / "packages" / "mining" / "src",
    ROOT / "packages" / "research" / "src",
    ROOT / "packages" / "robustness" / "src",
    ROOT / "packages" / "scoring" / "src",
):
    sys.path.insert(0, str(source))

from quantlab_core.canonical import sha256_file
from quantlab_data.session_catalog import SessionCatalog
from quantlab_mining.auto_discovery import (
    ProfileRegistry,
    load_candidate_plan_bundle,
    resolve_from_registry,
)
from quantlab_mining.contracts import (
    AutoDiscoveryProfileV1,
    EvaluationConfigV1,
    MiningRequestV1,
    load_contract,
)
from quantlab_research.contracts import GatePolicyV1, SplitPolicyV1
from quantlab_research.runner import composite_protocol_record
from quantlab_research.split import create_split
from quantlab_robustness.contracts import (
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
from quantlab_robustness.protocol import workload_preflight
from quantlab_robustness.stress import resolve_stress_scenarios
from quantlab_robustness.walk_forward import build_walk_forward_plan
from quantlab_scoring.contracts import (
    ResearchDiversityPolicyV1,
    ResearchRankingPolicyV1,
    ResearchScorePolicyV1,
)
from quantlab_scoring.contracts import (
    load_policy as load_scoring_policy,
)

from scripts.acquire_b3_real_sessions import load_whitelist, verify_sources

PRECOMMITMENT = (
    ROOT
    / "examples"
    / "acceptance"
    / "tenth-increment"
    / "real-b3-precommitment-v1.json"
)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def verify_file(record: dict[str, Any], path_key: str = "path") -> Path:
    path = ROOT / record[path_key]
    require(path.is_file(), f"missing frozen file: {path}")
    require(
        sha256_file(path) == record["file_sha256"],
        f"frozen file hash mismatch: {path}",
    )
    return path


def validate_static() -> dict[str, Any]:
    precommitment = load_json(PRECOMMITMENT)
    require(
        precommitment["schema_version"] == "tenth-increment-real-b3-precommitment/v1",
        "unexpected precommitment schema",
    )
    require(
        precommitment["phase"] == "PRECOMMITTED_BEFORE_REAL_EXECUTION",
        "precommitment phase is not closed",
    )
    auto = precommitment["auto_discovery"]
    profile_path = ROOT / auto["profile_path"]
    request_path = ROOT / auto["request_path"]
    require(sha256_file(profile_path) == auto["profile_file_sha256"], "profile changed")
    require(sha256_file(request_path) == auto["request_file_sha256"], "request changed")
    profile = load_contract(profile_path, AutoDiscoveryProfileV1)
    request = load_contract(request_path, MiningRequestV1)
    resolved, constraints, resolved_profile = resolve_from_registry(
        request, ProfileRegistry.from_directory(profile_path.parent)
    )
    require(resolved_profile == profile, "Acceptance profile resolution changed")
    require(profile.auto_discovery_profile_id == auto["profile_id"], "profile ID changed")
    require(request.mining_request_id == auto["mining_request_id"], "request ID changed")
    require(
        constraints.mining_constraints_id == profile.constraints.mining_constraints_id,
        "Acceptance constraints changed",
    )
    require(
        resolved.resolved_search_space_id == auto["resolved_search_space_id"],
        "resolved search space changed",
    )
    require(len(auto["candidate_ids"]) == 48, "candidate count changed")
    require(auto["candidate_ids"] == sorted(set(auto["candidate_ids"])), "candidate IDs changed")

    dataset = precommitment["dataset"]
    for key, hash_key in (
        ("whitelist_path", "whitelist_file_sha256"),
        ("source_manifest_path", "source_manifest_file_sha256"),
        ("dataset_freeze_path", "dataset_freeze_file_sha256"),
    ):
        path = ROOT / dataset[key]
        require(path.is_file(), f"missing dataset evidence: {path}")
        require(sha256_file(path) == dataset[hash_key], f"dataset evidence changed: {path}")
    whitelist = load_whitelist(ROOT / dataset["whitelist_path"])
    freeze = load_json(ROOT / dataset["dataset_freeze_path"])
    require(dataset["dataset_id"] == freeze["dataset_id"], "dataset ID changed")
    require(dataset["window_fingerprint"] == freeze["window_fingerprint"], "window changed")
    require(whitelist["sessions"] == [item["date"] for item in dataset["sessions"]], (
        "authorized calendar changed"
    ))
    require(
        [item["session_fingerprint"] for item in freeze["sessions"]]
        == [item["session_id"] for item in dataset["sessions"]],
        "session identities changed",
    )

    research = precommitment["research"]
    evaluation_path = ROOT / research["evaluation_path"]
    split_path = ROOT / research["split_policy_path"]
    require(sha256_file(evaluation_path) == research["evaluation_file_sha256"], (
        "evaluation policy changed"
    ))
    require(sha256_file(split_path) == research["split_policy_file_sha256"], (
        "split policy changed"
    ))
    evaluation = load_contract(evaluation_path, EvaluationConfigV1).canonicalized()
    split = SplitPolicyV1.model_validate(load_json(split_path))
    require(evaluation.max_sessions == research["research_max_sessions"] == 19, (
        "Research window changed"
    ))
    require(split.validation_sessions == 6 and split.min_discovery_sessions == 13, (
        "Research split changed"
    ))
    discovery_gate = GatePolicyV1.model_validate(
        load_json(verify_file(research["discovery_gate"]))
    )
    validation_gate = GatePolicyV1.model_validate(
        load_json(verify_file(research["validation_gate"]))
    )
    require(discovery_gate.policy_id == research["discovery_gate"]["policy_id"], (
        "Discovery gate changed"
    ))
    require(validation_gate.policy_id == research["validation_gate"]["policy_id"], (
        "Validation gate changed"
    ))
    require(not discovery_gate.criteria and not validation_gate.criteria, (
        "Research pass-through gate gained a threshold"
    ))

    score = precommitment["score"]
    score_policy = load_scoring_policy(
        verify_file(
            {
                "path": score["score_policy_path"],
                "file_sha256": score["score_policy_file_sha256"],
            }
        ),
        ResearchScorePolicyV1,
    )
    ranking_policy = load_scoring_policy(
        verify_file(
            {
                "path": score["ranking_policy_path"],
                "file_sha256": score["ranking_policy_file_sha256"],
            }
        ),
        ResearchRankingPolicyV1,
    )
    diversity_policy = load_scoring_policy(
        verify_file(
            {
                "path": score["diversity_policy_path"],
                "file_sha256": score["diversity_policy_file_sha256"],
            }
        ),
        ResearchDiversityPolicyV1,
    )
    require(score_policy.score_policy_id == score["score_policy_id"], "Score policy changed")
    require(ranking_policy.ranking_policy_id == score["ranking_policy_id"], (
        "ranking policy changed"
    ))
    require(diversity_policy.diversity_policy_id == score["diversity_policy_id"], (
        "diversity policy changed"
    ))

    robustness = precommitment["robustness"]
    policy_specs: list[tuple[str, type, str]] = [
        ("candidate_selection", CandidateSelectionV1, "selection_policy_id"),
        ("legacy_walk_forward", WalkForwardPolicyV1, "policy_id"),
        ("real_20_walk_forward", WalkForwardPolicyV1, "policy_id"),
        ("monte_carlo", MonteCarloPolicyV1, "policy_id"),
        ("sensitivity", SensitivityPolicyV1, "policy_id"),
        ("stress_v1_historical", ExecutionStressPolicyV1, "policy_id"),
        ("stress_v2", ExecutionStressPolicyV1, "policy_id"),
        ("workload", RobustnessWorkloadPolicyV1, "policy_id"),
        ("legacy_gate", RobustnessGatePolicyV1, "policy_id"),
        (
            "technical_completeness",
            RobustnessTechnicalCompletenessPolicyV1,
            "policy_id",
        ),
        ("quantitative_verdict", RobustnessQuantitativePolicyV1, "policy_id"),
    ]
    loaded: dict[str, Any] = {}
    for name, model, identity_property in policy_specs:
        spec = robustness[name]
        policy = load_robustness_policy(verify_file(spec), model)
        require(getattr(policy, identity_property) == spec["policy_id"], (
            f"robustness policy changed: {name}"
        ))
        loaded[name] = policy
    require(loaded["candidate_selection"].method == "ALL_SCORED", (
        "candidate selection is no longer ALL_SCORED"
    ))
    require(loaded["stress_v1_historical"].policy_id != loaded["stress_v2"].policy_id, (
        "Stress lineage collapsed"
    ))
    scenarios = resolve_stress_scenarios(
        evaluation.model_copy(update={"max_sessions": 20}), loaded["stress_v2"]
    )
    require(len(scenarios) == 1 and scenarios[0]["status"] == "VALID", (
        "Stress V2 is not a single valid scenario"
    ))
    require(scenarios[0]["resolved_cost"] == {"numerator": "3", "denominator": "2"}, (
        "Stress V2 resolved cost changed"
    ))
    require(
        scenarios[0]["resolved_slippage"] == {"numerator": "3", "denominator": "2"},
        "Stress V2 resolved slippage changed",
    )
    session_metadata = [
        {"session_id": item["session_id"], "trading_date": item["date"]}
        for item in dataset["sessions"]
    ]
    legacy_plan = build_walk_forward_plan(
        dataset_id=dataset["dataset_id"],
        logical_asset=dataset["logical_asset"],
        sessions=session_metadata,
        policy=loaded["legacy_walk_forward"],
    )
    real_plan = build_walk_forward_plan(
        dataset_id=dataset["dataset_id"],
        logical_asset=dataset["logical_asset"],
        sessions=session_metadata,
        policy=loaded["real_20_walk_forward"],
    )
    require(real_plan["walk_forward_plan_id"] == robustness["real_20_walk_forward"][
        "expected_plan_id"
    ], "real-20 Walk-Forward plan changed")
    require(real_plan["fold_count"] == 2 and legacy_plan["fold_count"] == 2, (
        "Walk-Forward folds changed"
    ))
    require(legacy_plan["status"] == "INSUFFICIENT_WALK_FORWARD_HISTORY", (
        "legacy Walk-Forward semantics changed"
    ))
    workload = workload_preflight(
        policy=loaded["workload"],
        candidate_count=48,
        fold_count=2,
        monte_carlo_paths=loaded["monte_carlo"].number_of_paths,
        path_length=loaded["monte_carlo"].path_length_sessions,
        sensitivity_scenarios=len(loaded["sensitivity"].perturbations),
        stress_scenarios=len(loaded["stress_v2"].scenarios),
        expected_cse_hits=0,
        expected_cse_builds=1632,
        expected_feature_hits=0,
        expected_feature_builds=1632,
    )
    require(workload["status"] == "READY", "48-candidate workload is not ready")
    require(
        precommitment["precommitment_integrity"]["real_results_observed"] is False,
        "precommitment claims observed real results",
    )
    return {
        "schema_version": "tenth-increment-phase4a-validation/v1",
        "status": "STATIC_PRECOMMITMENT_VALID",
        "candidate_count": 48,
        "dataset_id": dataset["dataset_id"],
        "research_protocol_id": research["expected_research_protocol_id"],
        "research_split_plan_id": research["expected_split_plan_id"],
        "real_20_walk_forward_plan_id": real_plan["walk_forward_plan_id"],
        "policy_ids": {
            "discovery_gate": discovery_gate.policy_id,
            "validation_gate": validation_gate.policy_id,
            "score": score_policy.score_policy_id,
            "ranking": ranking_policy.ranking_policy_id,
            "diversity": diversity_policy.diversity_policy_id,
            **{
                name: getattr(loaded[name], identity_property)
                for name, _, identity_property in policy_specs
            },
        },
        "workload": workload,
    }


def validate_local_artifacts(
    *,
    source_directory: Path,
    candidate_bundle: Path,
    candidate_cache: Path,
    market_history: Path,
    session_pack_directory: Path,
) -> dict[str, Any]:
    precommitment = load_json(PRECOMMITMENT)
    dataset = precommitment["dataset"]
    research = precommitment["research"]
    auto = precommitment["auto_discovery"]
    whitelist = load_whitelist(ROOT / dataset["whitelist_path"])
    source_manifest = load_json(ROOT / dataset["source_manifest_path"])
    expected_sources = {item["trading_date"]: item for item in source_manifest["sources"]}
    verified_sources = verify_sources(source_directory, whitelist, expected_sources)

    bundle = load_candidate_plan_bundle(candidate_bundle, cache_root=candidate_cache)
    require(bundle.plan.manifest.candidate_universe_id == auto["candidate_universe_id"], (
        "candidate bundle universe changed"
    ))
    require(list(bundle.plan.manifest.candidate_ids) == auto["candidate_ids"], (
        "candidate bundle members changed"
    ))
    require(bundle.plan.manifest.counts.U == 48, "candidate bundle size changed")

    freeze = load_json(ROOT / dataset["dataset_freeze_path"])
    require(sha256_file(market_history) == freeze["market_history_file_sha256"], (
        "market history artifact changed"
    ))
    catalog = SessionCatalog(load_json(market_history))
    require(catalog.manifest["dataset_id"] == dataset["dataset_id"], (
        "market history dataset changed"
    ))
    evaluation = load_contract(ROOT / research["evaluation_path"], EvaluationConfigV1)
    selected_catalog = catalog.select(evaluation.max_sessions)
    split, discovery, validation = create_split(
        selected_catalog,
        SplitPolicyV1.model_validate(load_json(ROOT / research["split_policy_path"])),
    )
    require(split["split_plan_id"] == research["expected_split_plan_id"], (
        "Research split identity changed"
    ))
    require(discovery["trading_dates"] == research["discovery_dates"], (
        "Discovery partition changed"
    ))
    require(validation["trading_dates"] == research["validation_dates"], (
        "Validation partition changed"
    ))
    protocol = composite_protocol_record(
        bundle.plan,
        split,
        GatePolicyV1.model_validate(load_json(ROOT / research["discovery_gate"]["path"])),
        GatePolicyV1.model_validate(load_json(ROOT / research["validation_gate"]["path"])),
        evaluation.canonicalized(),
    )
    require(protocol["research_protocol_id"] == research["expected_research_protocol_id"], (
        "Research v2 protocol identity changed"
    ))

    packs = sorted(session_pack_directory.glob("*.jsonl"))
    invalid_packs = []
    for pack in packs:
        digest = hashlib.sha256(pack.read_bytes()).hexdigest()
        if digest != pack.stem:
            invalid_packs.append(pack.name)
    require(not invalid_packs, "content-addressed CandidateSessionEvaluation cache is corrupt")
    return {
        "status": "LOCAL_INPUTS_VALID",
        "official_sources": len(verified_sources),
        "candidate_bundle_id": bundle.bundle_id,
        "candidate_universe_id": bundle.plan.manifest.candidate_universe_id,
        "research_protocol_id": protocol["research_protocol_id"],
        "candidate_session_evaluation_packs": len(packs),
        "candidate_session_evaluation_pack_hashes_valid": True,
        "real_backtest_executed": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-directory", type=Path, default=ROOT / "artifacts/ninth-increment/official-b3"
    )
    parser.add_argument(
        "--candidate-bundle",
        type=Path,
        default=(
            ROOT
            / "artifacts/tenth-increment/phase2-validation/acceptance-output"
            / "d0b0e4984e3a0cf0944eb787dbe5aaba2abae8949aa04c077b680056ed2e2666"
        ),
    )
    parser.add_argument(
        "--candidate-cache",
        type=Path,
        default=ROOT / "artifacts/tenth-increment/phase2-validation/acceptance-cache",
    )
    parser.add_argument(
        "--market-history",
        type=Path,
        default=ROOT / "artifacts/ninth-increment/acceptance-real-20-v3/market-history.json",
    )
    parser.add_argument(
        "--session-pack-directory",
        type=Path,
        default=ROOT / "artifacts/ninth-increment/cache-real-20/session-evaluations/packs",
    )
    args = parser.parse_args()
    report = {
        **validate_static(),
        "local_inputs": validate_local_artifacts(
            source_directory=args.source_directory,
            candidate_bundle=args.candidate_bundle,
            candidate_cache=args.candidate_cache,
            market_history=args.market_history,
            session_pack_directory=args.session_pack_directory,
        ),
    }
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
