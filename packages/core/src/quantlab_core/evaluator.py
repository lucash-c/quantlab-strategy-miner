"""Exact evaluator for the versioned strategy AST."""

from __future__ import annotations

from dataclasses import dataclass

from quantlab_core.errors import ContractError
from quantlab_core.market_data import FeatureRow
from quantlab_core.price import decimal_to_units
from quantlab_core.strategy import (
    ComparisonCondition,
    ConditionNode,
    DecimalOperand,
    FieldOperand,
    IndicatorOperand,
    LogicalGroup,
    Operand,
)

STRATEGY_EVALUATOR_VERSION = "1.0.0"


@dataclass(frozen=True, slots=True)
class ExactValue:
    numerator: int
    denominator: int
    dimension: str


def _resolve_operand(operand: Operand, feature: FeatureRow, price_scale: int) -> ExactValue | None:
    candle = feature.candle
    if isinstance(operand, FieldOperand):
        fields = {
            "open": ExactValue(candle.open_units, 1, "PRICE"),
            "high": ExactValue(candle.high_units, 1, "PRICE"),
            "low": ExactValue(candle.low_units, 1, "PRICE"),
            "close": ExactValue(candle.close_units, 1, "PRICE"),
            "volume": ExactValue(candle.volume, 1, "QUANTITY"),
        }
        return fields[operand.name]
    if isinstance(operand, DecimalOperand):
        return ExactValue(decimal_to_units(operand.value, price_scale), 1, "PRICE")
    if isinstance(operand, IndicatorOperand):
        if operand.name != "sma_close":
            raise ContractError(f"unsupported indicator: {operand.name}")
        if feature.sma_close_period != operand.parameters.period:
            if feature.sma_close_period is None:
                return None
            raise ContractError("materialized SMA period does not match the strategy")
        if feature.sma_close_sum_units is None:
            return None
        return ExactValue(
            feature.sma_close_sum_units,
            feature.sma_close_period,
            "PRICE",
        )
    raise ContractError(f"unsupported operand type: {type(operand).__name__}")


def _evaluate_comparison(
    condition: ComparisonCondition,
    feature: FeatureRow,
    price_scale: int,
) -> bool:
    left = _resolve_operand(condition.left, feature, price_scale)
    right = _resolve_operand(condition.right, feature, price_scale)
    if left is None or right is None:
        return False
    if left.dimension != right.dimension:
        raise ContractError(
            f"cannot compare {left.dimension.lower()} with {right.dimension.lower()}"
        )
    left_cross = left.numerator * right.denominator
    right_cross = right.numerator * left.denominator
    return {
        "GT": left_cross > right_cross,
        "GTE": left_cross >= right_cross,
        "LT": left_cross < right_cross,
        "LTE": left_cross <= right_cross,
        "EQ": left_cross == right_cross,
    }[condition.operator]


def evaluate_condition(
    condition: ConditionNode,
    feature: FeatureRow,
    price_scale: int,
) -> bool:
    if isinstance(condition, ComparisonCondition):
        return _evaluate_comparison(condition, feature, price_scale)
    if isinstance(condition, LogicalGroup):
        if condition.operator == "AND":
            return all(
                evaluate_condition(child, feature, price_scale) for child in condition.children
            )
        if condition.operator == "OR":
            return any(
                evaluate_condition(child, feature, price_scale) for child in condition.children
            )
        return not evaluate_condition(condition.children[0], feature, price_scale)
    raise ContractError(f"unsupported condition type: {type(condition).__name__}")
