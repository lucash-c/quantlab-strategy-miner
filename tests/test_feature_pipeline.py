from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from helpers import write_historical_catalog
from quantlab_cli.feature_pipeline import run_fourth_increment
from quantlab_core.canonical import sha256_file


def write_source(path: Path) -> Path:
    fixture = Path(__file__).parent / "fixtures" / "b3_listed" / "sample_drv.txt"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("10-09-2026_NEGOCIOSAVISTA_DRV.txt", fixture.read_bytes())
    return path


def write_strategy(path: Path) -> Path:
    features = [
        {
            "feature_id": "sma2",
            "name": "sma_close",
            "version": "1.0.0",
            "parameters": {"period": 2},
            "inputs": [],
        },
        {
            "feature_id": "vwap",
            "name": "session_trade_vwap",
            "version": "1.0.0",
            "parameters": {},
            "inputs": [],
        },
        {
            "feature_id": "relvol2",
            "name": "relative_volume",
            "version": "1.0.0",
            "parameters": {"period": 2, "include_current": True},
            "inputs": [],
        },
        {
            "feature_id": "close_vwap",
            "name": "close_vs_vwap",
            "version": "1.0.0",
            "parameters": {},
            "inputs": ["vwap"],
        },
    ]
    conditions = {
        "type": "logical",
        "operator": "AND",
        "children": [
            {
                "type": "comparison",
                "operator": "GT",
                "left": {"type": "feature", "feature_id": "sma2"},
                "right": {"type": "constant", "dimension": "PRICE", "value": "0"},
            },
            {
                "type": "comparison",
                "operator": "GT",
                "left": {"type": "feature", "feature_id": "vwap"},
                "right": {"type": "constant", "dimension": "PRICE", "value": "0"},
            },
            {
                "type": "comparison",
                "operator": "GT",
                "left": {"type": "feature", "feature_id": "relvol2"},
                "right": {"type": "constant", "dimension": "RATIO", "value": "0"},
            },
            {
                "type": "comparison",
                "operator": "NE",
                "left": {"type": "feature", "feature_id": "close_vwap"},
                "right": {
                    "type": "constant",
                    "dimension": "ENUM",
                    "value": "IMPOSSIBLE",
                },
            },
        ],
    }
    record = {
        "schema_version": "strategy-definition/v3",
        "strategy_id": "WIN.FEATURES.VALIDATION",
        "strategy_version": 1,
        "name": "Feature pipeline validation",
        "logical_asset": "WIN",
        "timeframe": "1m",
        "evaluation_mode": "ON_CLOSE",
        "direction": "BUY",
        "features": features,
        "entry_conditions": conditions,
        "entry_time_filter": {
            "start": "09:00",
            "end": "18:00",
            "start_inclusive": True,
            "end_inclusive": False,
        },
        "take_profit": {"unit": "POINTS", "value": "50"},
        "stop_loss": {"unit": "POINTS", "value": "50"},
        "execution": {
            "entry_fill": "NEXT_TRADE",
            "position_policy": "SINGLE_POSITION_NO_QUEUE",
            "same_tick_reentry": False,
            "session_end": "CLOSE_AT_LAST_TRADE",
            "require_post_fill_event": True,
            "position_size": 1,
        },
        "cost_model": {
            "type": "FIXED_PER_SIDE",
            "version": "1.0.0",
            "points_per_side": "0.5",
        },
        "slippage_model": {
            "type": "FIXED_POINTS",
            "version": "1.0.0",
            "points_per_side": "1",
        },
    }
    path.write_text(json.dumps(record), encoding="utf-8")
    return path


class FeaturePipelineTests(unittest.TestCase):
    def test_pipeline_is_deterministic_auditable_and_cacheable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = write_source(root / "10-09-2026_NEGOCIOSAVISTA_DRV.zip")
            catalog = root / "catalog.json"
            write_historical_catalog(
                catalog,
                [{"source": str(source), "physical_contract": "WINV26"}],
            )
            strategy = write_strategy(root / "strategy.json")
            cache = root / "cache"
            first = run_fourth_increment(catalog, strategy, cache, root / "first")
            second = run_fourth_increment(catalog, strategy, cache, root / "second")
            self.assertEqual(first["run_id"], second["run_id"])
            self.assertEqual(first["feature_set_id"], second["feature_set_id"])

            deterministic = (
                "strategy-definition.json",
                "market-dataset-manifest.json",
                "feature-set-manifest.json",
                "feature-registry.json",
                "ledger.jsonl",
                "signal-journal.jsonl",
                "metrics.json",
                "run-manifest.json",
            )
            for name in deterministic:
                self.assertEqual(
                    sha256_file(root / "first" / name),
                    sha256_file(root / "second" / name),
                    name,
                )
            report = json.loads((root / "second" / "build-report.json").read_text())
            self.assertTrue(
                all(
                    row["status"] == "CACHE_HIT"
                    for row in report["features"]["operations"]
                )
            )
            market = json.loads(
                (root / "first" / "market-dataset-manifest.json").read_text()
            )
            self.assertEqual(set(market["timeframes"]), {"1m", "2m", "5m", "15m"})
            feature_manifest = json.loads(
                (root / "first" / "feature-set-manifest.json").read_text()
            )
            self.assertEqual(
                [row["feature_id"] for row in feature_manifest["feature_specs"]],
                ["sma2", "vwap", "relvol2", "close_vwap"],
            )
            metrics = json.loads((root / "first" / "metrics.json").read_text())
            self.assertGreaterEqual(metrics["trades"], 1)
            self.assertEqual(
                metrics["gross_pnl_units"]
                - metrics["slippage_impact_units"]
                - metrics["costs_units"],
                metrics["net_pnl_units"],
            )
            journal = (root / "first" / "signal-journal.jsonl").read_text()
            self.assertIn('"condition_snapshot"', journal)


if __name__ == "__main__":
    unittest.main()
