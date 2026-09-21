"""Verified readers for immutable Score v1 artifacts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
from quantlab_core.canonical import sha256_file
from quantlab_core.errors import ContractError
from quantlab_mining.contracts import identity


def _reject_float(value: str) -> None:
    raise ContractError(f"JSON floating-point/special number forbidden: {value}")


def _loads(text: str, source: str) -> Any:
    try:
        return json.loads(text, parse_float=_reject_float, parse_constant=_reject_float)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ContractError(f"invalid robustness source JSON in {source}: {exc}") from exc


def _json(path: Path) -> dict[str, Any]:
    record = _loads(path.read_text(encoding="utf-8"), path.name)
    if not isinstance(record, dict):
        raise ContractError(f"expected object in {path.name}")
    return record


def _jsonl(path: Path) -> list[dict[str, Any]]:
    records = []
    with path.open(encoding="utf-8") as source:
        for ordinal, line in enumerate(source, 1):
            record = _loads(line, f"{path.name}:{ordinal}")
            if not isinstance(record, dict):
                raise ContractError(f"expected object in {path.name}:{ordinal}")
            records.append(record)
    return records


def _parquet(path: Path) -> list[dict[str, Any]]:
    records = []
    parquet = pq.ParquetFile(path)
    if "record_json" not in parquet.schema_arrow.names:
        raise ContractError(f"missing record_json in {path.name}")
    for batch in parquet.iter_batches(columns=["record_json"], batch_size=4096):
        for value in batch.column(0).to_pylist():
            record = _loads(value, path.name)
            if not isinstance(record, dict):
                raise ContractError(f"expected object in {path.name}")
            records.append(record)
    return records


@dataclass(frozen=True, slots=True)
class ScoreEvidence:
    directory: Path
    manifest: dict[str, Any]
    score_set: dict[str, Any]
    scores: dict[str, dict[str, Any]]
    raw: tuple[dict[str, Any], ...]
    diversified: tuple[dict[str, Any], ...]
    top_n: dict[str, Any]


def load_score_evidence(directory: Path) -> ScoreEvidence:
    manifest = _json(directory / "score-manifest.json")
    if manifest.get("schema_version") != "research-score-manifest/v1":
        raise ContractError("unsupported score manifest")
    expected = identity(
        "research-score-export/v1",
        {key: value for key, value in manifest.items() if key != "score_export_id"},
    )
    if manifest.get("score_export_id") != expected:
        raise ContractError("score_export_id mismatch")
    required = {
        "score-set.json",
        "strategy-scores.parquet",
        "raw-ranking.jsonl",
        "diversified-ranking.jsonl",
        "top-n.json",
    }
    if not required <= set(manifest.get("artifacts", {})):
        raise ContractError("score manifest is missing robustness inputs")
    for name, digest in manifest["artifacts"].items():
        if sha256_file(directory / name) != digest:
            raise ContractError(f"score artifact hash mismatch: {name}")
    scores = _parquet(directory / "strategy-scores.parquet")
    by_candidate = {record["candidate_id"]: record for record in scores}
    if len(by_candidate) != len(scores):
        raise ContractError("duplicate candidate in strategy scores")
    score_set = _json(directory / "score-set.json")
    if score_set.get("score_set_id") != manifest["score_set_id"]:
        raise ContractError("score set/manifest mismatch")
    return ScoreEvidence(
        directory,
        manifest,
        score_set,
        by_candidate,
        tuple(_jsonl(directory / "raw-ranking.jsonl")),
        tuple(_jsonl(directory / "diversified-ranking.jsonl")),
        _json(directory / "top-n.json"),
    )
