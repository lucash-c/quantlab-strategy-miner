from __future__ import annotations

import csv
import hashlib
import json
import tempfile
import unittest
import zipfile
from io import StringIO
from pathlib import Path

import pyarrow.parquet as pq
from quantlab_data.adapters.b3_listed_trades import (
    B3_DRV_HEADER,
    B3ImportRejected,
    import_b3_listed_trades_drv,
)

FIXTURE = Path(__file__).parent / "fixtures" / "b3_listed" / "sample_drv.txt"
ZIP_NAME = "10-09-2026_NEGOCIOSAVISTA_DRV.zip"
ENTRY_NAME = "10-09-2026_NEGOCIOSAVISTA_DRV.txt"


def write_zip(path: Path, text: str | None = None) -> Path:
    content = FIXTURE.read_text(encoding="utf-8") if text is None else text
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(ENTRY_NAME, content.encode("utf-8"))
    return path


def rows_from_fixture() -> list[list[str]]:
    return list(csv.reader(StringIO(FIXTURE.read_text(encoding="utf-8")), delimiter=";"))


class B3AdapterTests(unittest.TestCase):
    def assert_no_float(self, value: object) -> None:
        if isinstance(value, float):
            self.fail(f"floating-point value found: {value!r}")
        if isinstance(value, dict):
            for child in value.values():
                self.assert_no_float(child)
        elif isinstance(value, list):
            for child in value:
                self.assert_no_float(child)

    def test_parsing_price_quantity_timestamp_filter_and_order(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = write_zip(root / ZIP_NAME)
            report = import_b3_listed_trades_drv(source, "WINV26", root / "adapter")

            self.assertEqual(report["status"], "ACCEPTED")
            self.assertEqual(report["input"]["lines_read"], 13)
            self.assertEqual(report["input"]["action_counts"], {"0": 12, "2": 1})
            self.assertEqual(report["input"]["non_positive_price_rows"], 1)
            self.assertEqual(
                report["input"]["non_positive_price_instruments"],
                [{"symbol": "DIIF27J27", "event_count": 1}],
            )
            self.assertEqual(report["selection"]["events"], 9)
            self.assertEqual(report["selection"]["valid_trades"], 9)
            self.assertEqual(report["selection"]["session_counts"], {"1": 9})
            self.assertEqual(report["selection"]["cancellation_events"], 0)
            self.assertEqual(report["selection"]["orphan_cancellations"], 0)

            canonical_path = root / "adapter" / "canonical-trades.csv"
            with canonical_path.open(encoding="utf-8", newline="") as stream:
                canonical = list(csv.DictReader(stream))
            self.assertEqual(len(canonical), 9)
            self.assertEqual(canonical[0]["price"], "187500")
            self.assertEqual(canonical[0]["quantity"], "500")
            self.assertEqual(canonical[0]["timestamp"], "2026-09-10T09:03:00.560-03:00")
            self.assertEqual(canonical[0]["source_sequence"], "2")
            self.assertEqual(canonical[1]["source_sequence"], "3")
            self.assertEqual(canonical[0]["timestamp"], canonical[1]["timestamp"])

            audit = pq.read_table(root / "adapter" / "b3-selected-events.parquet").to_pylist()
            self.assertEqual(audit[0]["PrecoNegocio"], "187500,000")
            self.assertEqual(audit[0]["TipoDoCanal"], "1")
            self.assertEqual(audit[0]["timestamp_utc"], "2026-09-10T12:03:00.560000000Z")
            self.assertEqual(audit[0]["canonical_status"], "INCLUDED")
            self.assert_no_float(report)

    def test_real_new_delete_pair_is_tombstoned(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = write_zip(root / ZIP_NAME)
            report = import_b3_listed_trades_drv(source, "ETHF27", root / "adapter")

            selection = report["selection"]
            self.assertEqual(selection["events"], 2)
            self.assertEqual(selection["new_events"], 1)
            self.assertEqual(selection["cancellation_events"], 1)
            self.assertEqual(selection["valid_trades"], 0)
            self.assertEqual(selection["canceled_new_events"], 1)
            self.assertEqual(selection["matched_cancellation_keys"], 1)
            self.assertEqual(selection["orphan_cancellations"], 0)

            canonical = (root / "adapter" / "canonical-trades.csv").read_text(
                encoding="utf-8"
            )
            self.assertEqual(canonical, "symbol,timestamp,source_sequence,price,quantity\n")
            audit = pq.read_table(root / "adapter" / "b3-selected-events.parquet").to_pylist()
            self.assertEqual(
                [row["canonical_status"] for row in audit],
                ["CANCELED_NEW", "DELETE_TOMBSTONE"],
            )
            self.assertEqual(audit[0]["CodigoIdentificadorNegocio"], "50")
            self.assertNotEqual(audit[0]["HoraFechamento"], audit[1]["HoraFechamento"])

    def test_after_hours_domain_is_accepted_without_filtering(self) -> None:
        rows = rows_from_fixture()
        selected = rows[3]
        selected[7] = "6"
        text = "\n".join(";".join(row) for row in [rows[0], selected]) + "\n"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = write_zip(root / ZIP_NAME, text)
            report = import_b3_listed_trades_drv(source, "WINV26", root / "adapter")
            self.assertEqual(report["selection"]["session_counts"], {"6": 1})
            self.assertEqual(report["selection"]["valid_trades"], 1)

    def test_unknown_action_is_reported_and_blocks_import(self) -> None:
        rows = rows_from_fixture()
        selected = rows[3]
        selected[2] = "9"
        text = "\n".join(";".join(row) for row in [rows[0], selected]) + "\n"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = write_zip(root / ZIP_NAME, text)
            output = root / "adapter"
            with self.assertRaisesRegex(B3ImportRejected, "source rows were rejected"):
                import_b3_listed_trades_drv(source, "WINV26", output)
            report = json.loads((output / "b3-import-report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "REJECTED")
            self.assertEqual(report["input"]["rejected_rows"], 1)
            rejection = json.loads((output / "b3-rejections.jsonl").read_text())
            self.assertEqual(rejection["source_sequence"], 0)
            self.assertIn("AcaoAtualizacao", rejection["error"])

    def test_negative_price_only_blocks_when_instrument_is_selected(self) -> None:
        rows = rows_from_fixture()
        text = "\n".join(";".join(row) for row in [rows[0], rows[2]]) + "\n"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = write_zip(root / ZIP_NAME, text)
            output = root / "adapter"
            with self.assertRaisesRegex(B3ImportRejected, "source rows were rejected"):
                import_b3_listed_trades_drv(source, "DIIF27J27", output)
            report = json.loads((output / "b3-import-report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["input"]["non_positive_price_rows"], 1)
            self.assertEqual(report["selection"]["rejected_rows"], 1)
            rejection = json.loads((output / "b3-rejections.jsonl").read_text())
            self.assertIn("must be positive", rejection["error"])

    def test_profile_header_is_strict(self) -> None:
        rows = rows_from_fixture()
        rows[0][-1] = "CanalSemContrato"
        text = "\n".join(";".join(row) for row in rows[:2]) + "\n"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = write_zip(root / ZIP_NAME, text)
            with self.assertRaisesRegex(Exception, "header must contain exactly"):
                import_b3_listed_trades_drv(source, "WINZ26", root / "adapter")

    def test_two_imports_are_byte_identical(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = write_zip(root / ZIP_NAME)
            first = import_b3_listed_trades_drv(source, "WINV26", root / "first")
            second = import_b3_listed_trades_drv(source, "WINV26", root / "second")
            self.assertEqual(first["import_id"], second["import_id"])

            first_files = sorted(
                path.relative_to(root / "first")
                for path in (root / "first").iterdir()
                if path.is_file()
            )
            second_files = sorted(
                path.relative_to(root / "second")
                for path in (root / "second").iterdir()
                if path.is_file()
            )
            self.assertEqual(first_files, second_files)
            for relative in first_files:
                self.assertEqual(
                    (root / "first" / relative).read_bytes(),
                    (root / "second" / relative).read_bytes(),
                    relative,
                )

            expected_entry_sha = hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
            self.assertEqual(first["source"]["entry"]["sha256"], expected_entry_sha)
            self.assertEqual(B3_DRV_HEADER[-1], "TipoDoCanal")


if __name__ == "__main__":
    unittest.main()
