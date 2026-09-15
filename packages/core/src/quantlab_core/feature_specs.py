"""Closed, versioned feature registry and validated specifications."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from quantlab_core.canonical import canonical_json_bytes, sha256_bytes
from quantlab_core.errors import ContractError

FEATURE_REGISTRY_VERSION = "feature-registry/v1"
FEATURE_VERSION = "1.0.0"

DIRECT_FEATURES = {
    "sma_close",
    "ema_close",
    "session_trade_vwap",
    "true_range",
    "atr_wilder",
    "candle_range",
    "rolling_mean_range",
    "candle_volume",
    "rolling_mean_volume",
    "relative_volume",
    "rolling_high",
    "rolling_low",
    "distance_to_rolling_high",
    "distance_to_rolling_low",
    "breakout_above_previous_high",
    "breakout_below_previous_low",
    "candle_body",
    "absolute_body",
    "total_range",
    "upper_wick",
    "lower_wick",
    "close_range_position",
    "candle_direction",
    "point_change",
    "percent_change",
    "n_candle_return",
    "local_time",
    "minute_since_session_start",
}
DERIVED_FEATURES = {
    "close_vs_sma",
    "close_vs_ema",
    "close_vs_vwap",
    "sma_fast_vs_slow",
    "ema_fast_vs_slow",
    "distance_to_vwap",
    "distance_between_averages",
}
ALL_FEATURES = DIRECT_FEATURES | DERIVED_FEATURES

_PERIOD_FEATURES = {
    "sma_close",
    "ema_close",
    "atr_wilder",
    "rolling_mean_range",
    "rolling_mean_volume",
    "relative_volume",
    "rolling_high",
    "rolling_low",
    "distance_to_rolling_high",
    "distance_to_rolling_low",
    "breakout_above_previous_high",
    "breakout_below_previous_low",
    "point_change",
    "percent_change",
    "n_candle_return",
}
_ROLLING_POLICY_FEATURES = {
    "rolling_mean_range",
    "rolling_mean_volume",
    "relative_volume",
    "rolling_high",
    "rolling_low",
    "distance_to_rolling_high",
    "distance_to_rolling_low",
    "breakout_above_previous_high",
    "breakout_below_previous_low",
}
_NO_PARAMETER_FEATURES = DIRECT_FEATURES - _PERIOD_FEATURES
_ONE_INPUT_FEATURES = {
    "close_vs_sma",
    "close_vs_ema",
    "close_vs_vwap",
    "distance_to_vwap",
}
_TWO_INPUT_FEATURES = {
    "sma_fast_vs_slow",
    "ema_fast_vs_slow",
    "distance_between_averages",
}


@dataclass(frozen=True, slots=True)
class FeatureSpec:
    feature_id: str
    name: str
    version: str
    parameters: tuple[tuple[str, int | bool | str], ...]
    inputs: tuple[str, ...]

    def parameter(self, name: str) -> int | bool | str:
        values = dict(self.parameters)
        try:
            return values[name]
        except KeyError as exc:
            raise ContractError(f"missing feature parameter: {name}") from exc

    def to_record(self) -> dict[str, object]:
        return {
            "feature_id": self.feature_id,
            "name": self.name,
            "version": self.version,
            "parameters": dict(self.parameters),
            "inputs": list(self.inputs),
        }

    def semantic_sha256(self) -> str:
        return sha256_bytes(canonical_json_bytes(self.to_record()))


def _strict_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractError(f"{field} must be an integer")
    return value


def parse_feature_spec(record: dict[str, Any]) -> FeatureSpec:
    expected = {"feature_id", "name", "version", "parameters", "inputs"}
    if set(record) != expected:
        raise ContractError("feature declaration has unexpected fields")
    feature_id = record["feature_id"]
    name = record["name"]
    version = record["version"]
    parameters = record["parameters"]
    inputs = record["inputs"]
    if not isinstance(feature_id, str) or not feature_id:
        raise ContractError("feature_id must be a non-empty string")
    if name not in ALL_FEATURES:
        raise ContractError(f"feature is not registered: {name}")
    if version != FEATURE_VERSION:
        raise ContractError(f"unsupported feature version for {name}: {version}")
    if not isinstance(parameters, dict):
        raise ContractError("feature parameters must be an object")
    if not isinstance(inputs, list) or any(not isinstance(item, str) for item in inputs):
        raise ContractError("feature inputs must be an array of feature_id strings")

    expected_parameters: set[str]
    if name in _PERIOD_FEATURES:
        expected_parameters = {"period"}
        if name in _ROLLING_POLICY_FEATURES:
            expected_parameters.add("include_current")
    elif name in _NO_PARAMETER_FEATURES or name in DERIVED_FEATURES:
        expected_parameters = set()
    else:
        raise ContractError(f"feature registry is incomplete for: {name}")
    if set(parameters) != expected_parameters:
        raise ContractError(
            f"{name} parameters must contain exactly: {sorted(expected_parameters)}"
        )
    if "period" in parameters:
        period = _strict_int(parameters["period"], "period")
        if not 1 <= period <= 100_000:
            raise ContractError("period must be between 1 and 100000 observed candles")
    if "include_current" in parameters and not isinstance(
        parameters["include_current"], bool
    ):
        raise ContractError("include_current must be boolean")
    if name.startswith("breakout_") and parameters.get("include_current") is not False:
        raise ContractError("breakout requires include_current=false")

    expected_inputs = 1 if name in _ONE_INPUT_FEATURES else 2 if name in _TWO_INPUT_FEATURES else 0
    if len(inputs) != expected_inputs:
        raise ContractError(f"{name} requires exactly {expected_inputs} feature inputs")
    if len(set(inputs)) != len(inputs):
        raise ContractError("feature inputs cannot contain duplicates")
    normalized_parameters: list[tuple[str, int | bool | str]] = []
    for key in sorted(parameters):
        value = parameters[key]
        if not isinstance(value, (int, bool, str)):
            raise ContractError("feature parameter has unsupported type")
        normalized_parameters.append((key, value))
    return FeatureSpec(
        feature_id,
        name,
        version,
        tuple(normalized_parameters),
        tuple(inputs),
    )


def feature_registry_record() -> dict[str, object]:
    return {
        "schema_version": FEATURE_REGISTRY_VERSION,
        "feature_version": FEATURE_VERSION,
        "features": sorted(ALL_FEATURES),
        "period_semantics": "OBSERVED_CANDLES_WITHIN_TRADING_SESSION",
        "automatic_candidate_generation": False,
    }

