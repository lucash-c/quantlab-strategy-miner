from __future__ import annotations

import io
import json
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
