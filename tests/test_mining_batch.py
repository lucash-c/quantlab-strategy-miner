from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import pyarrow.parquet as pq
from helpers import write_historical_fixture_sources
from mining_helpers import evaluation, search_record, small_search
from quantlab_core.errors import ContractError
from quantlab_data.historical import SessionSource
from quantlab_mining.batch import ControlledInterruption, run_batch
from quantlab_mining.contracts import GenerationPolicyV1, MiningSearchSpaceV1


class BatchTests(unittest.TestCase):
    def sources(self, root: Path):
        return [
            SessionSource(Path(row["source"]), row["physical_contract"])
            for row in write_historical_fixture_sources(root)
        ]

    def execute(self, root: Path, sources, name: str, *, space=None, budget=160, **kwargs):
        return run_batch(
            space or small_search(),
            GenerationPolicyV1(candidate_budget=budget),
            evaluation(),
            sources,
            cache_root=root / "cache",
            checkpoint=root / (name + ".sqlite"),
            output=root / name,
            **kwargs,
        )

    def assert_files_equal(self, a: Path, b: Path):
        left = {p.relative_to(a).as_posix(): p.read_bytes() for p in a.rglob("*") if p.is_file()}
        right = {p.relative_to(b).as_posix(): p.read_bytes() for p in b.rglob("*") if p.is_file()}
        self.assertEqual(left, right)

    def test_clean_warm_and_resume_byte_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sources = self.sources(root)[:2]
            clean = self.execute(root, sources, "clean")
            warm = self.execute(root, sources, "warm")
            with self.assertRaises(ControlledInterruption):
                self.execute(root, sources, "resumed", stop_after=1)
            with closing(sqlite3.connect(root / "resumed.sqlite")) as db:
                self.assertEqual(db.execute("SELECT count(*) FROM results").fetchone()[0], 1)
            resumed = self.execute(root, sources, "resumed")
            self.assertEqual(clean, warm)
            self.assertEqual(clean, resumed)
            self.assert_files_equal(root / "clean", root / "warm")
            self.assert_files_equal(root / "clean", root / "resumed")
            report = json.loads((root / "resumed.operational.json").read_text())
            self.assertEqual((report["executed"], report["reused"]), (1, 1))
            operations = json.loads((root / "warm.operational.json").read_text())[
                "feature_operations"
            ]["1m"]["operations"]
            self.assertTrue(all(row["status"] == "CACHE_HIT" for row in operations))
            self.assertEqual(len(operations), 2)  # one shared SMA, not per BUY/SELL

    def test_fixture160_complete_zero_trade_and_19_session_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = self.execute(
                root,
                self.sources(root),
                "batch160",
                space=MiningSearchSpaceV1.model_validate(search_record(fixture160=True)),
            )
            rows = pq.read_table(root / "batch160" / "results.parquet").to_pylist()
            self.assertEqual(len(rows), 160)
            zero = [
                json.loads(row["metrics_json"])
                for row in rows
                if json.loads(row["metrics_json"])["trades"] == 0
            ]
            self.assertTrue(zero)
            self.assertTrue(all(row["status"] == "BACKTESTED" for row in rows))
            self.assertTrue(
                all(
                    m["win_rate"] == {"status": "UNDEFINED", "value": None, "reason": "NO_TRADES"}
                    for m in zero
                )
            )
            market = json.loads((root / "batch160" / "market-dataset-manifest.json").read_text())
            self.assertEqual(len(market["sessions"]), 19)
            self.assertEqual(manifest["counts"]["U"], 160)

    def test_budget159_has_no_market_or_backtest_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                patch("quantlab_mining.batch.build_market_history") as market,
                patch("quantlab_mining.batch.run_backtest_v3") as backtest,
            ):
                with self.assertRaisesRegex(ContractError, "SEARCH_SPACE_EXCEEDS_BUDGET"):
                    self.execute(
                        root,
                        [],
                        "over",
                        budget=159,
                        space=MiningSearchSpaceV1.model_validate(search_record(fixture160=True)),
                    )
                market.assert_not_called()
                backtest.assert_not_called()

    def test_corruption_and_context_mismatch_fail_fast(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sources = self.sources(root)[:1]
            with self.assertRaises(ControlledInterruption):
                self.execute(root, sources, "interrupted", stop_after=1)
            with closing(sqlite3.connect(root / "interrupted.sqlite")) as db:
                db.execute("UPDATE results SET metrics='{}'")
                db.commit()
            with patch("quantlab_mining.batch.run_backtest_v3") as backtest:
                with self.assertRaisesRegex(ContractError, "fingerprint"):
                    self.execute(root, sources, "interrupted")
                backtest.assert_not_called()
            with self.assertRaisesRegex(ContractError, "CHECKPOINT_CONTEXT_MISMATCH"):
                self.execute(root, sources, "interrupted", budget=200)

    def test_failure_rolls_back_current_candidate_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sources = self.sources(root)[:1]
            from quantlab_mining.batch import run_backtest_v3

            def fail_after_real(*args, **kwargs):
                run_backtest_v3(*args, **kwargs)
                raise ContractError("synthetic internal failure")

            with (
                patch("quantlab_mining.batch.run_backtest_v3", side_effect=fail_after_real),
                self.assertRaisesRegex(ContractError, "synthetic"),
            ):
                self.execute(root, sources, "failed")
            with closing(sqlite3.connect(root / "failed.sqlite")) as db:
                self.assertEqual(db.execute("SELECT count(*) FROM results").fetchone()[0], 0)
                self.assertEqual(db.execute("SELECT count(*) FROM audit").fetchone()[0], 0)
            self.execute(root, sources, "failed")

    def test_evaluation_changes_not_candidate_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sources = self.sources(root)[:1]
            a = self.execute(root, sources, "a")
            config = evaluation().model_copy(
                update={
                    "cost_model": evaluation().cost_model.model_copy(
                        update={"points_per_side": "2"}
                    )
                }
            )
            b = run_batch(
                small_search(),
                GenerationPolicyV1(candidate_budget=160),
                config,
                sources,
                cache_root=root / "cache",
                checkpoint=root / "b.sqlite",
                output=root / "b",
            )
            self.assertEqual(a["candidate_set_id"], b["candidate_set_id"])
            self.assertEqual(set(a["evaluation_ids"]), set(b["evaluation_ids"]))
            self.assertNotEqual(a["evaluation_ids"], b["evaluation_ids"])
