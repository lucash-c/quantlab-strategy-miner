from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from quantlab_core.canonical import canonical_json_bytes, sha256_file, write_canonical_json
from quantlab_core.strategy_v3 import StrategyDefinitionV3
from quantlab_mining.canonicalization import named_candidate
from quantlab_mining.contracts import identity
from quantlab_scoring.contracts import (
    ResearchDiversityPolicyV1,
    ResearchRankingPolicyV1,
    ResearchScorePolicyV1,
    load_policy,
)


def score_policy() -> ResearchScorePolicyV1:
    return load_policy(Path("examples/scoring/research-score-v1.json"), ResearchScorePolicyV1)


def ranking_policy() -> ResearchRankingPolicyV1:
    return load_policy(
        Path("examples/scoring/research-ranking-v1.json"), ResearchRankingPolicyV1
    )


def diversity_policy() -> ResearchDiversityPolicyV1:
    return load_policy(
        Path("examples/scoring/research-diversity-v1.json"), ResearchDiversityPolicyV1
    )


def strategy(*, stop="100", timeframe="1m", direction="BUY") -> StrategyDefinitionV3:
    record = __import__("json").loads(
        Path("examples/strategies/win-features-validation-v3.json").read_text()
    )
    record["stop_loss"]["value"] = stop
    record["timeframe"] = timeframe
    record["direction"] = direction
    return StrategyDefinitionV3.model_validate(record)


def defined(numerator, denominator=1):
    return {
        "status": "DEFINED",
        "value": {"numerator": str(numerator), "denominator": str(denominator)},
        "reason": None,
    }


def undefined(reason):
    return {"status": "UNDEFINED", "value": None, "reason": reason}


def scoring_evidence(candidate_id="candidate-A", *, midpoint=False):
    if midpoint:
        validation_metrics = {
            "trades": defined(3),
            "average_trade": defined(5),
            "net_pnl_per_session": defined(50),
            "win_rate": defined(1, 2),
            "max_drawdown": defined(600),
            "max_consecutive_losses": defined(3),
            "positive_session_rate": defined(1, 2),
            "total_sessions": defined(4),
            "losing_sessions": defined(1),
            "active_session_rate": defined(3, 4),
            "trades_per_session": defined(3, 4),
            "largest_profitable_session_share": defined(3, 4),
            "largest_trade_count_session_share": defined(3, 4),
        }
        comparison_metrics = {
            "average_trade_delta": defined(-55, 2),
            "net_pnl_per_session_delta": defined(-225, 2),
            "win_rate_delta": defined(-3, 20),
            "trades_per_session_ratio": defined(1, 2),
            "active_session_rate_delta": defined(-3, 20),
        }
    else:
        validation_metrics = {
            "trades": defined(60),
            "average_trade": defined(20),
            "net_pnl_per_session": defined(200),
            "win_rate": defined(3, 5),
            "max_drawdown": defined(200),
            "max_consecutive_losses": defined(1),
            "positive_session_rate": defined(2, 3),
            "total_sessions": defined(6),
            "losing_sessions": defined(0),
            "active_session_rate": defined(1),
            "trades_per_session": defined(10),
            "largest_profitable_session_share": defined(1, 2),
            "largest_trade_count_session_share": defined(1, 2),
        }
        comparison_metrics = {
            "average_trade_delta": defined(-5),
            "net_pnl_per_session_delta": defined(-25),
            "win_rate_delta": defined(-1, 20),
            "trades_per_session_ratio": defined(4, 5),
            "active_session_rate_delta": defined(-1, 20),
        }
    validation_metrics.update(
        profit_factor=undefined("NO_LOSSES"),
        payoff_ratio=undefined("NO_LOSSES"),
        average_loss=undefined("NO_LOSSES"),
    )
    discovery = {
        "candidate_id": candidate_id,
        "metrics": {},
        "result_fingerprint": "sha256:discovery",
    }
    validation = {
        "candidate_id": candidate_id,
        "metrics": validation_metrics,
        "result_fingerprint": "sha256:validation",
    }
    discovery_gate = {
        "candidate_id": candidate_id,
        "result": "PASS",
        "result_fingerprint": "sha256:discovery-gate",
    }
    validation_gate = {
        "candidate_id": candidate_id,
        "result": "PASS",
        "result_fingerprint": "sha256:validation-gate",
    }
    comparison = {
        "candidate_id": candidate_id,
        "metrics": comparison_metrics,
        "result_fingerprint": "sha256:comparison",
    }
    status = {
        "candidate_id": candidate_id,
        "discovery_status": "DISCOVERY_PASSED",
        "validation_status": "VALIDATION_PASSED",
    }
    return {
        "candidate_id": candidate_id,
        "strategy": strategy(),
        "status": status,
        "discovery": discovery,
        "validation": validation,
        "discovery_gate": discovery_gate,
        "validation_gate": validation_gate,
        "comparison": comparison,
    }


def _write_jsonl(path, records):
    with path.open("wb") as output:
        for record in records:
            output.write(canonical_json_bytes(record))


def _write_records(path, records):
    schema = pa.schema(
        [
            pa.field("candidate_id", pa.string(), nullable=False),
            pa.field("record_json", pa.string(), nullable=False),
        ]
    )
    rows = [
        {
            "candidate_id": record["candidate_id"],
            "record_json": canonical_json_bytes(record).decode().rstrip("\n"),
        }
        for record in records
    ]
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), path)


def write_research_evidence(root: Path, *, timeframes=("1m",), all_scored=False):
    root.mkdir(parents=True)
    candidates = []
    for index, role in enumerate(("A", "B", "C", "D")):
        base = strategy(timeframe=timeframes[index % len(timeframes)])
        base = base.model_copy(
            update={
                "take_profit": base.take_profit.model_copy(update={"value": str(100 + index)})
            }
        )
        candidate, named = named_candidate(base)
        candidates.append((role, candidate, named))
    by_role = {role: candidate for role, candidate, _ in candidates}
    dresults = []
    dgates = []
    vresults = []
    vgates = []
    comparisons = []
    statuses = []
    for role, candidate, _ in candidates:
        evidence = scoring_evidence(candidate, midpoint=role == "B")
        discovery = {
            "schema_version": "research-partition-evaluation/v1",
            "candidate_id": candidate,
            "metrics": evidence["discovery"]["metrics"],
            "partition_evaluation_id": "discovery-" + candidate,
        }
        discovery["result_fingerprint"] = identity(
            "research-partition-result/v1", discovery
        )
        dresults.append(discovery)
        discovery_pass = all_scored or role != "C"
        dgate = {
            "candidate_id": candidate,
            "evaluation_id": discovery["partition_evaluation_id"],
            "gate_policy_id": "discovery-policy",
            "criteria": [],
            "failures": [] if discovery_pass else [{"reason": "THRESHOLD_NOT_MET"}],
            "information": [],
            "result": "PASS" if discovery_pass else "FAIL",
        }
        dgate["result_fingerprint"] = identity("research-gate-result/v1", dgate)
        dgates.append(dgate)
        if not discovery_pass:
            statuses.append(
                {
                    "candidate_id": candidate,
                    "evaluation_status": "BACKTESTED",
                    "discovery_status": "DISCOVERY_FAILED_GATE",
                    "validation_status": "VALIDATION_NOT_RUN",
                    "reason": "NOT_IN_DISCOVERY_PASS_SET",
                }
            )
            continue
        validation_metrics = evidence["validation"]["metrics"]
        comparison_metrics = evidence["comparison"]["metrics"]
        if role == "D" and not all_scored:
            validation_metrics = dict(validation_metrics)
            validation_metrics.update(
                trades=defined(0),
                average_trade=undefined("NO_TRADES"),
                win_rate=undefined("NO_TRADES"),
                largest_trade_count_session_share=undefined("NO_TRADES"),
            )
            comparison_metrics = dict(comparison_metrics)
            comparison_metrics["average_trade_delta"] = undefined(
                "SOURCE_METRIC_UNDEFINED"
            )
        validation = {
            "schema_version": "research-partition-evaluation/v1",
            "candidate_id": candidate,
            "metrics": validation_metrics,
            "partition_evaluation_id": "validation-" + candidate,
        }
        validation["result_fingerprint"] = identity(
            "research-partition-result/v1", validation
        )
        vresults.append(validation)
        vgate = {
            "candidate_id": candidate,
            "evaluation_id": validation["partition_evaluation_id"],
            "gate_policy_id": "validation-policy",
            "criteria": [],
            "failures": [],
            "information": [],
            "result": "PASS",
        }
        vgate["result_fingerprint"] = identity("research-gate-result/v1", vgate)
        vgates.append(vgate)
        comparison = {
            "schema_version": "discovery-validation-comparison/v1",
            "candidate_id": candidate,
            "metrics": comparison_metrics,
            "discovery_evaluation_id": discovery["partition_evaluation_id"],
            "validation_evaluation_id": validation["partition_evaluation_id"],
        }
        comparison["result_fingerprint"] = identity(
            "research-comparison-result/v1", comparison
        )
        comparisons.append(comparison)
        statuses.append(
            {
                "candidate_id": candidate,
                "evaluation_status": "BACKTESTED",
                "discovery_status": "DISCOVERY_PASSED",
                "validation_status": "VALIDATION_PASSED",
                "reason": None,
            }
        )

    _write_jsonl(
        root / "candidates.jsonl",
        [
            {"candidate_id": candidate, "strategy": named.model_dump(mode="json")}
            for _, candidate, named in sorted(candidates, key=lambda item: item[1])
        ],
    )
    _write_jsonl(root / "status.jsonl", sorted(statuses, key=lambda item: item["candidate_id"]))
    _write_jsonl(
        root / "discovery-results.jsonl",
        sorted(dresults, key=lambda item: item["candidate_id"]),
    )
    _write_jsonl(
        root / "discovery-gate-results.jsonl",
        sorted(dgates, key=lambda item: item["candidate_id"]),
    )
    _write_records(
        root / "validation-results.parquet",
        sorted(vresults, key=lambda item: item["candidate_id"]),
    )
    _write_records(
        root / "validation-gate-results.parquet",
        sorted(vgates, key=lambda item: item["candidate_id"]),
    )
    _write_records(
        root / "discovery-validation-comparison.parquet",
        sorted(comparisons, key=lambda item: item["candidate_id"]),
    )
    ids = sorted(candidate for _, candidate, _ in candidates)
    write_canonical_json(
        root / "generation-manifest.json",
        {
            "schema_version": "candidate-generation-manifest/v1",
            "candidate_set_id": identity("candidate-set/v1", ids),
            "candidate_count": len(ids),
        },
    )
    experiment = {
        "schema_version": "validation-experiment/v1",
        "statuses": sorted(statuses, key=lambda item: item["candidate_id"]),
        "final_status": "COMPLETE",
    }
    experiment["validation_experiment_id"] = identity("validation-experiment/v1", experiment)
    write_canonical_json(root / "validation-experiment.json", experiment)
    names = (
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
    manifest = {
        "schema_version": "validation-manifest/v1",
        "validation_experiment_id": experiment["validation_experiment_id"],
        "export_version": "research-export/v1",
        "pyarrow_version": pa.__version__,
        "artifacts": {name: sha256_file(root / name) for name in names},
    }
    manifest["export_id"] = identity("research-export/v1", manifest)
    write_canonical_json(root / "validation-manifest.json", manifest)
    return by_role
