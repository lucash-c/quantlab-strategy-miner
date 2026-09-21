"""Orchestrate scoring from completed research evidence, never from market history."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from quantlab_core.canonical import write_canonical_json
from quantlab_mining.contracts import identity

from quantlab_scoring.cache import StrategyScoreCache
from quantlab_scoring.contracts import (
    ResearchDiversityPolicyV1,
    ResearchRankingPolicyV1,
    ResearchScorePolicyV1,
)
from quantlab_scoring.engine import score_candidate
from quantlab_scoring.evidence import load_research_evidence
from quantlab_scoring.export import export_scoring
from quantlab_scoring.ranking import (
    diversify,
    raw_ranking,
    score_distribution,
    selection_pressure,
    top_n,
)
from quantlab_scoring.semantic import semantic_group_record


class ControlledScoringInterruption(RuntimeError):
    """Acceptance-only interruption after durable score-cache writes."""


def run_scoring(
    research_directory: Path,
    score_policy: ResearchScorePolicyV1,
    ranking_policy: ResearchRankingPolicyV1,
    diversity_policy: ResearchDiversityPolicyV1,
    *,
    top_n_value: int,
    cache_root: Path,
    output: Path,
    stop_after: int | None = None,
) -> dict[str, Any]:
    if type(top_n_value) is not int or top_n_value < 1:
        raise ValueError("top_n must be an explicit positive integer")
    evidence = load_research_evidence(research_directory)
    cache = StrategyScoreCache(cache_root)
    started = time.perf_counter()
    scores = []
    built = 0
    reused = 0
    try:
        for processed, candidate in enumerate(sorted(evidence.candidates), 1):
            computed = score_candidate(
                candidate_id=candidate,
                strategy=evidence.candidates[candidate],
                status=evidence.statuses[candidate],
                discovery=evidence.discovery_results.get(candidate),
                validation=evidence.validation_results.get(candidate),
                discovery_gate=evidence.discovery_gates.get(candidate),
                validation_gate=evidence.validation_gates.get(candidate),
                comparison=evidence.comparisons.get(candidate),
                policy=score_policy,
            )
            cached = cache.get(computed["strategy_score_id"])
            if cached is None:
                cache.put(computed)
                record = computed
                built += 1
            else:
                record = cached
                reused += 1
            scores.append(record)
            if stop_after is not None and processed >= stop_after:
                raise ControlledScoringInterruption(
                    f"controlled scoring interruption after {processed} candidates"
                )

        score_set = {
            "schema_version": "research-score-set/v1",
            "score_policy_id": score_policy.score_policy_id,
            "candidates": [
                {
                    "candidate_id": record["candidate_id"],
                    "score_status": record["score_status"],
                    "strategy_score_id": record["strategy_score_id"],
                }
                for record in sorted(scores, key=lambda item: item["candidate_id"])
            ],
        }
        score_set["score_set_id"] = identity("research-score-set/v1", score_set)
        raw = raw_ranking(scores, ranking_policy)
        diversified = diversify(raw, diversity_policy)
        top = top_n(diversified, top_n_value)
        distribution = score_distribution(scores)
        pressure = selection_pressure(scores, raw, diversified, top)
        group_members: dict[str, list[str]] = {}
        group_signatures: dict[str, dict[str, Any]] = {}
        for candidate, strategy in evidence.candidates.items():
            score = next(item for item in scores if item["candidate_id"] == candidate)
            group = score["semantic_group_id"]
            group_members.setdefault(group, []).append(candidate)
            group_signatures.setdefault(group, semantic_group_record(strategy))
        semantic_groups = [
            {
                "schema_version": "research-semantic-group-membership/v1",
                "semantic_group_id": group,
                "signature": group_signatures[group],
                "candidate_ids": sorted(members),
                "candidate_count": len(members),
            }
            for group, members in group_members.items()
        ]
        manifest = export_scoring(
            destination=output,
            score_policy=score_policy,
            ranking_policy=ranking_policy,
            diversity_policy=diversity_policy,
            input_manifest=evidence.score_input_manifest(),
            score_set=score_set,
            scores=scores,
            semantic_groups=semantic_groups,
            raw=raw,
            diversified=diversified,
            top=top,
            distribution=distribution,
            pressure=pressure,
        )
        operational = {
            "schema_version": "research-score-operational/v1",
            "candidate_records": len(scores),
            "score_cache_built": built,
            "score_cache_reused": reused,
            "elapsed_seconds": str(time.perf_counter() - started),
            "scientific_identity_excludes_this_record": True,
        }
        write_canonical_json(output.parent / (output.name + ".operational.json"), operational)
        return manifest
    finally:
        cache.close()
