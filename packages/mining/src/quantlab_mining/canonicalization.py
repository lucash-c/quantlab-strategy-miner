"""Limited, type-checked mathematical equivalences, independent of provenance."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from quantlab_core.canonical import canonical_json_bytes
from quantlab_core.errors import ContractError
from quantlab_core.feature_engine_v2 import mathematical_policy_record
from quantlab_core.strategy_v3 import StrategyDefinitionV3

from quantlab_mining.contracts import decimal_text, identity

QUANTITY_FEATURES = {"candle_volume", "rolling_mean_volume"}
RATIO_FEATURES = {"relative_volume", "close_range_position", "n_candle_return"}
ENUM_FEATURES = {
    "candle_direction",
    "close_vs_sma",
    "close_vs_ema",
    "close_vs_vwap",
    "sma_fast_vs_slow",
    "ema_fast_vs_slow",
}


def feature_dimension(name: str) -> str:
    if name in QUANTITY_FEATURES:
        return "QUANTITY"
    if name in RATIO_FEATURES:
        return "RATIO"
    if name in ENUM_FEATURES:
        return "ENUM"
    if name.startswith("breakout_"):
        return "BOOLEAN"
    if name == "percent_change":
        return "PERCENT"
    if name == "minute_since_session_start":
        return "MINUTES"
    if name == "local_time":
        return "TIME_OF_DAY_NS"
    return "PRICE"


def stable_feature(record: dict[str, Any]) -> dict[str, Any]:
    spec = {k: record[k] for k in ("name", "version", "parameters", "inputs")}
    return {"feature_id": "f." + identity("candidate-feature/v1", spec).split(":")[1], **spec}


def normalize_condition(node: dict[str, Any], dimensions: dict[str, str]) -> dict[str, Any]:
    node = deepcopy(node)

    def operand(item: dict[str, Any]) -> str:
        match item["type"]:
            case "constant":
                if item["dimension"] not in {"BOOLEAN", "ENUM"}:
                    item["value"] = decimal_text(item["value"])
                return item["dimension"]
            case "feature":
                return dimensions[item["feature_id"]]
            case "candle_field":
                return "QUANTITY" if item["name"] == "volume" else "PRICE"
        raise ContractError("unsupported candidate operand")

    if node["type"] == "logical":
        if node["operator"] != "AND":
            raise ContractError("automatic candidates permit AND only")
        children: list[dict[str, Any]] = []
        for child in node["children"]:
            normalized = normalize_condition(child, dimensions)
            if normalized["type"] == "logical":
                children.extend(normalized["children"])
            else:
                children.append(normalized)
        children = sorted(
            {canonical_json_bytes(c): c for c in children}.values(), key=canonical_json_bytes
        )
        return children[0] if len(children) == 1 else {**node, "children": children}
    names = ("value", "lower", "upper") if node["type"] == "range" else ("left", "right")
    types = {operand(node[name]) for name in names}
    if len(types) != 1:
        raise ContractError(f"incompatible candidate dimensions: {sorted(types)}")
    if types & {"BOOLEAN", "ENUM"} and (
        node["type"] != "comparison" or node["operator"] not in {"EQ", "NE"}
    ):
        raise ContractError("ordered comparison/cross/range requires numeric operands")
    if node["type"] == "range":
        return node
    op = node["operator"]
    if op in {"LT", "LTE", "CROSS_BELOW"}:
        node["left"], node["right"] = node["right"], node["left"]
        node["operator"] = {"LT": "GT", "LTE": "GTE", "CROSS_BELOW": "CROSS_ABOVE"}[op]
    elif op in {"EQ", "NE"} and canonical_json_bytes(node["left"]) > canonical_json_bytes(
        node["right"]
    ):
        node["left"], node["right"] = node["right"], node["left"]
    return node


def canonical_strategy(strategy: StrategyDefinitionV3) -> StrategyDefinitionV3:
    record = strategy.model_dump(mode="json")
    aliases: dict[str, str] = {}
    features: dict[str, dict[str, Any]] = {}
    dimensions: dict[str, str] = {}
    for declaration in record["features"]:
        old_id = declaration["feature_id"]
        declaration["inputs"] = [aliases[i] for i in declaration["inputs"]]
        canonical = stable_feature(declaration)
        fid = canonical["feature_id"]
        aliases[old_id] = fid
        features[fid] = canonical
        dimensions[fid] = feature_dimension(canonical["name"])

    def rename(value: Any) -> None:
        if isinstance(value, dict):
            if value.get("type") == "feature":
                value["feature_id"] = aliases[value["feature_id"]]
            for item in value.values():
                rename(item)
        elif isinstance(value, list):
            for item in value:
                rename(item)

    rename(record["entry_conditions"])
    record["entry_conditions"] = normalize_condition(record["entry_conditions"], dimensions)
    needed: set[str] = set()

    def references(value: Any) -> None:
        if isinstance(value, dict):
            if value.get("type") == "feature":
                needed.add(value["feature_id"])
            for item in value.values():
                references(item)
        elif isinstance(value, list):
            for item in value:
                references(item)

    references(record["entry_conditions"])
    pending = list(needed)
    while pending:
        for dep in features[pending.pop()]["inputs"]:
            if dep not in needed:
                needed.add(dep)
                pending.append(dep)
    ordered: list[dict[str, Any]] = []
    emitted: set[str] = set()
    while emitted != needed:
        ready = sorted(fid for fid in needed - emitted if set(features[fid]["inputs"]) <= emitted)
        if not ready:
            raise ContractError("feature dependency cycle")
        for fid in ready:
            ordered.append(features[fid])
            emitted.add(fid)
    record["features"] = ordered
    for key in ("take_profit", "stop_loss"):
        record[key]["value"] = decimal_text(record[key]["value"])
    for key in ("cost_model", "slippage_model"):
        if "points_per_side" in record[key]:
            record[key]["points_per_side"] = decimal_text(record[key]["points_per_side"])
    return StrategyDefinitionV3.model_validate(record)


def candidate_payload(strategy: StrategyDefinitionV3) -> dict[str, Any]:
    record = canonical_strategy(strategy).model_dump(mode="json")
    for key in ("strategy_id", "strategy_version", "name", "cost_model", "slippage_model"):
        record.pop(key)
    return {**record, "mathematical_policy": mathematical_policy_record()}


def candidate_id(strategy: StrategyDefinitionV3) -> str:
    return identity("candidate-semantic/v1", candidate_payload(strategy))


def named_candidate(strategy: StrategyDefinitionV3) -> tuple[str, StrategyDefinitionV3]:
    normalized = canonical_strategy(strategy)
    cid = candidate_id(normalized)
    return cid, normalized.model_copy(
        update={
            "strategy_id": "c." + cid.split(":")[1],
            "strategy_version": 1,
            "name": "Generated formal candidate",
        }
    )
