"""Strict, explicit and dimension-checked scoring policies."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field, model_validator
from quantlab_core.errors import ContractError
from quantlab_core.numeric import CanonicalRational
from quantlab_core.strategy import StrictModel
from quantlab_mining.contracts import identity

from quantlab_scoring import SCORE_ENGINE_VERSION

Dimension = Literal["PRICE_POINTS", "RATIO", "COUNT", "COUNT_PER_SESSION"]


class RationalV1(StrictModel):
    numerator: str = Field(pattern=r"^-?(0|[1-9]\d*)$")
    denominator: str = Field(pattern=r"^[1-9]\d*$")

    @model_validator(mode="after")
    def canonical(self) -> RationalV1:
        value = CanonicalRational(int(self.numerator), int(self.denominator))
        if value.to_record() != self.model_dump(mode="json"):
            raise ValueError(
                "rational must be reduced with positive denominator and canonical zero"
            )
        return self

    def value(self) -> CanonicalRational:
        return CanonicalRational(int(self.numerator), int(self.denominator))


class LinearTransformV1(StrictModel):
    kind: Literal["LINEAR_CLAMP"]
    dimension: Dimension
    minimum: RationalV1
    maximum: RationalV1

    @model_validator(mode="after")
    def ordered(self) -> LinearTransformV1:
        if self.minimum.value().compare(self.maximum.value()) >= 0:
            raise ValueError("linear anchors must satisfy minimum < maximum")
        return self


class InverseTransformV1(StrictModel):
    kind: Literal["INVERSE_LINEAR_CLAMP"]
    dimension: Dimension
    best: RationalV1
    worst: RationalV1

    @model_validator(mode="after")
    def ordered(self) -> InverseTransformV1:
        if self.best.value().compare(self.worst.value()) >= 0:
            raise ValueError("inverse anchors must satisfy best < worst")
        return self


class DegradationTransformV1(StrictModel):
    kind: Literal["DEGRADATION"]
    dimension: Dimension
    tolerance: RationalV1
    failure: RationalV1

    @model_validator(mode="after")
    def ordered(self) -> DegradationTransformV1:
        zero = CanonicalRational(0)
        if (
            self.tolerance.value().compare(zero) < 0
            or self.failure.value().compare(self.tolerance.value()) <= 0
        ):
            raise ValueError("degradation anchors must satisfy failure > tolerance >= 0")
        return self


class PlateauTransformV1(StrictModel):
    kind: Literal["PLATEAU"]
    dimension: Dimension
    minimum: RationalV1
    plateau_start: RationalV1
    plateau_end: RationalV1
    maximum: RationalV1

    @model_validator(mode="after")
    def ordered(self) -> PlateauTransformV1:
        values = [
            self.minimum.value(),
            self.plateau_start.value(),
            self.plateau_end.value(),
            self.maximum.value(),
        ]
        if not (
            values[0].compare(values[1]) < 0
            and values[1].compare(values[2]) <= 0
            and values[2].compare(values[3]) < 0
        ):
            raise ValueError("plateau anchors must satisfy min < start <= end < max")
        return self


TransformV1 = Annotated[
    LinearTransformV1 | InverseTransformV1 | DegradationTransformV1 | PlateauTransformV1,
    Field(discriminator="kind"),
]


class ScoreTermV1(StrictModel):
    term_id: str
    component: Literal["PERFORMANCE", "RISK", "CONSISTENCY", "STABILITY", "ACTIVITY"]
    source: Literal["VALIDATION", "COMPARISON", "DERIVED_VALIDATION"]
    metric: str
    source_dimension: Dimension
    normalization: Literal["NONE", "DIVIDE_BY_RISK_UNIT"]
    normalized_dimension: Dimension
    transform: TransformV1
    max_points: RationalV1


class PenaltyTermV1(StrictModel):
    term_id: str
    component: Literal["CONCENTRATION"]
    source: Literal["VALIDATION"]
    metric: str
    source_dimension: Literal["RATIO"]
    normalization: Literal["NONE"]
    normalized_dimension: Literal["RATIO"]
    transform: LinearTransformV1
    max_points: RationalV1


_TERM_REGISTRY: dict[str, tuple[str, str, str, str, str]] = {
    "performance.average_trade_risk": (
        "PERFORMANCE", "VALIDATION", "average_trade", "PRICE_POINTS", "DIVIDE_BY_RISK_UNIT"
    ),
    "performance.net_pnl_per_session_risk": (
        "PERFORMANCE", "VALIDATION", "net_pnl_per_session", "PRICE_POINTS", "DIVIDE_BY_RISK_UNIT"
    ),
    "performance.win_rate": (
        "PERFORMANCE", "VALIDATION", "win_rate", "RATIO", "NONE"
    ),
    "risk.max_drawdown_risk": (
        "RISK", "VALIDATION", "max_drawdown", "PRICE_POINTS", "DIVIDE_BY_RISK_UNIT"
    ),
    "risk.max_consecutive_losses": (
        "RISK", "VALIDATION", "max_consecutive_losses", "COUNT", "NONE"
    ),
    "consistency.positive_session_rate": (
        "CONSISTENCY", "VALIDATION", "positive_session_rate", "RATIO", "NONE"
    ),
    "consistency.non_losing_session_rate": (
        "CONSISTENCY", "DERIVED_VALIDATION", "non_losing_session_rate", "RATIO", "NONE"
    ),
    "stability.average_trade_delta_risk": (
        "STABILITY", "COMPARISON", "average_trade_delta", "PRICE_POINTS", "DIVIDE_BY_RISK_UNIT"
    ),
    "stability.net_pnl_per_session_delta_risk": (
        "STABILITY",
        "COMPARISON",
        "net_pnl_per_session_delta",
        "PRICE_POINTS",
        "DIVIDE_BY_RISK_UNIT",
    ),
    "stability.win_rate_delta": (
        "STABILITY", "COMPARISON", "win_rate_delta", "RATIO", "NONE"
    ),
    "stability.trades_per_session_ratio": (
        "STABILITY", "COMPARISON", "trades_per_session_ratio", "RATIO", "NONE"
    ),
    "stability.active_session_rate_delta": (
        "STABILITY", "COMPARISON", "active_session_rate_delta", "RATIO", "NONE"
    ),
    "activity.active_session_rate": (
        "ACTIVITY", "VALIDATION", "active_session_rate", "RATIO", "NONE"
    ),
    "activity.trades_per_session": (
        "ACTIVITY", "VALIDATION", "trades_per_session", "COUNT_PER_SESSION", "NONE"
    ),
}

_PENALTY_REGISTRY: dict[str, tuple[str, str]] = {
    "concentration.profitable_session_share": ("VALIDATION", "largest_profitable_session_share"),
    "concentration.trade_count_session_share": (
        "VALIDATION", "largest_trade_count_session_share"
    ),
}

_COMPONENT_MAXIMA = {
    "PERFORMANCE": CanonicalRational(30),
    "RISK": CanonicalRational(20),
    "CONSISTENCY": CanonicalRational(20),
    "STABILITY": CanonicalRational(20),
    "ACTIVITY": CanonicalRational(10),
}


class ResearchScorePolicyV1(StrictModel):
    schema_version: Literal["research-score-policy/v1"]
    score_name: Literal["ResearchStrategyScoreV1"]
    engine_semantic_version: Literal["research-score-engine/v1"] = SCORE_ENGINE_VERSION
    score_scale: Literal[10000]
    rounding: Literal["ROUND_HALF_EVEN"]
    intermediate_math: Literal["CANONICAL_RATIONAL"]
    final_bounds: tuple[RationalV1, RationalV1] = Field(strict=False)
    risk_unit: Literal["STRATEGY_V3_STOP_LOSS_EXACT_POINTS"]
    positive_terms: tuple[ScoreTermV1, ...] = Field(strict=False)
    penalty_terms: tuple[PenaltyTermV1, ...] = Field(strict=False)
    statistical_precision_warning: Literal[
        "NUMERICAL_PRECISION_IS_NOT_STATISTICAL_PRECISION"
    ]

    @model_validator(mode="after")
    def validate_closed_policy(self) -> ResearchScorePolicyV1:
        if tuple(value.value() for value in self.final_bounds) != (
            CanonicalRational(0),
            CanonicalRational(100),
        ):
            raise ValueError("score bounds must be exactly [0,100]")
        terms = {term.term_id: term for term in self.positive_terms}
        if len(terms) != len(self.positive_terms) or set(terms) != set(_TERM_REGISTRY):
            raise ValueError("positive term registry must be exact and duplicate-free")
        component_sums = {name: CanonicalRational(0) for name in _COMPONENT_MAXIMA}
        for term_id, term in terms.items():
            expected = _TERM_REGISTRY[term_id]
            actual = (
                term.component,
                term.source,
                term.metric,
                term.source_dimension,
                term.normalization,
            )
            if actual != expected:
                raise ValueError(f"source/dimension contract mismatch for {term_id}")
            expected_normalized = (
                "RATIO" if term.normalization == "DIVIDE_BY_RISK_UNIT" else term.source_dimension
            )
            if term.normalized_dimension != expected_normalized:
                raise ValueError(f"normalized dimension mismatch for {term_id}")
            if term.transform.dimension != term.normalized_dimension:
                raise ValueError(f"transform dimension mismatch for {term_id}")
            if term.max_points.value().compare(CanonicalRational(0)) < 0:
                raise ValueError("term weight cannot be negative")
            component_sums[term.component] = (
                component_sums[term.component] + term.max_points.value()
            )
        if component_sums != _COMPONENT_MAXIMA:
            raise ValueError("positive component maxima must be exactly 30/20/20/20/10")
        if sum((value.numerator for value in component_sums.values()), 0) != 100:
            raise ValueError("positive weights must sum exactly to 100")
        penalties = {term.term_id: term for term in self.penalty_terms}
        if len(penalties) != len(self.penalty_terms) or set(penalties) != set(_PENALTY_REGISTRY):
            raise ValueError("penalty term registry must be exact and duplicate-free")
        total_penalty = CanonicalRational(0)
        for term_id, term in penalties.items():
            if (term.source, term.metric) != _PENALTY_REGISTRY[term_id]:
                raise ValueError(f"penalty source mismatch for {term_id}")
            if term.transform.dimension != term.normalized_dimension:
                raise ValueError(f"penalty transform dimension mismatch for {term_id}")
            total_penalty = total_penalty + term.max_points.value()
        if total_penalty != CanonicalRational(10):
            raise ValueError("maximum concentration penalty must equal 10")
        return self

    def canonical_record(self) -> dict[str, Any]:
        record = self.model_dump(mode="json")
        record["positive_terms"] = sorted(
            record["positive_terms"], key=lambda item: item["term_id"]
        )
        record["penalty_terms"] = sorted(record["penalty_terms"], key=lambda item: item["term_id"])
        return record

    @property
    def score_policy_id(self) -> str:
        return identity("research-score-policy/v1", self.canonical_record())


class TieBreakerV1(StrictModel):
    field: Literal[
        "score_units",
        "validation.net_pnl_per_session",
        "validation.max_drawdown",
        "validation.positive_session_rate",
        "candidate_id",
    ]
    direction: Literal["ASC", "DESC"]


class ResearchRankingPolicyV1(StrictModel):
    schema_version: Literal["research-ranking-policy/v1"]
    tie_breakers: tuple[TieBreakerV1, ...] = Field(strict=False)

    @model_validator(mode="after")
    def validate_ties(self) -> ResearchRankingPolicyV1:
        fields = [item.field for item in self.tie_breakers]
        required = {
            "score_units",
            "validation.net_pnl_per_session",
            "validation.max_drawdown",
            "validation.positive_session_rate",
            "candidate_id",
        }
        if len(fields) != len(set(fields)) or set(fields) != required:
            raise ValueError("ranking tie breakers must contain the closed field registry once")
        if self.tie_breakers[0].model_dump() != {"field": "score_units", "direction": "DESC"}:
            raise ValueError("score_units DESC must be the first ranking key")
        if self.tie_breakers[-1].model_dump() != {"field": "candidate_id", "direction": "ASC"}:
            raise ValueError("candidate_id ASC must be the absolute final ranking key")
        return self

    @property
    def ranking_policy_id(self) -> str:
        return identity("research-ranking-policy/v1", self.model_dump(mode="json"))


class ResearchDiversityPolicyV1(StrictModel):
    schema_version: Literal["research-diversity-policy/v1"]
    algorithm: Literal["ROUND_ROBIN_SEMANTIC_GROUP"]
    semantic_group_version: Literal["strategy-semantic-group/v1"]
    max_per_semantic_group: int = Field(ge=1)
    family_limit: Literal[None] = None
    timeframe_limit: Literal[None] = None
    direction_limit: Literal[None] = None

    @property
    def diversity_policy_id(self) -> str:
        return identity("research-diversity-policy/v1", self.model_dump(mode="json"))


def _reject_float(value: str) -> None:
    raise ContractError(f"JSON floating-point/special number forbidden: {value}")


def load_policy(path: Path, model: type[StrictModel]) -> Any:
    try:
        raw = json.loads(
            path.read_text(encoding="utf-8"),
            parse_float=_reject_float,
            parse_constant=_reject_float,
        )
        return model.model_validate(raw)
    except ContractError:
        raise
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ContractError(f"invalid {model.__name__}: {exc}") from exc
