"""End-to-end post-score robustness orchestration with granular invalidation."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from quantlab_core.canonical import write_canonical_json
from quantlab_core.errors import ContractError
from quantlab_data.session_catalog import SessionCatalog
from quantlab_mining.batch import ENGINE_VERSIONS
from quantlab_mining.contracts import EvaluationConfigV1, identity
from quantlab_research.session_cache import SessionEvaluationCache
from quantlab_scoring.contracts import ResearchDiversityPolicyV1
from quantlab_scoring.evidence import load_research_evidence

from quantlab_robustness import ROBUSTNESS_ENGINE_VERSION
from quantlab_robustness.assessment import assess_candidate
from quantlab_robustness.cache import RobustnessCheckpoint, RobustnessResultCache
from quantlab_robustness.candidate_set import build_candidate_set
from quantlab_robustness.contracts import (
    AuthorizedSessionUniverseV1,
    CandidateSelectionV1,
    ExecutionStressPolicyV1,
    MonteCarloPolicyV1,
    RobustnessGatePolicyV1,
    RobustnessWorkloadPolicyV1,
    SensitivityPolicyV1,
    SessionPoolSelectorV1,
    WalkForwardPolicyV1,
)
from quantlab_robustness.engines import (
    run_sensitivity_candidate,
    run_stress_candidate,
    run_walk_forward_candidate,
)
from quantlab_robustness.evaluator import CandidateSessionEvaluator
from quantlab_robustness.evidence import load_score_evidence
from quantlab_robustness.export import export_robustness
from quantlab_robustness.monte_carlo import evaluate_candidate_paths
from quantlab_robustness.protocol import protocol_record, workload_preflight
from quantlab_robustness.ranking import (
    diversify_qualified,
    qualified_ranking,
    qualified_set,
    robustness_top_n,
)
from quantlab_robustness.sampling import build_path_set, source_pool_record
from quantlab_robustness.sensitivity import build_sensitivity_scenarios
from quantlab_robustness.stress import resolve_stress_scenarios
from quantlab_robustness.walk_forward import build_walk_forward_plan


class ControlledRobustnessInterruption(ContractError):
    """Acceptance hook after a complete cached/checkpointed family-candidate unit."""


def _load_partition(directory: Path, partition_id: str | None = None) -> dict[str, Any]:
    candidates = []
    for name in ("discovery-partition.json", "validation-partition.json"):
        path = directory / name
        if path.exists():
            candidates.append(json.loads(path.read_text(encoding="utf-8")))
    if partition_id is None:
        validation = [item for item in candidates if "validation" in str(item).lower()]
        if validation:
            return validation[0]
        path = directory / "validation-partition.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        raise ContractError("Research Validation partition is unavailable")
    for item in candidates:
        if item.get("partition_id") == partition_id:
            return item
    raise ContractError("explicit Research partition was not found")


def _resolve_pool(
    *,
    selector: SessionPoolSelectorV1,
    catalog: SessionCatalog,
    authorized_ids: list[str],
    research_directory: Path,
    walk_forward_plan: dict[str, Any],
) -> dict[str, Any]:
    if selector.source == "RESEARCH_VALIDATION_PARTITION":
        session_ids = list(_load_partition(research_directory)["session_ids"])
    elif selector.source == "WALK_FORWARD_VALIDATION_UNION":
        session_ids = list(walk_forward_plan["overlap"]["unique_validation_session_ids"])
    elif selector.source == "EXPLICIT_RESEARCH_PARTITION":
        session_ids = list(
            _load_partition(research_directory, selector.partition_id)["session_ids"]
        )
    else:
        session_ids = list(selector.session_ids)
    allowed = set(authorized_ids)
    if not set(session_ids) <= allowed:
        raise ContractError("source pool contains a session outside the authorized universe")
    selected = [session for session in catalog.sessions if session.session_id in set(session_ids)]
    if [session.session_id for session in selected] != session_ids:
        raise ContractError("source pool session order is not the authorized chronological order")
    pool = source_pool_record(
        source=selector.model_dump(mode="json"),
        dataset_id=catalog.manifest["dataset_id"],
        sessions=[
            {"session_id": session.session_id, "trading_date": session.trading_date}
            for session in selected
        ],
    )
    pool["source_pool_id"] = identity(
        "robustness-session-pool/v1",
        {
            "source": selector.model_dump(mode="json"),
            "dataset_id": catalog.manifest["dataset_id"],
            "sessions": pool["sessions"],
        },
    )
    return pool


def run_robustness(
    *,
    research_directory: Path,
    score_directory: Path,
    catalog: SessionCatalog,
    authorized: AuthorizedSessionUniverseV1,
    baseline_evaluation: EvaluationConfigV1,
    candidate_selection: CandidateSelectionV1,
    walk_forward_policy: WalkForwardPolicyV1,
    monte_carlo_policy: MonteCarloPolicyV1,
    sensitivity_policy: SensitivityPolicyV1,
    stress_policy: ExecutionStressPolicyV1,
    workload_policy: RobustnessWorkloadPolicyV1,
    required_families: list[str],
    gate_policy: RobustnessGatePolicyV1 | None,
    diversity_policy: ResearchDiversityPolicyV1 | None,
    top_n: int | None,
    market_cache_root: Path,
    cache_root: Path,
    checkpoint_path: Path,
    output: Path,
    stop_after: int | None = None,
    observer: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    if output.exists():
        raise ContractError("refusing to overwrite completed robustness output")
    if authorized.dataset_id != catalog.manifest["dataset_id"]:
        raise ContractError("authorized session universe belongs to another dataset")
    by_id = {session.session_id: session for session in catalog.sessions}
    if any(sid not in by_id for sid in authorized.session_ids):
        raise ContractError("authorized session is absent from the supplied catalog")
    ordered = [
        session.session_id
        for session in catalog.sessions
        if session.session_id in set(authorized.session_ids)
    ]
    if ordered != list(authorized.session_ids):
        raise ContractError("authorized sessions must follow catalog chronology exactly")
    research = load_research_evidence(research_directory)
    scores = load_score_evidence(score_directory)
    candidate_set = build_candidate_set(scores, candidate_selection)
    candidate_ids = [item["candidate_id"] for item in candidate_set["candidates"]]
    if not set(candidate_ids) <= set(research.candidates):
        raise ContractError("score/research candidate mismatch")
    session_metadata = [
        {"session_id": by_id[sid].session_id, "trading_date": by_id[sid].trading_date}
        for sid in authorized.session_ids
    ]
    walk_plan = build_walk_forward_plan(
        dataset_id=authorized.dataset_id,
        logical_asset=catalog.manifest["logical_asset"],
        sessions=session_metadata,
        policy=walk_forward_policy,
    )
    monte_pool = _resolve_pool(
        selector=monte_carlo_policy.source_pool,
        catalog=catalog,
        authorized_ids=list(authorized.session_ids),
        research_directory=research_directory,
        walk_forward_plan=walk_plan,
    )
    sensitivity_pool = _resolve_pool(
        selector=sensitivity_policy.source_pool,
        catalog=catalog,
        authorized_ids=list(authorized.session_ids),
        research_directory=research_directory,
        walk_forward_plan=walk_plan,
    )
    stress_pool = _resolve_pool(
        selector=stress_policy.source_pool,
        catalog=catalog,
        authorized_ids=list(authorized.session_ids),
        research_directory=research_directory,
        walk_forward_plan=walk_plan,
    )
    path_set = build_path_set(monte_carlo_policy, monte_pool)
    protocol = protocol_record(
        candidate_set=candidate_set,
        research_export_id=research.manifest["export_id"],
        score_export_id=scores.manifest["score_export_id"],
        dataset_id=authorized.dataset_id,
        authorized_session_ids=list(authorized.session_ids),
        walk_forward=walk_forward_policy,
        monte_carlo=monte_carlo_policy,
        sensitivity=sensitivity_policy,
        stress=stress_policy,
        gate=gate_policy,
        required_families=required_families,
    )
    sensitivity_scenario_count = len(sensitivity_policy.perturbations)
    stress_scenarios = resolve_stress_scenarios(baseline_evaluation, stress_policy)
    valid_stress_count = sum(item["status"] == "VALID" for item in stress_scenarios)
    unique_variants = 0
    for candidate in candidate_ids:
        _, variants = build_sensitivity_scenarios(
            candidate, research.candidates[candidate], sensitivity_policy
        )
        unique_variants += len(variants)
    expected_builds = (
        len(candidate_ids) * len(authorized.session_ids)
        + unique_variants * len(sensitivity_pool["sessions"])
        + len(candidate_ids) * valid_stress_count * len(stress_pool["sessions"])
    )
    workload_plan = workload_preflight(
        policy=workload_policy,
        candidate_count=len(candidate_ids),
        fold_count=walk_plan["fold_count"],
        monte_carlo_paths=monte_carlo_policy.number_of_paths,
        path_length=monte_carlo_policy.path_length_sessions,
        sensitivity_scenarios=sensitivity_scenario_count,
        stress_scenarios=len(stress_policy.scenarios),
        expected_cse_hits=0,
        expected_cse_builds=expected_builds,
        expected_feature_hits=0,
        expected_feature_builds=expected_builds,
    )
    if observer:
        observer({"phase": "PREFLIGHT", **workload_plan["scientific_counts"]})
    result_cache = RobustnessResultCache(cache_root)
    checkpoint = RobustnessCheckpoint(checkpoint_path, protocol["robustness_protocol_id"])
    session_cache = SessionEvaluationCache(cache_root)
    evaluator = CandidateSessionEvaluator(
        catalog=catalog,
        market_cache_root=market_cache_root,
        session_cache=session_cache,
    )
    built = reused = processed = 0

    def cached(kind: str, candidate: str, inputs: dict[str, Any], builder):
        nonlocal built, reused, processed
        input_id = identity(f"robustness-{kind}-input/v1", inputs)
        record = result_cache.get(input_id, kind)
        was_reused = record is not None
        if record is None:
            record = builder()
            result_cache.put(input_id, kind, record)
            built += 1
        else:
            reused += 1
        unit_key = f"{kind}:{candidate}"
        if not checkpoint.completed(unit_key, input_id, record):
            checkpoint.mark(unit_key, input_id, record)
        processed += 1
        if observer:
            observer(
                {
                    "phase": "FAMILY_CANDIDATE_COMPLETED",
                    "family": kind,
                    "candidate_id": candidate,
                    "reused": was_reused,
                }
            )
        if stop_after is not None and processed >= stop_after:
            raise ControlledRobustnessInterruption(
                f"controlled robustness interruption after {processed} complete units"
            )
        return record

    walk_results = []
    monte_results = []
    sensitivity_results = []
    stress_results = []
    try:
        for candidate in candidate_ids:
            strategy = research.candidates[candidate]
            common = {
                "candidate_id": candidate,
                "dataset_id": authorized.dataset_id,
                "evaluation": baseline_evaluation.canonicalized().model_dump(mode="json"),
                "engine_versions": {
                    **ENGINE_VERSIONS,
                    "robustness": ROBUSTNESS_ENGINE_VERSION,
                },
            }
            walk_results.append(
                cached(
                    "walk-forward",
                    candidate,
                    {
                        **common,
                        "policy_id": walk_forward_policy.policy_id,
                        "plan_id": walk_plan["walk_forward_plan_id"],
                    },
                    lambda candidate=candidate, strategy=strategy: run_walk_forward_candidate(
                        candidate_id=candidate,
                        strategy=strategy,
                        evaluation=baseline_evaluation,
                        policy=walk_forward_policy,
                        plan=walk_plan,
                        evaluator=evaluator,
                    ),
                )
            )
            monte_records = evaluator.evaluate(
                candidate_id=candidate,
                strategy=strategy,
                evaluation=baseline_evaluation,
                session_ids=[item["session_id"] for item in monte_pool["sessions"]],
            )
            monte_results.append(
                cached(
                    "monte-carlo",
                    candidate,
                    {
                        **common,
                        "policy_id": monte_carlo_policy.policy_id,
                        "path_set_id": path_set.get("monte_carlo_path_set_id"),
                        "source_fingerprints": sorted(
                            record["result_fingerprint"] for record in monte_records.values()
                        ),
                    },
                    lambda candidate=candidate, strategy=strategy, records=monte_records: (
                        evaluate_candidate_paths(
                            candidate_id=candidate,
                            strategy=strategy,
                            policy=monte_carlo_policy,
                            path_set=path_set,
                            session_records=records,
                            ledger_loader=lambda eid: session_cache.audit(eid, "ledger"),
                        )
                    ),
                )
            )
            sensitivity_results.append(
                cached(
                    "sensitivity",
                    candidate,
                    {
                        **common,
                        "policy_id": sensitivity_policy.policy_id,
                        "source_pool_id": sensitivity_pool["source_pool_id"],
                    },
                    lambda candidate=candidate, strategy=strategy: run_sensitivity_candidate(
                        candidate_id=candidate,
                        strategy=strategy,
                        evaluation=baseline_evaluation,
                        policy=sensitivity_policy,
                        source_pool=sensitivity_pool,
                        evaluator=evaluator,
                    ),
                )
            )
            stress_results.append(
                cached(
                    "stress",
                    candidate,
                    {
                        **common,
                        "policy_id": stress_policy.policy_id,
                        "source_pool_id": stress_pool["source_pool_id"],
                    },
                    lambda candidate=candidate, strategy=strategy: run_stress_candidate(
                        candidate_id=candidate,
                        strategy=strategy,
                        baseline_evaluation=baseline_evaluation,
                        policy=stress_policy,
                        source_pool=stress_pool,
                        evaluator=evaluator,
                    ),
                )
            )
        score_by_candidate = {item["candidate_id"]: item for item in candidate_set["candidates"]}
        family_by_candidate = {
            candidate: {
                "WALK_FORWARD": next(
                    item for item in walk_results if item["candidate_id"] == candidate
                ),
                "MONTE_CARLO": next(
                    item for item in monte_results if item["candidate_id"] == candidate
                ),
                "SENSITIVITY": next(
                    item for item in sensitivity_results if item["candidate_id"] == candidate
                ),
                "STRESS": next(
                    item for item in stress_results if item["candidate_id"] == candidate
                ),
            }
            for candidate in candidate_ids
        }
        assessments = [
            assess_candidate(
                candidate_id=candidate,
                strategy_score_id=score_by_candidate[candidate]["strategy_score_id"],
                robustness_protocol_id=protocol["robustness_protocol_id"],
                family_results=family_by_candidate[candidate],
                gate_policy=gate_policy,
            )
            for candidate in candidate_ids
        ]
        qualified = ranking = diversified = shortlist = None
        if gate_policy is not None:
            qualified = qualified_set(assessments, gate_policy.policy_id)
            ranking = qualified_ranking(list(scores.raw), qualified)
            if diversity_policy is not None:
                diversified = diversify_qualified(ranking, diversity_policy)
                if top_n is not None:
                    shortlist = robustness_top_n(diversified, top_n)
        export_records = {
            "candidate_set": candidate_set,
            "protocol": protocol,
            "workload_policy": {
                **workload_policy.model_dump(mode="json"),
                "workload_policy_id": workload_policy.policy_id,
            },
            "workload_plan": workload_plan,
            "walk_forward_policy": {
                **walk_forward_policy.model_dump(mode="json"),
                "walk_forward_policy_id": walk_forward_policy.policy_id,
            },
            "walk_forward_plan": walk_plan,
            "walk_forward_results": sorted(walk_results, key=lambda item: item["candidate_id"]),
            "monte_carlo_policy": {
                **monte_carlo_policy.model_dump(mode="json"),
                "monte_carlo_policy_id": monte_carlo_policy.policy_id,
            },
            "monte_carlo_source_pool": monte_pool,
            "monte_carlo_path_set": path_set,
            "monte_carlo_results": sorted(monte_results, key=lambda item: item["candidate_id"]),
            "sensitivity_policy": {
                **sensitivity_policy.model_dump(mode="json"),
                "sensitivity_policy_id": sensitivity_policy.policy_id,
            },
            "sensitivity_source_pool": sensitivity_pool,
            "sensitivity_results": sorted(
                sensitivity_results, key=lambda item: item["candidate_id"]
            ),
            "stress_policy": {
                **stress_policy.model_dump(mode="json"),
                "stress_policy_id": stress_policy.policy_id,
            },
            "stress_source_pool": stress_pool,
            "stress_results": sorted(stress_results, key=lambda item: item["candidate_id"]),
            "gate_policy": None
            if gate_policy is None
            else {
                **gate_policy.model_dump(mode="json"),
                "robustness_gate_policy_id": gate_policy.policy_id,
            },
            "assessments": sorted(assessments, key=lambda item: item["candidate_id"]),
            "qualified_set": qualified,
            "ranking": ranking,
            "diversified": diversified,
            "shortlist": shortlist,
        }
        manifest = export_robustness(output, export_records)
        operational = {
            "schema_version": "robustness-operational/v1",
            "robustness_export_id": manifest["robustness_export_id"],
            "family_results_built": built,
            "family_results_reused": reused,
            **evaluator.report,
            "elapsed_seconds": str(time.perf_counter() - started),
            "scientific_identity_excludes_this_record": True,
        }
        write_canonical_json(output.parent / (output.name + ".operational.json"), operational)
        return manifest
    finally:
        session_cache.close()
        checkpoint.close()
        result_cache.close()
