"""Fourth-increment orchestration for formal features and Strategy Definition v3."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import cast

from quantlab_backtest.engine_v3 import (
    BACKTEST_ENGINE_V3_VERSION,
    FeatureSessionInputV3,
    run_backtest_v3,
)
from quantlab_backtest.historical_artifacts import CanonicalJsonlWriter
from quantlab_backtest.metrics_v3 import METRICS_ENGINE_V3_VERSION
from quantlab_core.canonical import (
    canonical_json_bytes,
    sha256_bytes,
    sha256_file,
    write_canonical_json,
)
from quantlab_core.condition_engine_v2 import CONDITION_ENGINE_V2_VERSION
from quantlab_core.errors import ContractError
from quantlab_core.feature_engine_v2 import FEATURE_ENGINE_V2_VERSION, mathematical_policy_record
from quantlab_core.feature_specs import feature_registry_record
from quantlab_core.price import common_decimal_scale
from quantlab_core.strategy_v3 import load_strategy_v3
from quantlab_data.feature_cache_v2 import build_feature_set, read_feature_observations
from quantlab_data.historical import (
    build_market_history,
    read_session_candles,
    read_session_trades,
)

from quantlab_cli.historical_pipeline import (
    DEFAULT_TIMEFRAMES,
    HistoricalSourceCatalog,
    load_historical_source_catalog,
)

FEATURE_PIPELINE_VERSION = "1.0.0"


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
    strategy = load_strategy_v3(strategy_source)
    if strategy.logical_asset != catalog.logical_asset:
        raise ContractError("catalog logical_asset does not match strategy")
    if strategy.timeframe not in timeframes:
        raise ContractError("strategy timeframe must be materialized by this feature run")

    market = build_market_history(
        catalog.sessions,
        logical_asset=catalog.logical_asset,
        cache_root=cache_root,
        timeframes=timeframes,
        max_sessions=max_sessions,
    )
    feature_set = build_feature_set(
        market,
        strategy.feature_specs,
        timeframe=strategy.timeframe,
        cache_root=cache_root,
    )
    common_scale = common_decimal_scale(
        [item.session.price_scale for item in market.sessions],
        strategy.decimal_inputs(),
    )

    strategy_path = destination / "strategy-definition.json"
    strategy_path.write_bytes(strategy.canonical_bytes())
    market_path = destination / "market-dataset-manifest.json"
    write_canonical_json(market_path, market.manifest)
    feature_set_path = destination / "feature-set-manifest.json"
    write_canonical_json(feature_set_path, feature_set.manifest)
    registry_path = destination / "feature-registry.json"
    write_canonical_json(
        registry_path,
        {
            **feature_registry_record(),
            "feature_engine_version": FEATURE_ENGINE_V2_VERSION,
            "mathematical_policy": mathematical_policy_record(),
        },
    )

    ledger_writer = CanonicalJsonlWriter(destination / "ledger.jsonl", "trade-ledger/v3")
    journal_writer = CanonicalJsonlWriter(
        destination / "signal-journal.jsonl", "signal-journal/v3"
    )
    try:
        inputs = (
            FeatureSessionInputV3(
                session_features.market.session,
                read_session_trades(
                    session_features.market.trades.directory / "trades.parquet"
                ),
                read_session_candles(
                    session_features.market.candles[strategy.timeframe].directory
                    / "candles.parquet"
                ),
                {
                    feature_id: read_feature_observations(
                        cache.directory / "feature-values.parquet"
                    )
                    for feature_id, cache in session_features.features.items()
                },
            )
            for session_features in feature_set.sessions
        )
        summary = run_backtest_v3(
            inputs,
            strategy,
            common_price_scale=common_scale,
            on_trade=ledger_writer.write,
            on_signal=journal_writer.write,
        )
    finally:
        ledger_artifact = ledger_writer.close()
        journal_artifact = journal_writer.close()

    metrics_path = destination / "metrics.json"
    write_canonical_json(metrics_path, summary.metrics)
    artifacts = {
        "market_dataset_manifest": _artifact(
            market_path, cast(str, market.manifest["schema_version"])
        ),
        "feature_set_manifest": _artifact(
            feature_set_path, cast(str, feature_set.manifest["schema_version"])
        ),
        "feature_registry": _artifact(registry_path, "feature-registry/v1"),
        "strategy": _artifact(strategy_path, strategy.schema_version),
        "ledger": ledger_artifact,
        "signal_journal": journal_artifact,
        "metrics": _artifact(metrics_path, "backtest-metrics/v3"),
    }
    identity = {
        "market_dataset_id": market.dataset.dataset_id,
        "feature_set_id": feature_set.feature_set_id,
        "strategy_semantic_sha256": strategy.semantic_sha256(),
        "common_price_scale": common_scale,
        "artifact_semantic_sha256": {
            name: artifact["semantic_sha256"] for name, artifact in artifacts.items()
        },
        "pipeline_version": FEATURE_PIPELINE_VERSION,
        "backtest_engine_version": BACKTEST_ENGINE_V3_VERSION,
    }
    run_id = "sha256:" + sha256_bytes(canonical_json_bytes(identity))
    run_manifest: dict[str, object] = {
        "manifest_version": "fourth-increment-run/v1",
        "run_id": run_id,
        "market_dataset_id": market.dataset.dataset_id,
        "window_fingerprint": market.dataset.window_fingerprint,
        "feature_set_id": feature_set.feature_set_id,
        "strategy_id": strategy.strategy_id,
        "strategy_version": strategy.strategy_version,
        "strategy_schema_version": strategy.schema_version,
        "logical_asset": catalog.logical_asset,
        "physical_contracts": sorted(
            {item.session.physical_contract for item in market.sessions}
        ),
        "trading_dates": [item.session.trading_date for item in market.sessions],
        "timeframes": list(timeframes),
        "backtest_timeframe": strategy.timeframe,
        "features": [spec.to_record() for spec in strategy.feature_specs],
        "cost_model": strategy.cost_model.model_dump(mode="json"),
        "slippage_model": strategy.slippage_model.model_dump(mode="json"),
        "common_price": {
            "representation": "scaled_integer",
            "decimal_scale": common_scale,
            "conversion": "EXACT_MULTIPLICATION_ONLY",
        },
        "artifacts": artifacts,
        "versions": {
            "feature_pipeline": FEATURE_PIPELINE_VERSION,
            "feature_engine": FEATURE_ENGINE_V2_VERSION,
            "condition_engine": CONDITION_ENGINE_V2_VERSION,
            "backtest": BACKTEST_ENGINE_V3_VERSION,
            "metrics": METRICS_ENGINE_V3_VERSION,
        },
        "mathematical_policy": mathematical_policy_record(),
        "determinism": {
            "content_addressed_market_and_features": True,
            "volatile_fields_excluded": True,
            "operational_cache_report_excluded_from_run_identity": True,
        },
    }
    write_canonical_json(destination / "run-manifest.json", run_manifest)
    write_canonical_json(
        destination / "build-report.json",
        {
            "schema_version": "fourth-increment-build-report/v1",
            "market": market.operational_report,
            "features": feature_set.operational_report,
        },
    )
    return run_manifest


def run_fourth_increment(
    catalog_source: Path,
    strategy_source: Path,
    cache_root: Path,
    output_directory: Path,
    *,
    max_sessions: int = 19,
    timeframes: tuple[str, ...] = DEFAULT_TIMEFRAMES,
) -> dict[str, object]:
    """Run the fourth increment atomically without overwriting artifacts."""

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
