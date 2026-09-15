"""Opt-in one-session third-increment regression over the full B3 ZIP."""

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

from quantlab_cli.historical_pipeline import run_third_increment  # noqa: E402
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
        default=ROOT / "examples" / "strategies" / "win-historical-sma-v2.json",
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
            "sessions": [
                {"source": str(source), "physical_contract": args.contract}
            ],
        },
    )
    cache = output / "cache"
    first = run_third_increment(
        catalog,
        args.strategy,
        cache,
        output / "run-1",
    )
    second = run_third_increment(
        catalog,
        args.strategy,
        cache,
        output / "run-2",
    )
    comparable = (
        "historical-dataset-manifest.json",
        "strategy-definition.json",
        "ledger.jsonl",
        "discarded-signals.jsonl",
        "metrics.json",
        "run-manifest.json",
    )
    artifact_hashes: dict[str, str] = {}
    for name in comparable:
        first_hash = sha256_file(output / "run-1" / name)
        second_hash = sha256_file(output / "run-2" / name)
        if first_hash != second_hash:
            raise ContractError(f"historical regression differs between runs: {name}")
        artifact_hashes[name] = first_hash
    dataset = json.loads(
        (output / "run-1" / "historical-dataset-manifest.json").read_text()
    )
    metrics = json.loads((output / "run-1" / "metrics.json").read_text())
    second_report = json.loads((output / "run-2" / "build-report.json").read_text())
    session = dataset["sessions"][0]
    summary = {
        "schema_version": "third-increment-real-regression/v1",
        "source": {
            "file_name": source.name,
            "sha256": sha256_file(source),
            "physical_contract": args.contract,
        },
        "run_id": first["run_id"],
        "second_run_id": second["run_id"],
        "dataset_id": first["dataset_id"],
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
        "metrics": metrics,
        "second_run_cache_statuses": sorted(
            {operation["status"] for operation in second_report["operations"]}
        ),
        "byte_identical_research_artifacts": True,
        "artifact_sha256": artifact_hashes,
    }
    write_canonical_json(output / "acceptance-summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
