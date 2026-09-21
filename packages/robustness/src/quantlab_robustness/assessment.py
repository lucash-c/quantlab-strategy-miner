"""Facts-only assessment and explicit all-criteria robustness gate."""

from __future__ import annotations

from typing import Any

from quantlab_core.errors import ContractError
from quantlab_mining.contracts import identity

from quantlab_robustness.contracts import RobustnessGatePolicyV1
from quantlab_robustness.exact import compare, rational, undefined

_FAMILY_KEYS = {
    "WALK_FORWARD": "walk_forward",
    "MONTE_CARLO": "monte_carlo",
    "SENSITIVITY": "sensitivity",
    "STRESS": "stress",
}


def apply_robustness_gate(
    *,
    candidate_id: str,
    family_results: dict[str, dict[str, Any]],
    policy: RobustnessGatePolicyV1,
) -> dict[str, Any]:
    criteria = []
    for criterion in sorted(policy.criteria, key=lambda item: item.criterion_id):
        namespace, metric_name = criterion.metric.split(".", 1)
        family = namespace.upper()
        result = family_results.get(family)
        if result is None or result.get("status") == "NOT_RUN":
            raise ContractError("gate criterion references a family that was not executed")
        observed = result.get("metrics", {}).get(metric_name)
        if observed is None:
            if result.get("status") == "INSUFFICIENT_EVIDENCE":
                observed = undefined("INSUFFICIENT_EVIDENCE")
            else:
                raise ContractError(
                    f"gate metric is outside the resolved registry: {criterion.metric}"
                )
        if observed["status"] != "DEFINED":
            passed = False
            reason = "UNDEFINED_METRIC"
        else:
            passed = compare(
                rational(observed["value"]), criterion.operator, criterion.threshold.value()
            )
            reason = None if passed else "THRESHOLD_NOT_MET"
        criteria.append(
            {
                "criterion_id": criterion.criterion_id,
                "metric": criterion.metric,
                "operator": criterion.operator,
                "threshold": criterion.threshold.model_dump(mode="json"),
                "observed_value": observed,
                "result": "PASS" if passed else "FAIL",
                "reason": reason,
                "undefined_reason": observed.get("reason")
                if observed["status"] != "DEFINED"
                else None,
            }
        )
    insufficient = [
        family
        for family in policy.required_families
        if family_results[family]["status"] == "INSUFFICIENT_EVIDENCE"
    ]
    result = (
        "INSUFFICIENT_EVIDENCE"
        if insufficient
        else "FAIL"
        if any(item["result"] == "FAIL" for item in criteria)
        else "PASS"
    )
    record = {
        "schema_version": "robustness-gate-result/v1",
        "candidate_id": candidate_id,
        "robustness_gate_policy_id": policy.policy_id,
        "result": result,
        "insufficient_required_families": sorted(insufficient),
        "criteria": criteria,
        "failures": [item for item in criteria if item["result"] == "FAIL"],
        "short_circuited": False,
    }
    record["robustness_gate_result_id"] = identity("robustness-gate-result/v1", record)
    return record


def assess_candidate(
    *,
    candidate_id: str,
    strategy_score_id: str,
    robustness_protocol_id: str,
    family_results: dict[str, dict[str, Any]],
    gate_policy: RobustnessGatePolicyV1 | None,
) -> dict[str, Any]:
    missing = set(_FAMILY_KEYS) - set(family_results)
    if missing:
        raise ContractError("assessment is missing family records: " + ",".join(sorted(missing)))
    family_references = {
        _FAMILY_KEYS[family]: {
            "status": result["status"],
            "reason": result.get("reason"),
            "result_id": result.get(
                {
                    "WALK_FORWARD": "walk_forward_result_id",
                    "MONTE_CARLO": "monte_carlo_result_id",
                    "SENSITIVITY": "sensitivity_result_id",
                    "STRESS": "stress_result_id",
                }[family]
            ),
        }
        for family, result in sorted(family_results.items())
    }
    gate = None
    if gate_policy is None:
        status = "ROBUSTNESS_FACTS_ONLY"
    else:
        gate = apply_robustness_gate(
            candidate_id=candidate_id, family_results=family_results, policy=gate_policy
        )
        status = {
            "PASS": "ROBUSTNESS_PASSED",
            "FAIL": "ROBUSTNESS_FAILED",
            "INSUFFICIENT_EVIDENCE": "ROBUSTNESS_INSUFFICIENT_EVIDENCE",
        }[gate["result"]]
    record = {
        "schema_version": "candidate-robustness-assessment/v1",
        "candidate_id": candidate_id,
        "strategy_score_id": strategy_score_id,
        "robustness_protocol_id": robustness_protocol_id,
        "families": family_references,
        "gate": gate,
        "status": status,
        "robustness_score": None,
        "robustness_rating": None,
    }
    record["robustness_assessment_id"] = identity("candidate-robustness-assessment/v1", record)
    return record
