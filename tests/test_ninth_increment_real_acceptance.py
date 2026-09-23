from __future__ import annotations

import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from acquire_b3_real_sessions import (  # noqa: E402
    B3_DRV_HEADER,
    source_filename,
    validate_archive,
    verify_sources,
)


class NinthIncrementRealAcceptanceTests(unittest.TestCase):
    def write_source(self, root: Path, trading_date: str) -> Path:
        path = root / source_filename(trading_date)
        entry = path.name.removesuffix(".zip") + ".txt"
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(entry, ";".join(B3_DRV_HEADER) + "\n")
        return path

    def test_drv_archive_validation_is_strict_and_exact(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = self.write_source(root, "2026-09-21")
            record = validate_archive(path, "2026-09-21")
            self.assertEqual(record["file"], path.name)
            self.assertEqual(record["entry"], path.name.removesuffix(".zip") + ".txt")
            self.assertEqual(len(record["sha256"]), 64)

    def test_missing_source_fails_without_substitution(self):
        whitelist = {
            "sessions": ["2026-09-18", "2026-09-21"],
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.write_source(root, "2026-09-18")
            with self.assertRaisesRegex(ValueError, "2026-09-21"):
                verify_sources(root, whitelist)

    def test_amendment_preserves_original_and_freezes_twenty_sessions(self):
        original = json.loads(
            Path("examples/acceptance/ninth-increment/session-whitelist-v1.json")
            .read_text(encoding="utf-8")
        )
        amended = json.loads(
            Path("examples/acceptance/ninth-increment/session-whitelist-v2.json")
            .read_text(encoding="utf-8")
        )
        evidence = json.loads(
            Path("docs/evidence/ninth-increment-precommitment-amendment-v1.json")
            .read_text(encoding="utf-8")
        )
        self.assertEqual(len(original["sessions"]), 21)
        self.assertEqual(len(amended["sessions"]), 20)
        self.assertEqual(amended["sessions"][0], "2026-08-24")
        self.assertEqual(amended["sessions"][-1], "2026-09-21")
        self.assertNotIn("2026-09-22", amended["sessions"])
        self.assertEqual(evidence["preserves_commit"], "6b65337")
        self.assertFalse(evidence["anti_tuning"]["change_made_in_response_to_strategy_performance"])
        self.assertEqual(evidence["quantitative_threshold_changes"], [])

    def test_pre_result_correction_preserves_legacy_and_all_thresholds(self):
        correction = json.loads(
            Path("docs/evidence/ninth-increment-precommitment-correction-v2.json")
            .read_text(encoding="utf-8")
        )
        original = json.loads(
            Path("examples/acceptance/ninth-increment/quantitative-gate-v1.json")
            .read_text(encoding="utf-8")
        )
        corrected = json.loads(
            Path(
                "examples/acceptance/ninth-increment/quantitative-gate-real-20-v2.json"
            ).read_text(encoding="utf-8")
        )
        legacy_gate = Path("examples/acceptance/ninth-increment/technical-gate-v1.json")
        eighth_gate = Path("examples/robustness/fixture-gate.json")
        self.assertEqual(
            json.loads(legacy_gate.read_text(encoding="utf-8")),
            json.loads(eighth_gate.read_text(encoding="utf-8")),
        )
        self.assertEqual(original.pop("technical_requirement"), "ROBUSTNESS_PASSED")
        self.assertEqual(
            corrected.pop("technical_requirement"),
            "NINTH_INCREMENT_TECHNICAL_COMPLETE",
        )
        self.assertEqual(original, corrected)
        self.assertFalse(correction["real_quantitative_results_observed_before_correction"])
        self.assertEqual(
            correction["quantitative_robustness"]["quantitative_threshold_changes"], []
        )
        self.assertFalse(correction["legacy_technical_robustness"]["policy_changed"])
        self.assertFalse(correction["legacy_technical_robustness"]["semantics_changed"])


if __name__ == "__main__":
    unittest.main()
