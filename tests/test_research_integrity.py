from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from helpers import write_historical_fixture_sources
from mining_helpers import small_search
from quantlab_core.errors import ContractError
from quantlab_data.historical import SessionSource, build_market_history
from quantlab_data.session_catalog import SessionCatalog
from quantlab_mining.batch import ControlledInterruption
from quantlab_mining.contracts import GenerationPolicyV1
from quantlab_research.contracts import SplitPolicyV1
from quantlab_research.runner import run_research
from research_helpers import execution_config, test_gate


def prepared(root):
    source_dir = root / "sources"
    source_dir.mkdir()
    sources = [
        SessionSource(Path(s["source"]), s["physical_contract"])
        for s in write_historical_fixture_sources(source_dir)
    ]
    return SessionCatalog(
        build_market_history(
            sources[:19],
            logical_asset="WIN",
            cache_root=root / "cache",
            timeframes=("1m",),
            max_sessions=19,
        ).manifest
    )


def execute(root, catalog, **kwargs):
    return run_research(
        small_search(),
        GenerationPolicyV1(candidate_budget=2),
        execution_config(),
        catalog,
        SplitPolicyV1(),
        test_gate(empty=True),
        kwargs.pop("vgate", test_gate(validation=True, empty=True)),
        cache_root=root / "cache",
        checkpoint=root / "run.sqlite",
        output=root / "run",
        **kwargs,
    )


class ResearchIntegrityTests(unittest.TestCase):
    def test_normative_change_cannot_resume_or_read_holdout(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            catalog = prepared(root)
            with self.assertRaises(ControlledInterruption):
                execute(root, catalog, stop_after=1, stop_stage="DISCOVERY")
            events = []
            changed = test_gate(
                validation=True, criteria=[{"metric": "trades", "operator": "GTE", "threshold": 1}]
            )
            with self.assertRaisesRegex(ContractError, "CHECKPOINT_CONTEXT_MISMATCH"):
                execute(root, catalog, vgate=changed, observer=events.append)
            self.assertEqual(events, [])

    def test_tampered_freeze_never_releases_validation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            catalog = prepared(root)
            with self.assertRaises(ControlledInterruption):
                execute(root, catalog, stop_after=1)
            evidence = root / "run.research/discovery-freeze/discovery-results.jsonl"
            evidence.write_bytes(evidence.read_bytes() + b" ")
            events = []
            with self.assertRaisesRegex(ContractError, "freeze artifact mismatch"):
                execute(root, catalog, observer=events.append)
            self.assertFalse(any(e.get("stage") == "VALIDATION" for e in events))

    def test_missing_freeze_cannot_reauthorize_existing_validation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            catalog = prepared(root)
            with self.assertRaises(ControlledInterruption):
                execute(root, catalog, stop_after=1)
            shutil.rmtree(root / "run.research/discovery-freeze")
            events = []
            with self.assertRaisesRegex(ContractError, "missing discovery freeze"):
                execute(root, catalog, observer=events.append)
            self.assertEqual(events, [])


if __name__ == "__main__":
    unittest.main()
