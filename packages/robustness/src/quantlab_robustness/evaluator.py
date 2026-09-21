"""Thin reusable adapter from robustness scenarios to CandidateSessionEvaluation v1."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from quantlab_backtest.partition_metrics import aggregate_partition
from quantlab_core.errors import ContractError
from quantlab_core.price import common_decimal_scale
from quantlab_core.sessions import HistoricalDataset
from quantlab_core.strategy_v3 import StrategyDefinitionV3
from quantlab_data.feature_cache_v2 import build_feature_set
from quantlab_data.historical import MarketHistoricalBuild
from quantlab_data.session_catalog import SessionCatalog, resolve_session
from quantlab_mining.contracts import EvaluationConfigV1, identity
from quantlab_research.session_cache import SessionEvaluationCache


class CandidateSessionEvaluator:
    def __init__(
        self,
        *,
        catalog: SessionCatalog,
        market_cache_root: Path,
        session_cache: SessionEvaluationCache,
    ):
        self.catalog = catalog
        self.market_cache_root = market_cache_root
        self.session_cache = session_cache
        self.verified: set[Path] = set()
        self.report = {
            "session_evaluations_built": 0,
            "session_evaluations_reused": 0,
            "feature_cache_built": 0,
            "feature_cache_reused": 0,
        }

    def evaluate(
        self,
        *,
        candidate_id: str,
        strategy: StrategyDefinitionV3,
        evaluation: EvaluationConfigV1,
        session_ids: list[str],
    ) -> dict[str, dict[str, Any]]:
        allowed = set(session_ids)
        selected = [session for session in self.catalog.sessions if session.session_id in allowed]
        if [session.session_id for session in selected] != session_ids:
            raise ContractError(
                "requested sessions are not an ordered subset of the authorized catalog"
            )
        effective = strategy.model_copy(
            update={
                "cost_model": evaluation.cost_model,
                "slippage_model": evaluation.slippage_model,
            }
        )
        records = {}
        for session in selected:
            market = resolve_session(
                self.catalog,
                session,
                (effective.timeframe,),
                self.market_cache_root,
                self.verified,
            )
            dataset_id = identity(
                "robustness-single-session-dataset/v1",
                {
                    "source_dataset_id": self.catalog.manifest["dataset_id"],
                    "session_id": session.session_id,
                    "timeframe": effective.timeframe,
                },
            )
            build = MarketHistoricalBuild(
                HistoricalDataset(
                    dataset_id,
                    dataset_id,
                    session.logical_asset,
                    1,
                    (effective.timeframe,),
                    (session,),
                ),
                (market,),
                {},
                {},
            )
            feature_set = build_feature_set(
                build,
                effective.feature_specs,
                timeframe=effective.timeframe,
                cache_root=self.market_cache_root,
            )
            for operation in feature_set.operational_report["operations"]:
                key = (
                    "feature_cache_reused"
                    if operation["status"] == "CACHE_HIT"
                    else "feature_cache_built"
                )
                self.report[key] += 1
            record, reused = self.session_cache.evaluate(
                candidate_id,
                feature_set.sessions[0],
                effective,
                lambda _kind: None,
            )
            self.report[
                "session_evaluations_reused" if reused else "session_evaluations_built"
            ] += 1
            records[session.session_id] = record
            self.session_cache.publish_pending(session.session_id)
        return records

    def aggregate(
        self,
        *,
        candidate_id: str,
        strategy: StrategyDefinitionV3,
        evaluation: EvaluationConfigV1,
        partition_id: str,
        session_ids: list[str],
        records: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        selected = [records[sid] for sid in session_ids]
        effective = strategy.model_copy(
            update={
                "cost_model": evaluation.cost_model,
                "slippage_model": evaluation.slippage_model,
            }
        )
        scale = common_decimal_scale(
            [record["price_scale"] for record in selected], effective.decimal_inputs()
        )
        return aggregate_partition(
            candidate_id,
            partition_id,
            (
                (
                    record,
                    self.session_cache.audit(record["evaluation_id"], "ledger"),
                    self.session_cache.audit(record["evaluation_id"], "journal"),
                )
                for record in selected
            ),
            effective,
            scale,
            lambda _record: None,
            lambda _record: None,
        )
