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


type Direction = Literal["BUY", "SELL"]
type Timeframe = Literal["1m", "2m", "5m", "15m"]
type Sha256Identity = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]


class MiningSideSelectionV1(StrictModel):
    enabled: bool


class MiningSideConstraintsV1(StrictModel):
    enabled: bool
    template_ids: tuple[Identifier, ...] = Field(strict=False)
    stops: tuple[str, ...] = Field(strict=False)
    targets: tuple[str, ...] = Field(strict=False)

    @model_validator(mode="after")
    def valid_side(self) -> MiningSideConstraintsV1:
        if self.enabled and not all((self.template_ids, self.stops, self.targets)):
            raise ValueError("enabled sides require templates, stops and targets")
        if len(set(self.template_ids)) != len(self.template_ids):
            raise ValueError("side template_ids must be unique")
        for value in (*self.stops, *self.targets):
            if CanonicalRational.from_decimal(value).numerator <= 0:
                raise ValueError("stops and targets must be positive exact points")
        return self


class MiningConstraintsV1(StrictModel):
    schema_version: Literal["mining-constraints/v1"]
    logic: Literal["AND_ONLY"]
    sampling: Literal["NONE"]
    truncation: Literal["PROHIBITED"]
    template_catalog: tuple[TemplateEntry, ...] = Field(strict=False)
    time_windows: tuple[TimeRangeV3, ...] = Field(strict=False)
    sides: dict[Direction, MiningSideConstraintsV1] = Field(strict=False)
    generation_policy: GenerationPolicyV1

    @model_validator(mode="before")
    @classmethod
    def require_explicit_generation_policy(cls, value: Any) -> Any:
        if isinstance(value, dict) and isinstance(value.get("generation_policy"), dict):
            policy = value["generation_policy"]
            missing = set(GenerationPolicyV1.model_fields) - set(policy)
            if missing:
                raise ValueError(
                    "generation policy must explicitly declare every scientific limit: "
                    f"missing={sorted(missing)}"
                )
        return value

    @model_validator(mode="after")
    def closed_constraints(self) -> MiningConstraintsV1:
        if set(self.sides) != {"BUY", "SELL"}:
            raise ValueError("sides must explicitly contain BUY and SELL")
        if not any(side.enabled for side in self.sides.values()):
            raise ValueError("at least one side must be enabled")
        if not self.time_windows:
            raise ValueError("at least one time window is required")
        catalog = {entry.template_id: entry for entry in self.template_catalog}
        if len(catalog) != len(self.template_catalog):
            raise ValueError("template catalog identifiers must be unique")
        referenced = {
            template_id for side in self.sides.values() for template_id in side.template_ids
        }
        if referenced != set(catalog):
            raise ValueError("template catalog must contain exactly the referenced templates")
        return self

    def side_search_space(
        self, logical_asset: Identifier, timeframes: tuple[Timeframe, ...], direction: Direction
    ) -> MiningSearchSpaceV1:
        side = self.sides[direction]
        if not side.enabled:
            raise ValueError(f"{direction} is disabled by the mining constraints")
        catalog = {entry.template_id: entry for entry in self.template_catalog}
        return MiningSearchSpaceV1(
            schema_version="mining-search-space/v1",
            logical_asset=logical_asset,
            timeframes=timeframes,
            directions=(direction,),
            templates=tuple(catalog[template_id] for template_id in side.template_ids),
            stops=side.stops,
            targets=side.targets,
            time_windows=self.time_windows,
            constraints="closed-template-constraints/v1",
        )

    def semantic_record(self) -> dict[str, object]:
        sides: dict[str, object] = {}
        for direction in ("BUY", "SELL"):
            side = self.sides[direction]
            if side.enabled:
                record = self.side_search_space("UNRESOLVED", ("1m",), direction).semantic_record()
                record.pop("logical_asset")
                record.pop("timeframes")
                record.pop("constraints")
                sides[direction] = {"enabled": True, **record}
            else:
                sides[direction] = {"enabled": False}
        return {
            "schema_version": self.schema_version,
            "logic": self.logic,
            "sampling": self.sampling,
            "truncation": self.truncation,
            "sides": sides,
            "generation_policy": self.generation_policy.model_dump(mode="json"),
        }

    @property
    def mining_constraints_id(self) -> str:
        return identity("mining-constraints/v1", self.semantic_record())


class MiningRequestV1(StrictModel):
    schema_version: Literal["mining-request/v1"]
    mode: Literal["CONSTRAINED", "AUTO_DISCOVERY"]
    logical_asset: Identifier
    timeframes: tuple[Timeframe, ...] = Field(strict=False)
    sides: dict[Direction, MiningSideSelectionV1] = Field(strict=False)
    auto_discovery_profile_id: Sha256Identity | None
    constraints: MiningConstraintsV1 | None

    @model_validator(mode="after")
    def valid_mode(self) -> MiningRequestV1:
        if not self.timeframes or len(set(self.timeframes)) != len(self.timeframes):
            raise ValueError("timeframes must be a nonempty unique collection")
        if set(self.sides) != {"BUY", "SELL"}:
            raise ValueError("sides must explicitly contain BUY and SELL")
        if not any(side.enabled for side in self.sides.values()):
            raise ValueError("at least one requested side must be enabled")
        if self.mode == "AUTO_DISCOVERY":
            if self.auto_discovery_profile_id is None or self.constraints is not None:
                raise ValueError("AUTO_DISCOVERY requires only a full profile identity")
        elif self.auto_discovery_profile_id is not None or self.constraints is None:
            raise ValueError("CONSTRAINED requires inline constraints and no profile identity")
        return self

    def semantic_record(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "mode": self.mode,
            "logical_asset": self.logical_asset,
            "timeframes": sorted(self.timeframes),
            "sides": {
                direction: {"enabled": self.sides[direction].enabled}
                for direction in ("BUY", "SELL")
            },
            "auto_discovery_profile_id": self.auto_discovery_profile_id,
            "mining_constraints_id": self.constraints.mining_constraints_id
            if self.constraints is not None
            else None,
        }

    @property
    def mining_request_id(self) -> str:
        return identity("mining-request/v1", self.semantic_record())


class GenerationCountsV1(StrictModel):
    T: int = Field(ge=0)
    R: int = Field(ge=0)
    V: int = Field(ge=0)
    D: int = Field(ge=0)
    U: int = Field(ge=0)

    @model_validator(mode="after")
    def valid_accounting(self) -> GenerationCountsV1:
        if self.T != self.R + self.D + self.U or self.V != self.T - self.R:
            raise ValueError("generation counts violate T=R+D+U and V=T-R")
        return self


class ExpectedCandidateUniverseV1(StrictModel):
    counts: GenerationCountsV1
    by_direction: dict[Direction, int]
    by_timeframe: dict[Timeframe, int]
    by_family: dict[str, int]

    @model_validator(mode="after")
    def valid_distribution(self) -> ExpectedCandidateUniverseV1:
        if set(self.by_direction) != {"BUY", "SELL"}:
            raise ValueError("expected direction distribution must contain BUY and SELL")
        if any(
            value < 0
            for value in (
                *self.by_direction.values(),
                *self.by_timeframe.values(),
                *self.by_family.values(),
            )
        ):
            raise ValueError("expected distributions cannot be negative")
        for distribution in (self.by_direction, self.by_timeframe, self.by_family):
            if sum(distribution.values()) != self.counts.U:
                raise ValueError("each expected distribution must sum to U")
        return self


class AutoDiscoveryProfileV1(StrictModel):
    schema_version: Literal["auto-discovery-profile/v1"]
    alias: Identifier
    logical_asset: Identifier
    timeframes: tuple[Timeframe, ...] = Field(strict=False)
    constraints: MiningConstraintsV1
    resolution_protocol_id: Sha256Identity
    expected_candidate_universe: ExpectedCandidateUniverseV1

    @model_validator(mode="after")
    def valid_profile(self) -> AutoDiscoveryProfileV1:
        if not self.timeframes or len(set(self.timeframes)) != len(self.timeframes):
            raise ValueError("profile timeframes must be a nonempty unique collection")
        expected = self.expected_candidate_universe
        if set(expected.by_timeframe) != set(self.timeframes):
            raise ValueError("expected timeframe distribution must match profile timeframes")
        catalog = {entry.template_id: entry for entry in self.constraints.template_catalog}
        expected_families = {
            catalog[template_id].base.kind
            for side in self.constraints.sides.values()
            for template_id in side.template_ids
            if side.enabled
        }
        if set(expected.by_family) != expected_families:
            raise ValueError("expected family distribution must match enabled templates")
        for direction, side in self.constraints.sides.items():
            if side.enabled != (expected.by_direction[direction] > 0):
                raise ValueError("expected direction distribution must match enabled sides")
        return self

    def semantic_record(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "logical_asset": self.logical_asset,
            "timeframes": sorted(self.timeframes),
            "mining_constraints_id": self.constraints.mining_constraints_id,
            "resolution_protocol_id": self.resolution_protocol_id,
            "expected_candidate_universe": self.expected_candidate_universe.model_dump(mode="json"),
        }

    @property
    def auto_discovery_profile_id(self) -> str:
        return identity("auto-discovery-profile/v1", self.semantic_record())


class ResolvedSideSearchSpaceV1(StrictModel):
    direction: Direction
    search_space: MiningSearchSpaceV1

    @model_validator(mode="after")
    def direction_matches(self) -> ResolvedSideSearchSpaceV1:
        if self.search_space.directions != (self.direction,):
            raise ValueError("resolved subspaces must contain exactly their declared direction")
        return self


class ResolvedSearchSpaceV1(StrictModel):
    schema_version: Literal["resolved-search-space/v1"]
    mining_request_id: Sha256Identity
    mining_constraints_id: Sha256Identity
    auto_discovery_profile_id: Sha256Identity | None
    resolution_protocol_id: Sha256Identity
    generation_policy: GenerationPolicyV1
    subspaces: tuple[ResolvedSideSearchSpaceV1, ...] = Field(strict=False)

    @model_validator(mode="after")
    def unique_sides(self) -> ResolvedSearchSpaceV1:
        directions = [subspace.direction for subspace in self.subspaces]
        if not directions or len(set(directions)) != len(directions):
            raise ValueError("resolved subspace directions must be nonempty and unique")
        return self

    def semantic_record(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "mining_request_id": self.mining_request_id,
            "mining_constraints_id": self.mining_constraints_id,
            "auto_discovery_profile_id": self.auto_discovery_profile_id,
            "resolution_protocol_id": self.resolution_protocol_id,
            "generation_policy": self.generation_policy.model_dump(mode="json"),
            "subspaces": [
                {
                    "direction": subspace.direction,
                    "search_space": subspace.search_space.semantic_record(),
                }
                for subspace in sorted(self.subspaces, key=lambda item: item.direction)
            ],
        }

    @property
    def resolved_search_space_id(self) -> str:
        return identity("resolved-search-space/v1", self.semantic_record())


class CandidateSubplanManifestV1(StrictModel):
    direction: Direction
    search_space_id: Sha256Identity
    generation_policy_id: Sha256Identity
    candidate_set_id: Sha256Identity
    counts: GenerationCountsV1


class CandidateUniverseManifestV1(StrictModel):
    schema_version: Literal["candidate-universe-manifest/v1"]
    resolved_search_space_id: Sha256Identity
    resolution_protocol_id: Sha256Identity
    subplans: tuple[CandidateSubplanManifestV1, ...] = Field(strict=False)
    counts: GenerationCountsV1
    by_direction: dict[Direction, int]
    by_timeframe: dict[Timeframe, int]
    by_family: dict[str, int]
    candidate_ids: tuple[Sha256Identity, ...] = Field(strict=False)
    candidate_universe_id: Sha256Identity

    @model_validator(mode="after")
    def valid_manifest(self) -> CandidateUniverseManifestV1:
        if tuple(sorted(set(self.candidate_ids))) != self.candidate_ids:
            raise ValueError("candidate_ids must be sorted and unique")
        expected_id = identity("candidate-set/v1", list(self.candidate_ids))
        if self.candidate_universe_id != expected_id:
            raise ValueError("candidate_universe_id does not match candidate_ids")
        if len(self.candidate_ids) != self.counts.U:
            raise ValueError("candidate universe U must equal the number of candidate_ids")
        if sum(self.by_direction.values()) != self.counts.U:
            raise ValueError("candidate direction distribution must sum to U")
        if sum(self.by_timeframe.values()) != self.counts.U:
            raise ValueError("candidate timeframe distribution must sum to U")
        if any(
            value < 0
            for value in (
                *self.by_direction.values(),
                *self.by_timeframe.values(),
                *self.by_family.values(),
            )
        ):
            raise ValueError("candidate distributions cannot be negative")
        directions = [subplan.direction for subplan in self.subplans]
        if not directions or len(set(directions)) != len(directions):
            raise ValueError("candidate subplan directions must be nonempty and unique")
        if set(directions) != set(self.by_direction):
            raise ValueError("candidate direction distribution must match subplans")
        for subplan in self.subplans:
            if self.by_direction[subplan.direction] != subplan.counts.U:
                raise ValueError("candidate direction count must match its subplan")
        for key in ("T", "R", "V"):
            if sum(getattr(subplan.counts, key) for subplan in self.subplans) != getattr(
                self.counts, key
            ):
                raise ValueError("candidate global counts must equal subplan totals")
        return self


def load_contract(path: Path, model: type[StrictModel]) -> Any:
    def reject(value: str) -> None:
        raise ContractError(f"JSON floating-point/special number forbidden: {value}")

    try:
        return model.model_validate(
            json.loads(path.read_text(encoding="utf-8"), parse_float=reject, parse_constant=reject)
        )
    except (ValueError, OSError) as exc:
        raise ContractError(f"invalid {model.__name__}: {exc}") from exc
