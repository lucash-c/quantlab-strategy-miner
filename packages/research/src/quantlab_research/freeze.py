"""Atomic immutable Discovery evidence; full validation precedes any holdout access."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

from quantlab_core.canonical import canonical_json_bytes, sha256_file, write_canonical_json
from quantlab_core.errors import ContractError
from quantlab_mining.contracts import identity


def pass_set(
    partition_id: str, gate_policy_id: str, evaluations: list[dict], gates: list[dict]
) -> dict:
    approved = {g["candidate_id"]: g for g in gates if g["result"] == "PASS"}
    members = [
        {
            "candidate_id": r["candidate_id"],
            "discovery_evaluation_id": r["partition_evaluation_id"],
            "evaluation_fingerprint": r["result_fingerprint"],
            "gate_fingerprint": approved[r["candidate_id"]]["result_fingerprint"],
        }
        for r in sorted(evaluations, key=lambda r: r["candidate_id"])
        if r["candidate_id"] in approved
    ]
    record = {
        "schema_version": "discovery-pass-set/v1",
        "discovery_partition_id": partition_id,
        "discovery_gate_policy_id": gate_policy_id,
        "members": members,
        "candidate_ids": [m["candidate_id"] for m in members],
    }
    record["discovery_pass_set_id"] = identity("discovery-pass-set/v1", record)
    return record


def validate_freeze(directory: Path, expected: dict | None = None) -> dict:
    try:
        record = json.loads((directory / "discovery-freeze.json").read_text())
        eid = record["discovery_freeze_id"]
        payload = {k: v for k, v in record.items() if k != "discovery_freeze_id"}
        if identity("discovery-freeze/v1", payload) != eid or (expected and record != expected):
            raise ContractError("Discovery freeze identity mismatch")
        for name, fingerprint in record["artifacts"].items():
            path = (directory / name).resolve()
            if not path.is_relative_to(directory.resolve()) or sha256_file(path) != fingerprint:
                raise ContractError("Discovery freeze artifact mismatch")
        return record
    except (OSError, KeyError, ValueError) as exc:
        raise ContractError(f"invalid Discovery freeze: {exc}") from exc


def publish_freeze(
    directory: Path,
    protocol: dict,
    split: dict,
    approved: dict,
    evaluations: list[dict],
    gates: list[dict],
) -> dict:
    directory.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".freeze-", dir=directory.parent))
    try:
        for name, rows in (
            ("discovery-results.jsonl", evaluations),
            ("discovery-gate-results.jsonl", gates),
        ):
            with (staging / name).open("wb") as output:
                for row in sorted(rows, key=lambda r: r["candidate_id"]):
                    output.write(canonical_json_bytes(row))
        write_canonical_json(staging / "discovery-pass-set.json", approved)
        record = {
            "schema_version": "discovery-freeze/v1",
            "research_protocol_id": protocol["research_protocol_id"],
            "search_space_id": protocol["search_space_id"],
            "candidate_set_id": protocol["candidate_set_id"],
            "split_plan_id": split["split_plan_id"],
            "discovery_partition_id": split["discovery"]["partition_id"],
            "discovery_gate_policy_id": approved["discovery_gate_policy_id"],
            "discovery_pass_set_id": approved["discovery_pass_set_id"],
            "artifacts": {p.name: sha256_file(p) for p in sorted(staging.iterdir())},
        }
        record["discovery_freeze_id"] = identity("discovery-freeze/v1", record)
        write_canonical_json(staging / "discovery-freeze.json", record)
        validate_freeze(staging, record)
        if directory.exists():
            return validate_freeze(directory, record)
        os.replace(staging, directory)
        return validate_freeze(directory, record)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
