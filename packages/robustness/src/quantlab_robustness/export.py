"""Atomic canonical aggregate export rebuilt from logical records."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

import pyarrow as pa
from quantlab_core.canonical import canonical_json_bytes, sha256_file, write_canonical_json
from quantlab_core.errors import ContractError
from quantlab_data.parquet import PYARROW_VERSION, write_record_batches
from quantlab_mining.contracts import identity


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("wb") as output:
        for record in records:
            output.write(canonical_json_bytes(record))


def _write_records(path: Path, schema_name: str, records: list[dict[str, Any]], key: str) -> None:
    schema = pa.schema(
        [
            pa.field("key", pa.string(), nullable=False),
            pa.field("record_json", pa.string(), nullable=False),
        ],
        metadata={b"quantlab.schema": schema_name.encode()},
    )
    write_record_batches(
        path,
        schema,
        (
            {
                "key": str(record[key]),
                "record_json": canonical_json_bytes(record).decode().rstrip("\n"),
            }
            for record in records
        ),
    )


def export_robustness(destination: Path, records: dict[str, Any]) -> dict[str, Any]:
    if destination.exists():
        raise ContractError("refusing to overwrite completed robustness export")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".robustness-", dir=destination.parent))
    try:
        json_records = {
            "robustness-candidate-set.json": records["candidate_set"],
            "robustness-protocol.json": records["protocol"],
            "robustness-workload-policy.json": records["workload_policy"],
            "robustness-workload-plan.json": records["workload_plan"],
            "walk-forward-policy.json": records["walk_forward_policy"],
            "walk-forward-plan.json": records["walk_forward_plan"],
            "monte-carlo-policy.json": records["monte_carlo_policy"],
            "monte-carlo-source-pool.json": records["monte_carlo_source_pool"],
            "monte-carlo-path-set.json": {
                key: value
                for key, value in records["monte_carlo_path_set"].items()
                if key != "paths"
            },
            "sensitivity-policy.json": records["sensitivity_policy"],
            "sensitivity-source-pool.json": records["sensitivity_source_pool"],
            "stress-policy.json": records["stress_policy"],
            "stress-source-pool.json": records["stress_source_pool"],
        }
        if records["gate_policy"] is not None:
            json_records["robustness-gate-policy.json"] = records["gate_policy"]
            json_records["robustness-qualified-set.json"] = records["qualified_set"]
            if records["shortlist"] is not None:
                json_records["robustness-shortlist.json"] = records["shortlist"]
        quantitative = records.get("quantitative")
        technical_completeness = records.get("technical_completeness")
        if technical_completeness is not None:
            json_records.update(
                {
                    "ninth-increment-technical-completeness-policy.json": (
                        technical_completeness["policy"]
                    ),
                    "ninth-increment-technical-completeness-protocol.json": (
                        technical_completeness["protocol"]
                    ),
                    "ninth-increment-walk-forward-policy.json": technical_completeness[
                        "walk_forward_policy"
                    ],
                    "ninth-increment-walk-forward-plan.json": technical_completeness[
                        "walk_forward_plan"
                    ],
                }
            )
        if quantitative is not None:
            json_records.update(
                {
                    "robustness-quantitative-policy.json": quantitative["policy"],
                    "robustness-quantitative-protocol.json": quantitative["protocol"],
                    "robustness-quantitative-qualified-set.json": quantitative[
                        "qualified_set"
                    ],
                }
            )
            if quantitative["shortlist"] is not None:
                json_records["robustness-quantitative-shortlist.json"] = quantitative[
                    "shortlist"
                ]
        for name, record in json_records.items():
            write_canonical_json(staging / name, record)
        _write_records(
            staging / "walk-forward-folds.parquet",
            "walk-forward-fold/v1",
            records["walk_forward_plan"]["folds"],
            "walk_forward_fold_id",
        )
        _write_records(
            staging / "walk-forward-results.parquet",
            "candidate-walk-forward-result/v1",
            records["walk_forward_results"],
            "candidate_id",
        )
        _write_records(
            staging / "monte-carlo-paths.parquet",
            "monte-carlo-path-definition/v1",
            records["monte_carlo_path_set"]["paths"],
            "path_id",
        )
        _write_records(
            staging / "monte-carlo-results.parquet",
            "candidate-monte-carlo-result/v1",
            records["monte_carlo_results"],
            "candidate_id",
        )
        _write_records(
            staging / "sensitivity-results.parquet",
            "candidate-sensitivity-result/v1",
            records["sensitivity_results"],
            "candidate_id",
        )
        _write_records(
            staging / "stress-results.parquet",
            "candidate-stress-result/v1",
            records["stress_results"],
            "candidate_id",
        )
        _write_records(
            staging / "robustness-assessments.parquet",
            "candidate-robustness-assessment/v1",
            records["assessments"],
            "candidate_id",
        )
        if technical_completeness is not None:
            _write_records(
                staging / "ninth-increment-walk-forward-folds.parquet",
                "walk-forward-fold/v1",
                technical_completeness["walk_forward_plan"]["folds"],
                "walk_forward_fold_id",
            )
            _write_records(
                staging / "ninth-increment-walk-forward-results.parquet",
                "candidate-walk-forward-result/v1",
                technical_completeness["walk_forward_results"],
                "candidate_id",
            )
            _write_records(
                staging / "ninth-increment-technical-assessments.parquet",
                "candidate-robustness-technical-completeness/v1",
                technical_completeness["assessments"],
                "candidate_id",
            )
            _write_records(
                staging / "ninth-increment-technical-completeness-results.parquet",
                "robustness-technical-completeness-result/v1",
                [item["gate"] for item in technical_completeness["assessments"]],
                "candidate_id",
            )
        if records["gate_policy"] is not None:
            _write_records(
                staging / "robustness-gate-results.parquet",
                "robustness-gate-result/v1",
                [item["gate"] for item in records["assessments"]],
                "candidate_id",
            )
            _write_jsonl(staging / "robustness-ranking.jsonl", records["ranking"]["records"])
            if records["diversified"] is not None:
                _write_jsonl(
                    staging / "robustness-diversified-ranking.jsonl",
                    records["diversified"]["records"],
                )
        if quantitative is not None:
            _write_records(
                staging / "robustness-quantitative-evidence.parquet",
                "candidate-robustness-quantitative-evidence/v1",
                quantitative["evidence"],
                "candidate_id",
            )
            _write_records(
                staging / "robustness-quantitative-assessments.parquet",
                "candidate-robustness-quantitative-assessment/v1",
                quantitative["assessments"],
                "candidate_id",
            )
            _write_jsonl(
                staging / "robustness-quantitative-ranking.jsonl",
                quantitative["ranking"]["records"],
            )
            if quantitative["diversified"] is not None:
                _write_jsonl(
                    staging / "robustness-quantitative-diversified-ranking.jsonl",
                    quantitative["diversified"]["records"],
                )
        artifacts = {
            str(path.relative_to(staging)).replace("\\", "/"): sha256_file(path)
            for path in sorted(staging.rglob("*"))
            if path.is_file()
        }
        manifest = {
            "schema_version": "robustness-manifest/v1",
            "robustness_protocol_id": records["protocol"]["robustness_protocol_id"],
            "robustness_candidate_set_id": records["candidate_set"]["robustness_candidate_set_id"],
            "workload_policy_id": records["workload_policy"]["workload_policy_id"],
            "walk_forward_plan_id": records["walk_forward_plan"]["walk_forward_plan_id"],
            "monte_carlo_path_set_id": records["monte_carlo_path_set"].get(
                "monte_carlo_path_set_id"
            ),
            "robustness_qualified_set_id": None
            if records["qualified_set"] is None
            else records["qualified_set"]["robustness_qualified_set_id"],
            "pyarrow_version": PYARROW_VERSION,
            "artifacts": artifacts,
        }
        if quantitative is not None:
            manifest.update(
                quantitative_policy_id=quantitative["policy"]["quantitative_policy_id"],
                quantitative_protocol_id=quantitative["protocol"]["quantitative_protocol_id"],
                quantitative_qualified_set_id=quantitative["qualified_set"][
                    "quantitative_qualified_set_id"
                ],
            )
        if technical_completeness is not None:
            manifest.update(
                technical_completeness_policy_id=technical_completeness["policy"][
                    "technical_completeness_policy_id"
                ],
                technical_completeness_protocol_id=technical_completeness["protocol"][
                    "technical_completeness_protocol_id"
                ],
                ninth_increment_walk_forward_plan_id=technical_completeness[
                    "walk_forward_plan"
                ]["walk_forward_plan_id"],
            )
        manifest["robustness_export_id"] = identity("robustness-export/v1", manifest)
        write_canonical_json(staging / "robustness-manifest.json", manifest)
        os.replace(staging, destination)
        return manifest
    finally:
        if staging.exists():
            shutil.rmtree(staging)
