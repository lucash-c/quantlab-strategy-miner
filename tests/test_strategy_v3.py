from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError
from quantlab_core.strategy_v3 import StrategyDefinitionV3, load_strategy_v3


def strategy_v3_record() -> dict[str, object]:
    return {
        "schema_version": "strategy-definition/v3",
        "strategy_id": "TEST.V3",
        "strategy_version": 1,
        "name": "Formal v3 fixture",
        "logical_asset": "WIN",
        "timeframe": "1m",
        "evaluation_mode": "ON_CLOSE",
        "direction": "BUY",
        "features": [
            {
                "feature_id": "ema9",
                "name": "ema_close",
                "version": "1.0.0",
                "parameters": {"period": 9},
                "inputs": [],
            }
        ],
        "entry_conditions": {
            "type": "comparison",
            "operator": "GT",
            "left": {"type": "candle_field", "name": "close"},
            "right": {"type": "feature", "feature_id": "ema9"},
        },
        "entry_time_filter": {
            "start": "09:15",
            "end": "11:30",
            "start_inclusive": True,
            "end_inclusive": False,
        },
        "take_profit": {"unit": "POINTS", "value": "100"},
        "stop_loss": {"unit": "POINTS", "value": "50"},
        "execution": {
            "entry_fill": "NEXT_TRADE",
            "position_policy": "SINGLE_POSITION_NO_QUEUE",
            "same_tick_reentry": False,
            "session_end": "CLOSE_AT_LAST_TRADE",
            "require_post_fill_event": True,
            "position_size": 1,
        },
        "cost_model": {
            "type": "FIXED_PER_SIDE",
            "version": "1.0.0",
            "points_per_side": "0.5",
        },
        "slippage_model": {
            "type": "FIXED_POINTS",
            "version": "1.0.0",
            "points_per_side": "1",
        },
    }


class StrategyV3Tests(unittest.TestCase):
    def test_valid_strategy_has_stable_fingerprint_and_decimal_inputs(self) -> None:
        first = StrategyDefinitionV3.model_validate(strategy_v3_record())
        second = StrategyDefinitionV3.model_validate(strategy_v3_record())
        self.assertEqual(first.semantic_sha256(), second.semantic_sha256())
        self.assertEqual(first.decimal_inputs(), ["100", "50", "0.5", "1"])

    def test_feature_set_must_be_exact_dependency_closure(self) -> None:
        record = strategy_v3_record()
        record["features"].append(  # type: ignore[union-attr]
            {
                "feature_id": "unused",
                "name": "candle_range",
                "version": "1.0.0",
                "parameters": {},
                "inputs": [],
            }
        )
        with self.assertRaisesRegex(ValidationError, "dependency closure"):
            StrategyDefinitionV3.model_validate(record)

    def test_breakout_must_exclude_current_candle(self) -> None:
        record = strategy_v3_record()
        record["features"] = [
            {
                "feature_id": "breakout",
                "name": "breakout_above_previous_high",
                "version": "1.0.0",
                "parameters": {"period": 5, "include_current": True},
                "inputs": [],
            }
        ]
        record["entry_conditions"]["right"] = {  # type: ignore[index]
            "type": "feature",
            "feature_id": "breakout",
        }
        with self.assertRaisesRegex(Exception, "include_current=false"):
            StrategyDefinitionV3.model_validate(record)

    def test_negative_friction_and_json_float_are_rejected(self) -> None:
        negative = strategy_v3_record()
        negative["cost_model"]["points_per_side"] = "-1"  # type: ignore[index]
        with self.assertRaises(ValidationError):
            StrategyDefinitionV3.model_validate(negative)

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "strategy.json"
            record = copy.deepcopy(strategy_v3_record())
            record["take_profit"]["value"] = 1.5  # type: ignore[index]
            path.write_text(json.dumps(record), encoding="utf-8")
            with self.assertRaisesRegex(Exception, "floating-point"):
                load_strategy_v3(path)


if __name__ == "__main__":
    unittest.main()
