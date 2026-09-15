from __future__ import annotations

import unittest

from quantlab_core.condition_engine_v2 import (
    ConditionEvaluatorV2,
    FeatureFrame,
    time_range_contains,
)
from quantlab_core.feature_engine_v2 import FeatureObservation, FeatureValue
from quantlab_core.market_data import Candle
from quantlab_core.numeric import CanonicalRational
from quantlab_core.sessions import TradingSession
from quantlab_core.strategy_v3 import (
    ComparisonConditionV3,
    CrossConditionV3,
    LogicalConditionV3,
    RangeConditionV3,
    TimeRangeV3,
)
from quantlab_core.time import NANOSECONDS_PER_MINUTE, parse_iso8601_ns


def frame(value: int | None, *, minute: int = 1) -> FeatureFrame:
    start = parse_iso8601_ns("2026-09-10T09:00:00-03:00")
    session = TradingSession(
        "s", "2026-09-10", "WIN", "WINV26", start, start + 20 * NANOSECONDS_PER_MINUTE,
        20, 0, "source", "import", "normalized",
    )
    candle = Candle(
        "WINV26", "1m", start + (minute - 1) * NANOSECONDS_PER_MINUTE,
        start + minute * NANOSECONDS_PER_MINUTE, 100, 101, 99, 100, 10, 1,
    )
    observation = FeatureObservation(
        "s", "2026-09-10", "WIN", "WINV26", "1m", candle.open_time_ns_utc,
        candle.close_time_ns_utc, candle.close_time_ns_utc,
        "READY", True, None,
        None if value is None else FeatureValue.number("PRICE", CanonicalRational(value)),
        "ZERO_RANGE" if value is None else None,
    )
    return FeatureFrame(session, candle, {"feature": observation})


def feature_operand() -> dict[str, str]:
    return {"type": "feature", "feature_id": "feature"}


def price(value: str) -> dict[str, str]:
    return {"type": "constant", "dimension": "PRICE", "value": value}


class ConditionEngineV2Tests(unittest.TestCase):
    def test_comparisons_and_logical_not(self) -> None:
        comparison = ComparisonConditionV3.model_validate(
            {
                "type": "comparison",
                "operator": "GTE",
                "left": feature_operand(),
                "right": price("2"),
            }
        )
        logical = LogicalConditionV3.model_validate(
            {"type": "logical", "operator": "NOT", "children": [comparison.model_dump()]}
        )
        self.assertFalse(ConditionEvaluatorV2().evaluate(logical, frame(3)).result)

    def test_between_has_explicit_boundaries(self) -> None:
        condition = RangeConditionV3.model_validate(
            {
                "type": "range", "operator": "BETWEEN", "value": feature_operand(),
                "lower": price("1"), "upper": price("3"),
                "lower_inclusive": False, "upper_inclusive": True,
            }
        )
        evaluator = ConditionEvaluatorV2()
        self.assertFalse(evaluator.evaluate(condition, frame(1)).result)
        self.assertTrue(evaluator.evaluate(condition, frame(3)).result)

    def test_all_comparison_and_range_operators_are_exact(self) -> None:
        expected = {
            "GT": False,
            "GTE": True,
            "LT": False,
            "LTE": True,
            "EQ": True,
            "NE": False,
        }
        for operator, result in expected.items():
            condition = ComparisonConditionV3.model_validate(
                {
                    "type": "comparison",
                    "operator": operator,
                    "left": feature_operand(),
                    "right": price("2"),
                }
            )
            self.assertEqual(
                ConditionEvaluatorV2().evaluate(condition, frame(2)).result,
                result,
            )
        not_between = RangeConditionV3.model_validate(
            {
                "type": "range",
                "operator": "NOT_BETWEEN",
                "value": feature_operand(),
                "lower": price("1"),
                "upper": price("3"),
                "lower_inclusive": True,
                "upper_inclusive": True,
            }
        )
        self.assertFalse(
            ConditionEvaluatorV2().evaluate(not_between, frame(2)).result
        )

    def test_cross_equality_undefined_and_restart(self) -> None:
        above = CrossConditionV3.model_validate(
            {
                "type": "cross",
                "operator": "CROSS_ABOVE",
                "left": feature_operand(),
                "right": price("2"),
            }
        )
        evaluator = ConditionEvaluatorV2()
        results = [
            evaluator.evaluate(above, frame(value, minute=index + 1)).result
            for index, value in enumerate((1, 2, 3, None, 3, 1, 3))
        ]
        self.assertEqual(results, [False, False, True, False, False, False, True])

        below = CrossConditionV3.model_validate(
            {
                "type": "cross",
                "operator": "CROSS_BELOW",
                "left": feature_operand(),
                "right": price("2"),
            }
        )
        evaluator = ConditionEvaluatorV2()
        self.assertEqual(
            [evaluator.evaluate(below, frame(value)).result for value in (3, 2, 1)],
            [False, False, True],
        )

    def test_cross_does_not_survive_new_evaluator_session(self) -> None:
        condition = CrossConditionV3.model_validate(
            {
                "type": "cross",
                "operator": "CROSS_ABOVE",
                "left": feature_operand(),
                "right": price("2"),
            }
        )
        first = ConditionEvaluatorV2()
        first.evaluate(condition, frame(1))
        self.assertFalse(ConditionEvaluatorV2().evaluate(condition, frame(3)).result)

    def test_time_range_boundaries_use_b3_local_time(self) -> None:
        value = TimeRangeV3(
            start="09:15", end="11:30", start_inclusive=True, end_inclusive=False
        )
        self.assertTrue(time_range_contains(value, parse_iso8601_ns("2026-09-10T09:15:00-03:00")))
        self.assertFalse(time_range_contains(value, parse_iso8601_ns("2026-09-10T11:30:00-03:00")))


if __name__ == "__main__":
    unittest.main()
