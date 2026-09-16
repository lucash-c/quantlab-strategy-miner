"""No executable formulas: exact comparisons of registered metrics, all reasons retained."""

from quantlab_core.errors import ContractError
from quantlab_core.numeric import CanonicalRational
from quantlab_mining.contracts import identity

from quantlab_research.contracts import GatePolicyV1


def defined(value: CanonicalRational | int) -> dict:
    number = CanonicalRational(value) if type(value) is int else value
    return {"status": "DEFINED", "value": number.to_record(), "reason": None}


def undefined(reason: str, **details: object) -> dict:
    return {"status": "UNDEFINED", "value": None, "reason": reason, **details}


def number(record: dict) -> CanonicalRational:
    if record["status"] != "DEFINED":
        raise ContractError("cannot coerce undefined metric")
    return CanonicalRational(int(record["value"]["numerator"]), int(record["value"]["denominator"]))


def _failure(metric: str, operator: str) -> str:
    if metric.endswith(("_delta", "_ratio")):
        return "VALIDATION_DEGRADATION_EXCEEDED"
    if operator in {"GT", "GTE"}:
        return {
            "trades": "MIN_TRADES_NOT_MET",
            "active_sessions": "MIN_ACTIVE_SESSIONS_NOT_MET",
            "net_pnl": "NET_PNL_NOT_MET",
            "profit_factor": "PROFIT_FACTOR_NOT_MET",
        }.get(metric, "THRESHOLD_NOT_MET")
    if metric == "max_drawdown" and operator in {"LT", "LTE"}:
        return "MAX_DRAWDOWN_EXCEEDED"
    return "THRESHOLD_NOT_MET"


def apply_gate(candidate_id: str, evaluation_id: str, metrics: dict, policy: GatePolicyV1) -> dict:
    results = []
    for criterion in policy.canonical_record()["criteria"]:
        observed = metrics[criterion["metric"]]
        op = criterion["operator"]
        if observed["status"] == "UNDEFINED":
            passed, reason = False, "UNDEFINED_METRIC"
        else:
            comparison = number(observed).compare(
                CanonicalRational(
                    int(criterion["threshold"]["numerator"]),
                    int(criterion["threshold"]["denominator"]),
                )
            )
            passed = {
                "GT": comparison > 0,
                "GTE": comparison >= 0,
                "LT": comparison < 0,
                "LTE": comparison <= 0,
                "EQ": comparison == 0,
                "NE": comparison != 0,
            }[op]
            reason = None if passed else _failure(criterion["metric"], op)
        results.append(
            {
                **criterion,
                "observed_value": observed,
                "result": "PASS" if passed else "FAIL",
                "reason": reason,
                "undefined_reason": observed["reason"]
                if observed["status"] == "UNDEFINED"
                else None,
            }
        )
    record = {
        "candidate_id": candidate_id,
        "evaluation_id": evaluation_id,
        "gate_policy_id": policy.policy_id,
        "result": "PASS" if all(r["result"] == "PASS" for r in results) else "FAIL",
        "information": [] if results else ["NO_CRITERIA_CONFIGURED"],
        "criteria": results,
        "failures": [r for r in results if r["result"] == "FAIL"],
    }
    record["result_fingerprint"] = identity("research-gate-result/v1", record)
    return record
