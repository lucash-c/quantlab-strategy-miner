"""Chronological rolling fixed folds for already-frozen strategies."""

from __future__ import annotations

from functools import cmp_to_key
from typing import Any

from quantlab_core.errors import ContractError
from quantlab_core.numeric import CanonicalRational
from quantlab_mining.contracts import identity

from quantlab_robustness.contracts import WalkForwardPolicyV1
from quantlab_robustness.exact import defined, exact_median, rational, undefined


def build_walk_forward_plan(
    *,
    dataset_id: str,
    logical_asset: str,
    sessions: list[dict[str, Any]],
    policy: WalkForwardPolicyV1,
) -> dict[str, Any]:
    ids = [item["session_id"] for item in sessions]
    dates = [item["trading_date"] for item in sessions]
    if len(ids) != len(set(ids)) or dates != sorted(set(dates)):
        raise ContractError("authorized Walk-Forward sessions must be unique and chronological")
    plan_input = {
        "schema_version": "walk-forward-plan/v1",
        "dataset_id": dataset_id,
        "logical_asset": logical_asset,
        "authorized_sessions": [
            {"session_id": item["session_id"], "trading_date": item["trading_date"]}
            for item in sessions
        ],
        "walk_forward_policy_id": policy.policy_id,
    }
    plan_id = identity("walk-forward-plan/v1", plan_input)
    width = policy.discovery_sessions + policy.gap_sessions + policy.validation_sessions
    folds = []
    start = 0
    while start + width <= len(sessions):
        discovery = sessions[start : start + policy.discovery_sessions]
        gap_start = start + policy.discovery_sessions
        gap = sessions[gap_start : gap_start + policy.gap_sessions]
        validation_start = gap_start + policy.gap_sessions
        validation = sessions[validation_start : validation_start + policy.validation_sessions]
        if discovery[-1]["trading_date"] >= validation[0]["trading_date"]:
            raise ContractError("Walk-Forward fold is not strictly chronological")
        fold = {
            "schema_version": "walk-forward-fold/v1",
            "walk_forward_plan_id": plan_id,
            "fold_ordinal": len(folds) + 1,
            "discovery_session_ids": [item["session_id"] for item in discovery],
            "gap_session_ids": [item["session_id"] for item in gap],
            "validation_session_ids": [item["session_id"] for item in validation],
            "discovery_first_date": discovery[0]["trading_date"],
            "discovery_last_date": discovery[-1]["trading_date"],
            "validation_first_date": validation[0]["trading_date"],
            "validation_last_date": validation[-1]["trading_date"],
        }
        fold["walk_forward_fold_id"] = identity("walk-forward-fold/v1", fold)
        folds.append(fold)
        start += policy.step_sessions
    last_end = 0 if not folds else (len(folds) - 1) * policy.step_sessions + width
    tail = ids[last_end:]
    validation_slots = [sid for fold in folds for sid in fold["validation_session_ids"]]
    unique = sorted(set(validation_slots), key=ids.index)
    overlap = len(validation_slots) - len(unique)
    sufficient = len(folds) >= policy.min_folds
    return {
        **plan_input,
        "walk_forward_plan_id": plan_id,
        "status": "READY" if sufficient else "INSUFFICIENT_WALK_FORWARD_HISTORY",
        "required_min_folds": policy.min_folds,
        "fold_count": len(folds),
        "folds": folds,
        "unused_tail_session_ids": tail,
        "overlap": {
            "validation_session_slots": len(validation_slots),
            "unique_validation_sessions": len(unique),
            "unique_validation_session_ids": unique,
            "overlap_session_slots": overlap,
            "unique_session_fraction": defined(
                CanonicalRational(len(unique), len(validation_slots))
            )
            if validation_slots
            else undefined("NO_FOLDS", status="INSUFFICIENT_EVIDENCE"),
            "statistically_independent": False,
        },
    }


def summarize_walk_forward(results: list[dict[str, Any]], plan: dict[str, Any]) -> dict[str, Any]:
    if plan["status"] != "READY":
        return {
            "status": "INSUFFICIENT_EVIDENCE",
            "reason": "INSUFFICIENT_WALK_FORWARD_HISTORY",
            "total_folds": len(plan["folds"]),
            "metrics": {},
        }
    if len(results) != len(plan["folds"]):
        raise ContractError("Walk-Forward result does not cover every fold")
    evaluable = [item for item in results if item["status"] == "COMPLETED"]
    if len(evaluable) != len(results):
        return {
            "status": "INSUFFICIENT_EVIDENCE",
            "reason": "INCOMPLETE_FOLD_RESULTS",
            "total_folds": len(results),
            "metrics": {},
        }
    validation_metrics = [item["validation"]["metrics"] for item in evaluable]

    def values(name: str) -> list[CanonicalRational]:
        records = [metrics[name] for metrics in validation_metrics]
        return [rational(record["value"]) for record in records if record["status"] == "DEFINED"]

    metrics: dict[str, Any] = {}
    for name in ("average_trade", "net_pnl_per_session", "max_drawdown", "win_rate"):
        series = values(name)
        if len(series) != len(evaluable):
            metrics[f"median_validation_{name}"] = undefined("SOURCE_METRIC_UNDEFINED")
            metrics[f"worst_validation_{name}"] = undefined("SOURCE_METRIC_UNDEFINED")
            continue
        metrics[f"median_validation_{name}"] = defined(exact_median(series))
        ordered = sorted(series, key=cmp_to_key(lambda left, right: left.compare(right)))
        worst = ordered[-1] if name == "max_drawdown" else ordered[0]
        metrics[f"worst_validation_{name}"] = defined(worst)
    positive = sum(
        rational(item["validation"]["metrics"]["net_pnl"]["value"]).numerator > 0
        for item in evaluable
    )
    dpass = sum(item["discovery_gate_result"] == "PASS" for item in evaluable)
    vpass = sum(item["validation_gate_result"] == "PASS" for item in evaluable)
    both = sum(
        item["discovery_gate_result"] == "PASS" and item["validation_gate_result"] == "PASS"
        for item in evaluable
    )
    metrics.update(
        total_folds=defined(len(results)),
        evaluable_folds=defined(len(evaluable)),
        discovery_pass_folds=defined(dpass)
        if all(item["discovery_gate_result"] != "NOT_APPLICABLE" for item in evaluable)
        else undefined("GATE_NOT_CONFIGURED", status="NOT_APPLICABLE"),
        validation_pass_folds=defined(vpass)
        if all(item["validation_gate_result"] != "NOT_APPLICABLE" for item in evaluable)
        else undefined("GATE_NOT_CONFIGURED", status="NOT_APPLICABLE"),
        both_pass_folds=defined(both)
        if all(
            item["discovery_gate_result"] != "NOT_APPLICABLE"
            and item["validation_gate_result"] != "NOT_APPLICABLE"
            for item in evaluable
        )
        else undefined("GATE_NOT_CONFIGURED", status="NOT_APPLICABLE"),
        validation_pass_rate=defined(CanonicalRational(vpass, len(evaluable)))
        if all(item["validation_gate_result"] != "NOT_APPLICABLE" for item in evaluable)
        else undefined("GATE_NOT_CONFIGURED", status="NOT_APPLICABLE"),
        positive_validation_fold_rate=defined(CanonicalRational(positive, len(evaluable))),
    )
    return {"status": "COMPLETED", "reason": None, "metrics": metrics, "overlap": plan["overlap"]}
