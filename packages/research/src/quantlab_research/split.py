"""Whole-session chronological views without parent/other-partition identity coupling."""

from quantlab_core.errors import ContractError
from quantlab_core.sessions import TradingSession
from quantlab_data.session_catalog import SessionCatalog
from quantlab_mining.contracts import identity

from quantlab_research.contracts import SESSION_POLICY, SplitPolicyV1
from quantlab_research.records import ResearchPartitionV1, ResearchSplitPlanV1


def partition(sessions: tuple[TradingSession, ...], logical_asset: str) -> dict:
    record = {
        "schema_version": "research-partition/v1",
        "logical_asset": logical_asset,
        "session_ids": [s.session_id for s in sessions],
        "trading_dates": [s.trading_date for s in sessions],
        "session_policy": SESSION_POLICY,
        "timezone": "B3_FIXED_UTC_MINUS_03",
        "empty_candles": "DO_NOT_FILL",
    }
    record["partition_id"] = identity("research-partition/v1", record)
    return ResearchPartitionV1.model_validate(record).model_dump(mode="json")


def create_split(catalog: SessionCatalog, policy: SplitPolicyV1) -> tuple[dict, dict, dict]:
    sessions = catalog.sessions
    n = policy.validation_sessions
    if len(sessions) - n < policy.min_discovery_sessions:
        raise ContractError("INSUFFICIENT_SESSIONS: Discovery below configured minimum")
    discovery, validation = sessions[:-n], sessions[-n:]
    if not discovery or len(validation) < policy.min_validation_sessions:
        raise ContractError("INSUFFICIENT_SESSIONS: Validation below configured minimum")
    if discovery[-1].trading_date >= validation[0].trading_date:
        raise ContractError("split is not strictly chronological")
    d, v = (
        partition(discovery, catalog.manifest["logical_asset"]),
        partition(validation, catalog.manifest["logical_asset"]),
    )
    record = {
        "schema_version": "research-split-plan/v1",
        "historical_dataset_id": catalog.manifest["dataset_id"],
        "ordered_session_ids": [s.session_id for s in sessions],
        "method": policy.method,
        "policy": policy.model_dump(mode="json"),
        "discovery": {
            "partition_id": d["partition_id"],
            "session_ids": d["session_ids"],
            "count": len(discovery),
            "first_date": discovery[0].trading_date,
            "last_date": discovery[-1].trading_date,
        },
        "validation": {
            "partition_id": v["partition_id"],
            "session_ids": v["session_ids"],
            "count": len(validation),
            "first_date": validation[0].trading_date,
            "last_date": validation[-1].trading_date,
        },
        "excluded_sessions": [],
    }
    record["split_plan_id"] = identity("research-split-plan/v1", record)
    return ResearchSplitPlanV1.model_validate(record).model_dump(mode="json"), d, v
