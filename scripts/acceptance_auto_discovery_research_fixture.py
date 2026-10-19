"""Synthetic four-session acceptance for AUTO_DISCOVERY -> Research integration."""

# ruff: noqa: E402 -- explicit source-workspace bootstrap for local acceptance.

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "apps" / "cli" / "src"))
for package in ("core", "data", "backtest", "mining", "research"):
    sys.path.insert(0, str(ROOT / "packages" / package / "src"))

from quantlab_core.canonical import sha256_file, write_canonical_json
from quantlab_mining.auto_discovery import (
    ProfileRegistry,
    load_candidate_plan_bundle,
    publish_candidate_plan_bundle,
)
from quantlab_mining.batch import ControlledInterruption
from quantlab_mining.contracts import AutoDiscoveryProfileV1, MiningRequestV1, load_contract
from quantlab_research.contracts import SplitPolicyV1
from quantlab_research.freeze import validate_freeze
from quantlab_research.runner import run_composite_research
from research_helpers import execution_config, prepare, test_gate

PROFILE_ROOT = ROOT / "examples" / "mining" / "auto-discovery"
DISCOVERY_GATE = test_gate(
    criteria=[{"metric": "trades", "operator": "GTE", "threshold": 0}]
)
VALIDATION_GATE = test_gate(
    validation=True,
    criteria=[{"metric": "trades", "operator": "GTE", "threshold": 0}],
)
SPLIT_POLICY = SplitPolicyV1(
    validation_sessions=2,
    min_discovery_sessions=2,
    min_validation_sessions=2,
)


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def hashes(path: Path) -> dict[str, str]:
    return {
        item.relative_to(path).as_posix(): sha256_file(item)
        for item in sorted(path.rglob("*"))
        if item.is_file()
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args()
    root = args.work
    if root.exists():
        raise ValueError("acceptance work directory must be new")
    root.mkdir(parents=True)

    profile = load_contract(PROFILE_ROOT / "acceptance-v1.json", AutoDiscoveryProfileV1)
    request = load_contract(
        PROFILE_ROOT / "requests" / "acceptance-v1.json", MiningRequestV1
    )
    published = publish_candidate_plan_bundle(
        request,
        registry=ProfileRegistry.from_directory(PROFILE_ROOT),
        cache_root=root / "candidate-cache",
        checkpoint=root / "candidate-checkpoint.json",
        output_root=root / "bundles",
    )
    bundle = load_candidate_plan_bundle(
        published.directory, cache_root=root / "candidate-cache"
    )
    catalog, _ = prepare(
        root / "history", count=4, timeframes=("1m", "2m", "5m", "15m")
    )
    evaluation = execution_config().model_copy(update={"max_sessions": 4})
    traces: dict[str, list[dict]] = {}

    def execute(name: str, *, stop_after: int | None = None) -> dict:
        events: list[dict] = []
        traces[name] = events
        return run_composite_research(
            bundle.plan,
            bundle.resolved,
            evaluation,
            catalog,
            SPLIT_POLICY,
            DISCOVERY_GATE,
            VALIDATION_GATE,
            cache_root=root / "history/cache",
            checkpoint=root / f"{name}.sqlite",
            output=root / name,
            stop_after=stop_after,
            stop_stage="DISCOVERY",
            observer=events.append,
        )

    execute("clean")
    execute("warm")
    try:
        execute("resumed", stop_after=5)
    except ControlledInterruption:
        pass
    else:
        raise AssertionError("controlled interruption did not occur")
    execute("resumed")

    clean_hashes = hashes(root / "clean")
    if clean_hashes != hashes(root / "warm") or clean_hashes != hashes(root / "resumed"):
        raise AssertionError("clean/warm/resume Research artifacts differ")
    freeze_position = next(
        index
        for index, event in enumerate(traces["clean"])
        if event["phase"] == "DISCOVERY_FREEZE_VALIDATED"
    )
    validation_before_freeze = [
        event
        for event in traces["clean"][:freeze_position]
        if event.get("stage") == "VALIDATION"
    ]
    if validation_before_freeze:
        raise AssertionError("Validation was accessed before Discovery Freeze")

    manifest = bundle.plan.manifest
    protocol = load(root / "clean/research-protocol.json")
    experiment = load(root / "clean/validation-experiment.json")
    pressure = load(root / "clean/selection-pressure.json")
    report = {
        "schema_version": "tenth-increment-phase3-synthetic-acceptance/v1",
        "synthetic": True,
        "real_b3_data_accessed": False,
        "fixture_gate_scope": "STRUCTURAL_TEST_ONLY_NOT_PRODUCTION_THRESHOLDS",
        "fixture_gates_predeclared": {
            "discovery": DISCOVERY_GATE.canonical_record(),
            "validation": VALIDATION_GATE.canonical_record(),
        },
        "auto_discovery_profile_id": profile.auto_discovery_profile_id,
        "bundle_id": bundle.bundle_id,
        "resolved_search_space_id": bundle.resolved.resolved_search_space_id,
        "candidate_universe_id": manifest.candidate_universe_id,
        "candidate_counts": manifest.counts.model_dump(mode="json"),
        "by_direction": manifest.by_direction,
        "by_timeframe": manifest.by_timeframe,
        "by_family": manifest.by_family,
        "research_protocol": protocol,
        "discovery_freeze": validate_freeze(root / "clean"),
        "validation_experiment_id": experiment["validation_experiment_id"],
        "research_counts": pressure["counts"],
        "validation_access_before_freeze": 0,
        "clean_warm_resume_byte_identical": True,
        "scientific_artifact_hashes": clean_hashes,
        "operational": {
            name: load(root / f"{name}.operational.json")
            for name in ("clean", "warm", "resumed")
        },
    }
    write_canonical_json(root / "acceptance-report.json", report)
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
