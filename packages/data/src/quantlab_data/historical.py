"""Session-aware materialization and deterministic layered historical cache."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections.abc import Iterator, Sequence
from dataclasses import asdict, dataclass
from itertools import chain
from pathlib import Path
from typing import Any, cast

import pyarrow as pa
import pyarrow.parquet as pq
from quantlab_core.canonical import (
    canonical_json_bytes,
    sha256_bytes,
    sha256_file,
    write_canonical_json,
)
from quantlab_core.errors import ContractError
from quantlab_core.indicators import (
    INDICATOR_ENGINE_VERSION,
    SMA_CLOSE_VERSION,
    calculate_sma_close,
)
from quantlab_core.market_data import (
    Candle,
    FeatureRow,
    SessionCandle,
    SessionFeatureRow,
    SessionTrade,
)
from quantlab_core.sessions import HistoricalDataset, TradingSession
from quantlab_core.timeframes import (
    SUPPORTED_TIMEFRAMES,
    TIMEFRAME_ENGINE_VERSION,
    build_timeframe_candles,
)

from quantlab_data.adapters.b3_listed_trades import (
    B3_DRV_ADAPTER_VERSION,
    B3_DRV_PROFILE_VERSION,
    import_b3_listed_trades_drv,
)
from quantlab_data.normalizer import NORMALIZER_VERSION, normalize_csv
from quantlab_data.parquet import (
    PARQUET_ENGINE_VERSION,
    PYARROW_VERSION,
    iter_trades,
    new_semantic_digest,
    update_semantic_digest,
    write_record_batches,
)

HISTORICAL_CACHE_VERSION = "1.0.0"
HISTORICAL_MATERIALIZER_VERSION = "1.0.0"
SESSION_TRADE_SCHEMA_VERSION = "session-trades/v2"
SESSION_CANDLE_SCHEMA_VERSION = "session-candles/v2"
SESSION_FEATURE_SCHEMA_VERSION = "session-features-sma/v2"
FEATURE_AFTER_SESSION_REASON = "AVAILABLE_AFTER_LAST_ELIGIBLE_TRADE"

_IDENTITY_FIELDS = [
    pa.field("session_id", pa.string(), nullable=False),
    pa.field("trading_date", pa.string(), nullable=False),
    pa.field("logical_asset", pa.string(), nullable=False),
    pa.field("physical_contract", pa.string(), nullable=False),
]

SESSION_TRADE_SCHEMA = pa.schema(
    [
        *_IDENTITY_FIELDS,
        pa.field("timestamp_ns_utc", pa.int64(), nullable=False),
        pa.field("source_sequence", pa.int64(), nullable=False),
        pa.field("price_units", pa.int64(), nullable=False),
        pa.field("quantity", pa.int64(), nullable=False),
    ],
    metadata={b"quantlab.schema": SESSION_TRADE_SCHEMA_VERSION.encode("ascii")},
)

SESSION_CANDLE_SCHEMA = pa.schema(
    [
        *_IDENTITY_FIELDS,
        pa.field("timeframe", pa.string(), nullable=False),
        pa.field("open_time_ns_utc", pa.int64(), nullable=False),
        pa.field("close_time_ns_utc", pa.int64(), nullable=False),
        pa.field("open_units", pa.int64(), nullable=False),
        pa.field("high_units", pa.int64(), nullable=False),
        pa.field("low_units", pa.int64(), nullable=False),
        pa.field("close_units", pa.int64(), nullable=False),
        pa.field("volume", pa.int64(), nullable=False),
        pa.field("trade_count", pa.int64(), nullable=False),
    ],
    metadata={b"quantlab.schema": SESSION_CANDLE_SCHEMA_VERSION.encode("ascii")},
)

SESSION_FEATURE_SCHEMA = pa.schema(
    [
        *SESSION_CANDLE_SCHEMA,
        pa.field("sma_close_sum_units", pa.int64(), nullable=True),
        pa.field("sma_close_period", pa.int32(), nullable=True),
        pa.field("available_at_ns_utc", pa.int64(), nullable=False),
        pa.field("executable_in_session", pa.bool_(), nullable=False),
        pa.field("non_executable_reason", pa.string(), nullable=True),
    ],
    metadata={b"quantlab.schema": SESSION_FEATURE_SCHEMA_VERSION.encode("ascii")},
)


@dataclass(frozen=True, slots=True)
class SessionSource:
    source: Path
    physical_contract: str


@dataclass(frozen=True, slots=True)
class CachedArtifact:
    key: str
    directory: Path
    manifest: dict[str, object]
    cache_hit: bool


@dataclass(frozen=True, slots=True)
class MaterializedSession:
    session: TradingSession
    trades: CachedArtifact
    candles: dict[str, CachedArtifact]
    features: dict[str, CachedArtifact]


@dataclass(frozen=True, slots=True)
class HistoricalBuild:
    dataset: HistoricalDataset
    sessions: tuple[MaterializedSession, ...]
    operational_report: dict[str, object]
    manifest: dict[str, object]


def _identity_key(identity: dict[str, object]) -> str:
    return sha256_bytes(canonical_json_bytes(identity))


def _artifact(path: Path, row_count: int, semantic_sha256: str) -> dict[str, object]:
    return {
        "file": path.name,
        "row_count": row_count,
        "byte_sha256": sha256_file(path),
        "semantic_sha256": semantic_sha256,
    }


def _validate_cached(directory: Path, manifest_name: str, expected_key: str) -> dict[str, object]:
    manifest_path = directory / manifest_name
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"corrupt cache manifest at {manifest_path}: {exc}") from exc
    if manifest.get("cache_key") != expected_key:
        raise ContractError(f"cache key mismatch at {manifest_path}")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ContractError(f"cache artifacts missing at {manifest_path}")
    for value in artifacts.values():
        if not isinstance(value, dict) or not isinstance(value.get("file"), str):
            raise ContractError(f"invalid cached artifact declaration at {manifest_path}")
        artifact_path = directory / cast(str, value["file"])
        if not artifact_path.is_file() or sha256_file(artifact_path) != value.get("byte_sha256"):
            raise ContractError(f"cached artifact failed hash validation: {artifact_path}")
    return cast(dict[str, object], manifest)


def _publish_cache(directory: Path, builder: Any) -> dict[str, object]:
    directory.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{directory.name}.tmp-", dir=directory.parent))
    try:
        manifest = builder(temporary)
        try:
            os.replace(temporary, directory)
        except FileExistsError:
            shutil.rmtree(temporary, ignore_errors=True)
            return _validate_cached(directory, cast(str, manifest["manifest_file"]), directory.name)
        return cast(dict[str, object], manifest)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def _ensure_import(source: Path, contract: str, cache_root: Path) -> CachedArtifact:
    source = source.resolve(strict=True)
    source_hash = sha256_file(source)
    identity = {
        "kind": "b3-drv-import",
        "source_sha256": source_hash,
        "physical_contract": contract,
        "profile_version": B3_DRV_PROFILE_VERSION,
        "adapter_version": B3_DRV_ADAPTER_VERSION,
        "cache_version": HISTORICAL_CACHE_VERSION,
    }
    key = _identity_key(identity)
    directory = cache_root / "imports" / key
    manifest_name = "cache-manifest.json"
    if directory.exists():
        manifest = _validate_cached(directory, manifest_name, key)
        return CachedArtifact(key, directory, manifest, True)

    def build(temporary: Path) -> dict[str, object]:
        adapter_directory = temporary / "adapter"
        report = import_b3_listed_trades_drv(source, contract, adapter_directory)
        report_path = adapter_directory / "b3-import-report.json"
        canonical_path = adapter_directory / "canonical-trades.csv"
        adapter_artifacts = cast(dict[str, dict[str, object]], report["artifacts"])
        audit_path = adapter_directory / cast(
            str, adapter_artifacts["selected_events_audit"]["file"]
        )
        rejections_path = adapter_directory / cast(
            str, adapter_artifacts["rejections"]["file"]
        )
        artifacts = {
            "import_report": _artifact(report_path, 1, sha256_file(report_path)),
            "canonical_trades": _artifact(
                canonical_path,
                int(cast(dict[str, object], report["selection"])["valid_trades"]),
                cast(
                    str,
                    adapter_artifacts["canonical_trades"]["semantic_sha256"],
                ),
            ),
            "selected_events_audit": _artifact(
                audit_path,
                int(cast(dict[str, object], report["selection"])["events"]),
                cast(
                    str,
                    adapter_artifacts["selected_events_audit"]["semantic_sha256"],
                ),
            ),
            "rejections": _artifact(
                rejections_path,
                int(adapter_artifacts["rejections"]["row_count"]),
                cast(str, adapter_artifacts["rejections"]["semantic_sha256"]),
            ),
        }
        for artifact in artifacts.values():
            artifact["file"] = "adapter/" + cast(str, artifact["file"])
        manifest: dict[str, object] = {
            "manifest_file": manifest_name,
            "schema_version": "historical-cache-import/v1",
            "cache_key": key,
            "identity": identity,
            "import_id": report["import_id"],
            "source_file_name": source.name,
            "artifacts": artifacts,
        }
        write_canonical_json(temporary / manifest_name, manifest)
        return manifest

    manifest = _publish_cache(directory, build)
    return CachedArtifact(key, directory, manifest, False)


def _selected_trading_date(audit_path: Path) -> str:
    dates: set[str] = set()
    parquet = pq.ParquetFile(audit_path)
    for batch in parquet.iter_batches(columns=["DataNegocio"]):
        dates.update(cast(list[str], batch.column(0).to_pylist()))
        if len(dates) > 1:
            break
    if len(dates) != 1:
        raise ContractError(
            "one historical source selection must contain exactly one DataNegocio; "
            f"found {sorted(dates)}"
        )
    return next(iter(dates))


def _session_trade_record(session: TradingSession, trade: Any) -> dict[str, object]:
    return {
        "session_id": session.session_id,
        "trading_date": session.trading_date,
        "logical_asset": session.logical_asset,
        "physical_contract": session.physical_contract,
        "timestamp_ns_utc": trade.timestamp_ns_utc,
        "source_sequence": trade.source_sequence,
        "price_units": trade.price_units,
        "quantity": trade.quantity,
    }


def _ensure_session(
    imported: CachedArtifact,
    logical_asset: str,
    contract: str,
    cache_root: Path,
) -> CachedArtifact:
    import_report = json.loads(
        (imported.directory / "adapter" / "b3-import-report.json").read_text(encoding="utf-8")
    )
    audit_file = cast(
        str,
        cast(dict[str, dict[str, object]], import_report["artifacts"])[
            "selected_events_audit"
        ]["file"],
    )
    trading_date = _selected_trading_date(imported.directory / "adapter" / audit_file)
    canonical_semantic = cast(
        str,
        cast(dict[str, dict[str, object]], import_report["artifacts"])["canonical_trades"][
            "semantic_sha256"
        ],
    )
    identity = {
        "kind": "trading-session",
        "logical_asset": logical_asset,
        "physical_contract": contract,
        "trading_date": trading_date,
        "import_id": import_report["import_id"],
        "canonical_semantic_sha256": canonical_semantic,
        "normalizer_version": NORMALIZER_VERSION,
        "materializer_version": HISTORICAL_MATERIALIZER_VERSION,
        "schema_version": SESSION_TRADE_SCHEMA_VERSION,
    }
    key = _identity_key(identity)
    session_id = "sha256:" + key
    directory = cache_root / "sessions" / key
    manifest_name = "session-manifest.json"
    if directory.exists():
        manifest = _validate_cached(directory, manifest_name, key)
        return CachedArtifact(key, directory, manifest, True)

    def build(temporary: Path) -> dict[str, object]:
        native_directory = temporary / ".native"
        native_manifest = normalize_csv(
            imported.directory / "adapter" / "canonical-trades.csv", native_directory
        )
        price_scale = int(cast(dict[str, object], native_manifest["price"])["decimal_scale"])
        native_path = native_directory / "normalized-trades.parquet"
        native_trades = iter_trades(native_path)
        first = next(native_trades, None)
        if first is None:
            raise ContractError("a TradingSession cannot be empty")
        source_range = cast(dict[str, str], import_report["selection"]["imported_temporal_range"])
        from quantlab_core.time import parse_iso8601_ns

        session = TradingSession(
            session_id=session_id,
            trading_date=trading_date,
            logical_asset=logical_asset,
            physical_contract=contract,
            first_event_ns_utc=parse_iso8601_ns(source_range["first_timestamp_utc"]),
            last_event_ns_utc=parse_iso8601_ns(source_range["last_timestamp_utc"]),
            event_count=int(import_report["selection"]["valid_trades"]),
            price_scale=price_scale,
            source_sha256=cast(str, import_report["source"]["sha256"]),
            import_id=cast(str, import_report["import_id"]),
            normalized_semantic_sha256=cast(
                str, native_manifest["artifacts"]["normalized_trades"]["semantic_sha256"]
            ),
        )
        destination = temporary / "trades.parquet"
        digest = new_semantic_digest(SESSION_TRADE_SCHEMA_VERSION)
        digest.update(f"price_scale\t{price_scale}\n".encode())

        def records() -> Iterator[dict[str, object]]:
            for trade in chain((first,), native_trades):
                record = _session_trade_record(session, trade)
                update_semantic_digest(digest, record.values())
                yield record

        row_count = write_record_batches(destination, SESSION_TRADE_SCHEMA, records())
        if row_count != session.event_count:
            raise ContractError("session event count changed during materialization")
        artifact = _artifact(destination, row_count, digest.hexdigest())
        shutil.rmtree(native_directory)
        manifest: dict[str, object] = {
            "manifest_file": manifest_name,
            "schema_version": "trading-session-manifest/v1",
            "cache_key": key,
            "identity": identity,
            "session": asdict(session),
            "price": {"representation": "scaled_integer", "decimal_scale": price_scale},
            "ordering": ["timestamp_ns_utc", "source_sequence"],
            "artifacts": {"trades": artifact},
        }
        write_canonical_json(temporary / manifest_name, manifest)
        return manifest

    manifest = _publish_cache(directory, build)
    return CachedArtifact(key, directory, manifest, False)


def read_session_trades(path: Path) -> Iterator[SessionTrade]:
    parquet = pq.ParquetFile(path)
    for batch in parquet.iter_batches():
        columns = batch.to_pydict()
        for values in zip(*(columns[name] for name in SESSION_TRADE_SCHEMA.names), strict=True):
            yield SessionTrade(*values)


def _candle_record(session: TradingSession, candle: Candle) -> dict[str, object]:
    return {
        "session_id": session.session_id,
        "trading_date": session.trading_date,
        "logical_asset": session.logical_asset,
        "physical_contract": session.physical_contract,
        "timeframe": candle.timeframe,
        "open_time_ns_utc": candle.open_time_ns_utc,
        "close_time_ns_utc": candle.close_time_ns_utc,
        "open_units": candle.open_units,
        "high_units": candle.high_units,
        "low_units": candle.low_units,
        "close_units": candle.close_units,
        "volume": candle.volume,
        "trade_count": candle.trade_count,
    }


def _ensure_candles(
    session_cache: CachedArtifact,
    session: TradingSession,
    timeframe: str,
    cache_root: Path,
) -> CachedArtifact:
    if timeframe not in SUPPORTED_TIMEFRAMES:
        raise ContractError(f"unsupported timeframe: {timeframe}")
    identity = {
        "kind": "session-candles",
        "session_id": session.session_id,
        "source_semantic_sha256": session.normalized_semantic_sha256,
        "timeframe": timeframe,
        "interval": "[open_time,close_time)",
        "empty_interval_policy": "DO_NOT_FILL",
        "boundary_timezone": "UTC-03:00",
        "engine_version": TIMEFRAME_ENGINE_VERSION,
        "schema_version": SESSION_CANDLE_SCHEMA_VERSION,
    }
    key = _identity_key(identity)
    directory = cache_root / "candles" / key
    manifest_name = "candle-manifest.json"
    if directory.exists():
        manifest = _validate_cached(directory, manifest_name, key)
        return CachedArtifact(key, directory, manifest, True)

    def build(temporary: Path) -> dict[str, object]:
        destination = temporary / "candles.parquet"
        digest = new_semantic_digest(SESSION_CANDLE_SCHEMA_VERSION)
        trades = (
            trade.as_market_trade()
            for trade in read_session_trades(session_cache.directory / "trades.parquet")
        )

        def records() -> Iterator[dict[str, object]]:
            for candle in build_timeframe_candles(trades, timeframe):
                record = _candle_record(session, candle)
                update_semantic_digest(digest, record.values())
                yield record

        row_count = write_record_batches(destination, SESSION_CANDLE_SCHEMA, records())
        artifact = _artifact(destination, row_count, digest.hexdigest())
        manifest: dict[str, object] = {
            "manifest_file": manifest_name,
            "schema_version": "session-candle-manifest/v1",
            "cache_key": key,
            "identity": identity,
            "artifacts": {"candles": artifact},
        }
        write_canonical_json(temporary / manifest_name, manifest)
        return manifest

    manifest = _publish_cache(directory, build)
    return CachedArtifact(key, directory, manifest, False)


def read_session_candles(path: Path) -> Iterator[SessionCandle]:
    parquet = pq.ParquetFile(path)
    for batch in parquet.iter_batches():
        columns = batch.to_pydict()
        for index in range(batch.num_rows):
            candle = Candle(
                columns["physical_contract"][index],
                columns["timeframe"][index],
                columns["open_time_ns_utc"][index],
                columns["close_time_ns_utc"][index],
                columns["open_units"][index],
                columns["high_units"][index],
                columns["low_units"][index],
                columns["close_units"][index],
                columns["volume"][index],
                columns["trade_count"][index],
            )
            yield SessionCandle(
                columns["session_id"][index],
                columns["trading_date"][index],
                columns["logical_asset"][index],
                columns["physical_contract"][index],
                candle,
            )


def _ensure_features(
    candles: CachedArtifact,
    session: TradingSession,
    timeframe: str,
    period: int,
    cache_root: Path,
) -> CachedArtifact:
    candle_artifact = cast(dict[str, dict[str, object]], candles.manifest["artifacts"])[
        "candles"
    ]
    identity = {
        "kind": "session-features",
        "session_id": session.session_id,
        "candle_semantic_sha256": candle_artifact["semantic_sha256"],
        "timeframe": timeframe,
        "indicator": "sma_close",
        "indicator_version": SMA_CLOSE_VERSION,
        "indicator_engine_version": INDICATOR_ENGINE_VERSION,
        "period": period,
        "reset_policy": "RESET_EACH_TRADING_SESSION",
        "availability_policy": "CANDLE_NOMINAL_CLOSE",
        "schema_version": SESSION_FEATURE_SCHEMA_VERSION,
    }
    key = _identity_key(identity)
    directory = cache_root / "features" / key
    manifest_name = "feature-manifest.json"
    if directory.exists():
        manifest = _validate_cached(directory, manifest_name, key)
        return CachedArtifact(key, directory, manifest, True)

    def build(temporary: Path) -> dict[str, object]:
        destination = temporary / "features.parquet"
        digest = new_semantic_digest(SESSION_FEATURE_SCHEMA_VERSION)
        source_rows = read_session_candles(candles.directory / "candles.parquet")

        def base_candles() -> Iterator[Candle]:
            for row in source_rows:
                yield row.candle

        def records() -> Iterator[dict[str, object]]:
            for feature in calculate_sma_close(base_candles(), period):
                executable = feature.available_at_ns_utc <= session.last_event_ns_utc
                record = _candle_record(session, feature.candle)
                record.update(
                    {
                        "sma_close_sum_units": feature.sma_close_sum_units,
                        "sma_close_period": feature.sma_close_period,
                        "available_at_ns_utc": feature.available_at_ns_utc,
                        "executable_in_session": executable,
                        "non_executable_reason": None
                        if executable
                        else FEATURE_AFTER_SESSION_REASON,
                    }
                )
                update_semantic_digest(digest, record.values())
                yield record

        row_count = write_record_batches(destination, SESSION_FEATURE_SCHEMA, records())
        artifact = _artifact(destination, row_count, digest.hexdigest())
        manifest: dict[str, object] = {
            "manifest_file": manifest_name,
            "schema_version": "session-feature-manifest/v1",
            "cache_key": key,
            "identity": identity,
            "artifacts": {"features": artifact},
        }
        write_canonical_json(temporary / manifest_name, manifest)
        return manifest

    manifest = _publish_cache(directory, build)
    return CachedArtifact(key, directory, manifest, False)


def read_session_features(path: Path) -> Iterator[SessionFeatureRow]:
    parquet = pq.ParquetFile(path)
    for batch in parquet.iter_batches():
        columns = batch.to_pydict()
        for index in range(batch.num_rows):
            candle = Candle(
                columns["physical_contract"][index],
                columns["timeframe"][index],
                columns["open_time_ns_utc"][index],
                columns["close_time_ns_utc"][index],
                columns["open_units"][index],
                columns["high_units"][index],
                columns["low_units"][index],
                columns["close_units"][index],
                columns["volume"][index],
                columns["trade_count"][index],
            )
            feature = FeatureRow(
                candle,
                columns["sma_close_sum_units"][index],
                columns["sma_close_period"][index],
                columns["available_at_ns_utc"][index],
            )
            yield SessionFeatureRow(
                columns["session_id"][index],
                columns["trading_date"][index],
                columns["logical_asset"][index],
                columns["physical_contract"][index],
                feature,
                columns["executable_in_session"][index],
                columns["non_executable_reason"][index],
            )


def _session_from_manifest(manifest: dict[str, object]) -> TradingSession:
    return TradingSession(**cast(dict[str, Any], manifest["session"]))


def build_historical_dataset(
    sources: Sequence[SessionSource],
    *,
    logical_asset: str,
    cache_root: Path,
    timeframes: Sequence[str],
    sma_period: int,
    max_sessions: int = 19,
) -> HistoricalBuild:
    """Build or reuse one immutable sliding historical window."""

    if not sources:
        raise ContractError("at least one historical source is required")
    if max_sessions <= 0:
        raise ContractError("max_sessions must be positive")
    canonical_timeframes = tuple(timeframes)
    if (
        not canonical_timeframes
        or len(set(canonical_timeframes)) != len(canonical_timeframes)
        or any(item not in SUPPORTED_TIMEFRAMES for item in canonical_timeframes)
    ):
        raise ContractError("timeframes must be a unique subset of 1m,2m,5m,15m")
    if sma_period <= 0:
        raise ContractError("SMA period must be positive")
    cache_root = cache_root.resolve()
    cache_root.mkdir(parents=True, exist_ok=True)

    prepared: dict[tuple[str, str], CachedArtifact] = {}
    operations: list[dict[str, object]] = []
    for source_spec in sources:
        imported = _ensure_import(
            source_spec.source, source_spec.physical_contract, cache_root
        )
        session_cache = _ensure_session(
            imported, logical_asset, source_spec.physical_contract, cache_root
        )
        session = _session_from_manifest(session_cache.manifest)
        existing = prepared.get(session.conflict_key)
        if existing is not None:
            existing_session = _session_from_manifest(existing.manifest)
            if existing_session.session_id == session.session_id:
                operations.append(
                    {
                        "layer": "session",
                        "key": session_cache.key,
                        "status": "DEDUPLICATED_IDENTICAL",
                    }
                )
                continue
            raise ContractError(
                "conflicting historical sources for "
                f"{logical_asset}/{session.trading_date}: "
                f"{existing_session.physical_contract} ({existing_session.source_sha256}) vs "
                f"{session.physical_contract} ({session.source_sha256}); "
                "explicit user choice required"
            )
        prepared[session.conflict_key] = session_cache
        operations.extend(
            [
                {
                    "layer": "import",
                    "key": imported.key,
                    "status": "CACHE_HIT" if imported.cache_hit else "BUILT",
                },
                {
                    "layer": "session",
                    "key": session_cache.key,
                    "status": "CACHE_HIT" if session_cache.cache_hit else "BUILT",
                },
            ]
        )

    ordered = sorted(
        prepared.values(),
        key=lambda item: _session_from_manifest(item.manifest).trading_date,
    )
    selected = ordered[-max_sessions:]
    removed = ordered[:-max_sessions]
    materialized: list[MaterializedSession] = []
    for session_cache in selected:
        session = _session_from_manifest(session_cache.manifest)
        candle_caches: dict[str, CachedArtifact] = {}
        feature_caches: dict[str, CachedArtifact] = {}
        for timeframe in canonical_timeframes:
            candle_cache = _ensure_candles(
                session_cache, session, timeframe, cache_root
            )
            feature_cache = _ensure_features(
                candle_cache, session, timeframe, sma_period, cache_root
            )
            candle_caches[timeframe] = candle_cache
            feature_caches[timeframe] = feature_cache
            operations.extend(
                [
                    {
                        "layer": "candles",
                        "session_id": session.session_id,
                        "timeframe": timeframe,
                        "key": candle_cache.key,
                        "status": "CACHE_HIT" if candle_cache.cache_hit else "BUILT",
                    },
                    {
                        "layer": "features",
                        "session_id": session.session_id,
                        "timeframe": timeframe,
                        "key": feature_cache.key,
                        "status": "CACHE_HIT" if feature_cache.cache_hit else "BUILT",
                    },
                ]
            )
        materialized.append(
            MaterializedSession(session, session_cache, candle_caches, feature_caches)
        )

    sessions = tuple(item.session for item in materialized)
    window_identity = {
        "logical_asset": logical_asset,
        "max_sessions": max_sessions,
        "selection_policy": "LATEST_AVAILABLE_TRADING_DATES",
        "session_ids": [session.session_id for session in sessions],
    }
    window_fingerprint = "sha256:" + _identity_key(window_identity)
    dataset_identity = {
        "window_fingerprint": window_fingerprint,
        "timeframes": list(canonical_timeframes),
        "sma_close": {"version": SMA_CLOSE_VERSION, "period": sma_period},
        "session_reset": True,
        "candle_engine_version": TIMEFRAME_ENGINE_VERSION,
        "indicator_engine_version": INDICATOR_ENGINE_VERSION,
        "materializer_version": HISTORICAL_MATERIALIZER_VERSION,
    }
    dataset_id = "sha256:" + _identity_key(dataset_identity)
    dataset = HistoricalDataset(
        dataset_id,
        window_fingerprint,
        logical_asset,
        max_sessions,
        canonical_timeframes,
        sessions,
    )

    session_records: list[dict[str, object]] = []
    for item in materialized:
        session_records.append(
            {
                **asdict(item.session),
                "cache": {
                    "session": item.trades.key,
                    "candles": {key: value.key for key, value in item.candles.items()},
                    "features": {key: value.key for key, value in item.features.items()},
                },
                "artifacts": {
                    "trades": item.trades.manifest["artifacts"]["trades"],
                    "candles": {
                        key: value.manifest["artifacts"]["candles"]
                        for key, value in item.candles.items()
                    },
                    "features": {
                        key: value.manifest["artifacts"]["features"]
                        for key, value in item.features.items()
                    },
                },
            }
        )
    manifest: dict[str, object] = {
        "schema_version": "historical-dataset-manifest/v1",
        "dataset_id": dataset_id,
        "window_fingerprint": window_fingerprint,
        "logical_asset": logical_asset,
        "window": {
            "max_sessions": max_sessions,
            "available_sessions": len(ordered),
            "selected_sessions": len(sessions),
            "selection_policy": "LATEST_AVAILABLE_TRADING_DATES",
        },
        "timeframes": list(canonical_timeframes),
        "indicator": {
            "name": "sma_close",
            "version": SMA_CLOSE_VERSION,
            "period": sma_period,
            "reset_policy": "RESET_EACH_TRADING_SESSION",
        },
        "sessions": session_records,
        "policies": {
            "one_physical_contract_per_logical_asset_trading_date": True,
            "automatic_rollover": False,
            "continuous_price_adjustment": False,
            "empty_candles": "DO_NOT_FILL",
            "partial_last_candle": "KEEP_NOMINAL_BOUNDS",
            "feature_after_last_trade": "MATERIALIZE_NON_EXECUTABLE",
        },
        "versions": {
            "cache": HISTORICAL_CACHE_VERSION,
            "materializer": HISTORICAL_MATERIALIZER_VERSION,
            "parquet": PARQUET_ENGINE_VERSION,
            "pyarrow": PYARROW_VERSION,
        },
    }
    operational_report = {
        "schema_version": "historical-build-report/v1",
        "dataset_id": dataset_id,
        "operations": operations,
        "logically_removed_session_ids": [
            _session_from_manifest(item.manifest).session_id for item in removed
        ],
        "removed_session_cache_preserved": all(item.directory.is_dir() for item in removed),
    }
    return HistoricalBuild(dataset, tuple(materialized), operational_report, manifest)
