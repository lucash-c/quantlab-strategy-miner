from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from quantlab_core.feature_specs import parse_feature_spec
from quantlab_data.feature_cache_v2 import build_feature_set, read_feature_observations
from quantlab_data.historical import SessionSource, build_market_history

from tests.test_b3_adapter import ZIP_NAME, write_zip


def spec(
    feature_id: str,
    name: str,
    parameters: dict[str, int | bool | str],
    inputs: list[str] | None = None,
):
    return parse_feature_spec(
        {
            "feature_id": feature_id,
            "name": name,
            "version": "1.0.0",
            "parameters": parameters,
            "inputs": inputs or [],
        }
    )


class FeatureCacheV2Tests(unittest.TestCase):
    def test_four_timeframes_requested_only_cache_hit_and_selective_invalidation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = write_zip(root / ZIP_NAME)
            cache = root / "cache"
            market = build_market_history(
                (SessionSource(source, "WINV26"),),
                logical_asset="WIN",
                cache_root=cache,
                timeframes=("1m", "2m", "5m", "15m"),
            )
            self.assertEqual(set(market.sessions[0].candles), {"1m", "2m", "5m", "15m"})
            self.assertEqual(market.sessions[0].features, {})

            requested = (
                spec("ema", "ema_close", {"period": 2}),
                spec(
                    "volume",
                    "rolling_mean_volume",
                    {"period": 2, "include_current": True},
                ),
            )
            first = build_feature_set(market, requested, timeframe="1m", cache_root=cache)
            second = build_feature_set(market, requested, timeframe="1m", cache_root=cache)
            self.assertEqual(first.feature_set_id, second.feature_set_id)
            self.assertTrue(
                all(row["status"] == "CACHE_HIT" for row in second.operational_report["operations"])
            )
            self.assertEqual(set(first.sessions[0].features), {"ema", "volume"})

            changed = (
                spec("ema", "ema_close", {"period": 3}),
                requested[1],
            )
            rebuilt = build_feature_set(market, changed, timeframe="1m", cache_root=cache)
            statuses = {
                row["feature_id"]: row["status"]
                for row in rebuilt.operational_report["operations"]
            }
            self.assertEqual(statuses, {"ema": "BUILT", "volume": "CACHE_HIT"})
            self.assertNotEqual(first.feature_set_id, rebuilt.feature_set_id)

            volume_path = (
                first.sessions[0].features["volume"].directory / "feature-values.parquet"
            )
            rows = list(read_feature_observations(volume_path))
            self.assertEqual(rows[0].warmup_status, "WARMING_UP")
            self.assertEqual(rows[1].warmup_status, "READY")


if __name__ == "__main__":
    unittest.main()
