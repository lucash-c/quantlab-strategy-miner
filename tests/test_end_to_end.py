from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from helpers import write_strategy
from quantlab_cli.main import main
from quantlab_cli.pipeline import run_first_increment


class EndToEndTests(unittest.TestCase):
    def assert_no_float(self, value: object) -> None:
        if isinstance(value, float):
            self.fail(f"floating-point value found in canonical artifact: {value!r}")
        if isinstance(value, dict):
            for child in value.values():
                self.assert_no_float(child)
        elif isinstance(value, list):
            for child in value:
                self.assert_no_float(child)

    def _write_source(self, root: Path) -> Path:
        source = root / "canonical.csv"
        source.write_bytes(
            b"symbol,timestamp,source_sequence,price,quantity\n"
            b"TEST,2026-01-02T12:02:00Z,1,116,1\n"
            b"TEST,2026-01-02T09:00:00-03:00,0,100,1\n"
            b"TEST,2026-01-02T12:02:00Z,0,111,1\n"
            b"TEST,2026-01-02T12:01:00Z,0,110,1\n"
        )
        return source

    def test_two_runs_are_byte_for_byte_identical(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self._write_source(root)
            strategy = root / "strategy.json"
            write_strategy(strategy)

            first_manifest = run_first_increment(source, strategy, root / "first")
            second_manifest = run_first_increment(source, strategy, root / "second")
            self.assertEqual(first_manifest["run_id"], second_manifest["run_id"])
            self.assertEqual(
                set(first_manifest["runtime_versions"]),
                {"python", "sqlite", "pyarrow", "pydantic"},
            )
            self.assertEqual(
                set(first_manifest["engine_versions"]),
                {
                    "core",
                    "data",
                    "normalizer",
                    "parquet",
                    "candle",
                    "indicator",
                    "strategy_schema",
                    "strategy_evaluator",
                    "backtest",
                    "metrics",
                },
            )

            first_files = sorted(
                path.relative_to(root / "first")
                for path in (root / "first").rglob("*")
                if path.is_file()
            )
            second_files = sorted(
                path.relative_to(root / "second")
                for path in (root / "second").rglob("*")
                if path.is_file()
            )
            self.assertEqual(first_files, second_files)
            self.assertEqual(
                first_files,
                [
                    Path("candles-1m.parquet"),
                    Path("dataset-manifest.json"),
                    Path("features-1m.parquet"),
                    Path("ledger.jsonl"),
                    Path("metrics.json"),
                    Path("normalized-trades.parquet"),
                    Path("run-manifest.json"),
                    Path("strategy-definition.json"),
                ],
            )
            for relative in first_files:
                self.assertEqual(
                    (root / "first" / relative).read_bytes(),
                    (root / "second" / relative).read_bytes(),
                    relative,
                )

            ledger = [
                json.loads(line)
                for line in (root / "first" / "ledger.jsonl").read_text().splitlines()
            ]
            self.assertEqual(len(ledger), 1)
            self.assertEqual(ledger[0]["entry_source_sequence"], 0)
            self.assertEqual(ledger[0]["exit_source_sequence"], 1)
            self.assertEqual(ledger[0]["exit_reason"], "TAKE_PROFIT")
            for name in (
                "dataset-manifest.json",
                "metrics.json",
                "run-manifest.json",
                "strategy-definition.json",
            ):
                self.assert_no_float(json.loads((root / "first" / name).read_text()))
            self.assert_no_float(ledger)

    def test_cli_runs_the_same_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self._write_source(root)
            strategy = root / "strategy.json"
            write_strategy(strategy)
            stdout = StringIO()
            with redirect_stdout(stdout):
                exit_code = main(
                    [
                        "run",
                        "--input",
                        str(source),
                        "--strategy",
                        str(strategy),
                        "--output",
                        str(root / "output"),
                    ]
                )
            self.assertEqual(exit_code, 0)
            self.assertRegex(stdout.getvalue(), r"^sha256:[0-9a-f]{64}\n$")
            self.assertTrue((root / "output" / "run-manifest.json").is_file())


if __name__ == "__main__":
    unittest.main()
