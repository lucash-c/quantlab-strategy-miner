"""One-at-a-time exact parameter perturbations without promotion or optimization."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic import ValidationError
from quantlab_core.errors import ContractError
from quantlab_core.numeric import CanonicalRational
from quantlab_core.strategy_v3 import StrategyDefinitionV3
from quantlab_mining.canonicalization import named_candidate
from quantlab_mining.contracts import identity

from quantlab_robustness.contracts import PerturbationSpecV1, SensitivityPolicyV1
from quantlab_robustness.exact import (
    compare,
    defined,
    exact_median,
    finite_decimal,
    rational,
    undefined,
)


def _pointer(record: Any, pointer: str) -> tuple[Any, str | int]:
    parts = pointer.split("/")[1:]
    if not parts:
        raise ContractError("TARGET_NOT_RESOLVED")
    current = record
    for encoded in parts[:-1]:
        token = encoded.replace("~1", "/").replace("~0", "~")
        try:
            current = current[int(token)] if isinstance(current, list) else current[token]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ContractError("TARGET_NOT_RESOLVED") from exc
    final = parts[-1].replace("~1", "/").replace("~0", "~")
    key: str | int = int(final) if isinstance(current, list) else final
    try:
        current[key]
    except (KeyError, IndexError, TypeError) as exc:
        raise ContractError("TARGET_NOT_RESOLVED") from exc
    return current, key


def _validate_target(spec: PerturbationSpecV1) -> None:
    path = spec.target.path
    kind = spec.target.kind
    valid = {
        "FEATURE_PARAMETER": path.startswith("/features/") and "/parameters/" in path,
        "CONDITION_CONSTANT": path.startswith("/entry_conditions/") and path.endswith("/value"),
        "STOP_LOSS": path == "/stop_loss/value",
        "TAKE_PROFIT": path == "/take_profit/value",
    }[kind]
    if not valid:
        raise ContractError("TARGET_KIND_PATH_MISMATCH")


def perturb_strategy(
    baseline_candidate_id: str,
    baseline: StrategyDefinitionV3,
    spec: PerturbationSpecV1,
) -> tuple[dict[str, Any], StrategyDefinitionV3 | None]:
    base_record = baseline.model_dump(mode="json")
    scenario_base = {
        "schema_version": "sensitivity-scenario/v1",
        "sensitivity_variant_of": baseline_candidate_id,
        "perturbation_spec_id": spec.perturbation_spec_id,
        "perturbation": spec.model_dump(mode="json"),
    }
    try:
        _validate_target(spec)
        record = deepcopy(base_record)
        container, key = _pointer(record, spec.target.path)
        current = container[key]
        if spec.method == "INTEGER_ABSOLUTE_DELTA":
            if type(current) is not int:
                raise ContractError("TARGET_TYPE_MISMATCH")
            value: int | str = current + spec.integer_delta  # type: ignore[operator]
        else:
            if not isinstance(current, str):
                raise ContractError("TARGET_TYPE_MISMATCH")
            baseline_value = CanonicalRational.from_decimal(current)
            delta = spec.rational_delta.value()  # type: ignore[union-attr]
            result = (
                baseline_value + delta
                if spec.method == "DECIMAL_ABSOLUTE_DELTA"
                else baseline_value * (CanonicalRational(1) + delta)
            )
            value = finite_decimal(result)
        container[key] = value
        parsed = StrategyDefinitionV3.model_validate(record)
        variant_id, variant = named_candidate(parsed)
        if variant_id == baseline_candidate_id:
            raise ContractError("NO_OP_PERTURBATION")
        scenario = {
            **scenario_base,
            "status": "VALID",
            "reason": None,
            "variant_candidate_id": variant_id,
            "variant_strategy": variant.model_dump(mode="json"),
        }
        scenario["sensitivity_scenario_id"] = identity("sensitivity-scenario/v1", scenario)
        return scenario, variant
    except (ContractError, ValidationError, ValueError) as exc:
        reason = str(exc).splitlines()[0]
        known = {
            "TARGET_NOT_RESOLVED",
            "TARGET_KIND_PATH_MISMATCH",
            "TARGET_TYPE_MISMATCH",
            "NON_TERMINATING_DECIMAL_RESULT",
            "DECIMAL_SCALE_EXCEEDED",
            "NO_OP_PERTURBATION",
        }
        if reason not in known:
            if "period must be between" in str(exc):
                reason = "PERIOD_NOT_POSITIVE"
            elif "distance must be positive" in str(exc):
                reason = "STOP_OR_TARGET_NOT_POSITIVE"
            else:
                reason = "STRATEGY_SCHEMA_INVALID"
        scenario = {
            **scenario_base,
            "status": "INVALID_SENSITIVITY_VARIANT",
            "reason": reason,
            "variant_candidate_id": None,
            "variant_strategy": None,
        }
        scenario["sensitivity_scenario_id"] = identity("sensitivity-scenario/v1", scenario)
        return scenario, None


def build_sensitivity_scenarios(
    candidate_id: str, strategy: StrategyDefinitionV3, policy: SensitivityPolicyV1
) -> tuple[list[dict[str, Any]], dict[str, StrategyDefinitionV3]]:
    scenarios = []
    variants: dict[str, StrategyDefinitionV3] = {}
    for spec in policy.perturbations:
        scenario, variant = perturb_strategy(candidate_id, strategy, spec)
        scenarios.append(scenario)
        if variant is not None:
            variants.setdefault(scenario["variant_candidate_id"], variant)
    return scenarios, variants


def compare_sensitivity_metrics(baseline: dict, variant: dict) -> dict[str, Any]:
    metrics = {}
    for name in (
        "trades",
        "average_trade",
        "net_pnl_per_session",
        "win_rate",
        "max_drawdown",
        "positive_session_rate",
    ):
        left, right = baseline["metrics"][name], variant["metrics"][name]
        key = name + "_delta"
        if left["status"] != "DEFINED" or right["status"] != "DEFINED":
            metrics[key] = undefined(
                "SOURCE_METRIC_UNDEFINED",
                sources=[
                    {"source": source, "reason": value["reason"]}
                    for source, value in (("BASELINE", left), ("VARIANT", right))
                    if value["status"] != "DEFINED"
                ],
            )
        else:
            metrics[key] = defined(rational(right["value"]) - rational(left["value"]))
    return metrics


def summarize_sensitivity(
    *, policy: SensitivityPolicyV1, scenarios: list[dict[str, Any]], results: list[dict[str, Any]]
) -> dict[str, Any]:
    valid = [item for item in scenarios if item["status"] == "VALID"]
    by_scenario = {item["sensitivity_scenario_id"]: item for item in results}
    if any(item["sensitivity_scenario_id"] not in by_scenario for item in valid):
        return {
            "status": "INSUFFICIENT_EVIDENCE",
            "reason": "MISSING_SCENARIO_RESULT",
            "metrics": {},
        }
    evaluated = [by_scenario[item["sensitivity_scenario_id"]] for item in valid]
    within = 0
    for result in evaluated:
        passed = True
        for criterion in policy.tolerance_criteria:
            observed = result["comparison_metrics"][criterion.metric]
            if observed["status"] != "DEFINED" or not compare(
                rational(observed["value"]), criterion.operator, criterion.threshold.value()
            ):
                passed = False
        result["within_tolerance"] = passed if policy.tolerance_criteria else None
        within += passed and bool(policy.tolerance_criteria)
    metrics: dict[str, Any] = {
        "requested_scenarios": defined(len(scenarios)),
        "valid_scenarios": defined(len(valid)),
        "invalid_scenarios": defined(len(scenarios) - len(valid)),
        "unique_variant_candidates": defined(len({item["variant_candidate_id"] for item in valid})),
        "fraction_within_tolerance": defined(CanonicalRational(within, len(evaluated)))
        if policy.tolerance_criteria and evaluated
        else undefined(
            "NO_TOLERANCE_CRITERIA" if not policy.tolerance_criteria else "NO_VALID_SCENARIOS",
            status="NOT_APPLICABLE" if not policy.tolerance_criteria else "UNDEFINED",
        ),
    }
    for name in (
        "trades_delta",
        "average_trade_delta",
        "net_pnl_per_session_delta",
        "win_rate_delta",
        "max_drawdown_delta",
        "positive_session_rate_delta",
    ):
        records = [item["comparison_metrics"][name] for item in evaluated]
        if records and all(item["status"] == "DEFINED" for item in records):
            values = [rational(item["value"]) for item in records]
            metrics["median_" + name] = defined(exact_median(values))
        else:
            metrics["median_" + name] = undefined("SOURCE_METRIC_UNDEFINED")
    return {"status": "COMPLETED", "reason": None, "metrics": metrics, "results": evaluated}
