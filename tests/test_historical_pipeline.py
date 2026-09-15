from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from helpers import (
    write_historical_catalog,
    write_historical_fixture_sources,
    write_historical_strategy,
)
from quantlab_cli.historical_pipeline import run_third_increment
from quantlab_core.canonical import sha256_file
from quantlab_core.errors import ContractError
from quantlab_data.historical import read_session_features


class HistoricalPipelineTests(unittest.TestCase):
    def _prepare(self, root: Path, *, period: int = 2) -> tuple[list[dict[str, str]], Path]:
        sources = write_historical_fixture_sources(root / "sources")
        strategy = root / f"strategy-{period}.json"
        write_historical_strategy(strategy, period=period)
        return sources, strategy

    def test_window_cache_incremental_and_full_equivalence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "sources").mkdir()
            sources, strategy = self._prepare(root)
            catalog_19 = root / "catalog-19.json"
            write_historical_catalog(catalog_19, sources[:19])
            cache = root / "cache"

            first = run_third_increment(
                catalog_19, strategy, cache, root / "first", max_sessions=19
            )
            repeated = run_third_increment(
                catalog_19, strategy, cache, root / "repeated", max_sessions=19
            )
            self.assertEqual(first["run_id"], repeated["run_id"])
            repeated_report = json.loads((root / "repeated" / "build-report.json").read_text())
            self.assertTrue(
                all(
                    operation["status"] in {"CACHE_HIT", "DEDUPLICATED_IDENTICAL"}
                    for operation in repeated_report["operations"]
                )
            )

            first_dataset = json.loads(
                (root / "first" / "historical-dataset-manifest.json").read_text()
            )
            removed_session_id = first_dataset["sessions"][0]["session_id"]
            catalog_20 = root / "catalog-20.json"
            write_historical_catalog(catalog_20, sources)
            incremental = run_third_increment(
                catalog_20, strategy, cache, root / "incremental", max_sessions=19
            )
            incremental_dataset = json.loads(
                (root / "incremental" / "historical-dataset-manifest.json").read_text()
            )
            self.assertEqual(len(incremental_dataset["sessions"]), 19)
            fixture_path = (
                Path(__file__).parent
                / "fixtures"
                / "historical"
                / "twenty_sessions.json"
            )
            fixture_dates = [
                row["trading_date"]
                for row in json.loads(fixture_path.read_text())["sessions"]
            ]
            self.assertEqual(
                [row["trading_date"] for row in incremental_dataset["sessions"]],
                fixture_dates[1:],
            )
            incremental_report = json.loads(
                (root / "incremental" / "build-report.json").read_text()
            )
            self.assertEqual(
                incremental_report["logically_removed_session_ids"], [removed_session_id]
            )
            self.assertTrue(incremental_report["removed_session_cache_preserved"])
            removed_cache = cache / "sessions" / removed_session_id.removeprefix("sha256:")
            self.assertTrue(removed_cache.is_dir())

            full_cache = root / "full-cache"
            full = run_third_increment(
                catalog_20, strategy, full_cache, root / "full", max_sessions=19
            )
            self.assertEqual(incremental["run_id"], full["run_id"])
            self.assertEqual(incremental["dataset_id"], full["dataset_id"])
            for name in (
                "historical-dataset-manifest.json",
                "strategy-definition.json",
                "ledger.jsonl",
                "discarded-signals.jsonl",
                "metrics.json",
                "run-manifest.json",
            ):
                self.assertEqual(
                    sha256_file(root / "incremental" / name),
                    sha256_file(root / "full" / name),
                    name,
                )

    def test_four_timeframes_reset_partial_feature_and_selective_invalidation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "sources").mkdir()
            sources, strategy = self._prepare(root)
            catalog = root / "catalog.json"
            write_historical_catalog(catalog, sources[:2])
            cache = root / "cache"
            run_third_increment(catalog, strategy, cache, root / "period-2")
            manifest = json.loads(
                (root / "period-2" / "historical-dataset-manifest.json").read_text()
            )
            expected_counts = {"1m": 5, "2m": 3, "5m": 1, "15m": 1}
            for session in manifest["sessions"]:
                for timeframe, expected in expected_counts.items():
                    self.assertEqual(
                        session["artifacts"]["candles"][timeframe]["row_count"], expected
                    )
                    feature_key = session["cache"]["features"][timeframe]
                    rows = list(
                        read_session_features(
                            cache / "features" / feature_key / "features.parquet"
                        )
                    )
                    self.assertIsNone(rows[0].feature.sma_close_sum_units)
                    self.assertFalse(rows[-1].executable_in_session)
                    self.assertEqual(
                        rows[-1].non_executable_reason,
                        "AVAILABLE_AFTER_LAST_ELIGIBLE_TRADE",
                    )

            changed_strategy = root / "strategy-3.json"
            write_historical_strategy(changed_strategy, period=3)
            run_third_increment(catalog, changed_strategy, cache, root / "period-3")
            report = json.loads((root / "period-3" / "build-report.json").read_text())
            statuses = {(row["layer"], row["status"]) for row in report["operations"]}
            self.assertIn(("features", "BUILT"), statuses)
            self.assertNotIn(("candles", "BUILT"), statuses)
            self.assertNotIn(("session", "BUILT"), statuses)
            self.assertNotIn(("import", "BUILT"), statuses)

    def test_conflicting_hash_or_contract_requires_explicit_choice(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "sources").mkdir()
            sources, strategy = self._prepare(root)
            original = Path(sources[0]["source"])
            conflict_root = root / "conflict"
            conflict_root.mkdir()
            conflict = conflict_root / original.name
            with zipfile.ZipFile(original) as archive:
                entry_name = archive.namelist()[0]
                changed = archive.read(entry_name).replace(b"WINV26", b"WINZ26")
            with zipfile.ZipFile(conflict, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(entry_name, changed)
            catalog = root / "conflict.json"
            write_historical_catalog(
                catalog,
                [
                    sources[0],
                    {"source": str(conflict), "physical_contract": "WINZ26"},
                ],
            )
            with self.assertRaisesRegex(ContractError, "explicit user choice required"):
                run_third_increment(catalog, strategy, root / "cache", root / "output")

    def test_corrupt_cache_fails_instead_of_recomputing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "sources").mkdir()
            sources, strategy = self._prepare(root)
            catalog = root / "catalog.json"
            write_historical_catalog(catalog, sources[:1])
            cache = root / "cache"
            run_third_increment(catalog, strategy, cache, root / "first")
            manifest = json.loads(
                (root / "first" / "historical-dataset-manifest.json").read_text()
            )
            candle_key = manifest["sessions"][0]["cache"]["candles"]["1m"]
            (cache / "candles" / candle_key / "candles.parquet").write_bytes(b"corrupt")
            with self.assertRaisesRegex(ContractError, "failed hash validation"):
                run_third_increment(catalog, strategy, cache, root / "second")

    def test_cache_staging_uses_windows_safe_short_names(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "sources").mkdir()
            sources, strategy = self._prepare(root)
            catalog = root / "catalog.json"
            write_historical_catalog(catalog, sources[:1])
            long_cache_name = "cache-" + ("x" * 80)
            result = run_third_increment(
                catalog,
                strategy,
                root / long_cache_name,
                root / "output",
            )
            self.assertEqual(result["trading_dates"], ["2026-08-17"])


if __name__ == "__main__":
    unittest.main()
