from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from helpers import write_strategy
from quantlab_cli.b3_pipeline import run_b3_second_increment
from quantlab_cli.main import main

FIXTURE = Path(__file__).parent / "fixtures" / "b3_listed" / "sample_drv.txt"
ZIP_NAME = "10-09-2026_NEGOCIOSAVISTA_DRV.zip"
ENTRY_NAME = "10-09-2026_NEGOCIOSAVISTA_DRV.txt"


def write_fixture_zip(path: Path) -> Path:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(ENTRY_NAME, FIXTURE.read_bytes())
    return path


class B3EndToEndTests(unittest.TestCase):
    def _prepare(self, root: Path) -> tuple[Path, Path]:
        source = write_fixture_zip(root / ZIP_NAME)
        strategy = root / "strategy.json"
        write_strategy(
            strategy,
            symbol="WINV26",
            period=2,
            target="5",
            stop="10",
        )
        return source, strategy

    def test_real_derived_slice_runs_twice_byte_for_byte(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, strategy = self._prepare(root)
            first = run_b3_second_increment(source, "WINV26", strategy, root / "first")
            second = run_b3_second_increment(source, "WINV26", strategy, root / "second")

            self.assertEqual(first["run_id"], second["run_id"])
            self.assertEqual(first["adapter_import_id"], second["adapter_import_id"])
            self.assertEqual(
                first["first_increment_run_id"],
                second["first_increment_run_id"],
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
            for relative in first_files:
                self.assertEqual(
                    (root / "first" / relative).read_bytes(),
                    (root / "second" / relative).read_bytes(),
                    relative,
                )

            run_manifest = json.loads(
                (root / "first" / "pipeline" / "run-manifest.json").read_text()
            )
            metrics = json.loads((root / "first" / "pipeline" / "metrics.json").read_text())
            import_report = json.loads(
                (root / "first" / "adapter" / "b3-import-report.json").read_text()
            )
            self.assertEqual(import_report["selection"]["valid_trades"], 9)
            self.assertEqual(run_manifest["artifacts"]["candles"]["row_count"], 8)
            self.assertEqual(run_manifest["artifacts"]["features"]["row_count"], 8)
            self.assertEqual(metrics["closed_trades"], 1)
            self.assertEqual(metrics["losses"], 1)
            self.assertEqual(metrics["net_pnl_units"], -30)
            ledger = (root / "first" / "pipeline" / "ledger.jsonl").read_text().splitlines()
            self.assertEqual(len(ledger), 1)

    def test_run_b3_cli_uses_the_same_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, strategy = self._prepare(root)
            stdout = StringIO()
            with redirect_stdout(stdout):
                exit_code = main(
                    [
                        "run-b3",
                        "--input",
                        str(source),
                        "--contract",
                        "WINV26",
                        "--strategy",
                        str(strategy),
                        "--output",
                        str(root / "output"),
                    ]
                )
            self.assertEqual(exit_code, 0)
            self.assertRegex(stdout.getvalue(), r"^sha256:[0-9a-f]{64}\n$")
            self.assertTrue((root / "output" / "second-increment-run.json").is_file())


if __name__ == "__main__":
    unittest.main()
