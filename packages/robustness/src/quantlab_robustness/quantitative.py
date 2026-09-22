"""Additive quantitative verdict over immutable Robustness v1 facts.

This module deliberately does not change ``ROBUSTNESS_PASSED``.  It produces a
second, versioned verdict from exact metrics and additional raw sensitivity / stress
evidence that the eighth-increment artifacts did not persist.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from quantlab_core.numeric import CanonicalRational
from quantlab_core.strategy_v3 import StrategyDefinitionV3
from quantlab_mining.contracts import EvaluationConfigV1, identity
from quantlab_scoring.contracts import ResearchDiversityPolicyV1

from quantlab_robustness.contracts import (
    ExecutionStressPolicyV1,
    QuantitativeThresholdV1,
    RobustnessQuantitativePolicyV1,
    SensitivityPolicyV1,
)
from quantlab_robustness.evaluator import CandidateSessionEvaluator
from quantlab_robustness.exact import compare, defined, divide, exact_median, rational, undefined
from quantlab_robustness.sensitivity import build_sensitivity_scenarios
from quantlab_robustness.stress import resolve_stress_scenarios

ROBUSTNESS_QUANTITATIVE_ENGINE_VERSION = "1.0.0"
_METRIC_NAMES = ("trades", "net_pnl", "average_trade", "win_rate", "max_drawdown")


def _metrics(record: dict[str, Any]) -> dict[str, Any]:
    return {name: record["metrics"][name] for name in _METRIC_NAMES}


def build_quantitative_evidence(
    *,
    candidate_id: str,
    strategy: StrategyDefinitionV3,
    baseline_evaluation: EvaluationConfigV1,
    sensitivity_policy: SensitivityPolicyV1,
    sensitivity_pool: dict[str, Any],
    stress_policy: ExecutionStressPolicyV1,
    stress_pool: dict[str, Any],
    family_results: dict[str, dict[str, Any]],
    evaluator: CandidateSessionEvaluator,
) -> dict[str, Any]:
    """Materialize raw metrics without mutating legacy family result schemas."""

    sensitivity_ids = [item["session_id"] for item in sensitivity_pool["sessions"]]
    baseline_records = evaluator.evaluate(
        candidate_id=candidate_id,
        strategy=strategy,
        evaluation=baseline_evaluation,
        session_ids=sensitivity_ids,
    )
    sensitivity_baseline = evaluator.aggregate(
        candidate_id=candidate_id,
        strategy=strategy,
        evaluation=baseline_evaluation,
        partition_id=identity(
            "robustness-quantitative-sensitivity-partition/v1",
            {"source_pool_id": sensitivity_pool["source_pool_id"], "role": "BASELINE"},
        ),
        session_ids=sensitivity_ids,
        records=baseline_records,
    )
    sensitivity_scenarios, variants = build_sensitivity_scenarios(
        candidate_id, strategy, sensitivity_policy
    )
    sensitivity_rows = []
    for scenario in sensitivity_scenarios:
        row = {
            "sensitivity_scenario_id": scenario["sensitivity_scenario_id"],
            "status": scenario["status"],
            "reason": scenario["reason"],
            "variant_candidate_id": scenario["variant_candidate_id"],
        }
        if scenario["status"] == "VALID":
            variant_id = scenario["variant_candidate_id"]
            variant = variants[variant_id]
            records = evaluator.evaluate(
                candidate_id=variant_id,
                strategy=variant,
                evaluation=baseline_evaluation,
                session_ids=sensitivity_ids,
            )
            aggregate = evaluator.aggregate(
                candidate_id=variant_id,
                strategy=variant,
                evaluation=baseline_evaluation,
                partition_id=identity(
                    "robustness-quantitative-sensitivity-partition/v1",
                    {
                        "source_pool_id": sensitivity_pool["source_pool_id"],
                        "variant_candidate_id": variant_id,
                    },
                ),
                session_ids=sensitivity_ids,
                records=records,
            )
            row.update(
                partition_evaluation_id=aggregate["partition_evaluation_id"],
                metrics=_metrics(aggregate),
                promoted=False,
                original_candidate_changed=False,
                research_score_changed=False,
            )
        sensitivity_rows.append(row)

    stress_ids = [item["session_id"] for item in stress_pool["sessions"]]
    stress_baseline_records = evaluator.evaluate(
        candidate_id=candidate_id,
        strategy=strategy,
        evaluation=baseline_evaluation,
        session_ids=stress_ids,
    )
    stress_baseline = evaluator.aggregate(
        candidate_id=candidate_id,
        strategy=strategy,
        evaluation=baseline_evaluation,
        partition_id=identity(
            "robustness-quantitative-stress-partition/v1",
            {"source_pool_id": stress_pool["source_pool_id"], "role": "BASELINE"},
        ),
        session_ids=stress_ids,
        records=stress_baseline_records,
    )
    stress_rows = []
    for scenario in resolve_stress_scenarios(baseline_evaluation, stress_policy):
        row = {
            "stress_scenario_id": scenario["stress_scenario_id"],
            "status": scenario["status"],
            "reason": scenario["reason"],
        }
        if scenario["status"] == "VALID":
            stressed_evaluation = EvaluationConfigV1.model_validate(
                scenario["resolved_evaluation_config"]
            )
            records = evaluator.evaluate(
                candidate_id=candidate_id,
                strategy=strategy,
                evaluation=stressed_evaluation,
                session_ids=stress_ids,
            )
            aggregate = evaluator.aggregate(
                candidate_id=candidate_id,
                strategy=strategy,
                evaluation=stressed_evaluation,
                partition_id=identity(
                    "robustness-quantitative-stress-partition/v1",
                    {
                        "source_pool_id": stress_pool["source_pool_id"],
                        "stress_scenario_id": scenario["stress_scenario_id"],
                    },
                ),
                session_ids=stress_ids,
                records=records,
            )
            row.update(
                partition_evaluation_id=aggregate["partition_evaluation_id"],
                metrics=_metrics(aggregate),
                rebacktested=True,
                candidate_id_unchanged=True,
            )
        stress_rows.append(row)

    record = {
        "schema_version": "candidate-robustness-quantitative-evidence/v1",
        "candidate_id": candidate_id,
        "engine_version": ROBUSTNESS_QUANTITATIVE_ENGINE_VERSION,
        "family_result_ids": {
            "walk_forward": family_results["WALK_FORWARD"]["walk_forward_result_id"],
            "monte_carlo": family_results["MONTE_CARLO"]["monte_carlo_result_id"],
            "sensitivity": family_results["SENSITIVITY"]["sensitivity_result_id"],
            "stress": family_results["STRESS"]["stress_result_id"],
        },
        "walk_forward_metrics": family_results["WALK_FORWARD"]["metrics"],
        "monte_carlo_metrics": family_results["MONTE_CARLO"]["metrics"],
        "sensitivity": {
            "source_pool_id": sensitivity_pool["source_pool_id"],
            "baseline_partition_evaluation_id": sensitivity_baseline[
                "partition_evaluation_id"
            ],
            "baseline_metrics": _metrics(sensitivity_baseline),
            "scenarios": sensitivity_rows,
        },
        "stress": {
            "source_pool_id": stress_pool["source_pool_id"],
            "baseline_partition_evaluation_id": stress_baseline["partition_evaluation_id"],
            "baseline_metrics": _metrics(stress_baseline),
            "scenarios": stress_rows,
        },
    }
    record["quantitative_evidence_id"] = identity(
        "candidate-robustness-quantitative-evidence/v1", record
    )
    return record


def _criterion(
    *,
    family: str,
    metric: str,
    observed: dict[str, Any],
    rule: QuantitativeThresholdV1,
    source_id: str | None = None,
) -> dict[str, Any]:
    base = {
        "family": family,
        "metric": metric,
        "source_id": source_id,
        "operator": rule.operator,
        "threshold": rule.threshold.model_dump(mode="json"),
        "observed_value": observed,
    }
    if observed["status"] != "DEFINED":
        return {
            **base,
            "result": "INSUFFICIENT_EVIDENCE",
            "reason": observed.get("reason", "UNDEFINED_METRIC"),
        }
    passed = compare(rational(observed["value"]), rule.operator, rule.threshold.value())
    return {
        **base,
        "result": "PASSED" if passed else "FAILED",
        "reason": None if passed else "THRESHOLD_NOT_MET",
    }


def _unavailable(family: str, metric: str, reason: str, source_id: str | None = None) -> dict:
    return {
        "family": family,
        "metric": metric,
        "source_id": source_id,
        "operator": None,
        "threshold": None,
        "observed_value": undefined(reason, status="INSUFFICIENT_EVIDENCE"),
        "result": "INSUFFICIENT_EVIDENCE",
        "reason": reason,
    }


def _diagnostics(
    names: tuple[str, ...], monte_carlo: dict[str, Any], stress: dict[str, Any]
) -> dict[str, Any]:
    records: dict[str, Any] = {}
    for name in names:
        if name.startswith("monte_carlo."):
            records[name] = monte_carlo.get(name.split(".", 1)[1])
        elif name == "stress.win_rate":
            records[name] = [
                {
                    "stress_scenario_id": row["stress_scenario_id"],
                    "status": row["status"],
                    "reason": row["reason"],
                    "win_rate": row.get("metrics", {}).get("win_rate"),
                }
                for row in stress["scenarios"]
            ]
        else:
            records[name] = None
    return records


def assess_quantitative_candidate(
    *,
    candidate_id: str,
    strategy_score_id: str,
    robustness_protocol_id: str,
    technical_assessment: dict[str, Any],
    evidence: dict[str, Any],
    policy: RobustnessQuantitativePolicyV1,
) -> dict[str, Any]:
    criteria: list[dict[str, Any]] = []
    technical_status = technical_assessment["status"]
    if technical_status == "ROBUSTNESS_PASSED":
        technical_result = "PASSED"
        technical_reason = None
    elif technical_status == "ROBUSTNESS_INSUFFICIENT_EVIDENCE":
        technical_result = "INSUFFICIENT_EVIDENCE"
        technical_reason = "TECHNICAL_EVIDENCE_INSUFFICIENT"
    elif technical_status == "ROBUSTNESS_FAILED":
        technical_result = "FAILED"
        technical_reason = "TECHNICAL_COMPLETENESS_FAILED"
    else:
        technical_result = "INSUFFICIENT_EVIDENCE"
        technical_reason = "TECHNICAL_GATE_NOT_CONFIGURED"
    criteria.append(
        {
            "family": "TECHNICAL",
            "metric": "technical_robustness_status",
            "source_id": technical_assessment["robustness_assessment_id"],
            "operator": "EQ",
            "threshold": policy.technical_requirement,
            "observed_value": technical_status,
            "result": technical_result,
            "reason": technical_reason,
        }
    )

    walk = evidence["walk_forward_metrics"]
    positive_rule = QuantitativeThresholdV1(
        operator="GTE", threshold=policy.walk_forward.minimum_positive_fold_fraction
    )
    positive = _criterion(
        family="WALK_FORWARD",
        metric="positive_validation_fold_rate",
        observed=walk.get(
            "positive_validation_fold_rate", undefined("MISSING_WALK_FORWARD_METRIC")
        ),
        rule=positive_rule,
    )
    evaluable = walk.get("evaluable_folds")
    if evaluable and evaluable["status"] == "DEFINED":
        folds = rational(evaluable["value"]).numerator
        fraction = policy.walk_forward.minimum_positive_fold_fraction.value()
        positive["evaluable_folds"] = folds
        positive["minimum_positive_folds"] = (
            fraction.numerator * folds + fraction.denominator - 1
        ) // fraction.denominator
    criteria.append(positive)
    criteria.append(
        _criterion(
            family="WALK_FORWARD",
            metric="median_validation_net_pnl_per_session",
            observed=walk.get(
                "median_validation_net_pnl_per_session",
                undefined("MISSING_WALK_FORWARD_METRIC"),
            ),
            rule=policy.walk_forward.median_validation_net_pnl_per_session,
        )
    )
    criteria.append(
        _criterion(
            family="WALK_FORWARD",
            metric="median_validation_average_trade",
            observed=walk.get(
                "median_validation_average_trade", undefined("MISSING_WALK_FORWARD_METRIC")
            ),
            rule=policy.walk_forward.median_validation_average_trade,
        )
    )

    monte = evidence["monte_carlo_metrics"]
    for metric, rule in (
        ("median_total_net", policy.monte_carlo.median_total_net),
        ("probability_net_le_zero", policy.monte_carlo.probability_net_le_zero),
    ):
        criteria.append(
            _criterion(
                family="MONTE_CARLO",
                metric=metric,
                observed=monte.get(metric, undefined("MISSING_MONTE_CARLO_METRIC")),
                rule=rule,
            )
        )

    sensitivity = evidence["sensitivity"]
    baseline_net = sensitivity["baseline_metrics"]["net_pnl"]
    criteria.append(
        _criterion(
            family="SENSITIVITY",
            metric="baseline_net",
            observed=baseline_net,
            rule=policy.sensitivity.baseline_net,
            source_id=sensitivity["baseline_partition_evaluation_id"],
        )
    )
    ratios: list[CanonicalRational] = []
    baseline_value = (
        rational(baseline_net["value"]) if baseline_net["status"] == "DEFINED" else None
    )
    for scenario in sensitivity["scenarios"]:
        scenario_id = scenario["sensitivity_scenario_id"]
        if scenario["status"] != "VALID":
            criteria.append(
                _unavailable(
                    "SENSITIVITY", "variant_net", scenario["reason"], scenario_id
                )
            )
            continue
        variant_net = scenario["metrics"]["net_pnl"]
        criteria.append(
            _criterion(
                family="SENSITIVITY",
                metric="variant_net",
                observed=variant_net,
                rule=policy.sensitivity.all_required_variants_net,
                source_id=scenario_id,
            )
        )
        if baseline_value is None:
            continue
        if baseline_value.numerator == 0:
            continue
        if variant_net["status"] == "DEFINED":
            ratios.append(divide(rational(variant_net["value"]), baseline_value))
    if baseline_value is None:
        ratio_metric = undefined("BASELINE_NET_UNDEFINED")
    elif baseline_value.numerator == 0:
        ratio_metric = undefined("ZERO_BASELINE_NET")
    elif len(ratios) != len(
        [row for row in sensitivity["scenarios"] if row["status"] == "VALID"]
    ) or len(ratios) != len(sensitivity["scenarios"]):
        ratio_metric = undefined("MISSING_VALID_VARIANT_NET")
    elif not ratios:
        ratio_metric = undefined("NO_REQUIRED_SENSITIVITY_VARIANTS")
    else:
        ratio_metric = defined(exact_median(ratios))
    criteria.append(
        _criterion(
            family="SENSITIVITY",
            metric="median_variant_to_baseline_net_ratio",
            observed=ratio_metric,
            rule=policy.sensitivity.median_variant_to_baseline_net_ratio,
        )
    )

    stress = evidence["stress"]
    for scenario in stress["scenarios"]:
        scenario_id = scenario["stress_scenario_id"]
        if scenario["status"] != "VALID":
            criteria.append(_unavailable("STRESS", "stressed_net", scenario["reason"], scenario_id))
            criteria.append(
                _unavailable("STRESS", "stressed_average_trade", scenario["reason"], scenario_id)
            )
            continue
        criteria.append(
            _criterion(
                family="STRESS",
                metric="stressed_net",
                observed=scenario["metrics"]["net_pnl"],
                rule=policy.stress.all_required_scenarios_net,
                source_id=scenario_id,
            )
        )
        criteria.append(
            _criterion(
                family="STRESS",
                metric="stressed_average_trade",
                observed=scenario["metrics"]["average_trade"],
                rule=policy.stress.all_required_scenarios_average_trade,
                source_id=scenario_id,
            )
        )

    if any(item["result"] == "INSUFFICIENT_EVIDENCE" for item in criteria):
        status = "INSUFFICIENT_EVIDENCE"
    elif any(item["result"] == "FAILED" for item in criteria):
        status = "FAILED"
    else:
        status = "PASSED"
    record = {
        "schema_version": "candidate-robustness-quantitative-assessment/v1",
        "candidate_id": candidate_id,
        "strategy_score_id": strategy_score_id,
        "robustness_protocol_id": robustness_protocol_id,
        "technical_robustness_assessment_id": technical_assessment[
            "robustness_assessment_id"
        ],
        "technical_robustness_status": technical_status,
        "quantitative_policy_id": policy.policy_id,
        "quantitative_evidence_id": evidence["quantitative_evidence_id"],
        "status": status,
        "criteria": criteria,
        "failures": [item for item in criteria if item["result"] == "FAILED"],
        "insufficient_evidence": [
            item for item in criteria if item["result"] == "INSUFFICIENT_EVIDENCE"
        ],
        "diagnostics": _diagnostics(policy.diagnostic_only, monte, stress),
        "research_score_recomputed": False,
        "candidate_changed": False,
        "technical_gate_changed": False,
    }
    record["quantitative_assessment_id"] = identity(
        "candidate-robustness-quantitative-assessment/v1", record
    )
    return record


def quantitative_qualified_set(
    assessments: list[dict[str, Any]], policy_id: str
) -> dict[str, Any]:
    passed = sorted(
        (item for item in assessments if item["status"] == "PASSED"),
        key=lambda item: item["candidate_id"],
    )
    record = {
        "schema_version": "robustness-quantitative-qualified-set/v1",
        "quantitative_policy_id": policy_id,
        "candidate_count": len(passed),
        "candidates": [
            {
                "candidate_id": item["candidate_id"],
                "strategy_score_id": item["strategy_score_id"],
                "quantitative_assessment_id": item["quantitative_assessment_id"],
            }
            for item in passed
        ],
    }
    record["quantitative_qualified_set_id"] = identity(
        "robustness-quantitative-qualified-set/v1", record
    )
    return record


def quantitative_qualified_ranking(
    raw_rows: list[dict[str, Any]], qualified: dict[str, Any]
) -> dict[str, Any]:
    assessments = {
        item["candidate_id"]: item["quantitative_assessment_id"]
        for item in qualified["candidates"]
    }
    records = [
        {
            "quantitative_qualified_rank": rank,
            "original_raw_rank": row["raw_rank"],
            "candidate_id": row["candidate_id"],
            "strategy_score_id": row["strategy_score_id"],
            "score_units": row["score_units"],
            "score_scale": row["score_scale"],
            "semantic_group_id": row["semantic_group_id"],
            "quantitative_assessment_id": assessments[row["candidate_id"]],
        }
        for rank, row in enumerate(
            (row for row in raw_rows if row["candidate_id"] in assessments), 1
        )
    ]
    record = {
        "schema_version": "robustness-quantitative-qualified-ranking/v1",
        "quantitative_qualified_set_id": qualified["quantitative_qualified_set_id"],
        "records": records,
        "research_score_recomputed": False,
        "original_raw_rank_recomputed": False,
    }
    record["quantitative_ranking_id"] = identity(
        "robustness-quantitative-qualified-ranking/v1", record
    )
    return record


def diversify_quantitative_qualified(
    ranking: dict[str, Any], policy: ResearchDiversityPolicyV1
) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in ranking["records"]:
        groups[row["semantic_group_id"]].append(row)
    ordered_groups = sorted(
        groups,
        key=lambda group: (groups[group][0]["quantitative_qualified_rank"], group),
    )
    selected = []
    for round_index in range(policy.max_per_semantic_group):
        for group in ordered_groups:
            if round_index < len(groups[group]):
                selected.append(groups[group][round_index])
    ranks = {row["candidate_id"]: rank for rank, row in enumerate(selected, 1)}
    record = {
        "schema_version": "robustness-quantitative-diversified-ranking/v1",
        "quantitative_ranking_id": ranking["quantitative_ranking_id"],
        "diversity_policy_id": policy.diversity_policy_id,
        "records": [
            {
                **row,
                "quantitative_diversified_rank": ranks.get(row["candidate_id"]),
                "selection_status": "DIVERSITY_SELECTED"
                if row["candidate_id"] in ranks
                else "DIVERSITY_GROUP_LIMIT",
            }
            for row in ranking["records"]
        ],
    }
    record["quantitative_diversified_ranking_id"] = identity(
        "robustness-quantitative-diversified-ranking/v1", record
    )
    return record


def quantitative_top_n(diversified: dict[str, Any], top_n: int) -> dict[str, Any]:
    selected = sorted(
        (
            row
            for row in diversified["records"]
            if row["quantitative_diversified_rank"] is not None
        ),
        key=lambda row: row["quantitative_diversified_rank"],
    )[:top_n]
    record = {
        "schema_version": "robustness-quantitative-top-n/v1",
        "title": "Top N Quantitatively Robust Research Strategies",
        "warning": "RESEARCH_ONLY_NOT_LIVE_TRADING_APPROVAL",
        "quantitative_diversified_ranking_id": diversified[
            "quantitative_diversified_ranking_id"
        ],
        "top_n": top_n,
        "emitted_count": len(selected),
        "emitted_candidate_ids": [row["candidate_id"] for row in selected],
    }
    record["quantitative_shortlist_id"] = identity(
        "robustness-quantitative-top-n/v1", record
    )
    return record
