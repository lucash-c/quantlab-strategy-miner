"""Granular content-addressed cache for Feature Engine v2 series."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pyarrow as pa
import pyarrow.parquet as pq
from quantlab_core.canonical import canonical_json_bytes, sha256_bytes, write_canonical_json
from quantlab_core.errors import ContractError
from quantlab_core.feature_engine_v2 import (
    FEATURE_ENGINE_V2_VERSION,
    FEATURE_SERIES_SCHEMA_VERSION,
    FeatureObservation,
    FeatureValue,
    calculate_derived_feature_series,
    calculate_feature_series,
    mathematical_policy_record,
)
from quantlab_core.feature_specs import DIRECT_FEATURES, FeatureSpec
from quantlab_core.market_data import Candle
from quantlab_core.numeric import CanonicalRational

from quantlab_data.historical import (
    CachedArtifact,
    MarketHistoricalBuild,
    MaterializedSession,
    _artifact,
    _identity_key,
    _publish_cache,
    _validate_cached,
    read_session_candles,
    read_session_trades,
)
from quantlab_data.parquet import new_semantic_digest, update_semantic_digest, write_record_batches

FEATURE_CACHE_V2_VERSION = "2.0.0"
FEATURE_SET_MANIFEST_VERSION = "feature-set-manifest/v3"

FEATURE_VALUE_SCHEMA = pa.schema(
    [
        pa.field("session_id", pa.string(), nullable=False),
        pa.field("trading_date", pa.string(), nullable=False),
        pa.field("logical_asset", pa.string(), nullable=False),
        pa.field("physical_contract", pa.string(), nullable=False),
        pa.field("timeframe", pa.string(), nullable=False),
        pa.field("candle_open_time_ns_utc", pa.int64(), nullable=False),
        pa.field("candle_close_time_ns_utc", pa.int64(), nullable=False),
        pa.field("available_at_ns_utc", pa.int64(), nullable=False),
        pa.field("warmup_status", pa.string(), nullable=False),
        pa.field("executable_in_session", pa.bool_(), nullable=False),
        pa.field("non_executable_reason", pa.string(), nullable=True),
        pa.field("value_kind", pa.string(), nullable=True),
        pa.field("value_dimension", pa.string(), nullable=True),
        pa.field("numeric_numerator", pa.string(), nullable=True),
        pa.field("numeric_denominator", pa.string(), nullable=True),
        pa.field("boolean_value", pa.bool_(), nullable=True),
        pa.field("text_value", pa.string(), nullable=True),
        pa.field("undefined_reason", pa.string(), nullable=True),
    ],
    metadata={b"quantlab.schema": FEATURE_SERIES_SCHEMA_VERSION.encode("ascii")},
)


@dataclass(frozen=True, slots=True)
class MaterializedFeatureSession:
    market: MaterializedSession
    timeframe: str
    features: dict[str, CachedArtifact]


@dataclass(frozen=True, slots=True)
class FeatureSetBuild:
    feature_set_id: str
    sessions: tuple[MaterializedFeatureSession, ...]
    operational_report: dict[str, object]
    manifest: dict[str, object]


def _record(observation: FeatureObservation) -> dict[str, object]:
    value = observation.value
    numeric = None if value is None else value.numeric
    return {
        "session_id": observation.session_id,
        "trading_date": observation.trading_date,
        "logical_asset": observation.logical_asset,
        "physical_contract": observation.physical_contract,
        "timeframe": observation.timeframe,
        "candle_open_time_ns_utc": observation.candle_open_time_ns_utc,
        "candle_close_time_ns_utc": observation.candle_close_time_ns_utc,
        "available_at_ns_utc": observation.available_at_ns_utc,
        "warmup_status": observation.warmup_status,
        "executable_in_session": observation.executable_in_session,
        "non_executable_reason": observation.non_executable_reason,
        "value_kind": None if value is None else value.kind,
        "value_dimension": None if value is None else value.dimension,
        "numeric_numerator": None if numeric is None else str(numeric.numerator),
        "numeric_denominator": None if numeric is None else str(numeric.denominator),
        "boolean_value": None if value is None else value.boolean,
        "text_value": None if value is None else value.text,
        "undefined_reason": observation.undefined_reason,
    }


def read_feature_observations(path: Path) -> Iterator[FeatureObservation]:
    parquet = pq.ParquetFile(path)
    for batch in parquet.iter_batches():
        columns = batch.to_pydict()
        for index in range(batch.num_rows):
            kind = columns["value_kind"][index]
            value: FeatureValue | None = None
            if kind == "NUMERIC":
                value = FeatureValue.number(
                    columns["value_dimension"][index],
                    CanonicalRational(
                        int(columns["numeric_numerator"][index]),
                        int(columns["numeric_denominator"][index]),
                    ),
                )
            elif kind == "BOOLEAN":
                value = FeatureValue.flag(columns["boolean_value"][index])
            elif kind == "ENUM":
                value = FeatureValue.enum(
                    columns["value_dimension"][index], columns["text_value"][index]
                )
            elif kind is not None:
                raise ContractError(f"unsupported cached feature value kind: {kind}")
            yield FeatureObservation(
                columns["session_id"][index],
                columns["trading_date"][index],
                columns["logical_asset"][index],
                columns["physical_contract"][index],
                columns["timeframe"][index],
                columns["candle_open_time_ns_utc"][index],
                columns["candle_close_time_ns_utc"][index],
                columns["available_at_ns_utc"][index],
                columns["warmup_status"][index],
                columns["executable_in_session"][index],
                columns["non_executable_reason"][index],
                value,
                columns["undefined_reason"][index],
            )


def _ensure_feature(
    market: MaterializedSession,
    timeframe: str,
    spec: FeatureSpec,
    dependencies: dict[str, CachedArtifact],
    cache_root: Path,
) -> CachedArtifact:
    candle_cache = market.candles[timeframe]
    candle_artifact = cast(dict[str, dict[str, object]], candle_cache.manifest["artifacts"])[
        "candles"
    ]
    dependency_semantics = {
        feature_id: cast(dict[str, dict[str, object]], cache.manifest["artifacts"])[
            "feature_values"
        ]["semantic_sha256"]
        for feature_id, cache in dependencies.items()
    }
    identity: dict[str, object] = {
        "kind": "feature-series-v2",
        "session_id": market.session.session_id,
        "timeframe": timeframe,
        "candle_semantic_sha256": candle_artifact["semantic_sha256"],
        "feature_spec": spec.to_record(),
        "dependency_semantic_sha256": dependency_semantics,
        "session_policy": "RESET_EACH_TRADING_SESSION",
        "availability_policy": "CANDLE_NOMINAL_CLOSE",
        "mathematical_policy": mathematical_policy_record(),
        "feature_engine_version": FEATURE_ENGINE_V2_VERSION,
        "cache_version": FEATURE_CACHE_V2_VERSION,
        "schema_version": FEATURE_SERIES_SCHEMA_VERSION,
    }
    if spec.name == "session_trade_vwap":
        trade_artifact = cast(
            dict[str, dict[str, object]], market.trades.manifest["artifacts"]
        )["trades"]
        identity["trade_semantic_sha256"] = trade_artifact["semantic_sha256"]
    key = _identity_key(identity)
    directory = cache_root / "feature-v2" / key
    manifest_name = "feature-manifest.json"
    if directory.exists():
        manifest = _validate_cached(directory, manifest_name, key)
        return CachedArtifact(key, directory, manifest, True)

    def build(temporary: Path) -> dict[str, object]:
        destination = temporary / "feature-values.parquet"
        digest = new_semantic_digest(FEATURE_SERIES_SCHEMA_VERSION)
        candle_path = candle_cache.directory / "candles.parquet"

        def candles() -> Iterator[Candle]:
            for row in read_session_candles(candle_path):
                yield row.candle

        if spec.name in DIRECT_FEATURES:
            trades = None
            if spec.name == "session_trade_vwap":
                trades = (
                    row.as_market_trade()
                    for row in read_session_trades(market.trades.directory / "trades.parquet")
                )
            observations = calculate_feature_series(
                market.session,
                candles(),
                spec,
                trades=trades,
            )
        else:
            observations = calculate_derived_feature_series(
                market.session,
                candles(),
                spec,
                [
                    read_feature_observations(
                        dependencies[feature_id].directory / "feature-values.parquet"
                    )
                    for feature_id in spec.inputs
                ],
            )

        def records() -> Iterator[dict[str, object]]:
            for observation in observations:
                record = _record(observation)
                update_semantic_digest(digest, record.values())
                yield record

        row_count = write_record_batches(destination, FEATURE_VALUE_SCHEMA, records())
        artifact = _artifact(destination, row_count, digest.hexdigest())
        manifest: dict[str, object] = {
            "manifest_file": manifest_name,
            "schema_version": "feature-cache-manifest/v2",
            "cache_key": key,
            "identity": identity,
            "feature_id": spec.feature_id,
            "feature_spec_sha256": spec.semantic_sha256(),
            "artifacts": {"feature_values": artifact},
        }
        write_canonical_json(temporary / manifest_name, manifest)
        return manifest

    manifest = _publish_cache(directory, build)
    return CachedArtifact(key, directory, manifest, False)


def build_feature_set(
    market_build: MarketHistoricalBuild,
    specs: Sequence[FeatureSpec],
    *,
    timeframe: str,
    cache_root: Path,
) -> FeatureSetBuild:
    """Materialize only the ordered feature dependency closure requested by a strategy."""

    if timeframe not in market_build.dataset.timeframes:
        raise ContractError("feature timeframe is absent from the market dataset")
    feature_ids = [spec.feature_id for spec in specs]
    if len(feature_ids) != len(set(feature_ids)):
        raise ContractError("feature specs must have unique feature_id values")
    declared: set[str] = set()
    for spec in specs:
        if not set(spec.inputs) <= declared:
            raise ContractError("feature specs must be dependency ordered")
        declared.add(spec.feature_id)

    sessions: list[MaterializedFeatureSession] = []
    operations: list[dict[str, object]] = []
    for market in market_build.sessions:
        caches: dict[str, CachedArtifact] = {}
        for spec in specs:
            dependencies = {feature_id: caches[feature_id] for feature_id in spec.inputs}
            cache = _ensure_feature(market, timeframe, spec, dependencies, cache_root)
            caches[spec.feature_id] = cache
            operations.append(
                {
                    "layer": "feature-v2",
                    "session_id": market.session.session_id,
                    "timeframe": timeframe,
                    "feature_id": spec.feature_id,
                    "key": cache.key,
                    "status": "CACHE_HIT" if cache.cache_hit else "BUILT",
                }
            )
        sessions.append(MaterializedFeatureSession(market, timeframe, caches))

    identity = {
        "market_dataset_id": market_build.dataset.dataset_id,
        "timeframe": timeframe,
        "feature_specs": [spec.to_record() for spec in specs],
        "series_cache_keys": [
            {
                "session_id": item.market.session.session_id,
                "features": {
                    feature_id: cache.key for feature_id, cache in item.features.items()
                },
            }
            for item in sessions
        ],
        "feature_engine_version": FEATURE_ENGINE_V2_VERSION,
        "mathematical_policy": mathematical_policy_record(),
    }
    feature_set_id = "sha256:" + sha256_bytes(canonical_json_bytes(identity))
    manifest: dict[str, object] = {
        "schema_version": FEATURE_SET_MANIFEST_VERSION,
        "feature_set_id": feature_set_id,
        **identity,
        "sessions": [
            {
                "session_id": item.market.session.session_id,
                "features": {
                    feature_id: {
                        "cache_key": cache.key,
                        "artifact": cache.manifest["artifacts"]["feature_values"],
                    }
                    for feature_id, cache in item.features.items()
                },
            }
            for item in sessions
        ],
    }
    report = {
        "schema_version": "feature-build-report/v2",
        "feature_set_id": feature_set_id,
        "operations": operations,
    }
    return FeatureSetBuild(feature_set_id, tuple(sessions), report, manifest)
