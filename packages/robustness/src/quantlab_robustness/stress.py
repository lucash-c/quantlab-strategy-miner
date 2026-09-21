"""Resolve exact non-improving execution-friction scenarios."""

from __future__ import annotations

from typing import Any

from quantlab_core.errors import ContractError
from quantlab_core.numeric import CanonicalRational
from quantlab_core.strategy_v3 import (
    FixedPerSideCostModelV3,
    FixedPointsSlippageModelV3,
)
from quantlab_mining.contracts import EvaluationConfigV1, identity

from quantlab_robustness.contracts import ExecutionStressPolicyV1, FrictionChangeV1
from quantlab_robustness.exact import defined, finite_decimal, rational, undefined


def _cost_value(config: EvaluationConfigV1) -> CanonicalRational:
    model = config.cost_model
    return (
        CanonicalRational.from_decimal(model.points_per_side)
        if isinstance(model, FixedPerSideCostModelV3)
        else CanonicalRational(0)
    )


def _slippage_value(config: EvaluationConfigV1) -> CanonicalRational:
    model = config.slippage_model
    return (
        CanonicalRational.from_decimal(model.points_per_side)
        if isinstance(model, FixedPointsSlippageModelV3)
        else CanonicalRational(0)
    )


def _resolve(baseline: CanonicalRational, change: FrictionChangeV1 | None) -> CanonicalRational:
    if change is None:
        return baseline
    value = change.value.value()
    return value if change.method == "ABSOLUTE_POINTS" else baseline * value


def resolve_stress_scenarios(
    baseline: EvaluationConfigV1, policy: ExecutionStressPolicyV1
) -> list[dict[str, Any]]:
    baseline = baseline.canonicalized()
    base_cost, base_slippage = _cost_value(baseline), _slippage_value(baseline)
    results = []
    for requested in policy.scenarios:
        base = {
            "schema_version": "execution-stress-scenario/v1",
            "stress_policy_id": policy.policy_id,
            "name": requested.name,
            "requested": requested.model_dump(mode="json"),
            "baseline_evaluation_config": baseline.model_dump(mode="json"),
        }
        try:
            cost = _resolve(base_cost, requested.cost)
            slippage = _resolve(base_slippage, requested.slippage)
            if cost.compare(base_cost) < 0 or slippage.compare(base_slippage) < 0:
                raise ContractError("NON_STRESS_SCENARIO")
            if cost.compare(base_cost) == 0 and slippage.compare(base_slippage) == 0:
                raise ContractError("NON_STRESS_SCENARIO")
            cost_text, slippage_text = finite_decimal(cost), finite_decimal(slippage)
            config = EvaluationConfigV1.model_validate(
                {
                    "schema_version": "mining-evaluation/v1",
                    "max_sessions": baseline.max_sessions,
                    "cost_model": {
                        "type": "FIXED_PER_SIDE",
                        "version": "1.0.0",
                        "points_per_side": cost_text,
                    }
                    if cost.numerator
                    else {"type": "NONE", "version": "1.0.0"},
                    "slippage_model": {
                        "type": "FIXED_POINTS",
                        "version": "1.0.0",
                        "points_per_side": slippage_text,
                    }
                    if slippage.numerator
                    else {"type": "NONE", "version": "1.0.0"},
                }
            ).canonicalized()
            record = {
                **base,
                "status": "VALID",
                "reason": None,
                "resolved_cost": cost.to_record(),
                "resolved_slippage": slippage.to_record(),
                "resolved_evaluation_config": config.model_dump(mode="json"),
            }
        except ContractError as exc:
            record = {
                **base,
                "status": "INVALID_STRESS_SCENARIO",
                "reason": str(exc),
                "resolved_cost": None,
                "resolved_slippage": None,
                "resolved_evaluation_config": None,
            }
        record["stress_scenario_id"] = identity("execution-stress-scenario/v1", record)
        results.append(record)
    return results


def compare_stress_metrics(baseline: dict, stressed: dict) -> dict[str, Any]:
    metrics = {}
    for name in (
        "trades",
        "gross_pnl",
        "costs",
        "slippage_impact",
        "net_pnl",
        "average_trade",
        "net_pnl_per_session",
        "max_drawdown",
        "win_rate",
        "profitable_sessions",
    ):
        left, right = baseline["metrics"][name], stressed["metrics"][name]
        if left["status"] == "DEFINED" and right["status"] == "DEFINED":
            metrics[name + "_delta"] = defined(rational(right["value"]) - rational(left["value"]))
        else:
            metrics[name + "_delta"] = undefined("SOURCE_METRIC_UNDEFINED")
    return metrics


def summarize_stress(
    results: list[dict[str, Any]], scenarios: list[dict[str, Any]]
) -> dict[str, Any]:
    valid = [item for item in scenarios if item["status"] == "VALID"]
    by_id = {item["stress_scenario_id"]: item for item in results}
    if any(item["stress_scenario_id"] not in by_id for item in valid):
        return {"status": "INSUFFICIENT_EVIDENCE", "reason": "MISSING_STRESS_RESULT", "metrics": {}}
    evaluated = [by_id[item["stress_scenario_id"]] for item in valid]
    transitions = {
        name: 0
        for name in (
            "PASS_TO_PASS",
            "PASS_TO_FAIL",
            "FAIL_TO_FAIL",
            "FAIL_TO_PASS",
            "NOT_APPLICABLE",
        )
    }
    for item in evaluated:
        transitions[item["gate_transition"]] += 1
    return {
        "status": "COMPLETED",
        "reason": None,
        "metrics": {
            "requested_scenarios": defined(len(scenarios)),
            "valid_scenarios": defined(len(valid)),
            "invalid_scenarios": defined(len(scenarios) - len(valid)),
            **{name.lower(): defined(value) for name, value in transitions.items()},
        },
        "results": evaluated,
    }
