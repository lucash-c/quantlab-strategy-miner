"""Canonical atomic scientific export for score, ranking and diversity."""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from quantlab_core.canonical import canonical_json_bytes, sha256_file, write_canonical_json
from quantlab_core.errors import ContractError
from quantlab_mining.contracts import identity

from quantlab_scoring import SCORE_ENGINE_VERSION
from quantlab_scoring.contracts import (
    ResearchDiversityPolicyV1,
    ResearchRankingPolicyV1,
    ResearchScorePolicyV1,
)

SCORING_EXPORT_VERSION = "research-score-export/v1"


def _write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> None:
    with path.open("wb") as output:
        for record in records:
            output.write(canonical_json_bytes(record))


def _write_parquet(path: Path, schema_name: str, records: list[dict[str, str]]) -> None:
    schema = pa.schema(
        [
            pa.field("key", pa.string(), nullable=False),
            pa.field("record_json", pa.string(), nullable=False),
        ],
        metadata={b"quantlab.schema": schema_name.encode()},
    )
    with pq.ParquetWriter(
        path,
        schema,
        version="2.6",
        compression="zstd",
        compression_level=3,
        use_dictionary=False,
        write_statistics=True,
        data_page_version="1.0",
    ) as writer:
        if records:
            writer.write_table(pa.Table.from_pylist(records, schema=schema), row_group_size=65_536)


def export_scoring(
    *,
    destination: Path,
    score_policy: ResearchScorePolicyV1,
    ranking_policy: ResearchRankingPolicyV1,
    diversity_policy: ResearchDiversityPolicyV1,
    input_manifest: dict[str, Any],
    score_set: dict[str, Any],
    scores: list[dict[str, Any]],
    semantic_groups: list[dict[str, Any]],
    raw: dict[str, Any],
    diversified: dict[str, Any],
    top: dict[str, Any],
    distribution: dict[str, Any],
    pressure: dict[str, Any],
) -> dict[str, Any]:
    if destination.exists():
        raise ContractError("refusing to overwrite completed scoring export")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".scoring-", dir=destination.parent))
    try:
        write_canonical_json(staging / "score-policy.json", score_policy.canonical_record())
        write_canonical_json(
            staging / "ranking-policy.json", ranking_policy.model_dump(mode="json")
        )
        write_canonical_json(
            staging / "diversity-policy.json", diversity_policy.model_dump(mode="json")
        )
        write_canonical_json(staging / "score-input-manifest.json", input_manifest)
        write_canonical_json(staging / "score-set.json", score_set)
        write_canonical_json(staging / "score-distribution.json", distribution)
        write_canonical_json(staging / "score-selection-pressure.json", pressure)
        write_canonical_json(staging / "top-n.json", top)
        write_canonical_json(
            staging / "raw-ranking-metadata.json",
            {key: value for key, value in raw.items() if key != "records"},
        )
        write_canonical_json(
            staging / "diversified-ranking-metadata.json",
            {key: value for key, value in diversified.items() if key != "records"},
        )
        ordered_scores = sorted(scores, key=lambda item: item["candidate_id"])
        _write_parquet(
            staging / "strategy-scores.parquet",
            "research-strategy-score/v1",
            [
                {
                    "key": item["candidate_id"],
                    "record_json": canonical_json_bytes(item).decode().rstrip("\n"),
                }
                for item in ordered_scores
            ],
        )
        components = []
        for score in ordered_scores:
            for kind, name in (("TERM", "terms"), ("PENALTY", "penalties")):
                for item in score[name]:
                    record = {
                        "candidate_id": score["candidate_id"],
                        "strategy_score_id": score["strategy_score_id"],
                        "kind": kind,
                        "component": item,
                    }
                    components.append(
                        {
                            "key": f"{score['candidate_id']}:{kind}:{item['term_id']}",
                            "record_json": canonical_json_bytes(record).decode().rstrip("\n"),
                        }
                    )
        _write_parquet(
            staging / "score-components.parquet",
            "research-score-component/v1",
            components,
        )
        _write_parquet(
            staging / "semantic-groups.parquet",
            "research-semantic-group/v1",
            [
                {
                    "key": item["semantic_group_id"],
                    "record_json": canonical_json_bytes(item).decode().rstrip("\n"),
                }
                for item in sorted(semantic_groups, key=lambda item: item["semantic_group_id"])
            ],
        )
        _write_jsonl(staging / "raw-ranking.jsonl", raw["records"])
        _write_jsonl(staging / "diversified-ranking.jsonl", diversified["records"])
        artifacts = {
            str(path.relative_to(staging)).replace("\\", "/"): sha256_file(path)
            for path in sorted(staging.rglob("*"))
            if path.is_file()
        }
        manifest = {
            "schema_version": "research-score-manifest/v1",
            "export_version": SCORING_EXPORT_VERSION,
            "score_engine_version": SCORE_ENGINE_VERSION,
            "pyarrow_version": pa.__version__,
            "score_input_id": input_manifest["score_input_id"],
            "score_policy_id": score_policy.score_policy_id,
            "ranking_policy_id": ranking_policy.ranking_policy_id,
            "diversity_policy_id": diversity_policy.diversity_policy_id,
            "score_set_id": score_set["score_set_id"],
            "raw_ranking_id": raw["raw_ranking_id"],
            "diversified_ranking_id": diversified["diversified_ranking_id"],
            "top_n_output_id": top["top_n_output_id"],
            "artifacts": artifacts,
        }
        manifest["score_export_id"] = identity("research-score-export/v1", manifest)
        write_canonical_json(staging / "score-manifest.json", manifest)
        os.replace(staging, destination)
        return manifest
    finally:
        if staging.exists():
            shutil.rmtree(staging)
