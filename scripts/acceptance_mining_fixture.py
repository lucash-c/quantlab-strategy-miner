"""Local persistent acceptance of 160 candidates and 20 fixture sessions."""

from __future__ import annotations

import argparse
import json
import sys
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
sys.path.insert(0, str(ROOT / "tests"))
from helpers import write_historical_fixture_sources  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args()
    args.work.mkdir(parents=True, exist_ok=False)
    source_dir = args.work / "sources"
    source_dir.mkdir()
    sources = [
        SessionSource(Path(row["source"]), row["physical_contract"])
        for row in write_historical_fixture_sources(source_dir)
    ]
    space = load_contract(ROOT / "examples/mining/fixture-160.json", MiningSearchSpaceV1)
    policy = load_contract(ROOT / "examples/mining/policy-160.json", GenerationPolicyV1)
    evaluation = load_contract(ROOT / "examples/mining/evaluation.json", EvaluationConfigV1)
    manifests = {}
    for name in ("clean", "warm", "resumed"):
        kwargs = {
            "cache_root": args.work / "cache",
            "checkpoint": args.work / (name + ".sqlite"),
            "output": args.work / name,
        }
        if name == "resumed":
            try:
                run_batch(space, policy, evaluation, sources, stop_after=80, **kwargs)
            except ControlledInterruption:
                pass
            else:
                raise RuntimeError("expected checkpoint interruption")
        manifests[name] = run_batch(space, policy, evaluation, sources, **kwargs)
        print(name + ": " + manifests[name]["run_id"], flush=True)
    hashes = {
        name: {
            p.relative_to(args.work / name).as_posix(): sha256_file(p)
            for p in (args.work / name).rglob("*")
            if p.is_file()
        }
        for name in manifests
    }
    assert manifests["clean"] == manifests["warm"] == manifests["resumed"]
    assert hashes["clean"] == hashes["warm"] == hashes["resumed"]
    rows = pq.read_table(args.work / "clean/results.parquet").to_pylist()
    report = {
        "schema_version": "mining-fixture-acceptance/v1",
        "byte_identical": True,
        "manifest": manifests["clean"],
        "hashes": hashes["clean"],
        "candidate_ids": [row["candidate_id"] for row in rows],
        "zero_trade_candidates": sum(
            json.loads(row["metrics_json"])["trades"] == 0 for row in rows
        ),
        "total_trades": sum(json.loads(row["metrics_json"])["trades"] for row in rows),
        "session_count": 19,
        "provided_sessions": 20,
        "reports": {
            name: json.loads((args.work / (name + ".operational.json")).read_text())
            for name in manifests
        },
    }
    write_canonical_json(args.work / "acceptance-report.json", report)


if __name__ == "__main__":
    main()
