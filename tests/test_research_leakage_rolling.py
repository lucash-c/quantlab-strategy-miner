from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from quantlab_core.canonical import sha256_file
from quantlab_data.historical import build_market_history
from quantlab_data.session_catalog import SessionCatalog
from quantlab_mining.contracts import GenerationPolicyV1
from quantlab_research.contracts import SplitPolicyV1
from quantlab_research.runner import run_research
from research_helpers import execution_config, prepare, research_search, test_gate


def execute(root, catalog, name, *, empty=False, previous=None, observer=None):
    return run_research(
        research_search(),
        GenerationPolicyV1(candidate_budget=20),
        execution_config(),
        catalog,
        SplitPolicyV1(),
        test_gate(empty=empty),
        test_gate(validation=True, empty=empty),
        cache_root=root / "cache",
        checkpoint=root / f"{name}.sqlite",
        output=root / name,
        previous_experiment=previous,
        observer=observer,
    )


class LeakageRollingTests(unittest.TestCase):
    def test_validation_payload_only_leaves_discovery_and_pass_set_byte_identical(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original, sources = prepare(root)
            original_hashes = [sha256_file(s.source) for s in sources[:13]]
            first = execute(root, original, "original")
            changed, changed_sources = prepare(root, validation_variant=True)
            self.assertEqual(original_hashes, [sha256_file(s.source) for s in changed_sources[:13]])
            second = execute(root, changed, "changed")
            for name in (
                "discovery-results.parquet",
                "discovery-gate-results.parquet",
                "discovery-pass-set.json",
                "discovery-partition.json",
                "candidates.jsonl",
            ):
                self.assertEqual(
                    (root / "original" / name).read_bytes(),
                    (root / "changed" / name).read_bytes(),
                    name,
                )
            self.assertNotEqual(
                first["validation_experiment_id"], second["validation_experiment_id"]
            )

    def test_discovery_changes_can_change_pass_set(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original, _ = prepare(root)
            execute(root, original, "original")
            changed, _ = prepare(root, discovery_variant=True)
            execute(root, changed, "changed")
            a = json.loads((root / "original/discovery-pass-set.json").read_text())
            b = json.loads((root / "changed/discovery-pass-set.json").read_text())
            self.assertNotEqual(a["candidate_ids"], b["candidate_ids"])

    def test_rolling_full_coverage_only_session20_new_and_five_known_holdout_overlaps(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original, sources = prepare(root)
            execute(root, original, "first", empty=True)
            market = build_market_history(
                sources,
                logical_asset="WIN",
                cache_root=root / "cache",
                timeframes=("1m",),
                max_sessions=19,
            )
            execute(
                root,
                SessionCatalog(market.manifest),
                "rolling",
                empty=True,
                previous=root / "first",
            )
            a = json.loads((root / "first/split-plan.json").read_text())
            b = json.loads((root / "rolling/split-plan.json").read_text())
            self.assertEqual(
                a["discovery"]["session_ids"][1:] + [a["validation"]["session_ids"][0]],
                b["discovery"]["session_ids"],
            )
            self.assertEqual(
                a["validation"]["session_ids"][1:], b["validation"]["session_ids"][:-1]
            )
            report = json.loads((root / "rolling.operational.json").read_text())
            self.assertEqual(report["stages"]["DISCOVERY"]["session_evaluations_built"], 0)
            self.assertEqual(report["stages"]["DISCOVERY"]["session_evaluations_reused"], 13 * 12)
            self.assertEqual(report["stages"]["VALIDATION"]["session_evaluations_built"], 12)
            self.assertEqual(report["stages"]["VALIDATION"]["session_evaluations_reused"], 5 * 12)
            removed = (
                root / "cache/sessions" / a["discovery"]["session_ids"][0].removeprefix("sha256:")
            )
            self.assertTrue(removed.is_dir())
            provenance = json.loads((root / "rolling/provenance.json").read_text())
            self.assertEqual(len(provenance["overlapping_validation_sessions"]), 5)

    def test_lineage_changes_provenance_not_scientific_experiment(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            catalog, _ = prepare(root)
            a = execute(root, catalog, "a")
            b = execute(root, catalog, "b", previous=root / "a")
            self.assertEqual(a["validation_experiment_id"], b["validation_experiment_id"])
            self.assertNotEqual(a["export_id"], b["export_id"])
            self.assertEqual(
                (root / "a/validation-results.parquet").read_bytes(),
                (root / "b/validation-results.parquet").read_bytes(),
            )

    def test_rolling_previously_barred_candidates_may_need_session14(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original, sources = prepare(root)
            execute(root, original, "first")
            first_split = json.loads((root / "first/split-plan.json").read_text())
            previously_approved = set(
                json.loads((root / "first/discovery-pass-set.json").read_text())["candidate_ids"]
            )
            market = build_market_history(
                sources,
                logical_asset="WIN",
                cache_root=root / "cache",
                timeframes=("1m",),
                max_sessions=19,
            )
            events = []
            execute(root, SessionCatalog(market.manifest), "rolling", observer=events.append)
            moved = [
                e
                for e in events
                if e["phase"] == "SESSION_BACKTESTED"
                and e["stage"] == "DISCOVERY"
                and e["session_id"] == first_split["validation"]["session_ids"][0]
            ]
            self.assertTrue(any(e["reused"] for e in moved))
            self.assertTrue(any(not e["reused"] for e in moved))
            self.assertTrue(
                all(e["reused"] == (e["candidate_id"] in previously_approved) for e in moved)
            )


if __name__ == "__main__":
    unittest.main()
