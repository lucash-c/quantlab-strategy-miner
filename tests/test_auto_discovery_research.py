from __future__ import annotations

import io
import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from quantlab_cli.main import main as cli_main
from quantlab_core.canonical import sha256_file, write_canonical_json
from quantlab_core.errors import ContractError
from quantlab_mining.auto_discovery import (
    ProfileRegistry,
    load_candidate_plan_bundle,
    publish_candidate_plan_bundle,
)
from quantlab_mining.batch import ControlledInterruption
from quantlab_mining.checkpoint import single_writer
from quantlab_mining.contracts import AutoDiscoveryProfileV1, MiningRequestV1, load_contract
from quantlab_research.contracts import SplitPolicyV1
from quantlab_research.freeze import validate_freeze
from quantlab_research.runner import run_composite_research
from quantlab_scoring.evidence import load_research_evidence
from research_helpers import execution_config, prepare, test_gate

ROOT = Path(__file__).resolve().parents[1]
PROFILE_ROOT = ROOT / "examples" / "mining" / "auto-discovery"
ACCEPTANCE_PROFILE = PROFILE_ROOT / "acceptance-v1.json"
ACCEPTANCE_REQUEST = PROFILE_ROOT / "requests" / "acceptance-v1.json"

# Structural fixture gates, committed before running the fixture.  They are not production
# thresholds and intentionally make no claim about candidate performance.
DISCOVERY_FIXTURE_GATE = test_gate(
    criteria=[{"metric": "trades", "operator": "GTE", "threshold": 0}]
)
VALIDATION_FIXTURE_GATE = test_gate(
    validation=True,
    criteria=[{"metric": "trades", "operator": "GTE", "threshold": 0}],
)
FIXTURE_SPLIT = SplitPolicyV1(
    validation_sessions=2,
    min_discovery_sessions=2,
    min_validation_sessions=2,
)


def artifact_hashes(directory: Path) -> dict[str, str]:
    return {
        path.relative_to(directory).as_posix(): sha256_file(path)
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


class AutoDiscoveryResearchTests(unittest.TestCase):
    def test_acceptance_48_clean_warm_resume_and_integrity_guards(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            profile = load_contract(ACCEPTANCE_PROFILE, AutoDiscoveryProfileV1)
            request = load_contract(ACCEPTANCE_REQUEST, MiningRequestV1)
            bundle = publish_candidate_plan_bundle(
                request,
                registry=ProfileRegistry.from_directory(PROFILE_ROOT),
                cache_root=root / "candidate-cache",
                checkpoint=root / "candidate-checkpoint.json",
                output_root=root / "bundles",
            )
            loaded = load_candidate_plan_bundle(
                bundle.directory, cache_root=root / "candidate-cache"
            )
            self.assertEqual(loaded.bundle_id, bundle.bundle_id)
            self.assertEqual(loaded.plan.manifest.candidate_universe_id, (
                "sha256:7d97661a7221f0de68597f65fd339e5406f3e248a9181308c53c71531f0e0cc1"
            ))
            self.assertEqual(loaded.plan.manifest.counts.U, 48)
            self.assertEqual(loaded.plan.manifest.by_direction, {"BUY": 24, "SELL": 24})
            self.assertEqual(
                loaded.plan.manifest.by_timeframe,
                {"1m": 12, "2m": 12, "5m": 12, "15m": 12},
            )
            self.assertEqual(set(loaded.plan.manifest.by_family.values()), {8})
            self.assertEqual(
                profile.auto_discovery_profile_id,
                "sha256:d948c66d986cf2834fa03fd9032205124f4191f329ecfe521585d3d139e6eb7b",
            )

            catalog, _ = prepare(
                root / "history", count=4, timeframes=("1m", "2m", "5m", "15m")
            )
            evaluation = execution_config().model_copy(update={"max_sessions": 4})
            traces: dict[str, list[dict]] = {}

            def execute(name: str, **overrides):
                events: list[dict] = []
                traces[name] = events
                return run_composite_research(
                    loaded.plan,
                    loaded.resolved,
                    evaluation,
                    catalog,
                    FIXTURE_SPLIT,
                    overrides.pop("dgate", DISCOVERY_FIXTURE_GATE),
                    overrides.pop("vgate", VALIDATION_FIXTURE_GATE),
                    cache_root=root / "history/cache",
                    checkpoint=root / f"{name}.sqlite",
                    output=root / name,
                    observer=events.append,
                    **overrides,
                )

            execute("clean")
            execute("warm")
            with self.assertRaises(ControlledInterruption):
                execute("resumed", stop_after=5, stop_stage="DISCOVERY")
            execute("resumed")
            self.assertEqual(artifact_hashes(root / "clean"), artifact_hashes(root / "warm"))
            self.assertEqual(artifact_hashes(root / "clean"), artifact_hashes(root / "resumed"))
            downstream = load_research_evidence(root / "clean")
            self.assertEqual(len(downstream.candidates), 48)
            self.assertEqual(
                downstream.generation_manifest["candidate_set_id"],
                loaded.plan.manifest.candidate_universe_id,
            )

            protocol = json.loads((root / "clean/research-protocol.json").read_text())
            self.assertEqual(protocol["schema_version"], "research-protocol/v2")
            self.assertNotIn("search_space_id", protocol)
            self.assertEqual(
                protocol["resolved_search_space_id"], loaded.resolved.resolved_search_space_id
            )
            self.assertEqual(
                protocol["candidate_universe_id"], loaded.plan.manifest.candidate_universe_id
            )
            freeze = validate_freeze(root / "clean")
            self.assertEqual(freeze["schema_version"], "discovery-freeze/v2")
            self.assertNotIn("search_space_id", freeze)
            self.assertEqual(freeze["candidate_universe_id"], protocol["candidate_universe_id"])
            statuses = [
                json.loads(line) for line in (root / "clean/status.jsonl").read_text().splitlines()
            ]
            self.assertEqual(len(statuses), 48)
            self.assertTrue(
                all(row["validation_status"] != "VALIDATION_NOT_RUN" for row in statuses)
            )

            freeze_position = next(
                index
                for index, event in enumerate(traces["clean"])
                if event["phase"] == "DISCOVERY_FREEZE_VALIDATED"
            )
            self.assertFalse(
                any(
                    event.get("stage") == "VALIDATION"
                    for event in traces["clean"][:freeze_position]
                )
            )
            self.assertTrue(
                all(
                    event.get("stage") != "VALIDATION"
                    or event.get("freeze_validated", True)
                    for event in traces["clean"]
                )
            )
            warm = json.loads((root / "warm.operational.json").read_text())
            self.assertEqual(warm["stages"]["DISCOVERY"]["session_evaluations_built"], 0)
            self.assertEqual(warm["stages"]["VALIDATION"]["session_evaluations_built"], 0)
            self.assertEqual(
                warm["stages"]["DISCOVERY"]["session_evaluations_reused"]
                + warm["stages"]["VALIDATION"]["session_evaluations_reused"],
                48 * 4,
            )

            policy_files = {
                "history.json": catalog.manifest,
                "evaluation.json": evaluation.model_dump(mode="json"),
                "split.json": FIXTURE_SPLIT.model_dump(mode="json"),
                "discovery.json": DISCOVERY_FIXTURE_GATE.canonical_record(),
                "validation.json": VALIDATION_FIXTURE_GATE.canonical_record(),
            }
            for name, record in policy_files.items():
                write_canonical_json(root / name, record)
            cli_arguments = [
                "run-auto-discovery-research",
                "--bundle",
                str(bundle.directory),
                "--candidate-cache",
                str(root / "candidate-cache"),
                "--history",
                str(root / "history.json"),
                "--evaluation",
                str(root / "evaluation.json"),
                "--split-policy",
                str(root / "split.json"),
                "--discovery-gate",
                str(root / "discovery.json"),
                "--validation-gate",
                str(root / "validation.json"),
                "--cache",
                str(root / "history/cache"),
                "--checkpoint",
                str(root / "cli.sqlite"),
                "--output",
                str(root / "cli"),
            ]
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(cli_main(cli_arguments), 0)
            self.assertEqual(artifact_hashes(root / "clean"), artifact_hashes(root / "cli"))

            for direction, disabled in (("BUY", "SELL"), ("SELL", "BUY")):
                narrowed_record = request.model_dump(mode="json")
                narrowed_record["sides"][disabled]["enabled"] = False
                narrowed_request = MiningRequestV1.model_validate(narrowed_record)
                narrowed_bundle = publish_candidate_plan_bundle(
                    narrowed_request,
                    registry=ProfileRegistry.from_directory(PROFILE_ROOT),
                    cache_root=root / "candidate-cache",
                    checkpoint=root / f"candidate-{direction.lower()}.json",
                    output_root=root / "bundles",
                )
                narrowed = load_candidate_plan_bundle(
                    narrowed_bundle.directory, cache_root=root / "candidate-cache"
                )
                self.assertEqual(narrowed.plan.manifest.counts.U, 24)
                self.assertEqual(narrowed.plan.manifest.by_direction, {direction: 24})
                self.assertEqual(
                    {strategy.direction for _, strategy in narrowed.plan.candidates()},
                    {direction},
                )
                run_composite_research(
                    narrowed.plan,
                    narrowed.resolved,
                    evaluation,
                    catalog,
                    FIXTURE_SPLIT,
                    DISCOVERY_FIXTURE_GATE,
                    VALIDATION_FIXTURE_GATE,
                    cache_root=root / "history/cache",
                    checkpoint=root / f"{direction.lower()}.sqlite",
                    output=root / direction.lower(),
                )
                narrowed_statuses = (root / direction.lower() / "status.jsonl").read_text()
                self.assertEqual(len(narrowed_statuses.splitlines()), 24)

            with self.assertRaises(ControlledInterruption):
                execute("divergent", stop_after=1, stop_stage="DISCOVERY")
            divergent_events: list[dict] = []
            traces["divergent"] = divergent_events
            changed_gate = test_gate(
                criteria=[{"metric": "trades", "operator": "GTE", "threshold": 1}]
            )
            with self.assertRaisesRegex(ContractError, "CHECKPOINT_CONTEXT_MISMATCH"):
                run_composite_research(
                    loaded.plan,
                    loaded.resolved,
                    evaluation,
                    catalog,
                    FIXTURE_SPLIT,
                    changed_gate,
                    VALIDATION_FIXTURE_GATE,
                    cache_root=root / "history/cache",
                    checkpoint=root / "divergent.sqlite",
                    output=root / "divergent",
                    observer=divergent_events.append,
                )
            self.assertEqual(divergent_events, [])

            with (
                single_writer(root / "busy.sqlite"),
                self.assertRaisesRegex(ContractError, "active writer"),
            ):
                run_composite_research(
                    loaded.plan,
                    loaded.resolved,
                    evaluation,
                    catalog,
                    FIXTURE_SPLIT,
                    DISCOVERY_FIXTURE_GATE,
                    VALIDATION_FIXTURE_GATE,
                    cache_root=root / "history/cache",
                    checkpoint=root / "busy.sqlite",
                    output=root / "busy",
                )

            corrupt_cache = root / "corrupt-candidate-cache"
            shutil.copytree(root / "candidate-cache", corrupt_cache)
            corrupt_database = (
                corrupt_cache
                / loaded.resolved.resolved_search_space_id.removeprefix("sha256:")
                / "composite-plan.sqlite"
            )
            corrupt_database.write_bytes(b"corrupt")
            with self.assertRaisesRegex(ContractError, "CANDIDATE_PLAN_CACHE_CORRUPT"):
                load_candidate_plan_bundle(bundle.directory, cache_root=corrupt_cache)


if __name__ == "__main__":
    unittest.main()
