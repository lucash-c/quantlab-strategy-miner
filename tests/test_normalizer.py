from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from quantlab_core.canonical import sha256_file
from quantlab_core.errors import ContractError
from quantlab_data.normalizer import normalize_csv
from quantlab_data.parquet import iter_trades


class NormalizerTests(unittest.TestCase):
    def _write_source(self, directory: Path) -> Path:
        source = directory / "ticks.csv"
        source.write_bytes(
            b"symbol,timestamp,source_sequence,price,quantity\n"
            b"TEST,2026-01-02T09:01:00-03:00,0,100.10,2\n"
            b"TEST,2026-01-02T12:00:00.000000001Z,1,100.0,1\n"
            b"TEST,2026-01-02T09:00:00.000000001-03:00,0,99.95,3\n"
        )
        return source

    def test_normalizes_order_timezone_scale_and_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self._write_source(root)
            source_hash_before = sha256_file(source)
            manifest = normalize_csv(source, root / "out")
            trades = list(iter_trades(root / "out" / "normalized-trades.parquet"))

            self.assertEqual(source_hash_before, sha256_file(source))
            self.assertEqual(manifest["source"]["sha256"], source_hash_before)
            self.assertEqual(manifest["price"]["decimal_scale"], 2)
            self.assertEqual([trade.price_units for trade in trades], [9995, 10000, 10010])
            self.assertEqual(
                [trade.order_key for trade in trades], sorted(trade.order_key for trade in trades)
            )
            stored = json.loads((root / "out" / "dataset-manifest.json").read_text())
            self.assertEqual(stored, manifest)

    def test_parquet_and_manifest_are_byte_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self._write_source(root)
            normalize_csv(source, root / "first")
            normalize_csv(source, root / "second")
            for name in ("normalized-trades.parquet", "dataset-manifest.json"):
                self.assertEqual(
                    (root / "first" / name).read_bytes(), (root / "second" / name).read_bytes()
                )

    def test_duplicate_order_key_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "duplicate.csv"
            source.write_text(
                "symbol,timestamp,source_sequence,price,quantity\n"
                "TEST,2026-01-02T12:00:00Z,0,100,1\n"
                "TEST,2026-01-02T09:00:00-03:00,0,101,1\n",
                encoding="utf-8",
                newline="",
            )
            with self.assertRaisesRegex(ContractError, "duplicate"):
                normalize_csv(source, root / "out")


if __name__ == "__main__":
    unittest.main()
