from __future__ import annotations

from dataclasses import asdict

from quantlab_backtest.models_v3 import ClosedTradeV3
from quantlab_core.strategy_v3 import StrategyDefinitionV3
from quantlab_mining.canonicalization import named_candidate
from quantlab_mining.contracts import EvaluationConfigV1
from quantlab_robustness.contracts import (
    ExecutionStressPolicyV1,
    MonteCarloPolicyV1,
    RobustnessGatePolicyV1,
    SensitivityPolicyV1,
    WalkForwardPolicyV1,
)
from scoring_helpers import strategy


def candidate_strategy(**changes):
    base = strategy()
    record = base.model_dump(mode="json")
    record.update(changes)
    return named_candidate(StrategyDefinitionV3.model_validate(record))


def walk_policy(**changes):
    record = {
        "schema_version": "walk-forward-policy/v1",
        "method": "ROLLING_FIXED",
        "discovery_sessions": 13,
        "validation_sessions": 6,
        "step_sessions": 1,
        "gap_sessions": 0,
        "min_folds": 3,
        "incomplete_tail_policy": "DROP_INCOMPLETE_TAIL",
        "discovery_gate": None,
        "validation_gate": None,
    }
    record.update(changes)
    return WalkForwardPolicyV1.model_validate(record)


def mc_policy(method="SESSION_PERMUTATION_WITHOUT_REPLACEMENT", **changes):
    record = {
        "schema_version": "monte-carlo-policy/v1",
        "sampling_method": method,
        "source_pool": {"source": "EXPLICIT_SESSION_IDS", "session_ids": ["A", "B", "C"]},
        "number_of_paths": 8,
        "path_length_sessions": 3,
        "seed": "0" * 63 + "1",
        "sampler_version": "SHA256_COUNTER_REJECTION_V1",
        "quantile_policy": "NEAREST_RANK_V1",
        "minimum_source_sessions": 3,
        "drawdown_thresholds": [
            {"threshold": {"numerator": "5", "denominator": "1"}, "operator": "GT"}
        ],
    }
    record.update(changes)
    return MonteCarloPolicyV1.model_validate(record)


def sensitivity_policy(perturbations, **changes):
    record = {
        "schema_version": "sensitivity-policy/v1",
        "method": "ONE_AT_A_TIME",
        "source_pool": {"source": "EXPLICIT_SESSION_IDS", "session_ids": ["A"]},
        "perturbations": perturbations,
        "tolerance_criteria": [],
        "validation_gate": None,
    }
    record.update(changes)
    return SensitivityPolicyV1.model_validate(record)


def stress_policy(scenarios):
    return ExecutionStressPolicyV1.model_validate(
        {
            "schema_version": "execution-stress-policy/v1",
            "source_pool": {"source": "EXPLICIT_SESSION_IDS", "session_ids": ["A"]},
            "scenarios": scenarios,
            "validation_gate": None,
        }
    )


def robustness_gate(criteria):
    return RobustnessGatePolicyV1.model_validate(
        {
            "schema_version": "robustness-gate-policy/v1",
            "required_families": ["WALK_FORWARD", "MONTE_CARLO", "SENSITIVITY", "STRESS"],
            "criteria": criteria,
            "semantics": "all-criteria-insufficient-precedence/v1",
        }
    )


def no_friction(max_sessions=30):
    return EvaluationConfigV1.model_validate(
        {
            "schema_version": "mining-evaluation/v1",
            "max_sessions": max_sessions,
            "cost_model": {"type": "NONE", "version": "1.0.0"},
            "slippage_model": {"type": "NONE", "version": "1.0.0"},
        }
    )


def fixed_friction(cost="1", slippage="1", max_sessions=30):
    return EvaluationConfigV1.model_validate(
        {
            "schema_version": "mining-evaluation/v1",
            "max_sessions": max_sessions,
            "cost_model": {
                "type": "FIXED_PER_SIDE",
                "version": "1.0.0",
                "points_per_side": cost,
            },
            "slippage_model": {
                "type": "FIXED_POINTS",
                "version": "1.0.0",
                "points_per_side": slippage,
            },
        }
    )


def trade(session_id, number, pnl):
    record = asdict(
        ClosedTradeV3(
            trade_number=number,
            signal_id=f"{session_id}-{number}",
            session_id=session_id,
            trading_date=f"2026-01-{number:02d}",
            logical_asset="WIN",
            physical_contract="WINV26",
            timeframe="1m",
            direction="BUY",
            signal_available_at_ns_utc=1,
            entry_timestamp_ns_utc=2,
            entry_source_sequence=1,
            entry_market_price_units=100,
            entry_execution_price_units=100,
            target_execution_price_units=110,
            stop_execution_price_units=90,
            exit_timestamp_ns_utc=3,
            exit_source_sequence=2,
            exit_market_price_units=100 + pnl,
            exit_execution_price_units=100 + pnl,
            exit_reason="SESSION_END",
            gross_pnl_units=pnl,
            slippage_impact_units=0,
            costs_units=0,
            net_pnl_units=pnl,
        )
    )
    return record


def session_record(session_id, trading_date, pnl):
    return {
        "evaluation_id": "evaluation-" + session_id,
        "result_fingerprint": "fingerprint-" + session_id,
        "price_scale": 0,
        "ledger_count": 1,
        "journal_count": 0,
        "session": {
            "session_id": session_id,
            "trading_date": trading_date,
            "physical_contract": "WINV26",
        },
        "ledger": [trade(session_id, 1, pnl)],
    }
