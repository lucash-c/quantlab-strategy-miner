"""Strict additive scientific output contracts; computed IDs are checked on deserialization."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator
from quantlab_core.strategy import StrictModel
from quantlab_mining.contracts import identity

from quantlab_research.contracts import SESSION_POLICY, SplitPolicyV1

SessionIds = Annotated[tuple[str, ...], Field(strict=False)]


class PartitionBoundsV1(StrictModel):
    partition_id: str
    session_ids: SessionIds
    count: int = Field(ge=1)
    first_date: str
    last_date: str

    @model_validator(mode="after")
    def validate_bounds(self) -> PartitionBoundsV1:
        if self.count != len(self.session_ids) or self.first_date > self.last_date:
            raise ValueError("partition count/date bounds mismatch")
        return self


class ResearchSplitPlanV1(StrictModel):
    schema_version: Literal["research-split-plan/v1"]
    historical_dataset_id: str
    ordered_session_ids: SessionIds
    method: Literal["CHRONOLOGICAL_TAIL_HOLDOUT"]
    policy: SplitPolicyV1
    discovery: PartitionBoundsV1
    validation: PartitionBoundsV1
    excluded_sessions: Annotated[tuple[dict, ...], Field(strict=False)]
    split_plan_id: str

    @model_validator(mode="after")
    def validate_plan(self) -> ResearchSplitPlanV1:
        if (
            self.discovery.session_ids + self.validation.session_ids != self.ordered_session_ids
            or len(set(self.ordered_session_ids)) != len(self.ordered_session_ids)
            or self.discovery.last_date >= self.validation.first_date
            or self.excluded_sessions
        ):
            raise ValueError("split must be whole-session, disjoint, exhaustive and chronological")
        if (
            self.validation.count != self.policy.validation_sessions
            or self.discovery.count < self.policy.min_discovery_sessions
        ):
            raise ValueError("split does not satisfy its policy")
        record = self.model_dump(mode="json", exclude={"split_plan_id"})
        if identity("research-split-plan/v1", record) != self.split_plan_id:
            raise ValueError("split_plan_id mismatch")
        return self


class ResearchPartitionV1(StrictModel):
    schema_version: Literal["research-partition/v1"]
    logical_asset: str
    session_ids: SessionIds
    trading_dates: SessionIds
    session_policy: Literal["reset-no-overnight-last-tick-forbidden/v1"] = SESSION_POLICY
    timezone: Literal["B3_FIXED_UTC_MINUS_03"]
    empty_candles: Literal["DO_NOT_FILL"]
    partition_id: str

    @model_validator(mode="after")
    def validate_partition(self) -> ResearchPartitionV1:
        if (
            not self.session_ids
            or len(self.session_ids) != len(self.trading_dates)
            or len(set(self.session_ids)) != len(self.session_ids)
            or list(self.trading_dates) != sorted(set(self.trading_dates))
        ):
            raise ValueError("partition sessions must be unique and strictly chronological")
        if (
            identity(
                "research-partition/v1", self.model_dump(mode="json", exclude={"partition_id"})
            )
            != self.partition_id
        ):
            raise ValueError("partition_id mismatch")
        return self
