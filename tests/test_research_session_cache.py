from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from quantlab_backtest.engine_v3 import FeatureSessionInputV3, run_backtest_v3
from quantlab_backtest.partition_metrics import aggregate_partition
from quantlab_core.errors import ContractError
from quantlab_core.price import common_decimal_scale
from quantlab_data.feature_cache_v2 import build_feature_set, read_feature_observations
from quantlab_data.historical import read_session_candles, read_session_trades
from quantlab_mining.batch import _ordered_specs
from quantlab_mining.contracts import GenerationPolicyV1
from quantlab_mining.generator import preflight
from quantlab_research.session_cache import SessionEvaluationCache
from quantlab_research.split import partition
from research_helpers import execution_config, prepare, research_search


class SessionCacheTests(unittest.TestCase):
    def test_four_timeframes_mixed_scales_cache_equals_direct_including_prices_and_signals(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tfs = ("1m", "2m", "5m", "15m")
            catalog, sources = prepare(root, count=2, timeframes=tfs, mixed_scale=True)
            from quantlab_data.historical import build_market_history

            market = build_market_history(
                sources[:2],
                logical_asset="WIN",
                cache_root=root / "cache",
                timeframes=tfs,
                max_sessions=2,
            )
            plan = preflight(
                research_search(tfs), GenerationPolicyV1(candidate_budget=50), root / "plan.sqlite"
            )
            execution = execution_config()
            from quantlab_core.strategy_v3 import (
                FixedPerSideCostModelV3,
                FixedPointsSlippageModelV3,
            )

            cost = FixedPerSideCostModelV3(
                type="FIXED_PER_SIDE", version="1.0.0", points_per_side="0.125"
            )
            slip = FixedPointsSlippageModelV3(
                type="FIXED_POINTS", version="1.0.0", points_per_side="0.25"
            )
            cache = SessionEvaluationCache(root / "cache")
            try:
                for tf in tfs:
                    cid, base = next(
                        (cid, s)
                        for cid, s in plan.candidates()
                        if s.timeframe == tf
                        and s.direction == "BUY"
                        and s.entry_time_filter.start == "09:00"
                        and len(s.feature_specs) == 1
                        and "1000000" not in s.canonical_bytes().decode()
                    )
                    strategy = base.model_copy(update={"cost_model": cost, "slippage_model": slip})
                    features = build_feature_set(
                        market,
                        _ordered_specs({s.feature_id: s for s in strategy.feature_specs}),
                        timeframe=tf,
                        cache_root=root / "cache",
                    )
                    inputs = []
                    records = []
                    for item in features.sessions:
                        record, hit = cache.evaluate(cid, item, strategy, lambda kind: None)
                        self.assertFalse(hit)
                        records.append(record)
                        inputs.append(
                            FeatureSessionInputV3(
                                item.market.session,
                                read_session_trades(
                                    item.market.trades.directory / "trades.parquet"
                                ),
                                read_session_candles(
                                    item.market.candles[tf].directory / "candles.parquet"
                                ),
                                {
                                    s.feature_id: read_feature_observations(
                                        item.features[s.feature_id].directory
                                        / "feature-values.parquet"
                                    )
                                    for s in strategy.feature_specs
                                },
                            )
                        )
                    cache.publish_pending()
                    self.assertEqual({r["price_scale"] for r in records}, {3, 4})
                    scale = common_decimal_scale(
                        [s.price_scale for s in catalog.sessions], strategy.decimal_inputs()
                    )
                    direct_ledger, direct_journal = [], []
                    direct = run_backtest_v3(
                        inputs,
                        strategy,
                        common_price_scale=scale,
                        on_trade=lambda r, target=direct_ledger: target.append(r.to_record()),
                        on_signal=lambda r, target=direct_journal: target.append(r.to_record()),
                    )
                    ledger, journal = [], []
                    aggregate = aggregate_partition(
                        cid,
                        partition(catalog.sessions, "WIN")["partition_id"],
                        (
                            (
                                r,
                                cache.audit(r["evaluation_id"], "ledger"),
                                cache.audit(r["evaluation_id"], "journal"),
                            )
                            for r in records
                        ),
                        strategy,
                        scale,
                        ledger.append,
                        journal.append,
                    )
                    self.assertEqual(ledger, direct_ledger, tf)
                    self.assertEqual(journal, direct_journal, tf)
                    self.assertEqual(aggregate["backtest_metrics"], direct.metrics, tf)
                    self.assertGreater(len(ledger), 0, tf)
                    for item, record in zip(features.sessions, records, strict=True):
                        with patch("quantlab_research.session_cache.run_backtest_v3") as engine:
                            loaded, hit = cache.evaluate(cid, item, strategy, lambda kind: None)
                            self.assertTrue(hit)
                            self.assertEqual(loaded, record)
                            engine.assert_not_called()
                    different = strategy.model_copy(
                        update={
                            "cost_model": execution.cost_model,
                            "slippage_model": execution.slippage_model,
                        }
                    )
                    other, hit = cache.evaluate(
                        cid, features.sessions[0], different, lambda kind: None
                    )
                    self.assertFalse(hit)
                    self.assertNotEqual(other["evaluation_id"], records[0]["evaluation_id"])
            finally:
                cache.close()

    def test_transaction_rollback_and_scientific_pack_corruption(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            catalog, sources = prepare(root, count=1)
            from quantlab_data.historical import build_market_history

            market = build_market_history(
                sources[:1],
                logical_asset="WIN",
                cache_root=root / "cache",
                timeframes=("1m",),
                max_sessions=1,
            )
            plan = preflight(
                research_search(), GenerationPolicyV1(candidate_budget=20), root / "plan.sqlite"
            )
            cid, strategy = next(plan.candidates())
            features = build_feature_set(
                market, strategy.feature_specs, timeframe="1m", cache_root=root / "cache"
            )
            cache = SessionEvaluationCache(root / "cache")
            try:

                def broken(inputs, strategy, *, on_trade, **kwargs):
                    cache.db.execute(
                        "INSERT INTO session_audit VALUES('incomplete','ledger',0,'{}')"
                    )
                    raise ContractError("simulated engine failure")

                with (
                    patch("quantlab_research.session_cache.run_backtest_v3", side_effect=broken),
                    self.assertRaises(ContractError),
                ):
                    cache.evaluate(cid, features.sessions[0], strategy, lambda kind: None)
                self.assertEqual(
                    cache.db.execute("SELECT count(*) FROM session_audit").fetchone()[0], 0
                )
                result, _ = cache.evaluate(cid, features.sessions[0], strategy, lambda kind: None)
                cache.publish_pending()
                pack = next((cache.root / "packs").glob("*.jsonl"))
                pack.write_bytes(pack.read_bytes() + b"corrupt\n")
                with self.assertRaisesRegex(ContractError, "corrupt immutable session pack"):
                    cache.load(result["evaluation_id"])
            finally:
                cache.close()

    def test_single_writer_before_index_mutation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = SessionEvaluationCache(root)
            try:
                with self.assertRaisesRegex(ContractError, "active writer"):
                    SessionEvaluationCache(root)
            finally:
                first.close()


if __name__ == "__main__":
    unittest.main()
