"""Run the opt-in B3 full-file acceptance twice and prove determinism."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parents[1]
for source_directory in (
    ROOT / "packages" / "core" / "src",
    ROOT / "packages" / "data" / "src",
    ROOT / "packages" / "backtest" / "src",
    ROOT / "apps" / "cli" / "src",
):
    sys.path.insert(0, str(source_directory))

from quantlab_cli.b3_pipeline import run_b3_second_increment  # noqa: E402
from quantlab_core.canonical import (  # noqa: E402
    canonical_json_bytes,
    sha256_bytes,
    sha256_file,
    write_canonical_json,
)
from quantlab_core.errors import QuantLabError  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="B3 DRV ZIP")
    parser.add_argument(
        "--strategy",
        type=Path,
        default=ROOT / "examples" / "strategies" / "winv26-validation-sma-v1.json",
        help="WINV26 manual validation strategy",
    )
    parser.add_argument("--output", type=Path, required=True, help="New acceptance directory")
    parser.add_argument("--contract", default="WINV26", help="Exact selected contract")
    return parser


def _read_json(path: Path) -> dict[str, object]:
    return cast(dict[str, object], json.loads(path.read_text(encoding="utf-8")))


def _file_inventory(root: Path) -> list[Path]:
    return sorted(path.relative_to(root) for path in root.rglob("*") if path.is_file())


def _compare_runs(first: Path, second: Path) -> dict[str, dict[str, object]]:
    first_files = _file_inventory(first)
    second_files = _file_inventory(second)
    if first_files != second_files:
        raise RuntimeError("acceptance runs produced different artifact inventories")

    fingerprints: dict[str, dict[str, object]] = {}
    for relative in first_files:
        first_path = first / relative
        second_path = second / relative
        first_size = first_path.stat().st_size
        second_size = second_path.stat().st_size
        first_hash = sha256_file(first_path)
        second_hash = sha256_file(second_path)
        if first_size != second_size or first_hash != second_hash:
            raise RuntimeError(f"artifact is not byte deterministic: {relative.as_posix()}")
        fingerprints[relative.as_posix()] = {
            "size_bytes": first_size,
            "sha256": first_hash,
            "byte_identical": True,
        }
    return fingerprints


def run_acceptance(
    source: Path,
    strategy: Path,
    output: Path,
    contract: str,
) -> dict[str, object]:
    source = source.resolve(strict=True)
    strategy = strategy.resolve(strict=True)
    output = output.resolve()
    if output.exists():
        raise RuntimeError("acceptance output already exists; refusing to overwrite it")
    output.mkdir(parents=True)

    first_directory = output / "run-a"
    second_directory = output / "run-b"
    first = run_b3_second_increment(source, contract, strategy, first_directory)
    second = run_b3_second_increment(source, contract, strategy, second_directory)
    if first["run_id"] != second["run_id"]:
        raise RuntimeError("second-increment run IDs differ")
    if first["adapter_import_id"] != second["adapter_import_id"]:
        raise RuntimeError("adapter import IDs differ")
    if first["first_increment_run_id"] != second["first_increment_run_id"]:
        raise RuntimeError("first-increment run IDs differ")

    fingerprints = _compare_runs(first_directory, second_directory)
    import_report = _read_json(first_directory / "adapter" / "b3-import-report.json")
    pipeline_manifest = _read_json(first_directory / "pipeline" / "run-manifest.json")
    metrics = _read_json(first_directory / "pipeline" / "metrics.json")
    selection = cast(dict[str, object], import_report["selection"])
    input_record = cast(dict[str, object], import_report["input"])
    pipeline_artifacts = cast(dict[str, dict[str, object]], pipeline_manifest["artifacts"])

    identity = {
        "source_sha256": cast(dict[str, object], import_report["source"])["sha256"],
        "run_id": first["run_id"],
        "artifact_sha256": {
            name: record["sha256"] for name, record in fingerprints.items()
        },
    }
    result: dict[str, object] = {
        "schema_version": "b3-full-acceptance/v1",
        "acceptance_id": "sha256:" + sha256_bytes(canonical_json_bytes(identity)),
        "status": "PASSED",
        "source": import_report["source"],
        "contract": contract,
        "input_counts": {
            "lines_read": input_record["lines_read"],
            "valid_rows": input_record["valid_rows"],
            "rejected_rows": input_record["rejected_rows"],
            "action_counts": input_record["action_counts"],
            "instrument_count": len(cast(list[object], input_record["instruments_found"])),
            "non_positive_price_rows": input_record["non_positive_price_rows"],
            "non_positive_price_instruments": input_record[
                "non_positive_price_instruments"
            ],
        },
        "selection": selection,
        "candles": pipeline_artifacts["candles"],
        "features": pipeline_artifacts["features"],
        "backtest_metrics": metrics,
        "run_fingerprints": {
            "adapter_import_id": first["adapter_import_id"],
            "first_increment_run_id": first["first_increment_run_id"],
            "second_increment_run_id": first["run_id"],
        },
        "artifact_fingerprints": fingerprints,
        "determinism": {
            "runs_compared": 2,
            "artifact_inventories_equal": True,
            "all_artifacts_byte_identical": True,
        },
        "observed_source_variations": [
            "The received official file uses the _DRV suffix.",
            "TipoDoCanal is preserved literally as an opaque audit-only field.",
            "Signed prices outside the selected contract are counted but not canonicalized.",
        ],
    }
    write_canonical_json(output / "acceptance-summary.json", result)
    return result


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = run_acceptance(args.input, args.strategy, args.output, args.contract)
    except (OSError, QuantLabError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
