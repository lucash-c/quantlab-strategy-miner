"""Opt-in fourth-increment regression over one full B3 DRV ZIP."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for source_root in (
    ROOT / "packages" / "core" / "src",
    ROOT / "packages" / "data" / "src",
    ROOT / "packages" / "backtest" / "src",
    ROOT / "apps" / "cli" / "src",
):
    sys.path.insert(0, str(source_root))

from quantlab_cli.feature_pipeline import run_fourth_increment  # noqa: E402
from quantlab_core.canonical import sha256_file, write_canonical_json  # noqa: E402
from quantlab_core.errors import ContractError  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--contract", default="WINV26")
    parser.add_argument(
        "--strategy",
        type=Path,
        default=ROOT / "examples" / "strategies" / "win-features-validation-v3.json",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    source = args.input.resolve(strict=True)
    output = args.output.resolve()
    if output.exists():
        raise ContractError("acceptance output already exists; refusing to overwrite it")
    output.mkdir(parents=True)
    catalog = output / "historical-sources.json"
    write_canonical_json(
        catalog,
        {
            "schema_version": "historical-sources/v1",
            "logical_asset": "WIN",
            "sessions": [{"source": str(source), "physical_contract": args.contract}],
        },
    )
    cache = output / "cache"
    first = run_fourth_increment(catalog, args.strategy, cache, output / "run-1")
    second = run_fourth_increment(catalog, args.strategy, cache, output / "run-2")
    comparable = (
        "market-dataset-manifest.json",
        "feature-set-manifest.json",
        "feature-registry.json",
        "strategy-definition.json",
        "ledger.jsonl",
        "signal-journal.jsonl",
        "metrics.json",
        "run-manifest.json",
    )
    artifact_hashes: dict[str, str] = {}
    for name in comparable:
        first_hash = sha256_file(output / "run-1" / name)
        second_hash = sha256_file(output / "run-2" / name)
        if first_hash != second_hash:
            raise ContractError(f"feature regression differs between runs: {name}")
        artifact_hashes[name] = first_hash
    market = json.loads((output / "run-1" / "market-dataset-manifest.json").read_text())
    feature_set = json.loads(
        (output / "run-1" / "feature-set-manifest.json").read_text()
    )
    metrics = json.loads((output / "run-1" / "metrics.json").read_text())
    second_report = json.loads((output / "run-2" / "build-report.json").read_text())
    session = market["sessions"][0]
    summary = {
        "schema_version": "fourth-increment-real-regression/v1",
        "source": {
            "file_name": source.name,
            "sha256": sha256_file(source),
            "physical_contract": args.contract,
        },
        "run_id": first["run_id"],
        "second_run_id": second["run_id"],
        "market_dataset_id": first["market_dataset_id"],
        "feature_set_id": first["feature_set_id"],
        "window_fingerprint": first["window_fingerprint"],
        "trading_date": session["trading_date"],
        "event_count": session["event_count"],
        "temporal_range_ns_utc": {
            "first": session["first_event_ns_utc"],
            "last": session["last_event_ns_utc"],
        },
        "candle_counts": {
            timeframe: artifact["row_count"]
            for timeframe, artifact in session["artifacts"]["candles"].items()
        },
        "features": feature_set["feature_specs"],
        "metrics": metrics,
        "second_run_market_cache_statuses": sorted(
            {row["status"] for row in second_report["market"]["operations"]}
        ),
        "second_run_feature_cache_statuses": sorted(
            {row["status"] for row in second_report["features"]["operations"]}
        ),
        "byte_identical_research_artifacts": True,
        "artifact_sha256": artifact_hashes,
    }
    write_canonical_json(output / "acceptance-summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
