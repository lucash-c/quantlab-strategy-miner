"""Rebuild scientific files from scratch in deterministic candidate-id order."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pyarrow as pa
from quantlab_core.canonical import canonical_json_bytes, sha256_file, write_canonical_json
from quantlab_core.errors import ContractError
from quantlab_data.parquet import PYARROW_VERSION, write_record_batches

from quantlab_mining.contracts import EvaluationConfigV1, identity
from quantlab_mining.generator import CandidatePlan

EXPORT_VERSION = "batch-export/v1"
AUDIT_SHARD_ROWS = 10_000
RESULT_SCHEMA = pa.schema(
    [
        pa.field("candidate_id", pa.string(), nullable=False),
        pa.field("evaluation_id", pa.string(), nullable=False),
        pa.field("status", pa.string(), nullable=False),
        pa.field("metrics_json", pa.string(), nullable=False),
    ],
    metadata={b"quantlab.schema": b"candidate-results/v1"},
)


def _jsonl(path: Path, rows: Iterable[dict]) -> int:
    count = 0
    with path.open("wb") as output:
        for row in rows:
            output.write(canonical_json_bytes(row))
            count += 1
    return count


def export_batch(
    db: sqlite3.Connection,
    plan: CandidatePlan,
    destination: Path,
    *,
    evaluation: EvaluationConfigV1,
    records: dict[str, Any],
    feature_index: list[dict],
    evaluation_ids: dict[str, str],
) -> dict:
    if destination.exists():
        raise ContractError("output directory already exists; refusing to overwrite")
    completed = db.execute("SELECT count(*) FROM results").fetchone()[0]
    if completed != plan.manifest["counts"]["U"]:
        raise ContractError("cannot export incomplete candidate batch")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".batch-", dir=destination.parent))
    try:
        row_counts: dict[str, int] = {}
        for name, record in records.items():
            write_canonical_json(staging / name, record)
            row_counts[name] = 1
        write_canonical_json(staging / "candidate-generation-manifest.json", plan.manifest)
        row_counts["candidate-generation-manifest.json"] = 1
        row_counts["candidates.jsonl"] = _jsonl(
            staging / "candidates.jsonl",
            (
                {
                    "candidate_id": cid,
                    "strategy": strategy.model_copy(
                        update={
                            "cost_model": evaluation.cost_model,
                            "slippage_model": evaluation.slippage_model,
                        }
                    ).model_dump(mode="json"),
                }
                for cid, strategy in plan.candidates()
            ),
        )
        row_counts["candidate-provenance.jsonl"] = _jsonl(
            staging / "candidate-provenance.jsonl",
            (
                {"candidate_id": cid, "origin": json.loads(origin), "multiplicity": multiplicity}
                for cid, origin, multiplicity in db.execute(
                    "SELECT * FROM provenance ORDER BY id,origin"
                )
            ),
        )
        row_counts["feature-index.jsonl"] = _jsonl(
            staging / "feature-index.jsonl", sorted(feature_index, key=canonical_json_bytes)
        )
        row_counts["results.parquet"] = write_record_batches(
            staging / "results.parquet",
            RESULT_SCHEMA,
            (
                {
                    "candidate_id": cid,
                    "evaluation_id": eid,
                    "status": "BACKTESTED",
                    "metrics_json": metrics.rstrip("\n"),
                }
                for cid, eid, metrics in db.execute(
                    "SELECT id,evaluation_id,metrics FROM results ORDER BY id"
                )
            ),
        )
        audit_dir = staging / "audit"
        audit_dir.mkdir()
        for kind, prefix in (("ledger", "ledger"), ("journal", "signal-journal")):
            handle = None
            total = 0
            try:
                for cid, ordinal, payload in db.execute(
                    "SELECT id,ordinal,payload FROM audit WHERE kind=? ORDER BY id,ordinal", (kind,)
                ):
                    if total % AUDIT_SHARD_ROWS == 0:
                        if handle:
                            handle.close()
                        name = f"audit/{prefix}-{total // AUDIT_SHARD_ROWS:05d}.jsonl"
                        handle = (staging / name).open("wb")
                        row_counts[name] = 0
                    handle.write(
                        canonical_json_bytes(
                            {
                                "candidate_id": cid,
                                "evaluation_id": evaluation_ids[cid],
                                "ordinal": ordinal,
                                "record": json.loads(payload),
                            }
                        )
                    )
                    row_counts[name] += 1
                    total += 1
            finally:
                if handle:
                    handle.close()
        artifacts = {
            name: {"file": name, "row_count": count, "byte_sha256": sha256_file(staging / name)}
            for name, count in sorted(row_counts.items())
        }
        manifest = {
            "schema_version": "candidate-batch-manifest/v1",
            **plan.manifest,
            "schema_version_generation": plan.manifest["schema_version"],
            "dataset_id": records["market-dataset-manifest.json"]["dataset_id"],
            "evaluation_ids": evaluation_ids,
            "export_version": EXPORT_VERSION,
            "pyarrow_version": PYARROW_VERSION,
            "audit_shard_rows": AUDIT_SHARD_ROWS,
            "artifacts": artifacts,
        }
        manifest["schema_version"] = "candidate-batch-manifest/v1"
        manifest["run_id"] = identity("candidate-batch/v1", manifest)
        write_canonical_json(staging / "batch-manifest.json", manifest)
        os.replace(staging, destination)
        return manifest
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
