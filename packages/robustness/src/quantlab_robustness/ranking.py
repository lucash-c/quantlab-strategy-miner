"""Derived filter/ranking that preserves every Research Score v1 fact."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from quantlab_mining.contracts import identity
from quantlab_scoring.contracts import ResearchDiversityPolicyV1


def qualified_set(assessments: list[dict[str, Any]], gate_policy_id: str) -> dict[str, Any]:
    passed = sorted(
        (item for item in assessments if item["status"] == "ROBUSTNESS_PASSED"),
        key=lambda item: item["candidate_id"],
    )
    record = {
        "schema_version": "robustness-qualified-set/v1",
        "robustness_gate_policy_id": gate_policy_id,
        "candidate_count": len(passed),
        "candidates": [
            {
                "candidate_id": item["candidate_id"],
                "strategy_score_id": item["strategy_score_id"],
                "robustness_assessment_id": item["robustness_assessment_id"],
            }
            for item in passed
        ],
    }
    record["robustness_qualified_set_id"] = identity("robustness-qualified-set/v1", record)
    return record


def qualified_ranking(raw_rows: list[dict[str, Any]], qualified: dict[str, Any]) -> dict[str, Any]:
    assessments = {
        item["candidate_id"]: item["robustness_assessment_id"] for item in qualified["candidates"]
    }
    selected = [row for row in raw_rows if row["candidate_id"] in assessments]
    records = [
        {
            "robustness_qualified_rank": rank,
            "original_raw_rank": row["raw_rank"],
            "candidate_id": row["candidate_id"],
            "strategy_score_id": row["strategy_score_id"],
            "score_units": row["score_units"],
            "score_scale": row["score_scale"],
            "semantic_group_id": row["semantic_group_id"],
            "robustness_assessment_id": assessments[row["candidate_id"]],
        }
        for rank, row in enumerate(selected, 1)
    ]
    result = {
        "schema_version": "robustness-qualified-ranking/v1",
        "robustness_qualified_set_id": qualified["robustness_qualified_set_id"],
        "records": records,
        "research_score_recomputed": False,
        "original_raw_rank_recomputed": False,
    }
    result["robustness_ranking_id"] = identity("robustness-qualified-ranking/v1", result)
    return result


def diversify_qualified(
    ranking: dict[str, Any], policy: ResearchDiversityPolicyV1
) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in ranking["records"]:
        groups[row["semantic_group_id"]].append(row)
    ordered_groups = sorted(
        groups,
        key=lambda group: (groups[group][0]["robustness_qualified_rank"], group),
    )
    selected = []
    for round_index in range(policy.max_per_semantic_group):
        for group in ordered_groups:
            if round_index < len(groups[group]):
                selected.append(groups[group][round_index])
    ranks = {row["candidate_id"]: rank for rank, row in enumerate(selected, 1)}
    records = [
        {
            **row,
            "robustness_diversified_rank": ranks.get(row["candidate_id"]),
            "selection_status": "DIVERSITY_SELECTED"
            if row["candidate_id"] in ranks
            else "DIVERSITY_GROUP_LIMIT",
        }
        for row in ranking["records"]
    ]
    result = {
        "schema_version": "robustness-diversified-ranking/v1",
        "robustness_ranking_id": ranking["robustness_ranking_id"],
        "diversity_policy_id": policy.diversity_policy_id,
        "records": records,
    }
    result["robustness_diversified_ranking_id"] = identity(
        "robustness-diversified-ranking/v1", result
    )
    return result


def robustness_top_n(diversified: dict[str, Any], top_n: int) -> dict[str, Any]:
    if type(top_n) is not int or top_n < 1:
        raise ValueError("robustness Top N must be an explicit positive integer")
    selected = sorted(
        (row for row in diversified["records"] if row["robustness_diversified_rank"] is not None),
        key=lambda row: row["robustness_diversified_rank"],
    )[:top_n]
    record = {
        "schema_version": "robustness-top-n/v1",
        "title": "Top N Robustness-Qualified Research Strategies",
        "warning": "RESEARCH_ONLY_NOT_LIVE_TRADING_APPROVAL",
        "robustness_diversified_ranking_id": diversified["robustness_diversified_ranking_id"],
        "top_n": top_n,
        "emitted_count": len(selected),
        "emitted_candidate_ids": [row["candidate_id"] for row in selected],
    }
    record["robustness_shortlist_id"] = identity("robustness-top-n/v1", record)
    return record
