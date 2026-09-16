"""Six controlled entry families and explicitly selected closed confirmations."""

from __future__ import annotations

import itertools
import math
from collections.abc import Iterator
from typing import Any

from quantlab_core.errors import ContractError
from quantlab_core.feature_specs import parse_feature_spec
from quantlab_core.numeric import CanonicalRational
from quantlab_core.strategy_v3 import StrategyDefinitionV3

from quantlab_mining.canonicalization import stable_feature
from quantlab_mining.contracts import AtomGrid, GenerationPolicyV1, MiningSearchSpaceV1

FAMILIES = {
    "trend_close",
    "trend_pair",
    "vwap_context",
    "breakout_previous",
    "momentum",
    "candle_context",
}
CONFIRMATION_PROFILES = {
    "trend_close": [
        {"volume"},
        {"vwap_context"},
        {"volatility"},
        {"volume", "vwap_context"},
        {"volume", "volatility"},
    ],
    "trend_pair": [
        {"volume"},
        {"vwap_context"},
        {"volatility"},
        {"volume", "vwap_context"},
        {"volume", "volatility"},
    ],
    "vwap_context": [{"volume"}, {"volatility"}],
    "breakout_previous": [{"volume"}, {"volatility"}, {"volume", "volatility"}],
    "momentum": [{"volume"}, {"candle_context"}, {"volume", "candle_context"}],
    "candle_context": [{"volume"}, {"momentum"}],
}


def _requirements(atom: AtomGrid, grids: dict[str, tuple]) -> dict[str, str | set]:
    def mode() -> str:
        if "mode" not in grids or len(grids["mode"]) != 1:
            raise ContractError("mode must be one explicit value per template atom")
        return grids["mode"][0]

    match atom.kind:
        case "trend_close":
            return {
                "average": {"sma_close", "ema_close"},
                "period": "period",
                "operator": {"COMPARE", "CROSS"},
            }
        case "trend_pair":
            return {
                "average": {"sma_close", "ema_close"},
                "short_period": "period",
                "long_period": "period",
                "operator": {"COMPARE", "CROSS"},
            }
        case "breakout_previous":
            return {"period": "period", "include_current": {False}}
        case "vwap_context":
            if mode() == "CLOSE":
                return {"mode": {"CLOSE"}, "operator": {"COMPARE", "CROSS"}}
            return {
                "mode": {"DISTANCE"},
                "lower": "decimal",
                "upper": "decimal",
                "lower_inclusive": "bool",
                "upper_inclusive": "bool",
            }
        case "momentum":
            return {
                "feature": {"point_change", "n_candle_return"},
                "period": "period",
                "threshold": "decimal",
            }
        case "candle_context":
            selected = mode()
            if selected == "DIRECTION":
                return {"mode": {"DIRECTION"}}
            if selected == "GEOMETRY":
                return {
                    "mode": {"GEOMETRY"},
                    "feature": {"absolute_body", "candle_range"},
                    "threshold": "nonnegative",
                }
            return {
                "mode": {"CLOSE_POSITION"},
                "lower": "decimal",
                "upper": "decimal",
                "lower_inclusive": "bool",
                "upper_inclusive": "bool",
            }
        case "volume":
            selected = mode()
            if selected == "ROLLING":
                return {"mode": {"ROLLING"}, "period": "period", "include_current": "bool"}
            if selected == "RELATIVE":
                return {
                    "mode": {"RELATIVE"},
                    "period": "period",
                    "include_current": "bool",
                    "threshold": "nonnegative",
                }
            return {"mode": {"RAW"}, "threshold": "nonnegative"}
        case "volatility":
            selected = mode()
            if selected == "ROLLING_RANGE":
                return {"mode": {"ROLLING_RANGE"}, "period": "period", "include_current": "bool"}
            if selected == "ATR_THRESHOLD":
                return {"mode": {"ATR_THRESHOLD"}, "period": "period", "threshold": "nonnegative"}
            return {
                "mode": {"ATR_RANGE"},
                "period": "period",
                "lower": "nonnegative",
                "upper": "nonnegative",
                "lower_inclusive": "bool",
                "upper_inclusive": "bool",
            }
    raise ContractError("unregistered template atom")


def validate_search(space: MiningSearchSpaceV1, policy: GenerationPolicyV1) -> int:
    if len(space.templates) > policy.max_templates:
        raise ContractError("SEARCH_SPACE_POLICY_LIMIT: templates")
    globals_ = (space.timeframes, space.stops, space.targets, space.time_windows)
    for values in (*globals_, space.directions):
        if len(values) > policy.max_grid_values:
            raise ContractError("SEARCH_SPACE_POLICY_LIMIT: grid values")
    total = 0
    for entry in space.templates:
        family = entry.base.kind
        if family not in FAMILIES:
            raise ContractError("confirmation cannot be an entry family")
        confirmations = {a.kind for a in entry.confirmations}
        if entry.confirmations and confirmations not in CONFIRMATION_PROFILES[family]:
            raise ContractError("confirmation profile is not whitelisted")
        if len(confirmations) != len(entry.confirmations):
            raise ContractError("confirmation kinds cannot repeat")
        if 1 + len(entry.confirmations) > policy.max_conditions:
            raise ContractError("SEARCH_SPACE_POLICY_LIMIT: conditions")
        if entry.confirmations and policy.max_ast_depth < 2:
            raise ContractError("SEARCH_SPACE_POLICY_LIMIT: AST depth")
        for direction in space.directions:
            axes: list[tuple] = list(globals_)
            cross_count = 0
            for atom in (entry.base, *entry.confirmations):
                grids = atom.effective(direction)
                requirements = _requirements(atom, grids)
                if set(grids) != set(requirements):
                    raise ContractError(f"{atom.kind} requires exactly {sorted(requirements)}")
                # Signed directional thresholds/ranges must never be silently negated.
                if (
                    atom.kind == "momentum"
                    or (atom.kind == "vwap_context" and grids.get("mode") == ("DISTANCE",))
                    or (atom.kind == "candle_context" and grids.get("mode") == ("CLOSE_POSITION",))
                ):
                    directional = {"threshold"} if atom.kind == "momentum" else {"lower", "upper"}
                    if not directional <= set(atom.direction_grids.get(direction, {})):
                        raise ContractError(
                            "directional thresholds require explicit BUY/SELL bindings"
                        )
                for key, values in grids.items():
                    if len(values) > policy.max_grid_values:
                        raise ContractError("SEARCH_SPACE_POLICY_LIMIT: grid values")
                    rule = requirements[key]
                    for value in values:
                        if rule == "period":
                            if (
                                type(value) is not int
                                or not policy.min_period <= value <= policy.max_period
                            ):
                                raise ContractError("generated period outside generation policy")
                        elif rule == "bool":
                            if type(value) is not bool:
                                raise ContractError("include_current/inclusivity must be boolean")
                        elif rule in ("decimal", "nonnegative"):
                            if not isinstance(value, str):
                                raise ContractError("numeric grids require exact decimal strings")
                            number = CanonicalRational.from_decimal(value)
                            if rule == "nonnegative" and number.numerator < 0:
                                raise ContractError("threshold cannot be negative")
                        elif isinstance(rule, set) and (
                            value not in rule or any(type(value) is not type(v) for v in rule)
                        ):
                            raise ContractError(f"unsupported {atom.kind}.{key} value: {value}")
                    axes.append(values)
                if "CROSS" in grids.get("operator", ()):
                    cross_count += 1
            if cross_count > policy.max_cross_conditions:
                raise ContractError("SEARCH_SPACE_POLICY_LIMIT: CROSS")
            if (
                sum(len(axis) > 1 for axis in axes) + (len(space.directions) > 1)
                > policy.max_variable_dimensions
            ):
                raise ContractError("SEARCH_SPACE_POLICY_LIMIT: variable dimensions")
            total += math.prod(len(axis) for axis in axes)
    if total > policy.expansion_limit:
        raise ContractError(f"SEARCH_SPACE_EXPANSION_LIMIT_EXCEEDED: T={total}")
    return total


def bindings(atom: AtomGrid, direction: str) -> Iterator[dict[str, Any]]:
    grids = atom.effective(direction)
    keys = sorted(grids)
    for values in itertools.product(*(grids[key] for key in keys)):
        yield dict(zip(keys, values, strict=True))


class StrategyBuilder:
    def __init__(self, direction: str):
        self.direction = direction
        self.features: dict[str, dict[str, Any]] = {}

    def feature(self, name: str, parameters: dict | None = None, inputs: tuple[str, ...] = ()):
        spec = stable_feature(
            {
                "name": name,
                "version": "1.0.0",
                "parameters": parameters or {},
                "inputs": list(inputs),
            }
        )
        parse_feature_spec(spec)
        self.features[spec["feature_id"]] = spec
        return {"type": "feature", "feature_id": spec["feature_id"]}

    @staticmethod
    def constant(dimension: str, value: str):
        return {"type": "constant", "dimension": dimension, "value": value}

    @staticmethod
    def field(name: str):
        return {"type": "candle_field", "name": name}

    def comparison(self, left: dict, right: dict, directional: bool = True, cross: bool = False):
        above = not directional or self.direction == "BUY"
        return {
            "type": "cross" if cross else "comparison",
            "operator": ("CROSS_ABOVE" if above else "CROSS_BELOW")
            if cross
            else ("GT" if above else "LT"),
            "left": left,
            "right": right,
        }

    def range(self, operand: dict, p: dict, dimension: str):
        return {
            "type": "range",
            "operator": "BETWEEN",
            "value": operand,
            "lower": self.constant(dimension, p["lower"]),
            "upper": self.constant(dimension, p["upper"]),
            "lower_inclusive": p["lower_inclusive"],
            "upper_inclusive": p["upper_inclusive"],
        }

    def atom(self, kind: str, p: dict):
        period = {"period": p["period"]} if "period" in p else {}
        rolling = (
            {**period, "include_current": p["include_current"]}
            if "include_current" in p
            else period
        )
        cross = p.get("operator") == "CROSS"
        match kind:
            case "trend_close":
                return self.comparison(
                    self.field("close"), self.feature(p["average"], period), cross=cross
                )
            case "trend_pair":
                return self.comparison(
                    self.feature(p["average"], {"period": p["short_period"]}),
                    self.feature(p["average"], {"period": p["long_period"]}),
                    cross=cross,
                )
            case "vwap_context":
                vwap = self.feature("session_trade_vwap")
                if p["mode"] == "CLOSE":
                    return self.comparison(self.field("close"), vwap, cross=cross)
                distance = self.feature("distance_to_vwap", inputs=(vwap["feature_id"],))
                return self.range(distance, p, "PRICE")
            case "breakout_previous":
                return self.comparison(
                    self.field("close"),
                    self.feature(
                        "rolling_high" if self.direction == "BUY" else "rolling_low", rolling
                    ),
                )
            case "momentum":
                return self.comparison(
                    self.feature(p["feature"], period),
                    self.constant(
                        "PRICE" if p["feature"] == "point_change" else "RATIO", p["threshold"]
                    ),
                )
            case "candle_context":
                if p["mode"] == "DIRECTION":
                    return {
                        "type": "comparison",
                        "operator": "EQ",
                        "left": self.feature("candle_direction"),
                        "right": self.constant("ENUM", "UP" if self.direction == "BUY" else "DOWN"),
                    }
                if p["mode"] == "GEOMETRY":
                    return self.comparison(
                        self.feature(p["feature"]), self.constant("PRICE", p["threshold"]), False
                    )
                return self.range(self.feature("close_range_position"), p, "RATIO")
            case "volume":
                if p["mode"] == "ROLLING":
                    return self.comparison(
                        self.field("volume"), self.feature("rolling_mean_volume", rolling), False
                    )
                if p["mode"] == "RELATIVE":
                    return self.comparison(
                        self.feature("relative_volume", rolling),
                        self.constant("RATIO", p["threshold"]),
                        False,
                    )
                return self.comparison(
                    self.field("volume"), self.constant("QUANTITY", p["threshold"]), False
                )
            case "volatility":
                if p["mode"] == "ROLLING_RANGE":
                    return self.comparison(
                        self.feature("candle_range"),
                        self.feature("rolling_mean_range", rolling),
                        False,
                    )
                atr = self.feature("atr_wilder", period)
                if p["mode"] == "ATR_RANGE":
                    return self.range(atr, p, "PRICE")
                return self.comparison(atr, self.constant("PRICE", p["threshold"]), False)
        raise ContractError("unregistered template")

    def strategy(
        self,
        space: MiningSearchSpaceV1,
        timeframe: str,
        stop: str,
        target: str,
        window: Any,
        atoms: list[dict],
    ) -> StrategyDefinitionV3:
        return StrategyDefinitionV3.model_validate(
            {
                "schema_version": "strategy-definition/v3",
                "strategy_id": "GENERATION.PLACEHOLDER",
                "strategy_version": 1,
                "name": "Generated formal candidate",
                "logical_asset": space.logical_asset,
                "timeframe": timeframe,
                "evaluation_mode": "ON_CLOSE",
                "direction": self.direction,
                "features": list(self.features.values()),
                "entry_conditions": atoms[0]
                if len(atoms) == 1
                else {"type": "logical", "operator": "AND", "children": atoms},
                "entry_time_filter": window.model_dump(mode="json"),
                "stop_loss": {"unit": "POINTS", "value": stop},
                "take_profit": {"unit": "POINTS", "value": target},
                "execution": {
                    "entry_fill": "NEXT_TRADE",
                    "position_policy": "SINGLE_POSITION_NO_QUEUE",
                    "same_tick_reentry": False,
                    "session_end": "CLOSE_AT_LAST_TRADE",
                    "require_post_fill_event": True,
                    "position_size": 1,
                },
                "cost_model": {"type": "NONE", "version": "1.0.0"},
                "slippage_model": {"type": "NONE", "version": "1.0.0"},
            }
        )
