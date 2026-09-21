"""ResearchStrategyScoreV1 over immutable completed research facts."""

from __future__ import annotations

from typing import Any

from quantlab_core.canonical import canonical_json_bytes
from quantlab_core.errors import ContractError
from quantlab_core.numeric import CanonicalRational, round_half_even
from quantlab_core.strategy_v3 import StrategyDefinitionV3
from quantlab_mining.contracts import identity

from quantlab_scoring import SCORE_ENGINE_VERSION
from quantlab_scoring.contracts import PenaltyTermV1, ResearchScorePolicyV1, ScoreTermV1
from quantlab_scoring.semantic import complexity_metadata, semantic_group_id
from quantlab_scoring.transforms import clamp, divide, normalize

ZERO = CanonicalRational(0)
HUNDRED = CanonicalRational(100)


def rational(record: dict[str, str]) -> CanonicalRational:
    value = CanonicalRational(int(record["numerator"]), int(record["denominator"]))
    if value.to_record() != record:
        raise ContractError("noncanonical rational in research scoring evidence")
    return value


def finalize_score(
    pre_clamp: CanonicalRational, score_scale: int
) -> tuple[CanonicalRational, int, CanonicalRational]:
    """Clamp to 0..100, quantize once with HALF_EVEN and return rounding adjustment."""

    exact = clamp(pre_clamp, ZERO, HUNDRED)
    score_units = round_half_even(exact.numerator * score_scale, exact.denominator)
    adjustment = CanonicalRational(score_units, score_scale) - exact
    return exact, score_units, adjustment


def _reason(
    code: str,
    candidate_id: str,
    *,
    metric: str | None = None,
    source: str | None = None,
    undefined_reason: str | None = None,
) -> dict[str, str | None]:
    return {
        "code": code,
        "candidate_id": candidate_id,
        "metric": metric,
        "source": source,
        "undefined_reason": undefined_reason,
    }


def _canonical_reasons(reasons: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique = {canonical_json_bytes(item): item for item in reasons}
    return [unique[key] for key in sorted(unique)]


def _source_metric(
    term: ScoreTermV1,
    validation: dict[str, Any],
    comparison: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    missing = {"status": "UNDEFINED", "value": None, "reason": "MISSING_METRIC"}
    if term.source == "VALIDATION":
        return validation["metrics"].get(term.metric, missing), None
    if term.source == "COMPARISON":
        return comparison["metrics"].get(term.metric, missing), None
    total = validation["metrics"].get("total_sessions")
    losing = validation["metrics"].get("losing_sessions")
    dependencies = {"total_sessions": total, "losing_sessions": losing}
    if total is None or losing is None:
        return {"status": "UNDEFINED", "value": None, "reason": "MISSING_METRIC"}, dependencies
    if total["status"] != "DEFINED" or losing["status"] != "DEFINED":
        return {
            "status": "UNDEFINED",
            "value": None,
            "reason": "SOURCE_METRIC_UNDEFINED",
        }, dependencies
    total_value = rational(total["value"])
    if total_value.numerator <= 0:
        return {
            "status": "UNDEFINED",
            "value": None,
            "reason": "TOTAL_SESSIONS_NOT_POSITIVE",
        }, dependencies
    result = divide(total_value - rational(losing["value"]), total_value)
    return {"status": "DEFINED", "value": result.to_record(), "reason": None}, dependencies


def _term_record(
    term: ScoreTermV1,
    validation: dict[str, Any],
    comparison: dict[str, Any],
    risk_unit: CanonicalRational,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    source_record, dependencies = _source_metric(term, validation, comparison)
    base = {
        "term_id": term.term_id,
        "component": term.component,
        "source": term.source,
        "metric": term.metric,
        "source_dimension": term.source_dimension,
        "source_value": source_record,
        "derived_dependencies": dependencies,
        "normalization": term.normalization,
        "normalized_dimension": term.normalized_dimension,
        "transform": term.transform.model_dump(mode="json"),
        "max_points": term.max_points.model_dump(mode="json"),
    }
    if source_record["status"] != "DEFINED":
        return {
            **base,
            "status": "UNDEFINED",
            "normalized_input": None,
            "normalized_value": None,
            "contribution": None,
            "reason": source_record["reason"],
        }, _reason(
            "MISSING_COMPARISON_METRIC"
            if term.source == "COMPARISON"
            else "REQUIRED_METRIC_UNDEFINED",
            "",
            metric=term.metric,
            source=term.source,
            undefined_reason=source_record["reason"],
        )
    value = rational(source_record["value"])
    if term.normalization == "DIVIDE_BY_RISK_UNIT":
        if risk_unit.numerator <= 0:
            return {
                **base,
                "status": "UNDEFINED",
                "normalized_input": None,
                "normalized_value": None,
                "contribution": None,
                "reason": "INVALID_RISK_UNIT",
            }, _reason(
                "INVALID_RISK_UNIT",
                "",
                metric=term.metric,
                source="STRATEGY",
                undefined_reason="RISK_UNIT_NOT_POSITIVE",
            )
        value = divide(value, risk_unit)
    normalized = normalize(value, term.transform)
    contribution = normalized * term.max_points.value()
    return {
        **base,
        "status": "DEFINED",
        "normalized_input": value.to_record(),
        "normalized_value": normalized.to_record(),
        "contribution": contribution.to_record(),
        "reason": None,
    }, None


def _penalty_record(
    term: PenaltyTermV1,
    validation: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    source = validation["metrics"].get(
        term.metric, {"status": "UNDEFINED", "value": None, "reason": "MISSING_METRIC"}
    )
    base = {
        "term_id": term.term_id,
        "component": term.component,
        "source": term.source,
        "metric": term.metric,
        "source_dimension": term.source_dimension,
        "source_value": source,
        "normalization": term.normalization,
        "normalized_dimension": term.normalized_dimension,
        "transform": term.transform.model_dump(mode="json"),
        "max_points": term.max_points.model_dump(mode="json"),
    }
    if (
        term.metric == "largest_profitable_session_share"
        and source["status"] == "UNDEFINED"
        and source["reason"] == "NO_PROFITABLE_SESSIONS"
    ):
        return {
            **base,
            "status": "NOT_APPLICABLE",
            "normalized_input": None,
            "normalized_value": None,
            "contribution": ZERO.to_record(),
            "reason": "NO_PROFITABLE_SESSIONS",
        }, None
    if source["status"] != "DEFINED":
        return {
            **base,
            "status": "UNDEFINED",
            "normalized_input": None,
            "normalized_value": None,
            "contribution": None,
            "reason": source["reason"],
        }, _reason(
            "REQUIRED_METRIC_UNDEFINED",
            "",
            metric=term.metric,
            source="VALIDATION",
            undefined_reason=source["reason"],
        )
    value = rational(source["value"])
    normalized = normalize(value, term.transform)
    contribution = normalized * term.max_points.value()
    return {
        **base,
        "status": "DEFINED",
        "normalized_input": value.to_record(),
        "normalized_value": normalized.to_record(),
        "contribution": contribution.to_record(),
        "reason": None,
    }, None


def _component_totals(terms: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for component in ("PERFORMANCE", "RISK", "CONSISTENCY", "STABILITY", "ACTIVITY"):
        items = [item for item in terms if item["component"] == component]
        undefined = [item["term_id"] for item in items if item["status"] != "DEFINED"]
        if undefined:
            result[component] = {
                "status": "UNDEFINED",
                "value": None,
                "reason": "UNDEFINED_TERMS",
                "undefined_terms": undefined,
            }
        else:
            total = sum(
                (rational(item["contribution"]) for item in items), start=CanonicalRational(0)
            )
            result[component] = {
                "status": "DEFINED",
                "value": total.to_record(),
                "reason": None,
                "undefined_terms": [],
            }
    return result


def _evidence_fingerprints(
    discovery: dict[str, Any] | None,
    validation: dict[str, Any] | None,
    discovery_gate: dict[str, Any] | None,
    validation_gate: dict[str, Any] | None,
    comparison: dict[str, Any] | None,
) -> dict[str, str | None]:
    return {
        "discovery_result": None if discovery is None else discovery["result_fingerprint"],
        "validation_result": None if validation is None else validation["result_fingerprint"],
        "discovery_gate_result": None
        if discovery_gate is None
        else discovery_gate["result_fingerprint"],
        "validation_gate_result": None
        if validation_gate is None
        else validation_gate["result_fingerprint"],
        "comparison": None if comparison is None else comparison["result_fingerprint"],
    }


def score_candidate(
    *,
    candidate_id: str,
    strategy: StrategyDefinitionV3,
    status: dict[str, Any],
    discovery: dict[str, Any] | None,
    validation: dict[str, Any] | None,
    discovery_gate: dict[str, Any] | None,
    validation_gate: dict[str, Any] | None,
    comparison: dict[str, Any] | None,
    policy: ResearchScorePolicyV1,
) -> dict[str, Any]:
    """Score one candidate without consulting any other candidate or market data."""

    risk_unit = CanonicalRational.from_decimal(strategy.stop_loss.value)
    source_fingerprints = _evidence_fingerprints(
        discovery, validation, discovery_gate, validation_gate, comparison
    )
    base: dict[str, Any] = {
        "schema_version": "research-strategy-score/v1",
        "score_name": "ResearchStrategyScoreV1",
        "score_engine_version": SCORE_ENGINE_VERSION,
        "candidate_id": candidate_id,
        "score_policy_id": policy.score_policy_id,
        "source_fingerprints": source_fingerprints,
        "risk_unit": {
            "definition": "STRATEGY_V3_STOP_LOSS_EXACT_POINTS",
            "value": risk_unit.to_record(),
        },
        "semantic_group_id": semantic_group_id(strategy),
        "complexity_metadata": complexity_metadata(strategy),
        "statistical_precision_warning": policy.statistical_precision_warning,
    }
    consistency_reasons: list[dict[str, Any]] = []
    for name, item in (
        ("DISCOVERY_RESULT", discovery),
        ("DISCOVERY_GATE", discovery_gate),
    ):
        if item is None or item.get("candidate_id") != candidate_id:
            consistency_reasons.append(
                _reason("SOURCE_RESULT_INCONSISTENT", candidate_id, source=name)
            )
    claimed_eligible = (
        status["discovery_status"] == "DISCOVERY_PASSED"
        and status["validation_status"] == "VALIDATION_PASSED"
    )
    if claimed_eligible:
        for name, item in (
            ("VALIDATION_RESULT", validation),
            ("VALIDATION_GATE", validation_gate),
            ("COMPARISON", comparison),
        ):
            if item is None or item.get("candidate_id") != candidate_id:
                consistency_reasons.append(
                    _reason("SOURCE_RESULT_INCONSISTENT", candidate_id, source=name)
                )
        if discovery_gate and discovery_gate["result"] != "PASS":
            consistency_reasons.append(
                _reason("SOURCE_RESULT_INCONSISTENT", candidate_id, source="DISCOVERY_GATE")
            )
        if validation_gate and validation_gate["result"] != "PASS":
            consistency_reasons.append(
                _reason("SOURCE_RESULT_INCONSISTENT", candidate_id, source="VALIDATION_GATE")
            )
    if consistency_reasons:
        record = {
            **base,
            "score_status": "NOT_SCORABLE",
            "reasons": _canonical_reasons(consistency_reasons),
            "terms": [],
            "penalties": [],
            "component_totals": {},
            "base_score": None,
            "penalty_total": None,
            "pre_clamp_score": None,
            "score_exact_before_quantization": None,
            "score_units": None,
            "score_scale": policy.score_scale,
            "rounding_adjustment": None,
            "ranking_values": None,
            "diagnostic_metrics": {},
        }
        record["strategy_score_id"] = identity("research-strategy-score/v1", record)
        return record
    if not claimed_eligible:
        reasons = []
        if status["discovery_status"] != "DISCOVERY_PASSED":
            reasons.append(_reason("DISCOVERY_GATE_FAILED", candidate_id, source="DISCOVERY"))
        if status["validation_status"] == "VALIDATION_NOT_RUN":
            reasons.append(_reason("VALIDATION_NOT_RUN", candidate_id, source="VALIDATION"))
        elif status["validation_status"] != "VALIDATION_PASSED":
            reasons.append(_reason("VALIDATION_GATE_FAILED", candidate_id, source="VALIDATION"))
        record = {
            **base,
            "score_status": "NOT_ELIGIBLE",
            "reasons": _canonical_reasons(reasons),
            "terms": [],
            "penalties": [],
            "component_totals": {},
            "base_score": None,
            "penalty_total": None,
            "pre_clamp_score": None,
            "score_exact_before_quantization": None,
            "score_units": None,
            "score_scale": policy.score_scale,
            "rounding_adjustment": None,
            "ranking_values": None,
            "diagnostic_metrics": {},
        }
        record["strategy_score_id"] = identity("research-strategy-score/v1", record)
        return record

    assert validation is not None and comparison is not None
    reasons: list[dict[str, Any]] = []
    trades = validation["metrics"].get("trades")
    if trades is None or trades["status"] != "DEFINED":
        reasons.append(
            _reason(
                "REQUIRED_METRIC_UNDEFINED",
                candidate_id,
                metric="trades",
                source="VALIDATION",
                undefined_reason=None if trades is None else trades["reason"],
            )
        )
    elif rational(trades["value"]).numerator == 0:
        reasons.append(_reason("ZERO_TRADES", candidate_id, metric="trades", source="VALIDATION"))
    if risk_unit.numerator <= 0:
        reasons.append(_reason("INVALID_RISK_UNIT", candidate_id, source="STRATEGY"))

    term_records: list[dict[str, Any]] = []
    for term in sorted(policy.positive_terms, key=lambda item: item.term_id):
        term_record, reason = _term_record(term, validation, comparison, risk_unit)
        term_records.append(term_record)
        if reason:
            reason["candidate_id"] = candidate_id
            reasons.append(reason)
    penalty_records: list[dict[str, Any]] = []
    for term in sorted(policy.penalty_terms, key=lambda item: item.term_id):
        penalty_record, reason = _penalty_record(term, validation)
        penalty_records.append(penalty_record)
        if reason:
            reason["candidate_id"] = candidate_id
            reasons.append(reason)
    component_totals = _component_totals(term_records)
    reasons = _canonical_reasons(reasons)
    diagnostic = {
        name: validation["metrics"].get(name)
        for name in ("profit_factor", "payoff_ratio", "average_loss")
    }
    ranking_values = {
        "validation.net_pnl_per_session": validation["metrics"]["net_pnl_per_session"]["value"],
        "validation.max_drawdown": validation["metrics"]["max_drawdown"]["value"],
        "validation.positive_session_rate": validation["metrics"]["positive_session_rate"]["value"],
    }
    if reasons:
        record = {
            **base,
            "score_status": "NOT_SCORABLE",
            "reasons": reasons,
            "terms": term_records,
            "penalties": penalty_records,
            "component_totals": component_totals,
            "base_score": None,
            "penalty_total": None,
            "pre_clamp_score": None,
            "score_exact_before_quantization": None,
            "score_units": None,
            "score_scale": policy.score_scale,
            "rounding_adjustment": None,
            "ranking_values": ranking_values,
            "diagnostic_metrics": diagnostic,
        }
        record["strategy_score_id"] = identity("research-strategy-score/v1", record)
        return record

    base_score = sum(
        (rational(item["contribution"]) for item in term_records), start=CanonicalRational(0)
    )
    penalty_total = sum(
        (rational(item["contribution"]) for item in penalty_records), start=CanonicalRational(0)
    )
    pre_clamp = base_score - penalty_total
    exact, score_units, rounding_adjustment = finalize_score(pre_clamp, policy.score_scale)
    record = {
        **base,
        "score_status": "SCORED",
        "reasons": [],
        "terms": term_records,
        "penalties": penalty_records,
        "component_totals": component_totals,
        "base_score": base_score.to_record(),
        "penalty_total": penalty_total.to_record(),
        "pre_clamp_score": pre_clamp.to_record(),
        "score_exact_before_quantization": exact.to_record(),
        "score_units": score_units,
        "score_scale": policy.score_scale,
        "rounding_adjustment": rounding_adjustment.to_record(),
        "ranking_values": ranking_values,
        "diagnostic_metrics": diagnostic,
    }
    record["strategy_score_id"] = identity("research-strategy-score/v1", record)
    return record


def validate_score_record(record: dict[str, Any]) -> None:
    expected = identity(
        "research-strategy-score/v1",
        {key: value for key, value in record.items() if key != "strategy_score_id"},
    )
    if record.get("strategy_score_id") != expected:
        raise ValueError("strategy_score_id mismatch")
