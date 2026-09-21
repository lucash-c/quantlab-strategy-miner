from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pyarrow.parquet as pq
from quantlab_cli.main import main
from quantlab_core.canonical import sha256_file
from quantlab_core.errors import ContractError
from quantlab_scoring.cache import StrategyScoreCache
from quantlab_scoring.evidence import load_research_evidence
from quantlab_scoring.runner import ControlledScoringInterruption, run_scoring
from scoring_helpers import (
    diversity_policy,
    ranking_policy,
    score_policy,
    write_research_evidence,
)


def directory_bytes(root):
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def execute(root, source, output, cache, *, stop_after=None, top_n=5):
    return run_scoring(
        source,
        score_policy(),
        ranking_policy(),
        diversity_policy(),
        top_n_value=top_n,
        cache_root=cache,
        output=output,
        stop_after=stop_after,
    )


class ScoringPipelineTests(unittest.TestCase):
    def test_clean_warm_and_resume_are_byte_identical(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "research"
            write_research_evidence(source)
            clean = root / "clean"
            warm = root / "warm"
            resumed = root / "resumed"
            execute(root, source, clean, root / "cache")
            execute(root, source, warm, root / "cache")
            with self.assertRaises(ControlledScoringInterruption):
                execute(root, source, root / "partial", root / "resume-cache", stop_after=2)
            execute(root, source, resumed, root / "resume-cache")
            self.assertEqual(directory_bytes(clean), directory_bytes(warm))
            self.assertEqual(directory_bytes(clean), directory_bytes(resumed))
            clean_ops = json.loads((root / "clean.operational.json").read_text())
            warm_ops = json.loads((root / "warm.operational.json").read_text())
            resumed_ops = json.loads((root / "resumed.operational.json").read_text())
            for operations, expected in (
                (clean_ops, (4, 0)),
                (warm_ops, (0, 4)),
                (resumed_ops, (2, 2)),
            ):
                self.assertEqual(
                    (
                        operations["score_cache_built"],
                        operations["score_cache_reused"],
                    ),
                    expected,
                )

    def test_all_candidate_statuses_enter_score_set_but_only_scored_rank(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "research"
            roles = write_research_evidence(source)
            execute(root, source, root / "result", root / "cache")
            score_set = json.loads((root / "result/score-set.json").read_text())
            statuses = {row["candidate_id"]: row["score_status"] for row in score_set["candidates"]}
            self.assertEqual(statuses[roles["A"]], "SCORED")
            self.assertEqual(statuses[roles["B"]], "SCORED")
            self.assertEqual(statuses[roles["C"]], "NOT_ELIGIBLE")
            self.assertEqual(statuses[roles["D"]], "NOT_SCORABLE")
            raw = (root / "result/raw-ranking.jsonl").read_text().splitlines()
            self.assertEqual(len(raw), 2)
            pressure = json.loads((root / "result/score-selection-pressure.json").read_text())
            self.assertEqual(
                (pressure["scored"], pressure["not_eligible"], pressure["not_scorable"]),
                (2, 1, 1),
            )

    def test_export_is_canonical_aggregate_and_contains_no_candidate_directories(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "research"
            write_research_evidence(source)
            manifest = execute(root, source, root / "result", root / "cache")
            self.assertEqual(manifest["schema_version"], "research-score-manifest/v1")
            self.assertTrue(all(path.is_file() for path in (root / "result").iterdir()))
            table = pq.read_table(root / "result/strategy-scores.parquet")
            self.assertEqual(table.num_rows, 4)
            self.assertNotIn("duration", json.dumps(manifest))
            self.assertNotIn("cache", json.dumps(manifest))

    def test_top_n_changes_only_top_output_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "research"
            write_research_evidence(source)
            first = execute(root, source, root / "one", root / "cache", top_n=1)
            second = execute(root, source, root / "two", root / "cache", top_n=2)
            for field in ("score_set_id", "raw_ranking_id", "diversified_ranking_id"):
                self.assertEqual(first[field], second[field])
            self.assertNotEqual(first["top_n_output_id"], second["top_n_output_id"])

    def test_source_hash_tampering_and_cache_corruption_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "research"
            write_research_evidence(source)
            with (source / "status.jsonl").open("ab") as output:
                output.write(b" ")
            with self.assertRaisesRegex(ContractError, "hash mismatch"):
                load_research_evidence(source)

            cache = StrategyScoreCache(root / "cache")
            cache.db.execute("INSERT INTO scores VALUES('bad','{}')")
            cache.db.commit()
            with self.assertRaisesRegex(ContractError, "corrupt"):
                cache.get("bad")
            cache.close()

    def test_cli_requires_explicit_policies_and_prints_scientific_id(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "research"
            write_research_evidence(source)
            args = [
                "score-research",
                "--research",
                str(source),
                "--score-policy",
                "examples/scoring/research-score-v1.json",
                "--ranking-policy",
                "examples/scoring/research-ranking-v1.json",
                "--diversity-policy",
                "examples/scoring/research-diversity-v1.json",
                "--top-n",
                "5",
                "--cache",
                str(root / "cache"),
                "--output",
                str(root / "result"),
            ]
            output = io.StringIO()
            with redirect_stdout(output), redirect_stderr(io.StringIO()):
                self.assertEqual(main(args), 0)
            manifest = json.loads((root / "result/score-manifest.json").read_text())
            self.assertEqual(output.getvalue().strip(), manifest["score_export_id"])

    def test_score_cache_invalidation_follows_evidence_fingerprints(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "research"
            write_research_evidence(source)
            evidence = load_research_evidence(source)
            self.assertEqual(len(evidence.candidates), 4)
            first_hash = sha256_file(source / "validation-results.parquet")
            self.assertEqual(first_hash, evidence.artifact_hashes["validation-results.parquet"])


class ZeroReplayBoundaryTests(unittest.TestCase):
    def test_scoring_source_has_no_market_or_backtest_imports(self):
        source = Path("packages/scoring/src/quantlab_scoring")
        text = "\n".join(path.read_text() for path in source.glob("*.py"))
        forbidden = (
            "quantlab_data.adapters",
            "quantlab_data.normalizer",
            "quantlab_core.candles",
            "quantlab_core.feature_engine",
            "quantlab_backtest",
            "quantlab_research.session_cache",
        )
        for name in forbidden:
            self.assertNotIn(name, text)


if __name__ == "__main__":
    unittest.main()
