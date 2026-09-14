from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from helpers import strategy_record, write_strategy
from pydantic import ValidationError
from quantlab_core.errors import ContractError
from quantlab_core.evaluator import evaluate_condition
from quantlab_core.market_data import Candle, FeatureRow
from quantlab_core.strategy import StrategyDefinition, load_strategy


class StrategySchemaTests(unittest.TestCase):
    def test_strategy_is_strict_and_canonical(self) -> None:
        strategy = StrategyDefinition.model_validate(strategy_record())
        self.assertEqual(strategy.sma_period, 2)
        self.assertEqual(strategy.canonical_bytes(), strategy.canonical_bytes())

    def test_json_float_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "strategy.json"
            write_strategy(path)
            text = path.read_text(encoding="utf-8").replace('"value": "5"', '"value": 5.0')
            path.write_text(text, encoding="utf-8")
            with self.assertRaisesRegex(ContractError, "floating-point"):
                load_strategy(path)

    def test_indicator_declaration_must_match_reference(self) -> None:
        record = strategy_record()
        record["required_indicators"][0]["parameters"]["period"] = 3
        with self.assertRaises(ValidationError):
            StrategyDefinition.model_validate(record)

    def test_exact_rational_comparison(self) -> None:
        strategy = StrategyDefinition.model_validate(strategy_record())
        candle = Candle("TEST", "1m", 0, 60, 101, 101, 101, 101, 1, 1)
        feature = FeatureRow(candle, 201, 2, 60)
        self.assertTrue(evaluate_condition(strategy.entry_conditions, feature, 0))


if __name__ == "__main__":
    unittest.main()
