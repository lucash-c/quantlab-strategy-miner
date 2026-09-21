"""Explicit post-score candidate universe; invalid references fail closed."""

from __future__ import annotations

from typing import Any

from quantlab_core.errors import ContractError
from quantlab_mining.contracts import identity

from quantlab_robustness.contracts import CandidateSelectionV1
from quantlab_robustness.evidence import ScoreEvidence


def build_candidate_set(evidence: ScoreEvidence, policy: CandidateSelectionV1) -> dict[str, Any]:
    match policy.method:
        case "ALL_SCORED":
            selected = sorted(
                candidate
                for candidate, record in evidence.scores.items()
                if record["score_status"] == "SCORED"
            )
            source_id = evidence.manifest["score_set_id"]
        case "RAW_RANKING_TOP_N":
            selected = [row["candidate_id"] for row in evidence.raw[: policy.top_n]]
            source_id = evidence.manifest["raw_ranking_id"]
        case "DIVERSIFIED_RANKING_TOP_N":
            rows = sorted(
                (row for row in evidence.diversified if row["diversified_rank"] is not None),
                key=lambda row: row["diversified_rank"],
            )
            selected = [row["candidate_id"] for row in rows[: policy.top_n]]
            source_id = evidence.manifest["diversified_ranking_id"]
        case "TOP_N_OUTPUT":
            selected = list(evidence.top_n["emitted_candidate_ids"])
            source_id = evidence.manifest["top_n_output_id"]
        case "EXPLICIT_CANDIDATE_IDS":
            selected = list(policy.candidate_ids)
            source_id = evidence.manifest["score_set_id"]
        case _:  # pragma: no cover - closed by Pydantic
            raise AssertionError(policy.method)
    rows = []
    raw_by_id = {row["candidate_id"]: row for row in evidence.raw}
    diversified_by_id = {row["candidate_id"]: row for row in evidence.diversified}
    for source_ordinal, candidate in enumerate(selected, 1):
        score = evidence.scores.get(candidate)
        if score is None:
            raise ContractError(f"unknown robustness candidate: {candidate}")
        if score["score_status"] != "SCORED":
            raise ContractError(f"robustness candidate is not SCORED: {candidate}")
        raw = raw_by_id.get(candidate)
        diversified = diversified_by_id.get(candidate)
        rows.append(
            {
                "candidate_id": candidate,
                "strategy_score_id": score["strategy_score_id"],
                "score_units": score["score_units"],
                "score_scale": score["score_scale"],
                "semantic_group_id": score["semantic_group_id"],
                "source_ordinal": source_ordinal,
                "original_raw_rank": None if raw is None else raw["raw_rank"],
                "original_diversified_rank": None
                if diversified is None
                else diversified["diversified_rank"],
            }
        )
    if len({row["candidate_id"] for row in rows}) != len(rows):
        raise ContractError("robustness candidate selection produced duplicates")
    record = {
        "schema_version": "robustness-candidate-set/v1",
        "score_export_id": evidence.manifest["score_export_id"],
        "score_set_id": evidence.manifest["score_set_id"],
        "selection_policy_id": policy.selection_policy_id,
        "selection": policy.model_dump(mode="json"),
        "source_artifact_id": source_id,
        "candidate_count": len(rows),
        "candidates": sorted(rows, key=lambda row: row["candidate_id"]),
    }
    record["robustness_candidate_set_id"] = identity("robustness-candidate-set/v1", record)
    return record
