"""First-increment vertical-slice orchestration."""

from __future__ import annotations

import os
import platform
import shutil
import tempfile
from pathlib import Path

from pydantic.version import VERSION as PYDANTIC_VERSION
from quantlab_backtest.artifacts import LedgerWriter, write_metrics
from quantlab_backtest.engine import BACKTEST_ENGINE_VERSION, run_backtest
from quantlab_backtest.metrics import METRICS_ENGINE_VERSION
from quantlab_core import CORE_VERSION
from quantlab_core.candles import CANDLE_ENGINE_VERSION
from quantlab_core.canonical import (
    canonical_json_bytes,
    sha256_bytes,
    sha256_file,
    write_canonical_json,
)
from quantlab_core.errors import ContractError
from quantlab_core.evaluator import STRATEGY_EVALUATOR_VERSION
from quantlab_core.indicators import INDICATOR_ENGINE_VERSION
from quantlab_core.price import decimal_to_units
from quantlab_core.strategy import STRATEGY_SCHEMA_VERSION, load_strategy
from quantlab_data import (
    DATA_ENGINE_VERSION,
    NORMALIZER_VERSION,
    materialize_one_minute_candles,
    materialize_sma_features,
    normalize_csv,
)
from quantlab_data.parquet import (
    PARQUET_ENGINE_VERSION,
    PYARROW_VERSION,
    iter_features,
    iter_trades,
)


def _run_into(source: Path, strategy_source: Path, destination: Path) -> dict[str, object]:
    strategy = load_strategy(strategy_source)
    dataset_manifest = normalize_csv(source, destination)
    if dataset_manifest["symbol"] != strategy.symbol:
        raise ContractError("dataset symbol does not match strategy symbol")
    price_scale = int(dataset_manifest["price"]["decimal_scale"])
    decimal_to_units(strategy.take_profit.value, price_scale)
    decimal_to_units(strategy.stop_loss.value, price_scale)

    canonical_strategy_path = destination / "strategy-definition.json"
    canonical_strategy_path.write_bytes(strategy.canonical_bytes())
    strategy_artifact = {
        "file": canonical_strategy_path.name,
        "source_file_name": strategy_source.name,
        "source_byte_sha256": sha256_file(strategy_source),
        "byte_sha256": sha256_file(canonical_strategy_path),
        "semantic_sha256": strategy.semantic_sha256(),
        "schema_version": strategy.schema_version,
    }

    candles_path = destination / "candles-1m.parquet"
    candles_artifact = materialize_one_minute_candles(
        destination / "normalized-trades.parquet",
        candles_path,
    )
    features_path = destination / "features-1m.parquet"
    features_artifact = materialize_sma_features(
        candles_path,
        features_path,
        period=strategy.sma_period,
    )

    ledger_path = destination / "ledger.jsonl"
    ledger_writer = LedgerWriter(ledger_path)
    try:
        summary = run_backtest(
            iter_trades(destination / "normalized-trades.parquet"),
            iter_features(features_path),
            strategy,
            price_scale=price_scale,
            on_trade=ledger_writer.write,
        )
    finally:
        ledger_artifact = ledger_writer.close()

    metrics_record = dict(summary.metrics)
    metrics_record["open_position"] = (
        summary.open_position.to_record() if summary.open_position is not None else None
    )
    metrics_artifact = write_metrics(destination / "metrics.json", metrics_record)

    artifacts = {
        "normalized_trades": dataset_manifest["artifacts"]["normalized_trades"],
        "candles": candles_artifact,
        "features": features_artifact,
        "strategy": strategy_artifact,
        "ledger": ledger_artifact,
        "metrics": metrics_artifact,
    }
    engine_versions = {
        "core": CORE_VERSION,
        "data": DATA_ENGINE_VERSION,
        "normalizer": NORMALIZER_VERSION,
        "parquet": PARQUET_ENGINE_VERSION,
        "candle": CANDLE_ENGINE_VERSION,
        "indicator": INDICATOR_ENGINE_VERSION,
        "strategy_schema": STRATEGY_SCHEMA_VERSION,
        "strategy_evaluator": STRATEGY_EVALUATOR_VERSION,
        "backtest": BACKTEST_ENGINE_VERSION,
        "metrics": METRICS_ENGINE_VERSION,
    }
    runtime_versions = {
        "python": platform.python_version(),
        "sqlite": dataset_manifest["runtime_versions"]["sqlite"],
        "pyarrow": PYARROW_VERSION,
        "pydantic": PYDANTIC_VERSION,
    }
    identity = {
        "dataset_id": dataset_manifest["dataset_id"],
        "strategy_semantic_sha256": strategy.semantic_sha256(),
        "artifact_semantic_sha256": {
            name: artifact["semantic_sha256"] for name, artifact in artifacts.items()
        },
        "engine_versions": engine_versions,
        "runtime_versions": runtime_versions,
    }
    run_id = "sha256:" + sha256_bytes(canonical_json_bytes(identity))
    run_manifest: dict[str, object] = {
        "manifest_version": "first-increment-run/v1",
        "run_id": run_id,
        "dataset_id": dataset_manifest["dataset_id"],
        "strategy_id": strategy.strategy_id,
        "strategy_version": strategy.strategy_version,
        "price": dataset_manifest["price"],
        "artifacts": artifacts,
        "engine_versions": engine_versions,
        "runtime_versions": runtime_versions,
        "determinism": {
            "json_encoding": "UTF-8 canonical sorted compact with LF",
            "parquet_byte_hash_recorded": True,
            "semantic_hashes_recorded": True,
            "volatile_fields_excluded": True,
        },
    }
    write_canonical_json(destination / "run-manifest.json", run_manifest)
    return run_manifest


def run_first_increment(
    source: Path,
    strategy_source: Path,
    output_directory: Path,
) -> dict[str, object]:
    """Run atomically, refusing to overwrite any existing output path."""

    source = source.resolve(strict=True)
    strategy_source = strategy_source.resolve(strict=True)
    output_directory = output_directory.resolve()
    if output_directory.exists():
        raise ContractError("output directory already exists; refusing to overwrite it")
    output_directory.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{output_directory.name}.tmp-", dir=output_directory.parent)
    )
    try:
        manifest = _run_into(source, strategy_source, temporary)
        os.replace(temporary, output_directory)
        return manifest
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
