"""Closed, strict research policies and exact metric semantics."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator
from quantlab_core.errors import ContractError
from quantlab_core.numeric import CanonicalRational
from quantlab_core.strategy import StrictModel
from quantlab_mining.contracts import identity

RESEARCH_VERSION = "1.0.0"
AGGREGATION_POLICY = "chronological-ledger-realized-equity/v1"
COMPARISON_POLICY = "exact-delta-positive-discovery-ratio/v1"
GATE_POLICY = "all-criteria-undefined-fails/v1"
SESSION_POLICY = "reset-no-overnight-last-tick-forbidden/v1"

COUNT_METRICS = frozenset(
    {
        "trades",
        "wins",
        "losses",
        "breakeven",
        "total_sessions",
        "active_sessions",
        "profitable_sessions",
        "losing_sessions",
        "flat_sessions",
        "max_consecutive_losses",
    }
)
POINT_METRICS = frozenset(
    {
        "gross_pnl",
        "costs",
        "slippage_impact",
        "net_pnl",
        "max_drawdown",
        "best_session_net_pnl",
        "worst_session_net_pnl",
        "average_trade",
        "average_win",
        "average_loss",
        "net_pnl_per_session",
    }
)
RATIO_METRICS = frozenset(
    {
        "win_rate",
        "profit_factor",
        "payoff_ratio",
        "trades_per_session",
        "positive_session_rate",
        "active_session_rate",
        "largest_profitable_session_share",
        "largest_trade_count_session_share",
    }
)
BASE_METRICS = COUNT_METRICS | POINT_METRICS | RATIO_METRICS
COMPARISON_BASES = (
    "trades",
    "trades_per_session",
    "active_sessions",
    "active_session_rate",
    "win_rate",
    "average_trade",
    "profit_factor",
    "net_pnl_per_session",
    "max_drawdown",
    "max_consecutive_losses",
)
COMPARISON_RATIOS = ("trades_per_session", "profit_factor")
COMPARISON_METRICS = frozenset(
    [f"{name}_delta" for name in COMPARISON_BASES] + [f"{name}_ratio" for name in COMPARISON_RATIOS]
)


class SplitPolicyV1(StrictModel):
    schema_version: Literal["research-split-policy/v1"] = "research-split-policy/v1"
    method: Literal["CHRONOLOGICAL_TAIL_HOLDOUT"] = "CHRONOLOGICAL_TAIL_HOLDOUT"
    validation_sessions: int = Field(default=6, ge=1)
    min_discovery_sessions: int = Field(default=13, ge=1)
    min_validation_sessions: int = Field(default=6, ge=1)
    insufficient_sessions: Literal["ERROR"] = "ERROR"

    @model_validator(mode="after")
    def valid_tail(self) -> SplitPolicyV1:
        if self.validation_sessions < self.min_validation_sessions:
            raise ValueError("validation_sessions is below min_validation_sessions")
        return self


class RationalThresholdV1(StrictModel):
    numerator: str = Field(pattern=r"^-?(0|[1-9]\d*)$")
    denominator: str = Field(pattern=r"^[1-9]\d*$")

    def rational(self) -> CanonicalRational:
        return CanonicalRational(int(self.numerator), int(self.denominator))


class MetricGateV1(StrictModel):
    metric: str
    operator: Literal["GT", "GTE", "LT", "LTE", "EQ", "NE"]
    threshold: int | str | RationalThresholdV1

    def canonical_record(self) -> dict:
        value = (
            self.threshold.rational()
            if isinstance(self.threshold, RationalThresholdV1)
            else CanonicalRational(self.threshold)
            if type(self.threshold) is int
            else CanonicalRational.from_decimal(self.threshold)
        )
        base = self.metric.removesuffix("_delta")
        if base in COUNT_METRICS and value.denominator != 1:
            raise ContractError("count metric thresholds must be integral")
        return {"metric": self.metric, "operator": self.operator, "threshold": value.to_record()}

    @property
    def criterion_id(self) -> str:
        return identity("research-criterion/v1", self.canonical_record())


class GatePolicyV1(StrictModel):
    schema_version: Literal["discovery-gate-policy/v1", "validation-gate-policy/v1"]
    criteria: Annotated[tuple[MetricGateV1, ...], Field(strict=False)] = ()
    semantics: Literal["all-criteria-undefined-fails/v1"] = GATE_POLICY

    @model_validator(mode="after")
    def closed_registry(self) -> GatePolicyV1:
        allowed = BASE_METRICS | (
            COMPARISON_METRICS
            if self.schema_version == "validation-gate-policy/v1"
            else frozenset()
        )
        for criterion in self.criteria:
            if criterion.metric not in allowed:
                raise ValueError(f"metric is not allowed in this gate: {criterion.metric}")
            criterion.canonical_record()
        return self

    def canonical_record(self) -> dict:
        criteria = {c.criterion_id: c.canonical_record() for c in self.criteria}
        return {
            "schema_version": self.schema_version,
            "semantics": self.semantics,
            "criteria": [{"criterion_id": key, **value} for key, value in sorted(criteria.items())],
        }

    @property
    def policy_id(self) -> str:
        return identity(self.schema_version, self.canonical_record())


def metric_registry_record() -> dict:
    return {
        "schema_version": "research-metric-registry/v1",
        "counts": sorted(COUNT_METRICS),
        "points": sorted(POINT_METRICS),
        "ratios": sorted(RATIO_METRICS),
        "comparison": sorted(COMPARISON_METRICS),
        "win_rate_unit": "FRACTION",
        "point_unit": "PRICE_POINTS_NOT_MONEY",
    }
