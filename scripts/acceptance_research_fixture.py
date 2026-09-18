"""Persistent opt-in twenty-session/four-timeframe acceptance and operational benchmark."""

# ruff: noqa: E402 -- explicit source-workspace bootstrap for local acceptance.

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
for package in ("core", "data", "backtest", "mining", "research"):
    sys.path.insert(0, str(ROOT / "packages" / package / "src"))

import pyarrow.parquet as pq
from quantlab_core.canonical import sha256_file, write_canonical_json
from quantlab_data.historical import build_market_history
from quantlab_data.session_catalog import SessionCatalog
from quantlab_mining.batch import ControlledInterruption
from quantlab_mining.contracts import GenerationPolicyV1
from quantlab_research.contracts import SplitPolicyV1
from quantlab_research.freeze import validate_freeze
from quantlab_research.runner import run_research
from research_helpers import execution_config, prepare, research_search, test_gate


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def rows(path):
    return [json.loads(r["record_json"]) for r in pq.read_table(path).to_pylist()]


def hashes(path):
    return {
        p.relative_to(path).as_posix(): sha256_file(p)
        for p in sorted(path.rglob("*"))
        if p.is_file()
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args()
    root = args.work
    if root.exists():
        raise ValueError("acceptance work directory must be new")
    root.mkdir(parents=True)
    tfs = ("1m", "2m", "5m", "15m")
    catalog, sources = prepare(root, timeframes=tfs)
    write_canonical_json(root / "history-19.json", catalog.manifest)
    space, policy, evaluation = (
        research_search(tfs),
        GenerationPolicyV1(candidate_budget=50),
        execution_config(),
    )
    for name, record in (
        ("search-space", space),
        ("generation-policy", policy),
        ("evaluation", evaluation),
        ("split-policy", SplitPolicyV1()),
        ("discovery-gate", test_gate()),
        ("validation-gate", test_gate(validation=True)),
    ):
        write_canonical_json(root / (name + ".json"), record.model_dump(mode="json"))
    traces = {}

    def execute(name, history=catalog, *, empty=False, previous=None, stop_after=None):
        events = []
        traces[name] = events
        print(json.dumps({"phase": "START", "name": name}), flush=True)
        return run_research(
            space,
            policy,
            evaluation,
            history,
            SplitPolicyV1(),
            test_gate(empty=empty),
            test_gate(validation=True, empty=empty),
            cache_root=root / "cache",
            checkpoint=root / (name + ".sqlite"),
            output=root / name,
            observer=events.append,
            previous_experiment=previous,
            stop_after=stop_after,
        )

    execute("clean")
    execute("warm")
    try:
        execute("resumed", stop_after=3)
    except ControlledInterruption:
        interrupted = load(root / "resumed.operational.json")
    else:
        raise AssertionError("controlled interruption did not occur")
    execute("resumed")
    assert hashes(root / "clean") == hashes(root / "warm") == hashes(root / "resumed")
    freeze = validate_freeze(root / "clean")
    split = load(root / "clean/split-plan.json")
    assert (split["discovery"]["count"], split["validation"]["count"]) == (13, 6)
    freeze_position = next(
        i for i, e in enumerate(traces["clean"]) if e["phase"] == "DISCOVERY_FREEZE_VALIDATED"
    )
    prefreeze_reads = {
        kind: sum(
            e.get("stage") == "VALIDATION" and e.get("kind") == kind
            for e in traces["clean"][:freeze_position]
        )
        for kind in ("ticks", "candles", "features", "session_evaluation")
    }
    assert all(n == 0 for n in prefreeze_reads.values())
    # Empty policies are explicit coverage fixtures, never production performance defaults.
    execute("covered", empty=True)
    rolling_market = build_market_history(
        sources,
        logical_asset="WIN",
        cache_root=root / "cache",
        timeframes=tfs,
        max_sessions=19,
    )
    rolling_catalog = SessionCatalog(rolling_market.manifest)
    write_canonical_json(root / "history-20.json", rolling_catalog.manifest)
    execute("rolling", rolling_catalog, empty=True, previous=root / "covered")
    rolling_split = load(root / "rolling/split-plan.json")
    assert rolling_split["discovery"]["session_ids"] == (
        split["discovery"]["session_ids"][1:] + split["validation"]["session_ids"][:1]
    )
    removed = root / "cache/sessions" / split["discovery"]["session_ids"][0].removeprefix("sha256:")
    assert removed.is_dir()
    pressure = load(root / "clean/selection-pressure.json")
    discovery = rows(root / "clean/discovery-results.parquet")
    validation = rows(root / "clean/validation-results.parquet")
    comparisons = rows(root / "clean/discovery-validation-comparison.parquet")
    statuses = [json.loads(line) for line in (root / "clean/status.jsonl").read_text().splitlines()]
    concentrated = [
        r
        for r in discovery
        if r["metrics"]["largest_profitable_session_share"]["value"]
        == {"numerator": "1", "denominator": "1"}
    ]
    sample_a = next(
        s["candidate_id"] for s in statuses if s["validation_status"] == "VALIDATION_PASSED"
    )
    sample_b = next(
        s["candidate_id"] for s in statuses if s["validation_status"] == "VALIDATION_FAILED_GATE"
    )
    report = {
        "schema_version": "research-fixture-acceptance/v1",
        "synthetic": True,
        "performance_thresholds": "FIXTURES_ONLY_NOT_PRODUCTION_DEFAULTS",
        "timeframes": list(tfs),
        "split": split,
        "discovery_freeze": freeze,
        "discovery_pass_set": load(root / "clean/discovery-pass-set.json"),
        "research_protocol": load(root / "clean/research-protocol.json"),
        "validation_experiment": load(root / "clean/validation-experiment.json"),
        "selection_pressure": pressure,
        "prefreeze_validation_reads": prefreeze_reads,
        "clean_warm_resume_byte_identical": True,
        "scientific_artifact_hashes": hashes(root / "clean"),
        "cases": {
            "A": {"candidate_id": sample_a, "result": "DISCOVERY_PASS_VALIDATION_PASS"},
            "B": {"candidate_id": sample_b, "result": "DISCOVERY_PASS_VALIDATION_FAIL"},
            "C": {"count": pressure["counts"]["validation_not_run"], "validation_access": 0},
            "D": {
                "zero_trade_discovery_candidates": sum(
                    r["metrics"]["trades"]["value"]["numerator"] == "0" for r in discovery
                )
            },
            "F": {"concentrated_candidates": len(concentrated), "profitable_share": "1/1"},
        },
        "sample_partition_results": {
            "discovery": next(r for r in discovery if r["candidate_id"] == sample_a),
            "validation": next(r for r in validation if r["candidate_id"] == sample_a),
            "comparison": next(r for r in comparisons if r["candidate_id"] == sample_a),
            "concentrated": concentrated[0],
        },
        "operational": {
            name: load(root / (name + ".operational.json"))
            for name in ("clean", "warm", "resumed", "covered", "rolling")
        },
        "interrupted_operational": interrupted,
        "rolling_split": rolling_split,
        "rolling_provenance": load(root / "rolling/provenance.json"),
        "removed_session_cache_preserved": True,
    }
    write_canonical_json(root / "acceptance-report.json", report)
    print(
        json.dumps(
            {
                "report": str(root / "acceptance-report.json"),
                "counts": pressure["counts"],
                "byte_identical": True,
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
