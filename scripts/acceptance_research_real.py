"""Opt-in ONE-SESSION B3 regression, never an OOS claim; compare to increment 5."""

# ruff: noqa: E402 -- source-workspace bootstrap, like the acceptance entry points.

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for package in ("core", "data", "backtest", "mining", "research"):
    sys.path.insert(0, str(ROOT / "packages" / package / "src"))

import pyarrow.parquet as pq
from quantlab_backtest.partition_metrics import aggregate_partition
from quantlab_core.canonical import sha256_file, write_canonical_json
from quantlab_data.feature_cache_v2 import build_feature_set
from quantlab_data.historical import SessionSource, build_market_history
from quantlab_mining.batch import _ordered_specs, directory_bytes
from quantlab_mining.contracts import (
    EvaluationConfigV1,
    GenerationPolicyV1,
    MiningSearchSpaceV1,
    load_contract,
)
from quantlab_mining.generator import preflight
from quantlab_research.session_cache import SessionEvaluationCache
from quantlab_research.split import partition


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument(
        "--session-cache",
        type=Path,
        help="Optional fresh session-evaluation cache; preserve the existing market cache",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("acceptance output already exists")
    args.output.mkdir(parents=True)
    source_hash = sha256_file(args.input)
    started = time.perf_counter()
    market = build_market_history(
        [SessionSource(args.input, "WINV26")],
        logical_asset="WIN",
        cache_root=args.cache,
        timeframes=("1m",),
        max_sessions=1,
    )
    space = load_contract(ROOT / "examples/mining/b3-six.json", MiningSearchSpaceV1)
    policy = load_contract(ROOT / "examples/mining/policy-six.json", GenerationPolicyV1)
    evaluation = load_contract(
        ROOT / "examples/mining/evaluation.json", EvaluationConfigV1
    ).canonicalized()
    plan = preflight(space, policy, args.output / "plan.sqlite")
    specs = {}
    for _, strategy in plan.candidates():
        specs.update({s.feature_id: s for s in strategy.feature_specs})
    features = build_feature_set(
        market, _ordered_specs(specs), timeframe="1m", cache_root=args.cache
    )
    view = partition(market.dataset.sessions, "WIN")
    baseline = {
        r["candidate_id"]: json.loads(r["metrics_json"])
        for r in pq.read_table(args.baseline / "results.parquet").to_pylist()
    }
    baseline_audit = {}
    for kind, prefix in (("ledger", "ledger"), ("journal", "signal-journal")):
        for path in sorted((args.baseline / "audit").glob(prefix + "-*.jsonl")):
            for line in path.read_text().splitlines():
                row = json.loads(line)
                baseline_audit.setdefault((row["candidate_id"], kind), []).append(row["record"])
    reports, files = {}, []
    session_cache_root = args.session_cache or args.cache
    cache = SessionEvaluationCache(session_cache_root)
    try:
        for name in ("clean", "warm"):
            began = time.perf_counter()
            scientific = []
            built = reused = 0
            for cid, base in plan.candidates():
                strategy = base.model_copy(
                    update={
                        "cost_model": evaluation.cost_model,
                        "slippage_model": evaluation.slippage_model,
                    }
                )
                record, hit = cache.evaluate(cid, features.sessions[0], strategy, lambda kind: None)
                built += not hit
                reused += hit
                cache.publish_pending(record["session"]["session_id"])
                record = cache.load(record["evaluation_id"])
                ledger, journal = [], []
                result = aggregate_partition(
                    cid,
                    view["partition_id"],
                    [
                        (
                            record,
                            cache.audit(record["evaluation_id"], "ledger"),
                            cache.audit(record["evaluation_id"], "journal"),
                        )
                    ],
                    strategy,
                    record["price_scale"],
                    ledger.append,
                    journal.append,
                )
                assert result["backtest_metrics"] == baseline[cid], cid
                assert ledger == baseline_audit.get((cid, "ledger"), []), cid
                assert journal == baseline_audit.get((cid, "journal"), []), cid
                scientific.append(
                    {
                        "candidate_id": cid,
                        "session_evaluation": record,
                        "partition_evaluation": result,
                        "ledger": ledger,
                        "journal": journal,
                    }
                )
                print(
                    json.dumps(
                        {
                            "path": name,
                            "candidate_id": cid,
                            "reused": hit,
                            "trades": result["backtest_metrics"]["trades"],
                        }
                    ),
                    flush=True,
                )
            path = args.output / (name + ".json")
            write_canonical_json(
                path,
                {
                    "source_sha256": source_hash,
                    "oos": False,
                    "candidate_set_id": plan.manifest["candidate_set_id"],
                    "results": scientific,
                },
            )
            files.append(path)
            reports[name] = {
                "seconds": str(time.perf_counter() - began),
                "candidate_session_evaluations_built": built,
                "candidate_session_evaluations_reused": reused,
                "scientific_sha256": sha256_file(path),
            }
        assert files[0].read_bytes() == files[1].read_bytes()
        assert sha256_file(args.input) == source_hash
        report = {
            "schema_version": "research-real-acceptance/v1",
            "oos": False,
            "real_oos_pending": True,
            "source_sha256": source_hash,
            "session": market.manifest["sessions"][0],
            "generation": plan.manifest,
            "partition": view,
            "paths": reports,
            "byte_identical": True,
            "increment_5_ledger_journal_metrics_equivalent": True,
            "total_seconds": str(time.perf_counter() - started),
            "cache_bytes": directory_bytes(args.cache)
            + (directory_bytes(session_cache_root) if session_cache_root != args.cache else 0),
            "market_cache_bytes": directory_bytes(args.cache),
            "session_evaluation_cache_bytes": directory_bytes(cache.root),
            "artifact_bytes": directory_bytes(args.output),
        }
        write_canonical_json(args.output / "acceptance-report.json", report)
        print(json.dumps(report), flush=True)
    finally:
        cache.close()


if __name__ == "__main__":
    main()
