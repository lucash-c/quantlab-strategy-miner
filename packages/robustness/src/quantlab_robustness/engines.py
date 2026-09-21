"""Family engines over immutable CandidateSessionEvaluations."""

from __future__ import annotations

from typing import Any

from quantlab_core.strategy_v3 import StrategyDefinitionV3
from quantlab_mining.contracts import EvaluationConfigV1, identity
from quantlab_research.comparison import compare as compare_partitions
from quantlab_research.gates import apply_gate

from quantlab_robustness.contracts import (
    ExecutionStressPolicyV1,
    SensitivityPolicyV1,
    WalkForwardPolicyV1,
)
from quantlab_robustness.evaluator import CandidateSessionEvaluator
from quantlab_robustness.sensitivity import (
    build_sensitivity_scenarios,
    compare_sensitivity_metrics,
    summarize_sensitivity,
)
from quantlab_robustness.stress import (
    compare_stress_metrics,
    resolve_stress_scenarios,
    summarize_stress,
)
from quantlab_robustness.walk_forward import summarize_walk_forward


def run_walk_forward_candidate(
    *,
    candidate_id: str,
    strategy: StrategyDefinitionV3,
    evaluation: EvaluationConfigV1,
    policy: WalkForwardPolicyV1,
    plan: dict[str, Any],
    evaluator: CandidateSessionEvaluator,
) -> dict[str, Any]:
    if plan["status"] != "READY":
        record = {
            "schema_version": "candidate-walk-forward-result/v1",
            "candidate_id": candidate_id,
            "walk_forward_policy_id": policy.policy_id,
            "walk_forward_plan_id": plan["walk_forward_plan_id"],
            "status": "INSUFFICIENT_EVIDENCE",
            "reason": "INSUFFICIENT_WALK_FORWARD_HISTORY",
            "folds": [],
            "metrics": {},
        }
        record["walk_forward_result_id"] = identity("candidate-walk-forward-result/v1", record)
        return record
    session_ids = [item["session_id"] for item in plan["authorized_sessions"]]
    session_records = evaluator.evaluate(
        candidate_id=candidate_id,
        strategy=strategy,
        evaluation=evaluation,
        session_ids=session_ids,
    )
    folds = []
    for fold in plan["folds"]:
        discovery_partition_id = identity(
            "walk-forward-partition/v1",
            {
                "walk_forward_fold_id": fold["walk_forward_fold_id"],
                "role": "DISCOVERY",
                "session_ids": fold["discovery_session_ids"],
            },
        )
        validation_partition_id = identity(
            "walk-forward-partition/v1",
            {
                "walk_forward_fold_id": fold["walk_forward_fold_id"],
                "role": "VALIDATION",
                "session_ids": fold["validation_session_ids"],
            },
        )
        discovery = evaluator.aggregate(
            candidate_id=candidate_id,
            strategy=strategy,
            evaluation=evaluation,
            partition_id=discovery_partition_id,
            session_ids=fold["discovery_session_ids"],
            records=session_records,
        )
        # Validation is unconditional: fold gates are diagnostics, not access controls.
        validation = evaluator.aggregate(
            candidate_id=candidate_id,
            strategy=strategy,
            evaluation=evaluation,
            partition_id=validation_partition_id,
            session_ids=fold["validation_session_ids"],
            records=session_records,
        )
        comparison = compare_partitions(candidate_id, discovery, validation)
        discovery_gate = (
            apply_gate(
                candidate_id,
                discovery["partition_evaluation_id"],
                discovery["metrics"],
                policy.discovery_gate,
            )
            if policy.discovery_gate
            else None
        )
        validation_gate = (
            apply_gate(
                candidate_id,
                validation["partition_evaluation_id"],
                {**validation["metrics"], **comparison["metrics"]},
                policy.validation_gate,
            )
            if policy.validation_gate
            else None
        )
        result = {
            "schema_version": "walk-forward-candidate-fold-result/v1",
            "candidate_id": candidate_id,
            "walk_forward_fold_id": fold["walk_forward_fold_id"],
            "status": "COMPLETED",
            "discovery": discovery,
            "validation": validation,
            "comparison": comparison,
            "discovery_gate": discovery_gate,
            "validation_gate": validation_gate,
            "discovery_gate_result": "NOT_APPLICABLE"
            if discovery_gate is None
            else discovery_gate["result"],
            "validation_gate_result": "NOT_APPLICABLE"
            if validation_gate is None
            else validation_gate["result"],
            "validation_evaluated_regardless_of_discovery_gate": True,
        }
        result["walk_forward_candidate_fold_result_id"] = identity(
            "walk-forward-candidate-fold-result/v1", result
        )
        folds.append(result)
    summary = summarize_walk_forward(folds, plan)
    record = {
        "schema_version": "candidate-walk-forward-result/v1",
        "candidate_id": candidate_id,
        "walk_forward_policy_id": policy.policy_id,
        "walk_forward_plan_id": plan["walk_forward_plan_id"],
        "status": summary["status"],
        "reason": summary["reason"],
        "folds": folds,
        "metrics": summary["metrics"],
        "overlap": plan["overlap"],
    }
    record["walk_forward_result_id"] = identity("candidate-walk-forward-result/v1", record)
    return record


def run_sensitivity_candidate(
    *,
    candidate_id: str,
    strategy: StrategyDefinitionV3,
    evaluation: EvaluationConfigV1,
    policy: SensitivityPolicyV1,
    source_pool: dict[str, Any],
    evaluator: CandidateSessionEvaluator,
) -> dict[str, Any]:
    source_pool_id = source_pool.get(
        "source_pool_id", source_pool.get("monte_carlo_source_pool_id")
    )
    session_ids = [item["session_id"] for item in source_pool["sessions"]]
    baseline_records = evaluator.evaluate(
        candidate_id=candidate_id,
        strategy=strategy,
        evaluation=evaluation,
        session_ids=session_ids,
    )
    baseline_partition = identity(
        "sensitivity-partition/v1",
        {"source_pool_id": source_pool_id, "role": "BASELINE"},
    )
    baseline = evaluator.aggregate(
        candidate_id=candidate_id,
        strategy=strategy,
        evaluation=evaluation,
        partition_id=baseline_partition,
        session_ids=session_ids,
        records=baseline_records,
    )
    scenarios, variants = build_sensitivity_scenarios(candidate_id, strategy, policy)
    evaluated_variants = {}
    for variant_id, variant in sorted(variants.items()):
        records = evaluator.evaluate(
            candidate_id=variant_id,
            strategy=variant,
            evaluation=evaluation,
            session_ids=session_ids,
        )
        evaluated_variants[variant_id] = evaluator.aggregate(
            candidate_id=variant_id,
            strategy=variant,
            evaluation=evaluation,
            partition_id=identity(
                "sensitivity-partition/v1",
                {"source_pool_id": source_pool_id, "variant_candidate_id": variant_id},
            ),
            session_ids=session_ids,
            records=records,
        )
    results = []
    for scenario in scenarios:
        if scenario["status"] != "VALID":
            continue
        variant = evaluated_variants[scenario["variant_candidate_id"]]
        gate = (
            apply_gate(
                scenario["variant_candidate_id"],
                variant["partition_evaluation_id"],
                variant["metrics"],
                policy.validation_gate,
            )
            if policy.validation_gate
            else None
        )
        result = {
            "sensitivity_scenario_id": scenario["sensitivity_scenario_id"],
            "variant_candidate_id": scenario["variant_candidate_id"],
            "baseline_partition_evaluation_id": baseline["partition_evaluation_id"],
            "variant_partition_evaluation_id": variant["partition_evaluation_id"],
            "comparison_metrics": compare_sensitivity_metrics(baseline, variant),
            "validation_gate": gate,
            "promoted": False,
            "original_candidate_changed": False,
            "research_score_changed": False,
        }
        result["sensitivity_scenario_result_id"] = identity(
            "sensitivity-scenario-result/v1", result
        )
        results.append(result)
    summary = summarize_sensitivity(policy=policy, scenarios=scenarios, results=results)
    record = {
        "schema_version": "candidate-sensitivity-result/v1",
        "candidate_id": candidate_id,
        "sensitivity_policy_id": policy.policy_id,
        "source_pool_id": source_pool_id,
        "status": summary["status"],
        "reason": summary["reason"],
        "scenarios": scenarios,
        "results": results,
        "metrics": summary["metrics"],
        "unique_quantitative_variant_evaluations": len(evaluated_variants),
    }
    record["sensitivity_result_id"] = identity("candidate-sensitivity-result/v1", record)
    return record


def run_stress_candidate(
    *,
    candidate_id: str,
    strategy: StrategyDefinitionV3,
    baseline_evaluation: EvaluationConfigV1,
    policy: ExecutionStressPolicyV1,
    source_pool: dict[str, Any],
    evaluator: CandidateSessionEvaluator,
) -> dict[str, Any]:
    source_pool_id = source_pool.get(
        "source_pool_id", source_pool.get("monte_carlo_source_pool_id")
    )
    session_ids = [item["session_id"] for item in source_pool["sessions"]]
    baseline_records = evaluator.evaluate(
        candidate_id=candidate_id,
        strategy=strategy,
        evaluation=baseline_evaluation,
        session_ids=session_ids,
    )
    baseline = evaluator.aggregate(
        candidate_id=candidate_id,
        strategy=strategy,
        evaluation=baseline_evaluation,
        partition_id=identity(
            "stress-partition/v1",
            {"source_pool_id": source_pool_id, "role": "BASELINE"},
        ),
        session_ids=session_ids,
        records=baseline_records,
    )
    baseline_gate = (
        apply_gate(
            candidate_id,
            baseline["partition_evaluation_id"],
            baseline["metrics"],
            policy.validation_gate,
        )
        if policy.validation_gate
        else None
    )
    scenarios = resolve_stress_scenarios(baseline_evaluation, policy)
    results = []
    for scenario in scenarios:
        if scenario["status"] != "VALID":
            continue
        config = EvaluationConfigV1.model_validate(scenario["resolved_evaluation_config"])
        stressed_records = evaluator.evaluate(
            candidate_id=candidate_id,
            strategy=strategy,
            evaluation=config,
            session_ids=session_ids,
        )
        stressed = evaluator.aggregate(
            candidate_id=candidate_id,
            strategy=strategy,
            evaluation=config,
            partition_id=identity(
                "stress-partition/v1",
                {
                    "source_pool_id": source_pool_id,
                    "stress_scenario_id": scenario["stress_scenario_id"],
                },
            ),
            session_ids=session_ids,
            records=stressed_records,
        )
        stressed_gate = (
            apply_gate(
                candidate_id,
                stressed["partition_evaluation_id"],
                stressed["metrics"],
                policy.validation_gate,
            )
            if policy.validation_gate
            else None
        )
        transition = (
            "NOT_APPLICABLE"
            if baseline_gate is None
            else f"{baseline_gate['result']}_TO_{stressed_gate['result']}"
        )
        result = {
            "stress_scenario_id": scenario["stress_scenario_id"],
            "baseline_partition_evaluation_id": baseline["partition_evaluation_id"],
            "stress_partition_evaluation_id": stressed["partition_evaluation_id"],
            "baseline_gate": baseline_gate,
            "stress_gate": stressed_gate,
            "gate_transition": transition,
            "comparison_metrics": compare_stress_metrics(baseline, stressed),
            "rebacktested": True,
            "candidate_id_unchanged": True,
            "baseline_ledger_fingerprint": baseline["ledger_fingerprint"],
            "stress_ledger_fingerprint": stressed["ledger_fingerprint"],
        }
        result["stress_scenario_result_id"] = identity("stress-scenario-result/v1", result)
        results.append(result)
    summary = summarize_stress(results, scenarios)
    record = {
        "schema_version": "candidate-stress-result/v1",
        "candidate_id": candidate_id,
        "stress_policy_id": policy.policy_id,
        "source_pool_id": source_pool_id,
        "status": summary["status"],
        "reason": summary["reason"],
        "scenarios": scenarios,
        "results": results,
        "metrics": summary["metrics"],
    }
    record["stress_result_id"] = identity("candidate-stress-result/v1", record)
    return record
