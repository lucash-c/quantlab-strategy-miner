"""Candidate evaluation over shared synthetic session paths; never replays market data."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable
from functools import cmp_to_key
from typing import Any

from quantlab_backtest.metrics_v3 import MetricsAccumulatorV3
from quantlab_backtest.partition_metrics import closed_trade
from quantlab_core.canonical import canonical_json_bytes
from quantlab_core.errors import ContractError
from quantlab_core.numeric import CanonicalRational
from quantlab_core.price import common_decimal_scale
from quantlab_core.strategy_v3 import StrategyDefinitionV3
from quantlab_mining.contracts import identity

from quantlab_robustness.contracts import MonteCarloPolicyV1
from quantlab_robustness.exact import defined, nearest_rank


def evaluate_candidate_paths(
    *,
    candidate_id: str,
    strategy: StrategyDefinitionV3,
    policy: MonteCarloPolicyV1,
    path_set: dict[str, Any],
    session_records: dict[str, dict[str, Any]],
    ledger_loader: Callable[[str], Iterable[dict[str, Any]]],
) -> dict[str, Any]:
    if path_set["status"] != "READY":
        record = {
            "schema_version": "candidate-monte-carlo-result/v1",
            "candidate_id": candidate_id,
            "status": "INSUFFICIENT_EVIDENCE",
            "reason": path_set["status"],
            "monte_carlo_policy_id": policy.policy_id,
            "monte_carlo_path_set_id": path_set["monte_carlo_path_set_id"],
            "candidate_session_evaluations": [],
            "paths": [],
            "metrics": {},
            "market_replayed": False,
        }
        record["monte_carlo_result_id"] = identity("candidate-monte-carlo-result/v1", record)
        return record
    source_ids = {
        block["source_session_id"] for path in path_set["paths"] for block in path["blocks"]
    }
    missing = source_ids - set(session_records)
    if missing:
        raise ContractError("missing CandidateSessionEvaluation for Monte Carlo source")
    scale = common_decimal_scale(
        [session_records[sid]["price_scale"] for sid in source_ids], strategy.decimal_inputs()
    )
    results = []
    for path in path_set["paths"]:
        accumulator = MetricsAccumulatorV3()
        occurrence_hash = hashlib.sha256()
        block_rows = []
        global_trade = 0
        for block in path["blocks"]:
            source = session_records[block["source_session_id"]]
            local_trades = 0
            local_net = 0
            for local_ordinal, raw in enumerate(ledger_loader(source["evaluation_id"]), 1):
                if raw["trade_number"] != local_ordinal:
                    raise ContractError("non-canonical source ledger ordinal")
                global_trade += 1
                trade = closed_trade(raw, source["price_scale"], scale, global_trade)
                accumulator.add(trade)
                local_trades += 1
                local_net += trade.net_pnl_units
                occurrence = {
                    "path_id": path["path_id"],
                    "synthetic_block_ordinal": block["synthetic_block_ordinal"],
                    "local_trade_ordinal": local_ordinal,
                    "source_session_id": block["source_session_id"],
                    "source_signal_id": raw["signal_id"],
                }
                occurrence["synthetic_trade_occurrence_id"] = identity(
                    "monte-carlo-trade-occurrence/v1", occurrence
                )
                occurrence_hash.update(canonical_json_bytes(occurrence))
            block_rows.append(
                {
                    **block,
                    "source_trading_date": source["session"]["trading_date"],
                    "candidate_session_evaluation_id": source["evaluation_id"],
                    "candidate_session_result_fingerprint": source["result_fingerprint"],
                    "trades": local_trades,
                    "net_pnl": CanonicalRational(local_net, 10**scale).to_record(),
                }
            )
        result = {
            "path_id": path["path_id"],
            "path_index": path["path_index"],
            "blocks": block_rows,
            "total_net": CanonicalRational(accumulator.net_pnl_units, 10**scale).to_record(),
            "max_drawdown": CanonicalRational(
                accumulator.max_drawdown_units, 10**scale
            ).to_record(),
            "max_consecutive_losses": accumulator.max_consecutive_losses,
            "total_trades": accumulator.trades,
            "profitable_blocks": sum(int(row["net_pnl"]["numerator"]) > 0 for row in block_rows),
            "losing_blocks": sum(int(row["net_pnl"]["numerator"]) < 0 for row in block_rows),
            "flat_blocks": sum(int(row["net_pnl"]["numerator"]) == 0 for row in block_rows),
            "synthetic_occurrences_fingerprint": "sha256:" + occurrence_hash.hexdigest(),
        }
        result["candidate_path_result_id"] = identity(
            "candidate-monte-carlo-path-result/v1", result
        )
        results.append(result)
    nets = [
        CanonicalRational(int(row["total_net"]["numerator"]), int(row["total_net"]["denominator"]))
        for row in results
    ]
    drawdowns = [
        CanonicalRational(
            int(row["max_drawdown"]["numerator"]), int(row["max_drawdown"]["denominator"])
        )
        for row in results
    ]
    streaks = [CanonicalRational(row["max_consecutive_losses"]) for row in results]
    threshold_probabilities = []
    for threshold in policy.drawdown_thresholds:
        count = sum(value.compare(threshold.threshold.value()) > 0 for value in drawdowns)
        threshold_probabilities.append(
            {
                "threshold_id": threshold.threshold_id,
                "operator": "GT",
                "threshold": threshold.threshold.model_dump(mode="json"),
                "probability": defined(CanonicalRational(count, len(results))),
            }
        )

    def worst_first(left: dict[str, Any], right: dict[str, Any]) -> int:
        left_dd = CanonicalRational(
            int(left["max_drawdown"]["numerator"]), int(left["max_drawdown"]["denominator"])
        )
        right_dd = CanonicalRational(
            int(right["max_drawdown"]["numerator"]), int(right["max_drawdown"]["denominator"])
        )
        comparison = right_dd.compare(left_dd)
        if comparison:
            return comparison
        left_net = CanonicalRational(
            int(left["total_net"]["numerator"]), int(left["total_net"]["denominator"])
        )
        right_net = CanonicalRational(
            int(right["total_net"]["numerator"]), int(right["total_net"]["denominator"])
        )
        comparison = left_net.compare(right_net)
        if comparison:
            return comparison
        return (left["path_id"] > right["path_id"]) - (left["path_id"] < right["path_id"])

    worst = sorted(results, key=cmp_to_key(worst_first))[0]
    summary = {
        "path_count": defined(len(results)),
        "median_total_net": defined(nearest_rank(nets, CanonicalRational(1, 2))),
        "p05_total_net": defined(nearest_rank(nets, CanonicalRational(5, 100))),
        "p95_max_drawdown": defined(nearest_rank(drawdowns, CanonicalRational(95, 100))),
        "p99_max_drawdown": defined(nearest_rank(drawdowns, CanonicalRational(99, 100))),
        "probability_net_le_zero": defined(
            CanonicalRational(sum(value.numerator <= 0 for value in nets), len(nets))
        ),
        "median_max_consecutive_losses": defined(nearest_rank(streaks, CanonicalRational(1, 2))),
        "drawdown_threshold_probabilities": threshold_probabilities,
        "worst_observed_path_id": worst["path_id"],
    }
    for item in threshold_probabilities:
        summary["probability_drawdown_above_threshold:" + item["threshold_id"]] = item[
            "probability"
        ]
    sources = sorted(
        (
            {
                "evaluation_id": record["evaluation_id"],
                "result_fingerprint": record["result_fingerprint"],
            }
            for record in session_records.values()
        ),
        key=lambda item: item["evaluation_id"],
    )
    record = {
        "schema_version": "candidate-monte-carlo-result/v1",
        "candidate_id": candidate_id,
        "status": "COMPLETED",
        "reason": None,
        "monte_carlo_policy_id": policy.policy_id,
        "monte_carlo_path_set_id": path_set["monte_carlo_path_set_id"],
        "candidate_session_evaluations": sources,
        "price_scale": scale,
        "paths": results,
        "metrics": summary,
        "summary": summary,
        "market_replayed": False,
    }
    record["monte_carlo_result_id"] = identity("candidate-monte-carlo-result/v1", record)
    return record
