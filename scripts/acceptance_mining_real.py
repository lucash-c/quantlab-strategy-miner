"""Opt-in B3 six-candidate clean/warm/interruption benchmark; no downloads."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import pyarrow.parquet as pq
from quantlab_core.canonical import sha256_file, write_canonical_json
from quantlab_data.historical import SessionSource
from quantlab_mining.batch import ControlledInterruption, run_batch
from quantlab_mining.contracts import (
    EvaluationConfigV1,
    GenerationPolicyV1,
    MiningSearchSpaceV1,
    load_contract,
)

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--zip", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True, help="new acceptance directory")
    parser.add_argument(
        "--cache", type=Path, help="optional existing immutable market/feature cache"
    )
    args = parser.parse_args()
    args.work.mkdir(parents=True, exist_ok=False)
    cache = args.cache or args.work / "cache"
    space = load_contract(ROOT / "examples/mining/b3-six.json", MiningSearchSpaceV1)
    policy = load_contract(ROOT / "examples/mining/policy-six.json", GenerationPolicyV1)
    evaluation = load_contract(ROOT / "examples/mining/evaluation.json", EvaluationConfigV1)
    sources = [SessionSource(args.zip.resolve(strict=True), "WINV26")]
    manifests = {}
    wall_times = {}
    for name in ("clean", "warm", "resumed"):
        begin = time.perf_counter()
        kwargs = {
            "cache_root": cache,
            "checkpoint": args.work / (name + ".sqlite"),
            "output": args.work / name,
            "on_progress": lambda record: print(json.dumps(record), flush=True),
        }
        if name == "resumed":
            try:
                run_batch(space, policy, evaluation, sources, stop_after=3, **kwargs)
            except ControlledInterruption:
                print("CONTROLLED_INTERRUPTION: 3 committed candidates", flush=True)
            else:
                raise RuntimeError("expected interruption did not happen")
        manifests[name] = run_batch(space, policy, evaluation, sources, **kwargs)
        wall_times[name] = time.perf_counter() - begin
    hashes = {
        name: {
            p.relative_to(args.work / name).as_posix(): sha256_file(p)
            for p in (args.work / name).rglob("*")
            if p.is_file()
        }
        for name in manifests
    }
    if not manifests["clean"] == manifests["warm"] == manifests["resumed"]:
        raise RuntimeError("scientific manifests differ across clean/warm/resume")
    if not hashes["clean"] == hashes["warm"] == hashes["resumed"]:
        raise RuntimeError("scientific byte fingerprints differ across clean/warm/resume")
    reports = {
        name: json.loads((args.work / (name + ".operational.json")).read_text())
        for name in manifests
    }
    warm = reports["warm"]
    average = sum(float(v) for v in warm["backtest_seconds"]) / warm["executed"]
    benchmark = {
        "schema_version": "mining-real-acceptance/v1",
        "source_file": args.zip.name,
        "source_sha256": sha256_file(args.zip),
        "physical_contract": "WINV26",
        "equivalent_manifests": True,
        "byte_identical_scientific_artifacts": True,
        "manifests": manifests,
        "scientific_artifact_hashes": hashes,
        "operational_reports": reports,
        "wall_seconds": {k: str(v) for k, v in wall_times.items()},
        "cache_mode": "EXISTING_CACHE" if args.cache else "FRESH_CACHE",
        "projections": {
            str(n): {
                "seconds": str(float(warm["materialization_seconds"]) + average * n),
                "basis": "warm materialization + mean tick backtest * N",
                "guarantee": False,
            }
            for n in (100, 1000, 10000)
        },
        "results": pq.read_table(args.work / "clean/results.parquet").to_pylist(),
    }
    write_canonical_json(args.work / "acceptance-report.json", benchmark)
    print(
        json.dumps(
            {
                "run_id": manifests["clean"]["run_id"],
                "report": str(args.work / "acceptance-report.json"),
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
