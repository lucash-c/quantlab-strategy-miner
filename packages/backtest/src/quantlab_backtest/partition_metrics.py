"""Additive session/partition views; ledger is normative, not averages of session metrics."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import fields

from quantlab_core.canonical import canonical_json_bytes, sha256_bytes
from quantlab_core.errors import ContractError
from quantlab_core.numeric import CanonicalRational
from quantlab_core.price import rescale_units_exact
from quantlab_core.strategy_v3 import StrategyDefinitionV3

from quantlab_backtest.engine_v3 import BACKTEST_ENGINE_V3_VERSION
from quantlab_backtest.metrics_v3 import MetricsAccumulatorV3
from quantlab_backtest.models_v3 import ClosedTradeV3

PARTITION_METRICS_VERSION = "chronological-ledger-realized-equity/v1"


def _id(domain: str, payload: dict) -> str:
    return "sha256:" + sha256_bytes(canonical_json_bytes({"domain": domain, "payload": payload}))


def _defined(numerator: int, denominator: int = 1) -> dict:
    return {
        "status": "DEFINED",
        "value": CanonicalRational(numerator, denominator).to_record(),
        "reason": None,
    }


def _undefined(reason: str) -> dict:
    return {"status": "UNDEFINED", "value": None, "reason": reason}


def normalized_metrics(metrics: dict) -> dict:
    """Convert price-unit rationals to POINTS; dimensionless existing fractions stay exact."""
    scale = metrics["price_representation"]["decimal_scale"]
    values = {
        name: _defined(metrics[name])
        for name in ("trades", "wins", "losses", "breakeven", "max_consecutive_losses")
    }
    for name, field in (
        ("gross_pnl", "gross_pnl_units"),
        ("net_pnl", "net_pnl_units"),
        ("costs", "costs_units"),
        ("slippage_impact", "slippage_impact_units"),
        ("max_drawdown", "max_drawdown_units"),
    ):
        values[name] = _defined(metrics[field], 10**scale)
    for name in (
        "average_trade",
        "average_win",
        "average_loss",
        "win_rate",
        "profit_factor",
        "payoff_ratio",
    ):
        original = metrics[name]
        if original["status"] == "UNDEFINED":
            values[name] = original
        else:
            denominator = int(original["value"]["denominator"])
            if name.startswith("average_"):
                denominator *= 10**scale
            values[name] = _defined(int(original["value"]["numerator"]), denominator)
    return values


def closed_trade(record: dict, from_scale: int, to_scale: int, ordinal: int) -> ClosedTradeV3:
    values = {field.name: record[field.name] for field in fields(ClosedTradeV3)}
    for name, value in values.items():
        if name.endswith("_units"):
            values[name] = rescale_units_exact(value, from_scale, to_scale)
    values["trade_number"] = ordinal
    return ClosedTradeV3(**values)


def aggregate_partition(
    candidate_id: str,
    partition_id: str,
    sessions: Iterable[tuple[dict, Iterable[dict], Iterable[dict]]],
    strategy: StrategyDefinitionV3,
    common_scale: int,
    on_trade: Callable[[dict], None],
    on_signal: Callable[[dict], None],
) -> dict:
    import hashlib

    global_metrics = MetricsAccumulatorV3()
    trade_count = signal_count = 0
    session_rows, evaluations = [], []
    ledger_hash, journal_hash = hashlib.sha256(), hashlib.sha256()
    previous_date = None
    pnl_units, trade_counts = [], []
    for session_record, ledger, journal in sessions:
        session = session_record["session"]
        if previous_date is not None and session["trading_date"] <= previous_date:
            raise ContractError("partition sessions are not strictly chronological")
        previous_date = session["trading_date"]
        local = MetricsAccumulatorV3()
        count = 0
        for raw in ledger:
            if raw["session_id"] != session["session_id"] or raw["trade_number"] != count + 1:
                raise ContractError("session ledger metadata/ordinal mismatch")
            trade_count += 1
            count += 1
            trade = closed_trade(raw, session_record["price_scale"], common_scale, trade_count)
            local.add(trade)
            global_metrics.add(trade)
            record = trade.to_record()
            ledger_hash.update(canonical_json_bytes(record))
            on_trade(record)
        journals = 0
        for raw in journal:
            journals += 1
            if raw["session_id"] != session["session_id"] or raw["signal_number"] != journals:
                raise ContractError("session journal metadata/ordinal mismatch")
            signal_count += 1
            record = {**raw, "signal_number": signal_count}
            journal_hash.update(canonical_json_bytes(record))
            on_signal(record)
        if count != session_record["ledger_count"] or journals != session_record["journal_count"]:
            raise ContractError("session audit count mismatch")
        local_metrics = local.to_record(
            price_scale=common_scale,
            cost_model=strategy.cost_model.model_dump(mode="json"),
            slippage_model=strategy.slippage_model.model_dump(mode="json"),
        )
        pnl_units.append(local.net_pnl_units)
        trade_counts.append(local.trades)
        session_rows.append(
            {
                "session_id": session["session_id"],
                "trading_date": session["trading_date"],
                "physical_contract": session["physical_contract"],
                "candidate_session_evaluation_id": session_record["evaluation_id"],
                "result_fingerprint": session_record["result_fingerprint"],
                "backtest_metrics": local_metrics,
                "metrics": normalized_metrics(local_metrics),
            }
        )
        evaluations.append(
            {
                "evaluation_id": session_record["evaluation_id"],
                "result_fingerprint": session_record["result_fingerprint"],
            }
        )
    if not session_rows:
        raise ContractError("cannot aggregate an empty partition")
    metrics_v3 = global_metrics.to_record(
        price_scale=common_scale,
        cost_model=strategy.cost_model.model_dump(mode="json"),
        slippage_model=strategy.slippage_model.model_dump(mode="json"),
    )
    metrics_v3["backtest_engine_version"] = BACKTEST_ENGINE_V3_VERSION
    metrics_v3["journal_records"] = signal_count
    values = normalized_metrics(metrics_v3)
    n = len(session_rows)
    positive = sum(p > 0 for p in pnl_units)
    positive_pnl = sum(max(p, 0) for p in pnl_units)
    for name, value in (
        ("total_sessions", n),
        ("active_sessions", sum(t > 0 for t in trade_counts)),
        ("profitable_sessions", positive),
        ("losing_sessions", sum(p < 0 for p in pnl_units)),
        ("flat_sessions", sum(p == 0 for p in pnl_units)),
    ):
        values[name] = _defined(value)
    values.update(
        {
            "trades_per_session": _defined(trade_count, n),
            "net_pnl_per_session": _defined(global_metrics.net_pnl_units, n * 10**common_scale),
            "positive_session_rate": _defined(positive, n),
            "active_session_rate": _defined(sum(t > 0 for t in trade_counts), n),
            "best_session_net_pnl": _defined(max(pnl_units), 10**common_scale),
            "worst_session_net_pnl": _defined(min(pnl_units), 10**common_scale),
            "largest_profitable_session_share": _defined(max(max(pnl_units), 0), positive_pnl)
            if positive_pnl
            else _undefined("NO_PROFITABLE_SESSIONS"),
            "largest_trade_count_session_share": _defined(max(trade_counts), trade_count)
            if trade_count
            else _undefined("NO_TRADES"),
        }
    )
    record = {
        "schema_version": "research-partition-evaluation/v1",
        "candidate_id": candidate_id,
        "partition_id": partition_id,
        "policy": PARTITION_METRICS_VERSION,
        "session_evaluations": evaluations,
        "sessions": session_rows,
        "backtest_metrics": metrics_v3,
        "metrics": values,
        "ledger_fingerprint": ledger_hash.hexdigest(),
        "journal_fingerprint": journal_hash.hexdigest(),
    }
    record["partition_evaluation_id"] = _id(
        "research-partition-evaluation/v1",
        {
            "candidate_id": candidate_id,
            "partition_id": partition_id,
            "session_evaluations": evaluations,
            "policy": PARTITION_METRICS_VERSION,
        },
    )
    record["result_fingerprint"] = _id("research-partition-result/v1", record)
    return record
