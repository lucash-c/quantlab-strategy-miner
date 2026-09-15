"""Stateful, exact evaluator for Strategy Definition v3 conditions."""

from __future__ import annotations

from dataclasses import dataclass

from quantlab_core.errors import ContractError
from quantlab_core.feature_engine_v2 import FeatureObservation, FeatureValue
from quantlab_core.market_data import Candle
from quantlab_core.numeric import CanonicalRational
from quantlab_core.sessions import TradingSession
from quantlab_core.strategy_v3 import (
    CandleFieldOperandV3,
    ComparisonConditionV3,
    ConditionNodeV3,
    ConstantOperandV3,
    CrossConditionV3,
    FeatureOperandV3,
    LogicalConditionV3,
    OperandV3,
    RangeConditionV3,
    TimeRangeV3,
    time_text_to_minute,
)
from quantlab_core.time import NANOSECONDS_PER_MINUTE

CONDITION_ENGINE_V2_VERSION = "2.0.0"
_DAY_NS = 24 * 60 * NANOSECONDS_PER_MINUTE
_B3_OFFSET_NS = 3 * 60 * NANOSECONDS_PER_MINUTE


@dataclass(frozen=True, slots=True)
class FeatureFrame:
    session: TradingSession
    candle: Candle
    features: dict[str, FeatureObservation]

    @property
    def available_at_ns_utc(self) -> int:
        return self.candle.close_time_ns_utc

    @property
    def executable_in_session(self) -> bool:
        return self.available_at_ns_utc <= self.session.last_event_ns_utc


@dataclass(frozen=True, slots=True)
class ConditionEvaluation:
    result: bool
    snapshot: dict[str, object]


@dataclass(frozen=True, slots=True)
class _Resolved:
    dimension: str
    numeric: CanonicalRational | None = None
    boolean: bool | None = None
    text: str | None = None

    def to_record(self) -> dict[str, object]:
        return {
            "dimension": self.dimension,
            "numeric": self.numeric.to_record() if self.numeric is not None else None,
            "boolean": self.boolean,
            "text": self.text,
        }


def _resolved_feature(value: FeatureValue) -> _Resolved:
    return _Resolved(value.dimension, value.numeric, value.boolean, value.text)


def _resolve_operand(
    operand: OperandV3,
    frame: FeatureFrame,
) -> tuple[_Resolved | None, dict[str, object]]:
    if isinstance(operand, CandleFieldOperandV3):
        candle = frame.candle
        if operand.name == "volume":
            value = _Resolved("QUANTITY", numeric=CanonicalRational(candle.volume))
        else:
            price = {
                "open": candle.open_units,
                "high": candle.high_units,
                "low": candle.low_units,
                "close": candle.close_units,
            }[operand.name]
            value = _Resolved(
                "PRICE",
                numeric=CanonicalRational(price, 10**frame.session.price_scale),
            )
        return value, {"operand": operand.model_dump(mode="json"), "value": value.to_record()}
    if isinstance(operand, FeatureOperandV3):
        try:
            observation = frame.features[operand.feature_id]
        except KeyError as exc:
            raise ContractError(f"missing feature observation: {operand.feature_id}") from exc
        value = None if observation.value is None else _resolved_feature(observation.value)
        return value, {
            "operand": operand.model_dump(mode="json"),
            "value": None if value is None else value.to_record(),
            "warmup_status": observation.warmup_status,
            "undefined_reason": observation.undefined_reason,
        }
    if isinstance(operand, ConstantOperandV3):
        if operand.dimension == "BOOLEAN":
            value = _Resolved("BOOLEAN", boolean=operand.value == "true")
        elif operand.dimension == "ENUM":
            value = _Resolved("ENUM", text=operand.value)
        else:
            value = _Resolved(
                operand.dimension,
                numeric=CanonicalRational.from_decimal(operand.value),
            )
        return value, {"operand": operand.model_dump(mode="json"), "value": value.to_record()}
    raise ContractError(f"unsupported operand type: {type(operand).__name__}")


def _compare(left: _Resolved, right: _Resolved, operator: str) -> bool:
    enum_wildcard = (
        left.text is not None
        and right.text is not None
        and "ENUM" in {left.dimension, right.dimension}
    )
    if left.dimension != right.dimension and not enum_wildcard:
        raise ContractError(f"cannot compare {left.dimension} with {right.dimension}")
    if left.numeric is not None and right.numeric is not None:
        comparison = left.numeric.compare(right.numeric)
    elif left.boolean is not None and right.boolean is not None:
        comparison = (left.boolean > right.boolean) - (left.boolean < right.boolean)
    elif left.text is not None and right.text is not None:
        comparison = (left.text > right.text) - (left.text < right.text)
    else:
        raise ContractError("operand value kinds do not match")
    return {
        "GT": comparison > 0,
        "GTE": comparison >= 0,
        "LT": comparison < 0,
        "LTE": comparison <= 0,
        "EQ": comparison == 0,
        "NE": comparison != 0,
    }[operator]


class ConditionEvaluatorV2:
    def __init__(self) -> None:
        self._cross_previous: dict[str, tuple[CanonicalRational, CanonicalRational] | None] = {}

    def evaluate(self, condition: ConditionNodeV3, frame: FeatureFrame) -> ConditionEvaluation:
        result, snapshot = self._visit(condition, frame, "root")
        return ConditionEvaluation(result, snapshot)

    def _visit(
        self,
        condition: ConditionNodeV3,
        frame: FeatureFrame,
        path: str,
    ) -> tuple[bool, dict[str, object]]:
        if isinstance(condition, LogicalConditionV3):
            children = [
                self._visit(child, frame, f"{path}.{index}")
                for index, child in enumerate(condition.children)
            ]
            values = [item[0] for item in children]
            result = (
                all(values)
                if condition.operator == "AND"
                else any(values)
                if condition.operator == "OR"
                else not values[0]
            )
            return result, {
                "type": "logical",
                "operator": condition.operator,
                "result": result,
                "children": [item[1] for item in children],
            }
        if isinstance(condition, RangeConditionV3):
            value, value_snapshot = _resolve_operand(condition.value, frame)
            lower, lower_snapshot = _resolve_operand(condition.lower, frame)
            upper, upper_snapshot = _resolve_operand(condition.upper, frame)
            defined = value is not None and lower is not None and upper is not None
            if not defined:
                result = False
            else:
                lower_ok = _compare(
                    value,
                    lower,
                    "GTE" if condition.lower_inclusive else "GT",
                )
                upper_ok = _compare(
                    value,
                    upper,
                    "LTE" if condition.upper_inclusive else "LT",
                )
                inside = lower_ok and upper_ok
                result = inside if condition.operator == "BETWEEN" else not inside
            return result, {
                "type": "range",
                "operator": condition.operator,
                "result": result,
                "value": value_snapshot,
                "lower": lower_snapshot,
                "upper": upper_snapshot,
                "lower_inclusive": condition.lower_inclusive,
                "upper_inclusive": condition.upper_inclusive,
            }
        left, left_snapshot = _resolve_operand(condition.left, frame)
        right, right_snapshot = _resolve_operand(condition.right, frame)
        if isinstance(condition, CrossConditionV3):
            previous = self._cross_previous.get(path)
            if left is not None and right is not None and left.dimension != right.dimension:
                raise ContractError(f"cannot cross {left.dimension} with {right.dimension}")
            if left is None or right is None or left.numeric is None or right.numeric is None:
                self._cross_previous[path] = None
                result = False
            elif previous is None:
                self._cross_previous[path] = (left.numeric, right.numeric)
                result = False
            else:
                previous_comparison = previous[0].compare(previous[1])
                current_comparison = left.numeric.compare(right.numeric)
                result = (
                    previous_comparison <= 0 and current_comparison > 0
                    if condition.operator == "CROSS_ABOVE"
                    else previous_comparison >= 0 and current_comparison < 0
                )
                self._cross_previous[path] = (left.numeric, right.numeric)
            return result, {
                "type": "cross",
                "operator": condition.operator,
                "result": result,
                "left": left_snapshot,
                "right": right_snapshot,
                "previous": None
                if previous is None
                else {
                    "left": previous[0].to_record(),
                    "right": previous[1].to_record(),
                },
            }
        if not isinstance(condition, ComparisonConditionV3):
            raise ContractError(f"unsupported condition: {type(condition).__name__}")
        result = left is not None and right is not None and _compare(
            left, right, condition.operator
        )
        return result, {
            "type": "comparison",
            "operator": condition.operator,
            "result": result,
            "left": left_snapshot,
            "right": right_snapshot,
        }


def time_range_contains(time_range: TimeRangeV3, timestamp_ns_utc: int) -> bool:
    local_ns = (timestamp_ns_utc - _B3_OFFSET_NS) % _DAY_NS
    start_ns = time_text_to_minute(time_range.start) * NANOSECONDS_PER_MINUTE
    end_ns = time_text_to_minute(time_range.end) * NANOSECONDS_PER_MINUTE
    lower = local_ns >= start_ns if time_range.start_inclusive else local_ns > start_ns
    upper = local_ns <= end_ns if time_range.end_inclusive else local_ns < end_ns
    return lower and upper
