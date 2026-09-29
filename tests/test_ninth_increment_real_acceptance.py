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
from quantlab_core.canonical import sha256_file  # noqa: E402
from quantlab_mining.contracts import EvaluationConfigV1, load_contract  # noqa: E402
from quantlab_robustness.contracts import (  # noqa: E402
    ExecutionStressPolicyV1,
    load_policy,
)
from quantlab_robustness.stress import resolve_stress_scenarios  # noqa: E402


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

    def test_stress_v2_amendment_is_exact_structural_and_precommitted(self):
        original_path = Path("examples/acceptance/ninth-increment/stress-policy-v1.json")
        amended_path = Path("examples/acceptance/ninth-increment/stress-policy-v2.json")
        evidence = json.loads(
            Path("docs/evidence/ninth-increment-stress-policy-amendment-v3.json")
            .read_text(encoding="utf-8")
        )
        baseline = load_contract(
            Path("examples/mining/evaluation.json"), EvaluationConfigV1
        ).model_copy(update={"max_sessions": 20})
        original = load_policy(original_path, ExecutionStressPolicyV1)
        amended = load_policy(amended_path, ExecutionStressPolicyV1)

        original_scenario = resolve_stress_scenarios(baseline, original)[0]
        amended_scenario = resolve_stress_scenarios(baseline, amended)[0]

        self.assertEqual(original.policy_id, evidence["original_policy"]["stress_policy_id"])
        self.assertEqual(sha256_file(original_path), evidence["original_policy"]["file_sha256"])
        self.assertEqual(original_scenario["status"], "INVALID_STRESS_SCENARIO")
        self.assertEqual(original_scenario["reason"], "NON_STRESS_SCENARIO")

        self.assertEqual(amended.policy_id, evidence["amended_policy"]["stress_policy_id"])
        self.assertEqual(sha256_file(amended_path), evidence["amended_policy"]["file_sha256"])
        self.assertNotEqual(original.policy_id, amended.policy_id)
        self.assertEqual(len(amended.scenarios), 1)
        self.assertEqual(amended_scenario["status"], "VALID")
        self.assertIsNone(amended_scenario["reason"])
        self.assertEqual(
            amended_scenario["resolved_cost"], {"numerator": "3", "denominator": "2"}
        )
        self.assertEqual(
            amended_scenario["resolved_slippage"],
            {"numerator": "3", "denominator": "2"},
        )
        self.assertEqual(
            amended_scenario["resolved_evaluation_config"]["cost_model"]["points_per_side"],
            "1.5",
        )
        self.assertEqual(
            amended_scenario["resolved_evaluation_config"]["slippage_model"][
                "points_per_side"
            ],
            "1.5",
        )

        self.assertFalse(
            evidence["scientific_context"]["candidate_performance_used_to_choose_amendment"]
        )
        self.assertFalse(evidence["scientific_context"]["multiple_stress_multipliers_tested"])
        self.assertFalse(evidence["amended_policy"]["new_scenario_results_observed_before_freeze"])
        self.assertTrue(evidence["original_policy"]["preserved"])
        for path, expected_hash in evidence["unchanged_file_sha256"].items():
            self.assertEqual(sha256_file(Path(path)), expected_hash, path)


if __name__ == "__main__":
    unittest.main()
