"""Safe, additive Strategy Definition v3 for the formal feature vocabulary."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field, field_validator, model_validator

from quantlab_core.canonical import canonical_json_bytes, sha256_bytes
from quantlab_core.errors import ContractError
from quantlab_core.feature_specs import FeatureSpec, parse_feature_spec
from quantlab_core.numeric import CanonicalRational
from quantlab_core.strategy import Identifier, NoCostModel, NoSlippageModel, StrictModel

STRATEGY_V3_SCHEMA_VERSION = "strategy-definition/v3"
STRATEGY_V3_SCHEMA_ID = "https://quantlab.local/schemas/strategy-definition/v3.schema.json"
_TIME_RE = re.compile(r"^(?P<hour>[01]\d|2[0-3]):(?P<minute>[0-5]\d)$")


class FeatureDeclaration(StrictModel):
    feature_id: Identifier
    name: str
    version: str
    parameters: dict[str, int | bool | str]
    inputs: tuple[Identifier, ...] = Field(strict=False)

    def as_spec(self) -> FeatureSpec:
        record = self.model_dump(mode="python")
        record["inputs"] = list(self.inputs)
        return parse_feature_spec(record)

    @model_validator(mode="after")
    def validate_registered_feature(self) -> FeatureDeclaration:
        self.as_spec()
        return self


class CandleFieldOperandV3(StrictModel):
    type: Literal["candle_field"]
    name: Literal["open", "high", "low", "close", "volume"]


class FeatureOperandV3(StrictModel):
    type: Literal["feature"]
    feature_id: Identifier


class ConstantOperandV3(StrictModel):
    type: Literal["constant"]
    dimension: Literal[
        "PRICE", "QUANTITY", "RATIO", "PERCENT", "MINUTES", "BOOLEAN", "ENUM"
    ]
    value: str

    @model_validator(mode="after")
    def validate_constant(self) -> ConstantOperandV3:
        if self.dimension in {"BOOLEAN", "ENUM"}:
            if not self.value:
                raise ValueError("text constant cannot be empty")
            if self.dimension == "BOOLEAN" and self.value not in {"true", "false"}:
                raise ValueError("BOOLEAN constant must be true or false")
        else:
            CanonicalRational.from_decimal(self.value)
        return self


type OperandV3 = Annotated[
    CandleFieldOperandV3 | FeatureOperandV3 | ConstantOperandV3,
    Field(discriminator="type"),
]


class ComparisonConditionV3(StrictModel):
    type: Literal["comparison"]
    operator: Literal["GT", "GTE", "LT", "LTE", "EQ", "NE"]
    left: OperandV3
    right: OperandV3


class CrossConditionV3(StrictModel):
    type: Literal["cross"]
    operator: Literal["CROSS_ABOVE", "CROSS_BELOW"]
    left: OperandV3
    right: OperandV3


class RangeConditionV3(StrictModel):
    type: Literal["range"]
    operator: Literal["BETWEEN", "NOT_BETWEEN"]
    value: OperandV3
    lower: OperandV3
    upper: OperandV3
    lower_inclusive: bool
    upper_inclusive: bool


class LogicalConditionV3(StrictModel):
    type: Literal["logical"]
    operator: Literal["AND", "OR", "NOT"]
    children: tuple[ConditionNodeV3, ...] = Field(strict=False)

    @model_validator(mode="after")
    def validate_arity(self) -> LogicalConditionV3:
        if not self.children:
            raise ValueError("logical condition cannot be empty")
        if self.operator == "NOT" and len(self.children) != 1:
            raise ValueError("NOT requires exactly one child")
        return self


type ConditionNodeV3 = Annotated[
    ComparisonConditionV3 | CrossConditionV3 | RangeConditionV3 | LogicalConditionV3,
    Field(discriminator="type"),
]
LogicalConditionV3.model_rebuild()


class TimeRangeV3(StrictModel):
    start: str
    end: str
    start_inclusive: bool
    end_inclusive: bool

    @field_validator("start", "end")
    @classmethod
    def validate_time(cls, value: str) -> str:
        if _TIME_RE.fullmatch(value) is None:
            raise ValueError("time must use HH:MM")
        return value

    @model_validator(mode="after")
    def validate_order(self) -> TimeRangeV3:
        if time_text_to_minute(self.start) >= time_text_to_minute(self.end):
            raise ValueError("time range must not be empty or cross midnight")
        return self


class PointDistanceV3(StrictModel):
    unit: Literal["POINTS"]
    value: str

    @field_validator("value")
    @classmethod
    def validate_positive(cls, value: str) -> str:
        if CanonicalRational.from_decimal(value).compare(CanonicalRational(0)) <= 0:
            raise ValueError("distance must be positive")
        return value


class NoCostModelV3(NoCostModel):
    version: Literal["1.0.0"]


class FixedPerSideCostModelV3(StrictModel):
    type: Literal["FIXED_PER_SIDE"]
    version: Literal["1.0.0"]
    points_per_side: str

    @field_validator("points_per_side")
    @classmethod
    def validate_non_negative(cls, value: str) -> str:
        if CanonicalRational.from_decimal(value).numerator < 0:
            raise ValueError("cost cannot be negative")
        return value


type CostModelV3 = Annotated[
    NoCostModelV3 | FixedPerSideCostModelV3,
    Field(discriminator="type"),
]


class NoSlippageModelV3(NoSlippageModel):
    version: Literal["1.0.0"]


class FixedPointsSlippageModelV3(StrictModel):
    type: Literal["FIXED_POINTS"]
    version: Literal["1.0.0"]
    points_per_side: str

    @field_validator("points_per_side")
    @classmethod
    def validate_non_negative(cls, value: str) -> str:
        if CanonicalRational.from_decimal(value).numerator < 0:
            raise ValueError("slippage cannot be negative")
        return value


type SlippageModelV3 = Annotated[
    NoSlippageModelV3 | FixedPointsSlippageModelV3,
    Field(discriminator="type"),
]


class ExecutionPolicyV3(StrictModel):
    entry_fill: Literal["NEXT_TRADE"]
    position_policy: Literal["SINGLE_POSITION_NO_QUEUE"]
    same_tick_reentry: Literal[False]
    session_end: Literal["CLOSE_AT_LAST_TRADE"]
    require_post_fill_event: Literal[True]
    position_size: Literal[1]


class StrategyDefinitionV3(StrictModel):
    schema_version: Literal[STRATEGY_V3_SCHEMA_VERSION]
    strategy_id: Identifier
    strategy_version: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=200)
    logical_asset: Identifier
    timeframe: Literal["1m", "2m", "5m", "15m"]
    evaluation_mode: Literal["ON_CLOSE"]
    direction: Literal["BUY", "SELL"]
    features: tuple[FeatureDeclaration, ...] = Field(strict=False)
    entry_conditions: ConditionNodeV3
    entry_time_filter: TimeRangeV3 | None
    take_profit: PointDistanceV3
    stop_loss: PointDistanceV3
    execution: ExecutionPolicyV3
    cost_model: CostModelV3
    slippage_model: SlippageModelV3

    @model_validator(mode="after")
    def validate_feature_graph(self) -> StrategyDefinitionV3:
        declared: set[str] = set()
        for declaration in self.features:
            if declaration.feature_id in declared:
                raise ValueError("feature_id values must be unique")
            missing = set(declaration.inputs) - declared
            if missing:
                raise ValueError(
                    "feature inputs must reference earlier declarations: "
                    + ",".join(sorted(missing))
                )
            declared.add(declaration.feature_id)

        referenced: set[str] = set()

        def operand(value: OperandV3) -> None:
            if isinstance(value, FeatureOperandV3):
                referenced.add(value.feature_id)

        def visit(node: ConditionNodeV3) -> None:
            if isinstance(node, LogicalConditionV3):
                for child in node.children:
                    visit(child)
            elif isinstance(node, RangeConditionV3):
                operand(node.value)
                operand(node.lower)
                operand(node.upper)
            else:
                operand(node.left)
                operand(node.right)

        visit(self.entry_conditions)
        if not referenced <= declared:
            raise ValueError(
                "entry condition references undeclared features: "
                + ",".join(sorted(referenced - declared))
            )

        needed = set(referenced)
        by_id = {item.feature_id: item for item in self.features}
        pending = list(referenced)
        while pending:
            current = pending.pop()
            for dependency in by_id[current].inputs:
                if dependency not in needed:
                    needed.add(dependency)
                    pending.append(dependency)
        if needed != declared:
            raise ValueError("features must contain exactly the dependency closure in use")
        return self

    @property
    def feature_specs(self) -> tuple[FeatureSpec, ...]:
        return tuple(item.as_spec() for item in self.features)

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.model_dump(mode="json"))

    def semantic_sha256(self) -> str:
        return sha256_bytes(self.canonical_bytes())

    def decimal_inputs(self) -> list[str]:
        values = [self.take_profit.value, self.stop_loss.value]
        if isinstance(self.cost_model, FixedPerSideCostModelV3):
            values.append(self.cost_model.points_per_side)
        if isinstance(self.slippage_model, FixedPointsSlippageModelV3):
            values.append(self.slippage_model.points_per_side)

        def add_operand(operand: OperandV3) -> None:
            if isinstance(operand, ConstantOperandV3) and operand.dimension not in {
                "BOOLEAN",
                "ENUM",
            }:
                values.append(operand.value)

        def visit(node: ConditionNodeV3) -> None:
            if isinstance(node, LogicalConditionV3):
                for child in node.children:
                    visit(child)
            elif isinstance(node, RangeConditionV3):
                add_operand(node.value)
                add_operand(node.lower)
                add_operand(node.upper)
            else:
                add_operand(node.left)
                add_operand(node.right)

        visit(self.entry_conditions)
        return values


def time_text_to_minute(value: str) -> int:
    match = _TIME_RE.fullmatch(value)
    if match is None:
        raise ContractError("time must use HH:MM")
    return int(match.group("hour")) * 60 + int(match.group("minute"))


def _reject_json_float(value: str) -> None:
    raise ContractError(f"JSON floating-point number is forbidden: {value}")


def load_strategy_v3(path: Path) -> StrategyDefinitionV3:
    try:
        raw: Any = json.loads(
            path.read_text(encoding="utf-8"),
            parse_float=_reject_json_float,
            parse_constant=_reject_json_float,
        )
        return StrategyDefinitionV3.model_validate(raw)
    except ContractError:
        raise
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise ContractError(f"invalid strategy v3 definition: {exc}") from exc


def strategy_v3_json_schema() -> dict[str, object]:
    schema = StrategyDefinitionV3.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = STRATEGY_V3_SCHEMA_ID
    return schema
