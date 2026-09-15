"""Manual strategy schema for session-aware historical validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator

from quantlab_core.canonical import canonical_json_bytes, sha256_bytes
from quantlab_core.errors import ContractError
from quantlab_core.price import normalize_decimal_text
from quantlab_core.strategy import (
    ComparisonCondition,
    ConditionNode,
    Identifier,
    IndicatorOperand,
    LogicalGroup,
    NoCostModel,
    NoSlippageModel,
    SmaDefinition,
    StrictModel,
)

HISTORICAL_STRATEGY_SCHEMA_VERSION = "strategy-definition/v2"
HISTORICAL_STRATEGY_SCHEMA_ID = (
    "https://quantlab.local/schemas/strategy-definition/v2.schema.json"
)


class HistoricalPointDistance(StrictModel):
    unit: Literal["POINTS"]
    value: str

    @field_validator("value")
    @classmethod
    def validate_positive_decimal(cls, value: str) -> str:
        normalized, _ = normalize_decimal_text(value)
        if normalized == "0":
            raise ValueError("distance must be positive")
        return value


class HistoricalExecutionPolicy(StrictModel):
    entry_fill: Literal["NEXT_TRADE"]
    position_policy: Literal["SINGLE_POSITION_NO_QUEUE"]
    same_tick_reentry: Literal[False]
    session_end: Literal["CLOSE_AT_LAST_TRADE"]
    require_post_fill_event: Literal[True]
    position_size: Literal[1]


class HistoricalStrategyDefinition(StrictModel):
    schema_version: Literal[HISTORICAL_STRATEGY_SCHEMA_VERSION]
    strategy_id: Identifier
    strategy_version: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=200)
    logical_asset: Identifier
    timeframe: Literal["1m", "2m", "5m", "15m"]
    evaluation_mode: Literal["ON_CLOSE"]
    direction: Literal["BUY", "SELL"]
    required_indicators: tuple[SmaDefinition, ...] = Field(strict=False)
    entry_conditions: LogicalGroup
    take_profit: HistoricalPointDistance
    stop_loss: HistoricalPointDistance
    execution: HistoricalExecutionPolicy
    cost_model: NoCostModel
    slippage_model: NoSlippageModel

    @model_validator(mode="after")
    def validate_indicator_references(self) -> HistoricalStrategyDefinition:
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
            if not isinstance(condition, ComparisonCondition):
                raise ValueError("unsupported condition node")
            for operand in (condition.left, condition.right):
                if isinstance(operand, IndicatorOperand):
                    references.add((operand.name, operand.version, operand.parameters.period))

        visit(self.entry_conditions)
        if references != definitions:
            raise ValueError(
                "required_indicators must exactly match indicators referenced by entry_conditions"
            )
        if len(definitions) != 1:
            raise ValueError("the third increment requires exactly one SMA definition")
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


def load_historical_strategy(path: Path) -> HistoricalStrategyDefinition:
    try:
        raw = json.loads(
            path.read_text(encoding="utf-8"),
            parse_float=_reject_json_float,
            parse_constant=_reject_json_float,
        )
        return HistoricalStrategyDefinition.model_validate(raw)
    except ContractError:
        raise
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise ContractError(f"invalid historical strategy definition: {exc}") from exc


def historical_strategy_json_schema() -> dict[str, object]:
    schema = HistoricalStrategyDefinition.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = HISTORICAL_STRATEGY_SCHEMA_ID
    return schema

