from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pyarrow.parquet as pq
from quantlab_core.canonical import sha256_file
from quantlab_mining.batch import ControlledInterruption
from quantlab_mining.contracts import GenerationPolicyV1
from quantlab_research.contracts import SplitPolicyV1
from quantlab_research.freeze import validate_freeze
from quantlab_research.runner import run_research
from research_helpers import execution_config, prepare, research_search, test_gate


def rows(path):
    return [json.loads(row["record_json"]) for row in pq.read_table(path).to_pylist()]


def hashes(path):
    return {str(p.relative_to(path)): sha256_file(p) for p in path.rglob("*") if p.is_file()}


class ResearchRunnerTests(unittest.TestCase):
    def execute(self, root, catalog, name, **kwargs):
        return run_research(
            research_search(),
            GenerationPolicyV1(candidate_budget=20),
            execution_config(),
            catalog,
            SplitPolicyV1(),
            kwargs.pop("dgate", test_gate()),
            kwargs.pop("vgate", test_gate(validation=True)),
            cache_root=root / "cache",
            checkpoint=root / f"{name}.sqlite",
            output=root / name,
            **kwargs,
        )

    def test_known_cases_a_b_c_d_f_and_zero_prefreeze_validation_reads(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            catalog, _ = prepare(root)
            events = []
            self.execute(root, catalog, "run", observer=events.append)
            statuses = [
                json.loads(line) for line in (root / "run/status.jsonl").read_text().splitlines()
            ]
            self.assertTrue(any(r["validation_status"] == "VALIDATION_PASSED" for r in statuses))
            self.assertTrue(
                any(r["validation_status"] == "VALIDATION_FAILED_GATE" for r in statuses)
            )
            rejected = {
                r["candidate_id"]
                for r in statuses
                if r["discovery_status"] == "DISCOVERY_FAILED_GATE"
            }
            self.assertTrue(rejected)
            freeze_position = next(
                i for i, e in enumerate(events) if e["phase"] == "DISCOVERY_FREEZE_VALIDATED"
            )
            before = events[:freeze_position]
            self.assertEqual([e for e in before if e.get("stage") == "VALIDATION"], [])
            self.assertFalse(
                any(
                    e.get("stage") == "VALIDATION" and e.get("candidate_id") in rejected
                    for e in events
                )
            )
            discovery = rows(root / "run/discovery-results.parquet")
            self.assertTrue(any(r["backtest_metrics"]["trades"] == 0 for r in discovery))
            f = [
                r
                for r in discovery
                if r["metrics"]["largest_profitable_session_share"]["value"]
                == {"numerator": "1", "denominator": "1"}
            ]
            self.assertTrue(f)
            self.assertTrue(all(len(r["sessions"]) == 13 for r in discovery))
            self.assertTrue(
                all(len(r["sessions"]) == 6 for r in rows(root / "run/validation-results.parquet"))
            )
            validate_freeze(root / "run")

    def test_clean_warm_validation_interruption_resume_byte_identical(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            catalog, _ = prepare(root)
            self.execute(root, catalog, "clean")
            self.execute(root, catalog, "warm")
            with self.assertRaises(ControlledInterruption):
                self.execute(root, catalog, "resumed", stop_after=3)
            self.execute(root, catalog, "resumed")
            self.assertEqual(hashes(root / "clean"), hashes(root / "warm"))
            self.assertEqual(hashes(root / "clean"), hashes(root / "resumed"))
            operation = json.loads((root / "warm.operational.json").read_text())
            self.assertEqual(operation["stages"]["DISCOVERY"]["session_evaluations_built"], 0)
            self.assertGreater(operation["stages"]["VALIDATION"]["session_evaluations_reused"], 0)

    def test_empty_pass_set_never_accesses_validation_and_zero_trade_valid(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            catalog, _ = prepare(root)
            events = []
            gate = test_gate(
                criteria=[{"metric": "trades", "operator": "GTE", "threshold": 100000}]
            )
            self.execute(root, catalog, "empty", dgate=gate, observer=events.append)
            approved = json.loads((root / "empty/discovery-pass-set.json").read_text())
            self.assertEqual(approved["candidate_ids"], [])
            self.assertTrue(approved["discovery_pass_set_id"].startswith("sha256:"))
            self.assertEqual(rows(root / "empty/validation-results.parquet"), [])
            self.assertFalse(any(e.get("stage") == "VALIDATION" for e in events))
            statuses = [
                json.loads(line) for line in (root / "empty/status.jsonl").read_text().splitlines()
            ]
            self.assertTrue(all(s["reason"] == "EMPTY_DISCOVERY_PASS_SET" for s in statuses))

    def test_case_e_undefined_profit_factor_and_no_implicit_gate(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            catalog, _ = prepare(root)
            gate = test_gate(
                criteria=[{"metric": "profit_factor", "operator": "GTE", "threshold": "1"}]
            )
            self.execute(root, catalog, "undefined", dgate=gate)
            results = rows(root / "undefined/discovery-gate-results.parquet")
            undefined_failures = [
                f for g in results for f in g["failures"] if f["reason"] == "UNDEFINED_METRIC"
            ]
            self.assertTrue(any(f["undefined_reason"] == "NO_LOSSES" for f in undefined_failures))
            self.execute(
                root,
                catalog,
                "allow",
                dgate=test_gate(empty=True),
                vgate=test_gate(validation=True, empty=True),
            )
            self.assertTrue(
                all(
                    g["result"] == "PASS"
                    for g in rows(root / "allow/discovery-gate-results.parquet")
                )
            )


if __name__ == "__main__":
    unittest.main()
