"""Verified read-only view of completed Research Experiment evidence."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
from quantlab_core.canonical import sha256_file
from quantlab_core.errors import ContractError
from quantlab_core.numeric import CanonicalRational
from quantlab_core.strategy_v3 import StrategyDefinitionV3
from quantlab_mining.canonicalization import candidate_id as compute_candidate_id
from quantlab_mining.contracts import identity

_REQUIRED_ARTIFACTS = (
    "candidates.jsonl",
    "discovery-gate-results.jsonl",
    "discovery-results.jsonl",
    "discovery-validation-comparison.parquet",
    "generation-manifest.json",
    "status.jsonl",
    "validation-experiment.json",
    "validation-gate-results.parquet",
    "validation-results.parquet",
)


def _reject_float(value: str) -> None:
    raise ContractError(f"JSON floating-point/special number forbidden: {value}")


def _parse_json(text: str, source: str) -> Any:
    try:
        return json.loads(text, parse_float=_reject_float, parse_constant=_reject_float)
    except ContractError:
        raise
    except (json.JSONDecodeError, ValueError) as exc:
        raise ContractError(f"invalid canonical research JSON in {source}: {exc}") from exc


def _json(path: Path) -> dict[str, Any]:
    try:
        record = _parse_json(path.read_text(encoding="utf-8"), path.name)
    except OSError as exc:
        raise ContractError(f"cannot read research artifact {path.name}: {exc}") from exc
    if not isinstance(record, dict):
        raise ContractError(f"research artifact must contain an object: {path.name}")
    return record


def _jsonl(path: Path) -> Iterator[dict[str, Any]]:
    try:
        with path.open(encoding="utf-8") as source:
            for ordinal, line in enumerate(source, 1):
                record = _parse_json(line, f"{path.name}:{ordinal}")
                if not isinstance(record, dict):
                    raise ContractError(f"JSONL row must be an object: {path.name}:{ordinal}")
                yield record
    except OSError as exc:
        raise ContractError(f"cannot read research artifact {path.name}: {exc}") from exc


def _parquet_records(path: Path) -> Iterator[dict[str, Any]]:
    try:
        parquet = pq.ParquetFile(path)
        if "record_json" not in parquet.schema_arrow.names:
            raise ContractError(f"missing record_json in {path.name}")
        for batch in parquet.iter_batches(columns=["record_json"], batch_size=4096):
            for value in batch.column(0).to_pylist():
                record = _parse_json(value, path.name)
                if not isinstance(record, dict):
                    raise ContractError(f"Parquet record must be an object: {path.name}")
                yield record
    except ContractError:
        raise
    except Exception as exc:
        raise ContractError(f"cannot read research Parquet {path.name}: {exc}") from exc


def _index(records: Iterator[dict[str, Any]], source: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for record in records:
        candidate = record.get("candidate_id")
        if not isinstance(candidate, str) or candidate in result:
            raise ContractError(f"missing or duplicate candidate_id in {source}")
        result[candidate] = record
    return result


def _validate_fingerprint(record: dict[str, Any], domain: str, source: str) -> None:
    fingerprint = record.get("result_fingerprint")
    content = {key: value for key, value in record.items() if key != "result_fingerprint"}
    if fingerprint != identity(domain, content):
        raise ContractError(f"result fingerprint mismatch in {source}")


def _validate_metrics(record: dict[str, Any], source: str) -> None:
    count_metrics = {
        "trades",
        "wins",
        "losses",
        "breakeven",
        "total_sessions",
        "active_sessions",
        "profitable_sessions",
        "losing_sessions",
        "flat_sessions",
        "max_consecutive_losses",
    }
    for name, metric in record.get("metrics", {}).items():
        if metric.get("status") == "UNDEFINED":
            if metric.get("value") is not None or not isinstance(metric.get("reason"), str):
                raise ContractError(f"invalid undefined metric {name} in {source}")
            continue
        value = metric.get("value")
        if metric.get("status") != "DEFINED" or not isinstance(value, dict):
            raise ContractError(f"invalid metric status {name} in {source}")
        try:
            exact = CanonicalRational(int(value["numerator"]), int(value["denominator"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ContractError(f"invalid rational metric {name} in {source}") from exc
        if exact.to_record() != value:
            raise ContractError(f"noncanonical rational metric {name} in {source}")
        base_name = name.removesuffix("_delta")
        if base_name in count_metrics and exact.denominator != 1:
            raise ContractError(f"nonintegral count metric {name} in {source}")


@dataclass(frozen=True, slots=True)
class ResearchEvidence:
    directory: Path
    manifest: dict[str, Any]
    experiment: dict[str, Any]
    generation_manifest: dict[str, Any]
    candidates: dict[str, StrategyDefinitionV3]
    statuses: dict[str, dict[str, Any]]
    discovery_results: dict[str, dict[str, Any]]
    validation_results: dict[str, dict[str, Any]]
    discovery_gates: dict[str, dict[str, Any]]
    validation_gates: dict[str, dict[str, Any]]
    comparisons: dict[str, dict[str, Any]]
    artifact_hashes: dict[str, str]

    def score_input_manifest(self) -> dict[str, Any]:
        record = {
            "schema_version": "research-score-input-manifest/v1",
            "validation_experiment_id": self.experiment["validation_experiment_id"],
            "research_export_id": self.manifest["export_id"],
            "candidate_set_id": self.generation_manifest["candidate_set_id"],
            "required_artifacts": self.artifact_hashes,
            "source_semantics": "COMPLETED_RESEARCH_EVIDENCE_ONLY_NO_MARKET_REPLAY",
        }
        record["score_input_id"] = identity("research-score-input-manifest/v1", record)
        return record


def load_research_evidence(directory: Path) -> ResearchEvidence:
    directory = directory.resolve()
    manifest_path = directory / "validation-manifest.json"
    manifest = _json(manifest_path)
    if manifest.get("schema_version") != "validation-manifest/v1":
        raise ContractError("research source is not validation-manifest/v1")
    expected_export = identity(
        "research-export/v1",
        {key: value for key, value in manifest.items() if key != "export_id"},
    )
    if manifest.get("export_id") != expected_export:
        raise ContractError("research export_id mismatch")
    declared = manifest.get("artifacts")
    if not isinstance(declared, dict):
        raise ContractError("research manifest artifacts are missing")
    hashes: dict[str, str] = {}
    for name in _REQUIRED_ARTIFACTS:
        path = (directory / name).resolve()
        if not path.is_relative_to(directory) or name not in declared or not path.is_file():
            raise ContractError(f"required research artifact is missing: {name}")
        actual = sha256_file(path)
        if actual != declared[name]:
            raise ContractError(f"research artifact hash mismatch: {name}")
        hashes[name] = actual

    experiment = _json(directory / "validation-experiment.json")
    if experiment.get("final_status") not in {"COMPLETE", "COMPLETE_WITHOUT_VALIDATION"}:
        raise ContractError("research experiment is not complete")
    expected_experiment = identity(
        "validation-experiment/v1",
        {key: value for key, value in experiment.items() if key != "validation_experiment_id"},
    )
    if experiment.get("validation_experiment_id") != expected_experiment:
        raise ContractError("validation_experiment_id mismatch")
    if manifest.get("validation_experiment_id") != experiment["validation_experiment_id"]:
        raise ContractError("manifest and experiment identity mismatch")

    candidate_rows = _index(_jsonl(directory / "candidates.jsonl"), "candidates.jsonl")
    candidates: dict[str, StrategyDefinitionV3] = {}
    for candidate, row in candidate_rows.items():
        try:
            strategy = StrategyDefinitionV3.model_validate(row["strategy"])
        except (KeyError, ValueError) as exc:
            raise ContractError(f"invalid candidate strategy {candidate}: {exc}") from exc
        if compute_candidate_id(strategy) != candidate:
            raise ContractError(f"candidate_id mismatch for {candidate}")
        candidates[candidate] = strategy

    statuses = _index(_jsonl(directory / "status.jsonl"), "status.jsonl")
    discovery_results = _index(
        _jsonl(directory / "discovery-results.jsonl"), "discovery-results.jsonl"
    )
    discovery_gates = _index(
        _jsonl(directory / "discovery-gate-results.jsonl"),
        "discovery-gate-results.jsonl",
    )
    validation_results = _index(
        _parquet_records(directory / "validation-results.parquet"),
        "validation-results.parquet",
    )
    validation_gates = _index(
        _parquet_records(directory / "validation-gate-results.parquet"),
        "validation-gate-results.parquet",
    )
    comparisons = _index(
        _parquet_records(directory / "discovery-validation-comparison.parquet"),
        "discovery-validation-comparison.parquet",
    )
    for records, domain, source in (
        (discovery_results, "research-partition-result/v1", "discovery-results.jsonl"),
        (validation_results, "research-partition-result/v1", "validation-results.parquet"),
        (discovery_gates, "research-gate-result/v1", "discovery-gate-results.jsonl"),
        (validation_gates, "research-gate-result/v1", "validation-gate-results.parquet"),
        (comparisons, "research-comparison-result/v1", "comparison.parquet"),
    ):
        for record in records.values():
            _validate_fingerprint(record, domain, source)
            _validate_metrics(record, source)

    ids = set(candidates)
    if set(statuses) != ids or set(discovery_results) != ids or set(discovery_gates) != ids:
        raise ContractError("research candidate/status/Discovery sets are inconsistent")
    generation_manifest = _json(directory / "generation-manifest.json")
    if generation_manifest.get("candidate_set_id") != identity("candidate-set/v1", sorted(ids)):
        raise ContractError("generation candidate_set_id mismatch")
    if experiment.get("statuses") != [statuses[candidate] for candidate in sorted(statuses)]:
        raise ContractError("validation experiment/status evidence mismatch")

    validation_ids = {
        candidate
        for candidate, status in statuses.items()
        if status["discovery_status"] == "DISCOVERY_PASSED"
    }
    if not (
        set(validation_results)
        == set(validation_gates)
        == set(comparisons)
        == validation_ids
    ):
        raise ContractError("Validation evidence does not match the frozen Discovery pass set")
    for candidate, status in statuses.items():
        discovery_passed = discovery_gates[candidate]["result"] == "PASS"
        if discovery_passed != (status["discovery_status"] == "DISCOVERY_PASSED"):
            raise ContractError("Discovery gate/status inconsistency")
        if candidate in validation_ids:
            validation_passed = validation_gates[candidate]["result"] == "PASS"
            if validation_passed != (status["validation_status"] == "VALIDATION_PASSED"):
                raise ContractError("Validation gate/status inconsistency")
        elif status["validation_status"] != "VALIDATION_NOT_RUN":
            raise ContractError("non-Discovery candidate has Validation evidence")

    return ResearchEvidence(
        directory=directory,
        manifest=manifest,
        experiment=experiment,
        generation_manifest=generation_manifest,
        candidates=candidates,
        statuses=statuses,
        discovery_results=discovery_results,
        validation_results=validation_results,
        discovery_gates=discovery_gates,
        validation_gates=validation_gates,
        comparisons=comparisons,
        artifact_hashes=hashes,
    )
