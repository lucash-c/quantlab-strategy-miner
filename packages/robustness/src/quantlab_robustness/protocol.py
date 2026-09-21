"""Scientific protocol and separate operational workload preflight."""

from __future__ import annotations

from typing import Any

from quantlab_core.errors import ContractError
from quantlab_core.feature_engine_v2 import mathematical_policy_record
from quantlab_mining.batch import ENGINE_VERSIONS
from quantlab_mining.contracts import identity

from quantlab_robustness import ROBUSTNESS_ENGINE_VERSION
from quantlab_robustness.contracts import (
    ExecutionStressPolicyV1,
    MonteCarloPolicyV1,
    RobustnessGatePolicyV1,
    RobustnessWorkloadPolicyV1,
    SensitivityPolicyV1,
    WalkForwardPolicyV1,
)


def protocol_record(
    *,
    candidate_set: dict[str, Any],
    research_export_id: str,
    score_export_id: str,
    dataset_id: str,
    authorized_session_ids: list[str],
    walk_forward: WalkForwardPolicyV1,
    monte_carlo: MonteCarloPolicyV1,
    sensitivity: SensitivityPolicyV1,
    stress: ExecutionStressPolicyV1,
    gate: RobustnessGatePolicyV1 | None,
    required_families: list[str],
) -> dict[str, Any]:
    if len(set(required_families)) != len(required_families) or not required_families:
        raise ContractError("required_families must be explicit and unique")
    allowed = {"WALK_FORWARD", "MONTE_CARLO", "SENSITIVITY", "STRESS"}
    if not set(required_families) <= allowed:
        raise ContractError("unknown required robustness family")
    if gate is not None and set(gate.required_families) != set(required_families):
        raise ContractError("protocol/gate required_families mismatch")
    record = {
        "schema_version": "robustness-protocol/v1",
        "robustness_candidate_set_id": candidate_set["robustness_candidate_set_id"],
        "research_export_id": research_export_id,
        "score_export_id": score_export_id,
        "dataset_id": dataset_id,
        "authorized_session_ids": authorized_session_ids,
        "required_families": sorted(required_families),
        "walk_forward_policy_id": walk_forward.policy_id,
        "monte_carlo_policy_id": monte_carlo.policy_id,
        "sensitivity_policy_id": sensitivity.policy_id,
        "stress_policy_id": stress.policy_id,
        "robustness_gate_policy_id": None if gate is None else gate.policy_id,
        "engine_versions": {**ENGINE_VERSIONS, "robustness": ROBUSTNESS_ENGINE_VERSION},
        "mathematical_policy": mathematical_policy_record(),
        "budgets_excluded": True,
    }
    record["robustness_protocol_id"] = identity("robustness-protocol/v1", record)
    return record


def workload_preflight(
    *,
    policy: RobustnessWorkloadPolicyV1,
    candidate_count: int,
    fold_count: int,
    monte_carlo_paths: int,
    path_length: int,
    sensitivity_scenarios: int,
    stress_scenarios: int,
    expected_cse_hits: int,
    expected_cse_builds: int,
    expected_feature_hits: int,
    expected_feature_builds: int,
) -> dict[str, Any]:
    # Path definitions are sampled once and then shared by every candidate.
    sampled_blocks = monte_carlo_paths * path_length
    combinations = candidate_count * (sensitivity_scenarios + stress_scenarios)
    counts = {
        "candidates": candidate_count,
        "walk_forward_fold_count": fold_count,
        "monte_carlo_paths": monte_carlo_paths,
        "monte_carlo_sampled_blocks": sampled_blocks,
        "sensitivity_requested_scenarios": sensitivity_scenarios,
        "stress_scenarios": stress_scenarios,
        "candidate_scenario_combinations": combinations,
    }
    checks = {
        "candidates": (candidate_count, policy.max_candidates),
        "walk_forward_folds": (fold_count, policy.max_walk_forward_folds),
        "paths": (monte_carlo_paths, policy.max_paths),
        "path_length": (path_length, policy.max_path_length_sessions),
        "sampled_blocks": (sampled_blocks, policy.max_total_sampled_blocks),
        "sensitivity_scenarios": (sensitivity_scenarios, policy.max_sensitivity_scenarios),
        "stress_scenarios": (stress_scenarios, policy.max_stress_scenarios),
        "candidate_scenario_combinations": (
            combinations,
            policy.max_candidate_scenario_combinations,
        ),
        "expected_cse_builds": (expected_cse_builds, policy.max_expected_cse_builds),
    }
    exceeded = [
        {"dimension": name, "requested": requested, "maximum": maximum}
        for name, (requested, maximum) in checks.items()
        if requested > maximum
    ]
    record = {
        "schema_version": "robustness-workload-plan/v1",
        "workload_policy_id": policy.policy_id,
        "scientific_counts": counts,
        "operational_estimates": {
            "expected_cse_hits": expected_cse_hits,
            "expected_cse_builds": expected_cse_builds,
            "expected_feature_hits": expected_feature_hits,
            "expected_feature_builds": expected_feature_builds,
            "excluded_from_scientific_fingerprints": True,
        },
        "status": "WORKLOAD_EXCEEDED" if exceeded else "READY",
        "exceeded": exceeded,
    }
    record["workload_plan_id"] = identity("robustness-workload-plan/v1", record)
    if exceeded:
        raise ContractError(
            "ROBUSTNESS_WORKLOAD_EXCEEDED: " + ",".join(item["dimension"] for item in exceeded)
        )
    return record
