"""Third-increment historical B3 orchestration."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from quantlab_backtest.historical_artifacts import CanonicalJsonlWriter
from quantlab_backtest.historical_engine import (
    HISTORICAL_BACKTEST_ENGINE_VERSION,
    HistoricalSessionInput,
    run_historical_backtest,
)
from quantlab_core.canonical import (
    canonical_json_bytes,
    sha256_bytes,
    sha256_file,
    write_canonical_json,
)
from quantlab_core.errors import ContractError
from quantlab_core.historical_strategy import load_historical_strategy
from quantlab_core.price import common_decimal_scale
from quantlab_data.historical import (
    SessionSource,
    build_historical_dataset,
    read_session_features,
    read_session_trades,
)

HISTORICAL_PIPELINE_VERSION = "1.0.0"
HISTORICAL_SOURCE_CATALOG_VERSION = "historical-sources/v1"
DEFAULT_TIMEFRAMES = ("1m", "2m", "5m", "15m")


@dataclass(frozen=True, slots=True)
class HistoricalSourceCatalog:
    logical_asset: str
    sessions: tuple[SessionSource, ...]


def _reject_json_number(value: str) -> None:
    raise ContractError(f"floating-point number is forbidden in source catalog: {value}")


def load_historical_source_catalog(path: Path) -> HistoricalSourceCatalog:
    path = path.resolve(strict=True)
    try:
        raw = json.loads(
            path.read_text(encoding="utf-8"),
            parse_float=_reject_json_number,
            parse_constant=_reject_json_number,
        )
    except ContractError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"invalid historical source catalog: {exc}") from exc
    if not isinstance(raw, dict) or set(raw) != {"schema_version", "logical_asset", "sessions"}:
        raise ContractError("historical source catalog has unexpected fields")
    if raw["schema_version"] != HISTORICAL_SOURCE_CATALOG_VERSION:
        raise ContractError("unsupported historical source catalog version")
    logical_asset = raw["logical_asset"]
    if (
        not isinstance(logical_asset, str)
        or not logical_asset
        or logical_asset.strip() != logical_asset
    ):
        raise ContractError("catalog logical_asset must be a non-empty string")
    rows = raw["sessions"]
    if not isinstance(rows, list) or not rows:
        raise ContractError("catalog sessions must be a non-empty array")
    sessions: list[SessionSource] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or set(row) != {"source", "physical_contract"}:
            raise ContractError(f"catalog session {index} has unexpected fields")
        source = row["source"]
        contract = row["physical_contract"]
        if not isinstance(source, str) or not source:
            raise ContractError(f"catalog session {index} source must be a non-empty string")
        if not isinstance(contract, str) or not contract or contract.strip() != contract:
            raise ContractError(
                f"catalog session {index} physical_contract must be a non-empty string"
            )
        source_path = Path(source)
        if not source_path.is_absolute():
            source_path = path.parent / source_path
        sessions.append(SessionSource(source_path.resolve(strict=True), contract))
    return HistoricalSourceCatalog(logical_asset, tuple(sessions))


def _artifact(path: Path, schema_version: str, *, rows: int = 1) -> dict[str, object]:
    digest = sha256_file(path)
    return {
        "file": path.name,
        "row_count": rows,
        "byte_sha256": digest,
        "semantic_sha256": digest,
        "schema_version": schema_version,
    }


def _run_into(
    catalog: HistoricalSourceCatalog,
    strategy_source: Path,
    cache_root: Path,
    destination: Path,
    *,
    max_sessions: int,
    timeframes: tuple[str, ...],
) -> dict[str, object]:
    strategy = load_historical_strategy(strategy_source)
    if strategy.logical_asset != catalog.logical_asset:
        raise ContractError("catalog logical_asset does not match strategy")
    if strategy.timeframe not in timeframes:
        raise ContractError("strategy timeframe must be materialized by this historical run")

    build = build_historical_dataset(
        catalog.sessions,
        logical_asset=catalog.logical_asset,
        cache_root=cache_root,
        timeframes=timeframes,
        sma_period=strategy.sma_period,
        max_sessions=max_sessions,
    )
    common_scale = common_decimal_scale(
        [item.session.price_scale for item in build.sessions],
        [strategy.take_profit.value, strategy.stop_loss.value],
    )

    strategy_path = destination / "strategy-definition.json"
    strategy_path.write_bytes(strategy.canonical_bytes())
    historical_manifest_path = destination / "historical-dataset-manifest.json"
    write_canonical_json(historical_manifest_path, build.manifest)

    ledger_writer = CanonicalJsonlWriter(destination / "ledger.jsonl", "trade-ledger/v2")
    discard_writer = CanonicalJsonlWriter(
        destination / "discarded-signals.jsonl", "discarded-signals/v1"
    )
    try:
        inputs = (
            HistoricalSessionInput(
                item.session,
                read_session_trades(item.trades.directory / "trades.parquet"),
                read_session_features(
                    item.features[strategy.timeframe].directory / "features.parquet"
                ),
            )
            for item in build.sessions
        )
        summary = run_historical_backtest(
            inputs,
            strategy,
            common_price_scale=common_scale,
            on_trade=ledger_writer.write,
            on_discard=discard_writer.write,
        )
    finally:
        ledger_artifact = ledger_writer.close()
        discard_artifact = discard_writer.close()

    metrics_path = destination / "metrics.json"
    write_canonical_json(metrics_path, summary.metrics)
    metrics_artifact = _artifact(metrics_path, "backtest-metrics/v2")
    strategy_artifact = _artifact(strategy_path, strategy.schema_version)
    historical_manifest_artifact = _artifact(
        historical_manifest_path, cast(str, build.manifest["schema_version"])
    )
    artifacts = {
        "historical_dataset_manifest": historical_manifest_artifact,
        "strategy": strategy_artifact,
        "ledger": ledger_artifact,
        "discarded_signals": discard_artifact,
        "metrics": metrics_artifact,
    }
    identity = {
        "dataset_id": build.dataset.dataset_id,
        "strategy_semantic_sha256": strategy.semantic_sha256(),
        "common_price_scale": common_scale,
        "artifact_semantic_sha256": {
            name: artifact["semantic_sha256"] for name, artifact in artifacts.items()
        },
        "pipeline_version": HISTORICAL_PIPELINE_VERSION,
        "backtest_engine_version": HISTORICAL_BACKTEST_ENGINE_VERSION,
    }
    run_id = "sha256:" + sha256_bytes(canonical_json_bytes(identity))
    run_manifest: dict[str, object] = {
        "manifest_version": "third-increment-run/v1",
        "run_id": run_id,
        "dataset_id": build.dataset.dataset_id,
        "window_fingerprint": build.dataset.window_fingerprint,
        "strategy_id": strategy.strategy_id,
        "strategy_version": strategy.strategy_version,
        "logical_asset": catalog.logical_asset,
        "physical_contracts": sorted(
            {item.session.physical_contract for item in build.sessions}
        ),
        "trading_dates": [item.session.trading_date for item in build.sessions],
        "timeframes": list(timeframes),
        "backtest_timeframe": strategy.timeframe,
        "common_price": {
            "representation": "scaled_integer",
            "decimal_scale": common_scale,
            "conversion": "EXACT_MULTIPLICATION_ONLY",
        },
        "artifacts": artifacts,
        "versions": {
            "historical_pipeline": HISTORICAL_PIPELINE_VERSION,
            "historical_backtest": HISTORICAL_BACKTEST_ENGINE_VERSION,
        },
        "determinism": {
            "content_addressed_dataset": True,
            "volatile_fields_excluded": True,
            "operational_cache_report_excluded_from_run_identity": True,
        },
    }
    write_canonical_json(destination / "run-manifest.json", run_manifest)
    write_canonical_json(destination / "build-report.json", build.operational_report)
    return run_manifest


def run_third_increment(
    catalog_source: Path,
    strategy_source: Path,
    cache_root: Path,
    output_directory: Path,
    *,
    max_sessions: int = 19,
    timeframes: tuple[str, ...] = DEFAULT_TIMEFRAMES,
) -> dict[str, object]:
    """Run the historical slice atomically without overwriting output artifacts."""

    catalog = load_historical_source_catalog(catalog_source)
    strategy_source = strategy_source.resolve(strict=True)
    cache_root = cache_root.resolve()
    output_directory = output_directory.resolve()
    if output_directory.exists():
        raise ContractError("output directory already exists; refusing to overwrite it")
    output_directory.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{output_directory.name}.tmp-", dir=output_directory.parent)
    )
    try:
        manifest = _run_into(
            catalog,
            strategy_source,
            cache_root,
            temporary,
            max_sessions=max_sessions,
            timeframes=timeframes,
        )
        os.replace(temporary, output_directory)
        return manifest
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
