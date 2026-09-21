"""Operational, non-normative scorer benchmark versus candidate count."""

from __future__ import annotations

import argparse
import copy
import json
import time
from pathlib import Path

from quantlab_scoring.contracts import (
    ResearchDiversityPolicyV1,
    ResearchRankingPolicyV1,
    ResearchScorePolicyV1,
    load_policy,
)
from quantlab_scoring.engine import score_candidate
from quantlab_scoring.evidence import load_research_evidence
from quantlab_scoring.ranking import diversify, raw_ranking


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--research", type=Path, required=True)
    result.add_argument("--score-policy", type=Path, required=True)
    result.add_argument("--ranking-policy", type=Path, required=True)
    result.add_argument("--diversity-policy", type=Path, required=True)
    result.add_argument("--sizes", type=int, nargs="+", default=[48, 160, 1000, 10000])
    return result


def relabel(record, candidate):
    if record is None:
        return None
    result = copy.deepcopy(record)
    result["candidate_id"] = candidate
    return result


def main() -> None:
    args = parser().parse_args()
    evidence = load_research_evidence(args.research)
    score_policy = load_policy(args.score_policy, ResearchScorePolicyV1)
    ranking_policy = load_policy(args.ranking_policy, ResearchRankingPolicyV1)
    diversity_policy = load_policy(args.diversity_policy, ResearchDiversityPolicyV1)
    eligible = [
        candidate
        for candidate, status in evidence.statuses.items()
        if status["discovery_status"] == "DISCOVERY_PASSED"
        and status["validation_status"] == "VALIDATION_PASSED"
    ]
    if not eligible:
        raise SystemExit("benchmark requires at least one eligible candidate")
    results = []
    for size in args.sizes:
        scores = []
        started = time.perf_counter()
        for index in range(size):
            source = eligible[index % len(eligible)]
            candidate = f"benchmark:{index:05d}"
            status = relabel(evidence.statuses[source], candidate)
            scores.append(
                score_candidate(
                    candidate_id=candidate,
                    strategy=evidence.candidates[source],
                    status=status,
                    discovery=relabel(evidence.discovery_results[source], candidate),
                    validation=relabel(evidence.validation_results[source], candidate),
                    discovery_gate=relabel(evidence.discovery_gates[source], candidate),
                    validation_gate=relabel(evidence.validation_gates[source], candidate),
                    comparison=relabel(evidence.comparisons[source], candidate),
                    policy=score_policy,
                )
            )
        score_seconds = time.perf_counter() - started
        started = time.perf_counter()
        raw = raw_ranking(scores, ranking_policy)
        diversified = diversify(raw, diversity_policy)
        rank_seconds = time.perf_counter() - started
        results.append(
            {
                "candidates": size,
                "score_seconds": str(score_seconds),
                "ranking_and_diversity_seconds": str(rank_seconds),
                "ranked": len(raw["records"]),
                "diversity_selected": sum(
                    row["selection_status"] == "DIVERSITY_SELECTED"
                    for row in diversified["records"]
                ),
            }
        )
    print(
        json.dumps(
            {
                "schema_version": "research-score-benchmark/v1",
                "non_normative": True,
                "market_replay": False,
                "results": results,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
