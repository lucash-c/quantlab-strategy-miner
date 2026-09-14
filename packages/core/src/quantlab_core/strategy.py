"""Formal, versioned manual strategy schema for the first increment."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from quantlab_core.canonical import canonical_json_bytes, sha256_bytes
from quantlab_core.errors import ContractError
from quantlab_core.indicators import SMA_CLOSE_VERSION
from quantlab_core.price import normalize_decimal_text

STRATEGY_SCHEMA_VERSION = "strategy-definition/v1"
STRATEGY_SCHEMA_ID = "https://quantlab.local/schemas/strategy-definition/v1.schema.json"
Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class SmaParameters(StrictModel):
    period: int = Field(ge=1, le=100_000)


class SmaDefinition(StrictModel):
    name: Literal["sma_close"]
    version: Literal[SMA_CLOSE_VERSION]
    parameters: SmaParameters


class FieldOperand(StrictModel):
    type: Literal["field"]
    name: Literal["open", "high", "low", "close", "volume"]


class IndicatorOperand(StrictModel):
    type: Literal["indicator"]
    name: Literal["sma_close"]
    version: Literal[SMA_CLOSE_VERSION]
    parameters: SmaParameters


class DecimalOperand(StrictModel):
    type: Literal["decimal"]
    value: str

    @field_validator("value")
    @classmethod
    def validate_decimal(cls, value: str) -> str:
        normalize_decimal_text(value)
        return value


type Operand = Annotated[
    FieldOperand | IndicatorOperand | DecimalOperand,
    Field(discriminator="type"),
]


class ComparisonCondition(StrictModel):
    type: Literal["comparison"]
    operator: Literal["GT", "GTE", "LT", "LTE", "EQ"]
    left: Operand
    right: Operand


class LogicalGroup(StrictModel):
    type: Literal["logical"]
    operator: Literal["AND", "OR", "NOT"]
    children: tuple[ConditionNode, ...] = Field(strict=False)

    @model_validator(mode="after")
    def validate_arity(self) -> LogicalGroup:
        if not self.children:
            raise ValueError("logical groups cannot be empty")
        if self.operator == "NOT" and len(self.children) != 1:
            raise ValueError("NOT requires exactly one child")
        return self


type ConditionNode = Annotated[
    ComparisonCondition | LogicalGroup,
    Field(discriminator="type"),
]
LogicalGroup.model_rebuild()


class PointDistance(StrictModel):
    unit: Literal["POINTS"]
    value: str

    @field_validator("value")
    @classmethod
    def validate_positive_decimal(cls, value: str) -> str:
        normalized, _ = normalize_decimal_text(value)
        if normalized == "0":
            raise ValueError("distance must be positive")
        return value


class ExecutionPolicy(StrictModel):
    entry_fill: Literal["NEXT_TRADE"]
    position_policy: Literal["SINGLE_POSITION_NO_QUEUE"]
    same_tick_reentry: Literal[False]
    end_of_data: Literal["CLOSE_AT_LAST_TRADE", "LEAVE_OPEN"]
    position_size: Literal[1]


class NoCostModel(StrictModel):
    type: Literal["NONE"]


class NoSlippageModel(StrictModel):
    type: Literal["NONE"]


class StrategyDefinition(StrictModel):
    schema_version: Literal[STRATEGY_SCHEMA_VERSION]
    strategy_id: Identifier
    strategy_version: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=200)
    symbol: Identifier
    timeframe: Literal["1m"]
    evaluation_mode: Literal["ON_CLOSE"]
    direction: Literal["BUY", "SELL"]
    required_indicators: tuple[SmaDefinition, ...] = Field(strict=False)
    entry_conditions: LogicalGroup
    take_profit: PointDistance
    stop_loss: PointDistance
    execution: ExecutionPolicy
    cost_model: NoCostModel
    slippage_model: NoSlippageModel

    @model_validator(mode="after")
    def validate_indicator_references(self) -> StrategyDefinition:
        definitions = {
            (definition.name, definition.version, definition.parameters.period)
            for definition in self.required_indicators
        }
        if len(definitions) != len(self.required_indicators):
            raise ValueError("required_indicators contains duplicates")

        references: set[tuple[str, str, int]] = set()

        def visit(condition: ConditionNode) -> None:
            if isinstance(condition, LogicalGroup):
                for child in condition.children:
                    visit(child)
                return
            for operand in (condition.left, condition.right):
                if isinstance(operand, IndicatorOperand):
                    references.add((operand.name, operand.version, operand.parameters.period))

        visit(self.entry_conditions)
        if references != definitions:
            raise ValueError(
                "required_indicators must exactly match indicators referenced by entry_conditions"
            )
        if len(definitions) != 1:
            raise ValueError("the first increment requires exactly one SMA definition")
        return self

    @property
    def sma_period(self) -> int:
        return self.required_indicators[0].parameters.period

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.model_dump(mode="json"))

    def semantic_sha256(self) -> str:
        return sha256_bytes(self.canonical_bytes())


def _reject_json_float(value: str) -> None:
    raise ContractError(f"JSON floating-point number is forbidden: {value}")


def load_strategy(path: Path) -> StrategyDefinition:
    try:
        raw = json.loads(
            path.read_text(encoding="utf-8"),
            parse_float=_reject_json_float,
            parse_constant=_reject_json_float,
        )
        return StrategyDefinition.model_validate(raw)
    except ContractError:
        raise
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise ContractError(f"invalid strategy definition: {exc}") from exc


def strategy_json_schema() -> dict[str, object]:
    schema = StrategyDefinition.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = STRATEGY_SCHEMA_ID
    return schema
