"""Strategy complexity metadata and topology-preserving semantic diversity groups."""

from __future__ import annotations

from typing import Any

from quantlab_core.canonical import canonical_json_bytes
from quantlab_core.strategy_v3 import StrategyDefinitionV3
from quantlab_mining.contracts import identity


def _is_numeric_text(value: str) -> bool:
    if not value:
        return False
    try:
        int(value)
        return True
    except ValueError:
        pass
    parts = value.removeprefix("-").split(".")
    return len(parts) <= 2 and all(part.isdigit() for part in parts)


def _parameter_shape(name: str, value: int | bool | str) -> object:
    if type(value) is bool:
        return value
    if type(value) is int:
        return {"placeholder": "INTEGER", "role": name}
    if _is_numeric_text(value):
        return {"placeholder": "RATIONAL", "role": name}
    return value


def _operand_shape(
    operand: dict[str, Any], feature_shapes: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    if operand["type"] == "feature":
        return {"type": "feature", "feature": feature_shapes[operand["feature_id"]]}
    if operand["type"] == "constant":
        value: object = operand["value"]
        if operand["dimension"] not in {"BOOLEAN", "ENUM"}:
            value = {
                "placeholder": "NUMERIC_CONSTANT",
                "dimension": operand["dimension"],
            }
        return {"type": "constant", "dimension": operand["dimension"], "value": value}
    return operand


def _condition_shape(
    node: dict[str, Any], feature_shapes: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    kind = node["type"]
    if kind == "logical":
        return {
            "type": kind,
            "operator": node["operator"],
            "children": [
                _condition_shape(child, feature_shapes) for child in node["children"]
            ],
        }
    if kind == "range":
        return {
            "type": kind,
            "operator": node["operator"],
            "value": _operand_shape(node["value"], feature_shapes),
            "lower": _operand_shape(node["lower"], feature_shapes),
            "upper": _operand_shape(node["upper"], feature_shapes),
            "lower_inclusive": node["lower_inclusive"],
            "upper_inclusive": node["upper_inclusive"],
        }
    return {
        "type": kind,
        "operator": node["operator"],
        "left": _operand_shape(node["left"], feature_shapes),
        "right": _operand_shape(node["right"], feature_shapes),
    }


def semantic_group_record(strategy: StrategyDefinitionV3) -> dict[str, Any]:
    record = strategy.model_dump(mode="json")
    declarations = {feature["feature_id"]: feature for feature in record["features"]}
    feature_shapes: dict[str, dict[str, Any]] = {}

    def shape(feature_id: str) -> dict[str, Any]:
        existing = feature_shapes.get(feature_id)
        if existing is not None:
            return existing
        feature = declarations[feature_id]
        result = {
            "name": feature["name"],
            "version": feature["version"],
            "parameters": {
                key: _parameter_shape(key, value)
                for key, value in sorted(feature["parameters"].items())
            },
            "inputs": [shape(value) for value in feature["inputs"]],
        }
        feature_shapes[feature_id] = result
        return result

    features = sorted((shape(feature_id) for feature_id in declarations), key=canonical_json_bytes)
    time_filter = record["entry_time_filter"]
    return {
        "schema_version": "strategy-semantic-group/v1",
        "logical_asset": record["logical_asset"],
        "timeframe": record["timeframe"],
        "direction": record["direction"],
        "features": features,
        "entry_conditions": _condition_shape(record["entry_conditions"], feature_shapes),
        "time_filter": None
        if time_filter is None
        else {
            "type": "LOCAL_B3_TIME_RANGE",
            "start": {"placeholder": "TIME_BOUND", "role": "start"},
            "end": {"placeholder": "TIME_BOUND", "role": "end"},
            "start_inclusive": time_filter["start_inclusive"],
            "end_inclusive": time_filter["end_inclusive"],
        },
    }


def semantic_group_id(strategy: StrategyDefinitionV3) -> str:
    return identity("strategy-semantic-group/v1", semantic_group_record(strategy))


def complexity_metadata(strategy: StrategyDefinitionV3) -> dict[str, Any]:
    record = strategy.model_dump(mode="json")
    leaves = 0
    depth = 0
    has_cross = False
    numeric_constants = 0

    def visit(node: dict[str, Any], level: int) -> None:
        nonlocal leaves, depth, has_cross, numeric_constants
        depth = max(depth, level)
        if node["type"] == "logical":
            for child in node["children"]:
                visit(child, level + 1)
            return
        leaves += 1
        has_cross = has_cross or node["type"] == "cross"
        names = ("value", "lower", "upper") if node["type"] == "range" else ("left", "right")
        for name in names:
            operand = node[name]
            if operand["type"] == "constant" and operand["dimension"] not in {"BOOLEAN", "ENUM"}:
                numeric_constants += 1

    visit(record["entry_conditions"], 1)
    feature_numeric_parameters = sum(
        type(value) is int or (type(value) is str and _is_numeric_text(value))
        for feature in record["features"]
        for value in feature["parameters"].values()
    )
    return {
        "leaf_conditions": leaves,
        "ast_depth": depth,
        "feature_count": len(record["features"]),
        "numeric_parameter_count": feature_numeric_parameters + numeric_constants + 2,
        "has_cross": has_cross,
        "confirmation_count": max(0, leaves - 1),
        "affects_score": False,
        "affects_tie_breaking": False,
    }
