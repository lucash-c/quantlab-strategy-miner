"""Canonical aggregate experiment files rebuilt from logical results, never index bytes."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path

import pyarrow as pa
from quantlab_core.canonical import canonical_json_bytes, sha256_file, write_canonical_json
from quantlab_core.errors import ContractError
from quantlab_data.parquet import PYARROW_VERSION, write_record_batches
from quantlab_mining.contracts import identity

RESEARCH_EXPORT_VERSION = "research-export/v1"


def export_research(
    db: sqlite3.Connection, destination: Path, records: dict, experiment: dict
) -> dict:
    if destination.exists():
        raise ContractError("refusing to overwrite completed research export")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".research-", dir=destination.parent))
    try:
        for name, record in records.items():
            write_canonical_json(staging / name, record)
        for name, stage in (
            ("discovery-results.jsonl", "DISCOVERY"),
            ("discovery-gate-results.jsonl", "DISCOVERY"),
        ):
            table = "research_results" if name == "discovery-results.jsonl" else "research_gates"
            with (staging / name).open("wb") as output:
                for row in db.execute(
                    f"SELECT payload FROM {table} WHERE stage=? ORDER BY cid", (stage,)
                ):
                    output.write(row[0].encode())
        schema = pa.schema(
            [
                pa.field("candidate_id", pa.string(), nullable=False),
                pa.field("record_json", pa.string(), nullable=False),
            ],
            metadata={b"quantlab.schema": b"research-result/v1"},
        )
        queries = {
            "discovery-results.parquet": (
                "SELECT cid,payload FROM research_results WHERE stage='DISCOVERY' ORDER BY cid",
                (),
            ),
            "validation-results.parquet": (
                "SELECT cid,payload FROM research_results WHERE stage='VALIDATION' ORDER BY cid",
                (),
            ),
            "discovery-gate-results.parquet": (
                "SELECT cid,payload FROM research_gates WHERE stage='DISCOVERY' ORDER BY cid",
                (),
            ),
            "validation-gate-results.parquet": (
                "SELECT cid,payload FROM research_gates WHERE stage='VALIDATION' ORDER BY cid",
                (),
            ),
            "discovery-validation-comparison.parquet": (
                "SELECT cid,payload FROM research_comparisons ORDER BY cid",
                (),
            ),
            "candidate-session-evaluations.parquet": (
                "SELECT cid,payload FROM research_session_records ORDER BY cid,trading_date",
                (),
            ),
        }
        for name, (query, params) in queries.items():
            write_record_batches(
                staging / name,
                schema,
                (
                    {"candidate_id": cid, "record_json": payload.rstrip("\n")}
                    for cid, payload in db.execute(query, params)
                ),
            )
        with (staging / "candidates.jsonl").open("wb") as output:
            for cid, payload in db.execute("SELECT id,strategy FROM candidates ORDER BY id"):
                strategy = json.loads(payload)
                strategy["cost_model"] = records["research-protocol.json"]["cost_model"]
                strategy["slippage_model"] = records["research-protocol.json"]["slippage_model"]
                output.write(canonical_json_bytes({"candidate_id": cid, "strategy": strategy}))
        with (staging / "candidate-provenance.jsonl").open("wb") as output:
            for cid, origin, count in db.execute("SELECT * FROM provenance ORDER BY id,origin"):
                output.write(
                    canonical_json_bytes(
                        {"candidate_id": cid, "origin": json.loads(origin), "multiplicity": count}
                    )
                )
        with (staging / "status.jsonl").open("wb") as output:
            for record in experiment["statuses"]:
                output.write(canonical_json_bytes(record))
        audit = staging / "audit"
        audit.mkdir()
        for stage in ("DISCOVERY", "VALIDATION"):
            for kind in ("ledger", "journal"):
                output = None
                try:
                    for index, (cid, ordinal, payload) in enumerate(
                        db.execute(
                            "SELECT cid,ordinal,payload FROM research_audit "
                            "WHERE stage=? AND kind=? "
                            "ORDER BY cid,ordinal",
                            (stage, kind),
                        )
                    ):
                        if index % 10_000 == 0:
                            if output:
                                output.close()
                            output = (
                                audit / f"{stage.lower()}-{kind}-{index // 10_000:05d}.jsonl"
                            ).open("wb")
                        output.write(
                            canonical_json_bytes(
                                {
                                    "candidate_id": cid,
                                    "ordinal": ordinal,
                                    "record": json.loads(payload),
                                }
                            )
                        )
                finally:
                    if output:
                        output.close()
        write_canonical_json(staging / "validation-experiment.json", experiment)
        artifacts = {
            str(p.relative_to(staging)).replace("\\", "/"): sha256_file(p)
            for p in sorted(staging.rglob("*"))
            if p.is_file()
        }
        manifest = {
            "schema_version": "validation-manifest/v1",
            "validation_experiment_id": experiment["validation_experiment_id"],
            "export_version": RESEARCH_EXPORT_VERSION,
            "pyarrow_version": PYARROW_VERSION,
            "artifacts": artifacts,
        }
        manifest["export_id"] = identity("research-export/v1", manifest)
        write_canonical_json(staging / "validation-manifest.json", manifest)
        os.replace(staging, destination)
        return manifest
    finally:
        if staging.exists():
            shutil.rmtree(staging)
