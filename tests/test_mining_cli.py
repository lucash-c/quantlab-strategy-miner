from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from quantlab_cli.main import main


class MiningCliTests(unittest.TestCase):
    def test_plan_cli_prints_exact_fixture_counts_without_backtest(self):
        root = Path(__file__).resolve().parents[1]
        with (
            redirect_stdout(io.StringIO()) as output,
            patch("quantlab_mining.batch.run_backtest_v3") as backtest,
        ):
            status = main(
                [
                    "plan-mining",
                    "--search-space",
                    str(root / "examples/mining/fixture-160.json"),
                    "--policy",
                    str(root / "examples/mining/policy-160.json"),
                ]
            )
        self.assertEqual(status, 0)
        self.assertEqual(
            json.loads(output.getvalue())["counts"], {"T": 192, "R": 32, "V": 160, "D": 0, "U": 160}
        )
        backtest.assert_not_called()

    def test_registry_remains_independent_of_mining(self):
        import sys

        from quantlab_core.feature_specs import feature_registry_record

        self.assertFalse(feature_registry_record()["automatic_candidate_generation"])
        self.assertNotIn("quantlab_mining", sys.modules.get("quantlab_core").__dict__)

    def test_resolve_auto_discovery_uses_explicit_profile_hash_without_preflight(self):
        root = Path(__file__).resolve().parents[1]
        profile_root = root / "examples" / "mining" / "auto-discovery"
        with (
            redirect_stdout(io.StringIO()) as output,
            patch("quantlab_cli.main.preflight") as legacy_preflight,
            patch("quantlab_cli.main.run_batch") as run_batch,
            patch("quantlab_cli.main.run_research") as run_research,
        ):
            status = main(
                [
                    "resolve-auto-discovery",
                    "--request",
                    str(profile_root / "requests" / "acceptance-v1.json"),
                    "--profile-registry",
                    str(profile_root),
                ]
            )
        self.assertEqual(status, 0)
        record = json.loads(output.getvalue())
        self.assertEqual(
            record["resolved_search_space_id"],
            "sha256:a1e776bbe2c0c1fa4090aa5e9b952dd773a3c1114b8ed6ed83f33c576c027a34",
        )
        legacy_preflight.assert_not_called()
        run_batch.assert_not_called()
        run_research.assert_not_called()

    def test_plan_auto_discovery_is_operationally_invariant_and_market_free(self):
        root = Path(__file__).resolve().parents[1]
        profile_root = root / "examples" / "mining" / "auto-discovery"
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            base = [
                "plan-auto-discovery",
                "--request",
                str(profile_root / "requests" / "acceptance-v1.json"),
                "--profile-registry",
                str(profile_root),
                "--cache",
                str(temporary_root / "cache"),
                "--checkpoint",
                str(temporary_root / "checkpoint.json"),
                "--output",
                str(temporary_root / "output"),
            ]
            with (
                redirect_stdout(io.StringIO()) as first_output,
                patch("quantlab_cli.main.run_batch") as run_batch,
                patch("quantlab_cli.main.run_research") as run_research,
            ):
                first_status = main([*base, "--workers", "1", "--batch-size", "32"])
            with redirect_stdout(io.StringIO()) as second_output:
                second_status = main([*base, "--workers", "4", "--batch-size", "7"])
        self.assertEqual(first_status, 0)
        self.assertEqual(second_status, 0)
        first = json.loads(first_output.getvalue())
        second = json.loads(second_output.getvalue())
        self.assertEqual(first["bundle_id"], second["bundle_id"])
        self.assertEqual(first["candidate_universe_id"], second["candidate_universe_id"])
        self.assertEqual(first["counts"], {"T": 48, "R": 0, "V": 48, "D": 0, "U": 48})
        run_batch.assert_not_called()
        run_research.assert_not_called()
