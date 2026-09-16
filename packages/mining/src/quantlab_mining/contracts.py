"""Strict search universe, separate safety policy and evaluation configuration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field, model_validator
from quantlab_core.canonical import canonical_json_bytes, sha256_bytes
from quantlab_core.errors import ContractError
from quantlab_core.numeric import CanonicalRational
from quantlab_core.strategy import Identifier, StrictModel
from quantlab_core.strategy_v3 import CostModelV3, SlippageModelV3, TimeRangeV3

GENERATOR_VERSION = "1.0.0"
CANONICALIZATION_VERSION = "candidate-canonicalization/v1"
TEMPLATE_REGISTRY_VERSION = "candidate-templates/v1"
type Grid = Annotated[tuple[int | bool | str, ...], Field(strict=False)]


def identity(domain: str, payload: Any) -> str:
    return "sha256:" + sha256_bytes(canonical_json_bytes({"domain": domain, "payload": payload}))


def decimal_text(value: str) -> str:
    CanonicalRational.from_decimal(value)
    result = value.rstrip("0").rstrip(".") if "." in value else value
    return "0" if result == "-0" else result


class AtomGrid(StrictModel):
    kind: Literal[
        "trend_close",
        "trend_pair",
        "vwap_context",
        "breakout_previous",
        "momentum",
        "candle_context",
        "volume",
        "volatility",
    ]
    grids: dict[str, Grid] = Field(strict=False)
    direction_grids: dict[Literal["BUY", "SELL"], dict[str, Grid]] = Field(
        default_factory=dict, strict=False
    )

    @model_validator(mode="after")
    def nonempty_grids(self) -> AtomGrid:
        for values in [self.grids, *self.direction_grids.values()]:
            for key, grid in values.items():
                if not key or not grid:
                    raise ValueError("every explicit grid must be nonempty")
        for values in self.direction_grids.values():
            if set(values) & set(self.grids):
                raise ValueError("direction grids cannot override shared grids")
        return self

    def effective(self, direction: str) -> dict[str, tuple[int | bool | str, ...]]:
        return {**self.grids, **self.direction_grids.get(direction, {})}


class TemplateEntry(StrictModel):
    template_id: Identifier
    base: AtomGrid
    confirmations: tuple[AtomGrid, ...] = Field(strict=False)


class MiningSearchSpaceV1(StrictModel):
    schema_version: Literal["mining-search-space/v1"]
    logical_asset: Identifier
    timeframes: tuple[Literal["1m", "2m", "5m", "15m"], ...] = Field(strict=False)
    directions: tuple[Literal["BUY", "SELL"], ...] = Field(strict=False)
    templates: tuple[TemplateEntry, ...] = Field(strict=False)
    stops: tuple[str, ...] = Field(strict=False)
    targets: tuple[str, ...] = Field(strict=False)
    time_windows: tuple[TimeRangeV3, ...] = Field(strict=False)
    constraints: Literal["closed-template-constraints/v1"]

    @model_validator(mode="after")
    def validate_universe(self) -> MiningSearchSpaceV1:
        for values in (
            self.timeframes,
            self.directions,
            self.templates,
            self.stops,
            self.targets,
            self.time_windows,
        ):
            if not values:
                raise ValueError("search axes cannot be empty")
        if len({entry.template_id for entry in self.templates}) != len(self.templates):
            raise ValueError("template_id must be unique")
        for value in (*self.stops, *self.targets):
            if CanonicalRational.from_decimal(value).numerator <= 0:
                raise ValueError("stops and targets must be positive exact points")
        return self

    def semantic_record(self) -> dict[str, object]:
        # Template identifiers are operational labels, not requested candidate semantics.
        record = self.model_dump(mode="json")
        templates = []
        for entry in record["templates"]:
            entry.pop("template_id")
            for atom in [entry["base"], *entry["confirmations"]]:
                atom["direction_grids"] = {
                    direction: grids
                    for direction, grids in atom["direction_grids"].items()
                    if direction in record["directions"]
                }
                for grids in [atom["grids"], *atom["direction_grids"].values()]:
                    for key, values in grids.items():
                        if key in {"threshold", "lower", "upper"}:
                            values = [decimal_text(value) for value in values]
                        grids[key] = sorted(
                            {canonical_json_bytes(v): v for v in values}.values(),
                            key=canonical_json_bytes,
                        )
            entry["confirmations"].sort(key=canonical_json_bytes)
            templates.append(entry)
        record["templates"] = sorted(
            {canonical_json_bytes(v): v for v in templates}.values(), key=canonical_json_bytes
        )
        for name in ("timeframes", "directions", "time_windows"):
            record[name] = sorted(
                {canonical_json_bytes(v): v for v in record[name]}.values(),
                key=canonical_json_bytes,
            )
        for name in ("stops", "targets"):
            record[name] = sorted({decimal_text(v) for v in record[name]})
        return record

    @property
    def search_space_id(self) -> str:
        return identity("search-space/v1", self.semantic_record())


class GenerationPolicyV1(StrictModel):
    schema_version: Literal["generation-policy/v1"] = "generation-policy/v1"
    candidate_budget: int = Field(default=10_000, ge=1, le=50_000)
    expansion_limit: int = Field(default=1_000_000, ge=1, le=1_000_000)
    max_conditions: int = Field(default=3, ge=1, le=3)
    max_ast_depth: int = Field(default=2, ge=1, le=2)
    max_features: int = Field(default=6, ge=1, le=6)
    max_variable_dimensions: int = Field(default=12, ge=1, le=12)
    max_cross_conditions: int = Field(default=1, ge=0, le=1)
    max_time_filters: int = Field(default=1, ge=1, le=1)
    max_grid_values: int = Field(default=32, ge=1, le=32)
    max_templates: int = Field(default=32, ge=1, le=32)
    min_period: int = Field(default=1, ge=1, le=1000)
    max_period: int = Field(default=1000, ge=1, le=1000)

    @model_validator(mode="after")
    def ordered_periods(self) -> GenerationPolicyV1:
        if self.min_period > self.max_period:
            raise ValueError("period limits are inverted")
        return self

    @property
    def generation_policy_id(self) -> str:
        return identity("generation-policy/v1", self.model_dump(mode="json"))


class EvaluationConfigV1(StrictModel):
    schema_version: Literal["mining-evaluation/v1"]
    cost_model: CostModelV3
    slippage_model: SlippageModelV3
    max_sessions: int = Field(default=19, ge=1)

    def canonicalized(self) -> EvaluationConfigV1:
        updates = {}
        for name in ("cost_model", "slippage_model"):
            model = getattr(self, name)
            if hasattr(model, "points_per_side"):
                updates[name] = model.model_copy(
                    update={"points_per_side": decimal_text(model.points_per_side)}
                )
        return self.model_copy(update=updates)


def load_contract(path: Path, model: type[StrictModel]) -> Any:
    def reject(value: str) -> None:
        raise ContractError(f"JSON floating-point/special number forbidden: {value}")

    try:
        return model.model_validate(
            json.loads(path.read_text(encoding="utf-8"), parse_float=reject, parse_constant=reject)
        )
    except (ValueError, OSError) as exc:
        raise ContractError(f"invalid {model.__name__}: {exc}") from exc
