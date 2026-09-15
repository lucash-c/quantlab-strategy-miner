"""Generate durable third-increment acceptance evidence from the 20-session fixture."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for source_root in (
    ROOT / "tests",
    ROOT / "packages" / "core" / "src",
    ROOT / "packages" / "data" / "src",
    ROOT / "packages" / "backtest" / "src",
    ROOT / "apps" / "cli" / "src",
):
    sys.path.insert(0, str(source_root))

from helpers import (  # noqa: E402
    write_historical_catalog,
    write_historical_fixture_sources,
    write_historical_strategy,
)
from quantlab_cli.historical_pipeline import run_third_increment  # noqa: E402
from quantlab_core.canonical import sha256_file, write_canonical_json  # noqa: E402
from quantlab_core.errors import ContractError  # noqa: E402
from quantlab_data.historical import read_session_features  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    return parser


def _load(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _status_counts(report: dict[str, object]) -> dict[str, int]:
    values = Counter(
        f"{row['layer']}:{row['status']}" for row in report["operations"]  # type: ignore[index]
    )
    return {key: values[key] for key in sorted(values)}


def main() -> int:
    args = _parser().parse_args()
    output = args.output.resolve()
    if output.exists():
        raise ContractError("acceptance output already exists; refusing to overwrite it")
    output.mkdir(parents=True)
    sources_root = output / "sources"
    sources_root.mkdir()
    sources = write_historical_fixture_sources(sources_root)
    strategy_2 = output / "strategy-period-2.json"
    strategy_3 = output / "strategy-period-3.json"
    write_historical_strategy(strategy_2, period=2)
    write_historical_strategy(strategy_3, period=3)
    catalog_19 = output / "catalog-19.json"
    catalog_20 = output / "catalog-20.json"
    write_historical_catalog(catalog_19, sources[:19])
    write_historical_catalog(catalog_20, sources)

    incremental_cache = output / "incremental-cache"
    first_19 = run_third_increment(
        catalog_19, strategy_2, incremental_cache, output / "run-19"
    )
    repeated_19 = run_third_increment(
        catalog_19, strategy_2, incremental_cache, output / "run-19-repeat"
    )
    incremental_20 = run_third_increment(
        catalog_20, strategy_2, incremental_cache, output / "run-20-incremental"
    )
    period_3 = run_third_increment(
        catalog_20, strategy_3, incremental_cache, output / "run-20-period-3"
    )
    full_20 = run_third_increment(
        catalog_20, strategy_2, output / "full-cache", output / "run-20-full"
    )
    if first_19["run_id"] != repeated_19["run_id"]:
        raise ContractError("repeated 19-session run changed its deterministic run_id")

    first_dataset = _load(output / "run-19" / "historical-dataset-manifest.json")
    dataset = _load(output / "run-20-incremental" / "historical-dataset-manifest.json")
    metrics = _load(output / "run-20-incremental" / "metrics.json")
    repeated_report = _load(output / "run-19-repeat" / "build-report.json")
    incremental_report = _load(output / "run-20-incremental" / "build-report.json")
    invalidation_report = _load(output / "run-20-period-3" / "build-report.json")

    comparable = (
        "historical-dataset-manifest.json",
        "strategy-definition.json",
        "ledger.jsonl",
        "discarded-signals.jsonl",
        "metrics.json",
        "run-manifest.json",
    )
    equivalence_hashes: dict[str, str] = {}
    for name in comparable:
        incremental_hash = sha256_file(output / "run-20-incremental" / name)
        full_hash = sha256_file(output / "run-20-full" / name)
        if incremental_hash != full_hash:
            raise ContractError(f"full and incremental artifacts differ: {name}")
        equivalence_hashes[name] = incremental_hash

    candle_counts: Counter[str] = Counter()
    warmup_reset_rows = 0
    non_executable_features = 0
    for session in dataset["sessions"]:  # type: ignore[index]
        for timeframe, artifact in session["artifacts"]["candles"].items():
            candle_counts[timeframe] += artifact["row_count"]
        for _timeframe, feature_key in session["cache"]["features"].items():
            rows = list(
                read_session_features(
                    incremental_cache / "features" / feature_key / "features.parquet"
                )
            )
            if rows and rows[0].feature.sma_close_sum_units is None:
                warmup_reset_rows += 1
            non_executable_features += sum(not row.executable_in_session for row in rows)

    ledger = [
        json.loads(line)
        for line in (output / "run-20-incremental" / "ledger.jsonl").read_text().splitlines()
    ]
    removed_session_id = first_dataset["sessions"][0]["session_id"]  # type: ignore[index]
    summary = {
        "schema_version": "third-increment-fixture-acceptance/v1",
        "test_sessions_available": 20,
        "default_window_size": 19,
        "first_window_dates": first_19["trading_dates"],
        "incremental_window_dates": incremental_20["trading_dates"],
        "first_dataset_id": first_19["dataset_id"],
        "first_run_id": first_19["run_id"],
        "first_window_fingerprint": first_19["window_fingerprint"],
        "repeated_run_id": repeated_19["run_id"],
        "incremental_dataset_id": incremental_20["dataset_id"],
        "incremental_window_fingerprint": incremental_20["window_fingerprint"],
        "full_dataset_id": full_20["dataset_id"],
        "full_run_id": full_20["run_id"],
        "incremental_run_id": incremental_20["run_id"],
        "period_3_dataset_id": period_3["dataset_id"],
        "common_price_scale": incremental_20["common_price"]["decimal_scale"],
        "physical_contracts": incremental_20["physical_contracts"],
        "candle_counts": {key: candle_counts[key] for key in sorted(candle_counts)},
        "indicator_reset_first_rows": warmup_reset_rows,
        "non_executable_features": non_executable_features,
        "ledger_rows": len(ledger),
        "session_end_rows": sum(row["exit_reason"] == "SESSION_END" for row in ledger),
        "overnight_rows": sum(
            row["trading_date"] not in row["entry_timestamp_utc"]
            or row["trading_date"] not in row["exit_timestamp_utc"]
            for row in ledger
        ),
        "metrics": metrics,
        "repeat_cache_statuses": _status_counts(repeated_report),
        "incremental_cache_statuses": _status_counts(incremental_report),
        "selective_invalidation_statuses": _status_counts(invalidation_report),
        "logically_removed_session_id": removed_session_id,
        "removed_session_cache_preserved": (
            incremental_cache
            / "sessions"
            / removed_session_id.removeprefix("sha256:")
        ).is_dir(),
        "full_incremental_byte_equivalent": True,
        "equivalent_artifact_sha256": equivalence_hashes,
    }
    write_canonical_json(output / "acceptance-summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
