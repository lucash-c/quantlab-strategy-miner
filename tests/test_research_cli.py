from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from mining_helpers import small_search
from quantlab_cli.main import main
from quantlab_core.canonical import write_canonical_json
from quantlab_mining.contracts import GenerationPolicyV1
from research_helpers import execution_config, test_gate
from test_research_integrity import prepared


def arguments(root, command="plan-research"):
    catalog = prepared(root)
    records = {
        "space": small_search().model_dump(mode="json"),
        "policy": GenerationPolicyV1(candidate_budget=2).model_dump(mode="json"),
        "evaluation": execution_config().model_dump(mode="json"),
        "history": catalog.manifest,
        "discovery": test_gate(empty=True).canonical_record(),
        "validation": test_gate(validation=True, empty=True).canonical_record(),
    }
    for name, record in records.items():
        write_canonical_json(root / (name + ".json"), record)
    args = [command]
    for flag, name in (
        ("search-space", "space"),
        ("policy", "policy"),
        ("evaluation", "evaluation"),
        ("history", "history"),
        ("discovery-gate", "discovery"),
        ("validation-gate", "validation"),
    ):
        args.extend(["--" + flag, str(root / (name + ".json"))])
    return args


class ResearchCliTests(unittest.TestCase):
    def test_plan_metadata_only_never_resolves_payload_or_evaluates_cache(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            args = arguments(root)
            output = io.StringIO()
            with (
                redirect_stdout(output),
                patch("quantlab_research.runner.resolve_session") as resolve,
                patch("quantlab_research.runner.build_feature_set") as features,
                patch("quantlab_research.session_cache.SessionEvaluationCache.evaluate") as cache,
            ):
                self.assertEqual(main(args), 0)
                resolve.assert_not_called()
                features.assert_not_called()
                cache.assert_not_called()
            plan = json.loads(output.getvalue())
            self.assertEqual(
                (plan["split"]["discovery"]["count"], plan["split"]["validation"]["count"]), (13, 6)
            )

    def test_run_prints_final_scientific_experiment_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            args = arguments(root, "run-research") + [
                "--cache",
                str(root / "cache"),
                "--checkpoint",
                str(root / "cli.sqlite"),
                "--output",
                str(root / "result"),
            ]
            output = io.StringIO()
            with redirect_stdout(output), redirect_stderr(io.StringIO()):
                self.assertEqual(main(args), 0)
            manifest = json.loads((root / "result/validation-manifest.json").read_text())
            self.assertEqual(output.getvalue().strip(), manifest["validation_experiment_id"])

    def test_plan_wrong_gate_stage_is_explicit_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            args = arguments(root)
            write_canonical_json(
                root / "discovery.json", test_gate(validation=True).canonical_record()
            )
            error = io.StringIO()
            with redirect_stderr(error):
                self.assertEqual(main(args), 2)
            self.assertIn("wrong stages", error.getvalue())

    def test_plan_insufficient_sessions_never_resolves_payload(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            args = arguments(root)
            execution = execution_config().model_copy(update={"max_sessions": 18})
            write_canonical_json(root / "evaluation.json", execution.model_dump(mode="json"))
            error = io.StringIO()
            with (
                redirect_stderr(error),
                patch("quantlab_research.runner.resolve_session") as resolve,
            ):
                self.assertEqual(main(args), 2)
                resolve.assert_not_called()
            self.assertIn("INSUFFICIENT_SESSIONS", error.getvalue())
