"""Explicit raw ranking, structural round-robin diversity and Top N derivation."""

from __future__ import annotations

from collections import defaultdict
from functools import cmp_to_key
from typing import Any

from quantlab_core.numeric import CanonicalRational
from quantlab_mining.contracts import identity

from quantlab_scoring.contracts import ResearchDiversityPolicyV1, ResearchRankingPolicyV1
from quantlab_scoring.engine import rational


def _compare_exact(left: dict[str, str], right: dict[str, str]) -> int:
    return rational(left).compare(rational(right))


def raw_ranking(
    scores: list[dict[str, Any]], policy: ResearchRankingPolicyV1
) -> dict[str, Any]:
    eligible = [record for record in scores if record["score_status"] == "SCORED"]

    def compare(left: dict[str, Any], right: dict[str, Any]) -> int:
        for rule in policy.tie_breakers:
            if rule.field == "candidate_id":
                result = (left["candidate_id"] > right["candidate_id"]) - (
                    left["candidate_id"] < right["candidate_id"]
                )
            elif rule.field == "score_units":
                result = (left["score_units"] > right["score_units"]) - (
                    left["score_units"] < right["score_units"]
                )
            else:
                result = _compare_exact(
                    left["ranking_values"][rule.field], right["ranking_values"][rule.field]
                )
            if result:
                return -result if rule.direction == "DESC" else result
        return 0

    ordered = sorted(eligible, key=cmp_to_key(compare))
    rows = [
        {
            "raw_rank": index,
            "candidate_id": record["candidate_id"],
            "strategy_score_id": record["strategy_score_id"],
            "score_units": record["score_units"],
            "score_scale": record["score_scale"],
            "tie_breaker_values": record["ranking_values"],
            "semantic_group_id": record["semantic_group_id"],
        }
        for index, record in enumerate(ordered, 1)
    ]
    result = {
        "schema_version": "research-raw-ranking/v1",
        "ranking_policy_id": policy.ranking_policy_id,
        "tie_breakers": [item.model_dump(mode="json") for item in policy.tie_breakers],
        "records": rows,
    }
    result["raw_ranking_id"] = identity("research-raw-ranking/v1", result)
    return result


def diversify(
    raw: dict[str, Any], policy: ResearchDiversityPolicyV1
) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in raw["records"]:
        groups[row["semantic_group_id"]].append(row)
    ordered_groups = sorted(
        groups,
        key=lambda group: (groups[group][0]["raw_rank"], group),
    )
    selected: list[dict[str, Any]] = []
    for round_index in range(policy.max_per_semantic_group):
        for group in ordered_groups:
            queue = groups[group]
            if round_index < len(queue):
                selected.append(queue[round_index])
    diversified_ranks = {
        row["candidate_id"]: index for index, row in enumerate(selected, 1)
    }
    records = []
    for row in raw["records"]:
        selected_rank = diversified_ranks.get(row["candidate_id"])
        records.append(
            {
                **row,
                "diversified_rank": selected_rank,
                "selection_status": "DIVERSITY_SELECTED"
                if selected_rank is not None
                else "DIVERSITY_GROUP_LIMIT",
                "selection_reason": None
                if selected_rank is not None
                else "DIVERSITY_GROUP_LIMIT",
                "semantic_group_size": len(groups[row["semantic_group_id"]]),
            }
        )
    result = {
        "schema_version": "research-diversified-ranking/v1",
        "raw_ranking_id": raw["raw_ranking_id"],
        "diversity_policy_id": policy.diversity_policy_id,
        "algorithm": policy.algorithm,
        "records": records,
    }
    result["diversified_ranking_id"] = identity(
        "research-diversified-ranking/v1", result
    )
    return result


def top_n(diversified: dict[str, Any], top_n_value: int) -> dict[str, Any]:
    if type(top_n_value) is not int or top_n_value < 1:
        raise ValueError("top_n must be an explicit positive integer")
    records = []
    selected = sorted(
        (row for row in diversified["records"] if row["diversified_rank"] is not None),
        key=lambda row: row["diversified_rank"],
    )
    emitted = [row["candidate_id"] for row in selected[:top_n_value]]
    emitted_set = set(emitted)
    for row in diversified["records"]:
        rank = row["diversified_rank"]
        if rank is None:
            status = "NOT_SELECTABLE_DIVERSITY"
        elif row["candidate_id"] in emitted_set:
            status = "EMITTED"
        else:
            status = "NOT_EMITTED_TOP_N_LIMIT"
        records.append(
            {
                "candidate_id": row["candidate_id"],
                "raw_rank": row["raw_rank"],
                "diversified_rank": rank,
                "top_n_status": status,
            }
        )
    result = {
        "schema_version": "research-top-n/v1",
        "title": "Top N Research Strategies",
        "warning": "RESEARCH_ONLY_NOT_LIVE_TRADING_APPROVAL",
        "diversified_ranking_id": diversified["diversified_ranking_id"],
        "top_n": top_n_value,
        "emitted_count": len(emitted),
        "emitted_candidate_ids": emitted,
        "records": records,
    }
    result["top_n_output_id"] = identity("research-top-n/v1", result)
    return result


def score_distribution(scores: list[dict[str, Any]]) -> dict[str, Any]:
    values = sorted(
        record["score_units"] for record in scores if record["score_status"] == "SCORED"
    )
    buckets = [
        {
            "lower_inclusive_units": index * 100_000,
            "upper_exclusive_units": None if index == 9 else (index + 1) * 100_000,
            "upper_inclusive_units": 1_000_000 if index == 9 else None,
            "count": 0,
        }
        for index in range(10)
    ]
    for value in values:
        buckets[min(value // 100_000, 9)]["count"] += 1
    median = None
    if values:
        middle = len(values) // 2
        median = (
            CanonicalRational(values[middle], 1)
            if len(values) % 2
            else CanonicalRational(values[middle - 1] + values[middle], 2)
        ).to_record()
    return {
        "schema_version": "research-score-distribution/v1",
        "descriptive_only": True,
        "score_scale": 10000,
        "count": len(values),
        "min_score_units": None if not values else values[0],
        "max_score_units": None if not values else values[-1],
        "median_score_units": median,
        "buckets": buckets,
    }


def selection_pressure(
    scores: list[dict[str, Any]],
    raw: dict[str, Any],
    diversified: dict[str, Any],
    top: dict[str, Any],
) -> dict[str, Any]:
    counts = {status: 0 for status in ("SCORED", "NOT_SCORABLE", "NOT_ELIGIBLE")}
    for record in scores:
        counts[record["score_status"]] += 1
    return {
        "schema_version": "research-score-selection-pressure/v1",
        "candidate_set": len(scores),
        "scored": counts["SCORED"],
        "not_scorable": counts["NOT_SCORABLE"],
        "not_eligible": counts["NOT_ELIGIBLE"],
        "raw_ranked": len(raw["records"]),
        "diversity_selected": sum(
            row["selection_status"] == "DIVERSITY_SELECTED"
            for row in diversified["records"]
        ),
        "top_n_emitted": top["emitted_count"],
    }
