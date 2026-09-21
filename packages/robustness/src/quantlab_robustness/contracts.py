"""Strict policies for post-score robustness; no quantitative defaults."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, model_validator
from quantlab_core.errors import ContractError
from quantlab_core.numeric import CanonicalRational
from quantlab_core.strategy import StrictModel
from quantlab_mining.contracts import identity
from quantlab_research.contracts import GatePolicyV1

ROBUSTNESS_FAMILIES = ("WALK_FORWARD", "MONTE_CARLO", "SENSITIVITY", "STRESS")
_HEX_256 = re.compile(r"^[0-9a-f]{64}$")


class RationalValueV1(StrictModel):
    numerator: str = Field(pattern=r"^-?(0|[1-9]\d*)$")
    denominator: str = Field(pattern=r"^[1-9]\d*$")

    @model_validator(mode="after")
    def canonical(self) -> RationalValueV1:
        value = CanonicalRational(int(self.numerator), int(self.denominator))
        if value.to_record() != self.model_dump(mode="json"):
            raise ValueError("rational must use its unique canonical representation")
        return self

    def value(self) -> CanonicalRational:
        return CanonicalRational(int(self.numerator), int(self.denominator))


class CandidateSelectionV1(StrictModel):
    schema_version: Literal["robustness-candidate-selection/v1"]
    method: Literal[
        "ALL_SCORED",
        "RAW_RANKING_TOP_N",
        "DIVERSIFIED_RANKING_TOP_N",
        "TOP_N_OUTPUT",
        "EXPLICIT_CANDIDATE_IDS",
    ]
    top_n: int | None = Field(default=None, ge=1)
    candidate_ids: Annotated[tuple[str, ...], Field(default=(), strict=False)]

    @model_validator(mode="after")
    def method_arguments(self) -> CandidateSelectionV1:
        needs_n = self.method in {"RAW_RANKING_TOP_N", "DIVERSIFIED_RANKING_TOP_N"}
        needs_ids = self.method == "EXPLICIT_CANDIDATE_IDS"
        if needs_n != (self.top_n is not None):
            raise ValueError("top_n must be present exactly for ranking Top N selectors")
        if needs_ids != bool(self.candidate_ids):
            raise ValueError("candidate_ids must be present exactly for explicit selection")
        if len(set(self.candidate_ids)) != len(self.candidate_ids):
            raise ValueError("candidate_ids must be unique")
        return self

    @property
    def selection_policy_id(self) -> str:
        return identity(self.schema_version, self.model_dump(mode="json"))


class WalkForwardPolicyV1(StrictModel):
    schema_version: Literal["walk-forward-policy/v1"]
    method: Literal["ROLLING_FIXED"]
    discovery_sessions: int = Field(ge=1)
    validation_sessions: int = Field(ge=1)
    step_sessions: int = Field(ge=1)
    gap_sessions: int = Field(ge=0)
    min_folds: int = Field(ge=1)
    incomplete_tail_policy: Literal["DROP_INCOMPLETE_TAIL"]
    discovery_gate: GatePolicyV1 | None
    validation_gate: GatePolicyV1 | None

    @model_validator(mode="after")
    def gate_stages(self) -> WalkForwardPolicyV1:
        if self.discovery_gate and self.discovery_gate.schema_version != "discovery-gate-policy/v1":
            raise ValueError("Walk-Forward Discovery gate has the wrong stage")
        if (
            self.validation_gate
            and self.validation_gate.schema_version != "validation-gate-policy/v1"
        ):
            raise ValueError("Walk-Forward Validation gate has the wrong stage")
        return self

    @property
    def policy_id(self) -> str:
        return identity(self.schema_version, self.model_dump(mode="json"))


class SessionPoolSelectorV1(StrictModel):
    source: Literal[
        "RESEARCH_VALIDATION_PARTITION",
        "WALK_FORWARD_VALIDATION_UNION",
        "EXPLICIT_RESEARCH_PARTITION",
        "EXPLICIT_SESSION_IDS",
    ]
    partition_id: str | None = None
    session_ids: Annotated[tuple[str, ...], Field(default=(), strict=False)]

    @model_validator(mode="after")
    def explicit_arguments(self) -> SessionPoolSelectorV1:
        if self.source == "EXPLICIT_RESEARCH_PARTITION" and self.partition_id is None:
            raise ValueError("explicit partition source requires partition_id")
        if self.source != "EXPLICIT_RESEARCH_PARTITION" and self.partition_id is not None:
            raise ValueError("partition_id is only valid for explicit partition source")
        if self.source == "EXPLICIT_SESSION_IDS" and not self.session_ids:
            raise ValueError("explicit session source requires session_ids")
        if self.source != "EXPLICIT_SESSION_IDS" and self.session_ids:
            raise ValueError("session_ids are only valid for explicit session source")
        if len(set(self.session_ids)) != len(self.session_ids):
            raise ValueError("session_ids must be unique")
        return self


class DrawdownThresholdV1(StrictModel):
    threshold: RationalValueV1
    operator: Literal["GT"] = "GT"

    @property
    def threshold_id(self) -> str:
        return identity("monte-carlo-drawdown-threshold/v1", self.model_dump(mode="json"))


class MonteCarloPolicyV1(StrictModel):
    schema_version: Literal["monte-carlo-policy/v1"]
    sampling_method: Literal[
        "SESSION_PERMUTATION_WITHOUT_REPLACEMENT",
        "SESSION_BOOTSTRAP_WITH_REPLACEMENT",
    ]
    source_pool: SessionPoolSelectorV1
    number_of_paths: int = Field(ge=1)
    path_length_sessions: int = Field(ge=1)
    seed: str
    sampler_version: Literal["SHA256_COUNTER_REJECTION_V1"]
    quantile_policy: Literal["NEAREST_RANK_V1"]
    minimum_source_sessions: int = Field(ge=1)
    drawdown_thresholds: Annotated[tuple[DrawdownThresholdV1, ...], Field(default=(), strict=False)]

    @model_validator(mode="after")
    def validate_seed_thresholds(self) -> MonteCarloPolicyV1:
        if _HEX_256.fullmatch(self.seed) is None:
            raise ValueError("seed must be exactly 256 lowercase hexadecimal bits")
        ids = [item.threshold_id for item in self.drawdown_thresholds]
        if len(set(ids)) != len(ids):
            raise ValueError("drawdown thresholds must be unique")
        return self

    @property
    def policy_id(self) -> str:
        return identity(self.schema_version, self.model_dump(mode="json"))


class ParameterTargetV1(StrictModel):
    kind: Literal["FEATURE_PARAMETER", "CONDITION_CONSTANT", "STOP_LOSS", "TAKE_PROFIT"]
    path: str

    @model_validator(mode="after")
    def path_shape(self) -> ParameterTargetV1:
        if not self.path.startswith("/"):
            raise ValueError("parameter path must be an absolute JSON pointer")
        return self


class PerturbationSpecV1(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    target: ParameterTargetV1
    method: Literal["INTEGER_ABSOLUTE_DELTA", "DECIMAL_ABSOLUTE_DELTA", "DECIMAL_RELATIVE_DELTA"]
    integer_delta: int | None = None
    rational_delta: RationalValueV1 | None = None

    @model_validator(mode="after")
    def delta_shape(self) -> PerturbationSpecV1:
        integer = self.method == "INTEGER_ABSOLUTE_DELTA"
        if integer != (self.integer_delta is not None) or integer == (
            self.rational_delta is not None
        ):
            raise ValueError("perturbation delta type does not match its method")
        value = (
            CanonicalRational(self.integer_delta) if integer else self.rational_delta.value()  # type: ignore[union-attr]
        )
        if value.numerator == 0:
            raise ValueError("NO_OP_PERTURBATION")
        return self

    @property
    def perturbation_spec_id(self) -> str:
        return identity("perturbation-spec/v1", self.model_dump(mode="json"))


class ToleranceCriterionV1(StrictModel):
    metric: Literal[
        "trades_delta",
        "average_trade_delta",
        "net_pnl_per_session_delta",
        "win_rate_delta",
        "max_drawdown_delta",
        "positive_session_rate_delta",
    ]
    operator: Literal["GT", "GTE", "LT", "LTE", "EQ", "NE"]
    threshold: RationalValueV1


class SensitivityPolicyV1(StrictModel):
    schema_version: Literal["sensitivity-policy/v1"]
    method: Literal["ONE_AT_A_TIME"]
    source_pool: SessionPoolSelectorV1
    perturbations: Annotated[tuple[PerturbationSpecV1, ...], Field(strict=False)]
    tolerance_criteria: Annotated[tuple[ToleranceCriterionV1, ...], Field(default=(), strict=False)]
    validation_gate: GatePolicyV1 | None

    @model_validator(mode="after")
    def nonempty(self) -> SensitivityPolicyV1:
        if not self.perturbations:
            raise ValueError("Sensitivity requires explicit perturbations")
        if (
            self.validation_gate
            and self.validation_gate.schema_version != "validation-gate-policy/v1"
        ):
            raise ValueError("Sensitivity gate must be a Validation gate")
        return self

    @property
    def policy_id(self) -> str:
        return identity(self.schema_version, self.model_dump(mode="json"))


class FrictionChangeV1(StrictModel):
    method: Literal["ABSOLUTE_POINTS", "MULTIPLIER"]
    value: RationalValueV1

    @model_validator(mode="after")
    def stress_direction(self) -> FrictionChangeV1:
        value = self.value.value()
        if value.numerator < 0:
            raise ValueError("friction change cannot be negative")
        if self.method == "MULTIPLIER" and value.compare(CanonicalRational(1)) < 0:
            raise ValueError("Stress multiplier must be >= 1")
        return self


class StressScenarioV1(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    cost: FrictionChangeV1 | None
    slippage: FrictionChangeV1 | None

    @model_validator(mode="after")
    def at_least_one_change(self) -> StressScenarioV1:
        if self.cost is None and self.slippage is None:
            raise ValueError("NON_STRESS_SCENARIO")
        return self


class ExecutionStressPolicyV1(StrictModel):
    schema_version: Literal["execution-stress-policy/v1"]
    source_pool: SessionPoolSelectorV1
    scenarios: Annotated[tuple[StressScenarioV1, ...], Field(strict=False)]
    validation_gate: GatePolicyV1 | None

    @model_validator(mode="after")
    def valid_scenarios(self) -> ExecutionStressPolicyV1:
        if not self.scenarios:
            raise ValueError("Stress requires explicit scenarios")
        if len({item.name for item in self.scenarios}) != len(self.scenarios):
            raise ValueError("Stress scenario names must be unique")
        if (
            self.validation_gate
            and self.validation_gate.schema_version != "validation-gate-policy/v1"
        ):
            raise ValueError("Stress gate must be a Validation gate")
        return self

    @property
    def policy_id(self) -> str:
        return identity(self.schema_version, self.model_dump(mode="json"))


_GATE_PREFIXES = ("walk_forward.", "monte_carlo.", "sensitivity.", "stress.")


class RobustnessCriterionV1(StrictModel):
    metric: str
    operator: Literal["GT", "GTE", "LT", "LTE", "EQ", "NE"]
    threshold: RationalValueV1

    @model_validator(mode="after")
    def registered_namespace(self) -> RobustnessCriterionV1:
        if not self.metric.startswith(_GATE_PREFIXES):
            raise ValueError("metric is outside the robustness registry")
        return self

    @property
    def criterion_id(self) -> str:
        return identity("robustness-gate-criterion/v1", self.model_dump(mode="json"))


class RobustnessGatePolicyV1(StrictModel):
    schema_version: Literal["robustness-gate-policy/v1"]
    required_families: Annotated[tuple[Literal[*ROBUSTNESS_FAMILIES], ...], Field(strict=False)]
    criteria: Annotated[tuple[RobustnessCriterionV1, ...], Field(strict=False)]
    semantics: Literal["all-criteria-insufficient-precedence/v1"]

    @model_validator(mode="after")
    def nonempty_consistent(self) -> RobustnessGatePolicyV1:
        if not self.criteria:
            raise ValueError("Robustness Gate must contain at least one criterion")
        if not self.required_families or len(set(self.required_families)) != len(
            self.required_families
        ):
            raise ValueError("required_families must be a nonempty set")
        required = set(self.required_families)
        for criterion in self.criteria:
            family = criterion.metric.split(".", 1)[0].upper()
            if family not in required:
                raise ValueError("gate criterion references a family that is not required")
        return self

    @property
    def policy_id(self) -> str:
        criteria = sorted(
            (
                {"criterion_id": item.criterion_id, **item.model_dump(mode="json")}
                for item in self.criteria
            ),
            key=lambda item: item["criterion_id"],
        )
        record = {
            "schema_version": self.schema_version,
            "required_families": sorted(self.required_families),
            "criteria": criteria,
            "semantics": self.semantics,
        }
        return identity(self.schema_version, record)


class RobustnessWorkloadPolicyV1(StrictModel):
    schema_version: Literal["robustness-workload-policy/v1"]
    max_candidates: int = Field(ge=1)
    max_walk_forward_folds: int = Field(ge=1)
    max_paths: int = Field(ge=1)
    max_path_length_sessions: int = Field(ge=1)
    max_total_sampled_blocks: int = Field(ge=1)
    max_sensitivity_scenarios: int = Field(ge=1)
    max_stress_scenarios: int = Field(ge=1)
    max_candidate_scenario_combinations: int = Field(ge=1)
    max_expected_cse_builds: int = Field(ge=1)

    @property
    def policy_id(self) -> str:
        return identity(self.schema_version, self.model_dump(mode="json"))


def load_policy(path: Path, model: type[StrictModel]):
    def reject(value: str) -> None:
        raise ContractError(f"JSON floating-point/special number forbidden: {value}")

    try:
        return model.model_validate(
            json.loads(path.read_text(encoding="utf-8"), parse_float=reject, parse_constant=reject)
        )
    except (OSError, ValueError) as exc:
        raise ContractError(f"invalid {model.__name__}: {exc}") from exc
