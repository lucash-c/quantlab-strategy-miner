"""Small, completely explicit generation universes used by local and CI tests."""

from quantlab_mining.contracts import EvaluationConfigV1, MiningSearchSpaceV1


def search_record(*, fixture160: bool = False) -> dict:
    return {
        "schema_version": "mining-search-space/v1",
        "logical_asset": "WIN",
        "timeframes": ["1m", "2m", "5m", "15m"] if fixture160 else ["1m"],
        "directions": ["BUY", "SELL"],
        "templates": [
            {
                "template_id": "TREND.CLOSE",
                "base": {
                    "kind": "trend_close",
                    "grids": {
                        "average": ["sma_close"],
                        "period": [2, 3] if fixture160 else [2],
                        "operator": ["COMPARE"],
                    },
                },
                "confirmations": [],
            },
            *(
                [
                    {
                        "template_id": "TREND.PAIR",
                        "base": {
                            "kind": "trend_pair",
                            "grids": {
                                "average": ["ema_close"],
                                "short_period": [2, 3],
                                "long_period": [3, 4],
                                "operator": ["COMPARE"],
                            },
                        },
                        "confirmations": [],
                    }
                ]
                if fixture160
                else []
            ),
        ],
        "stops": ["5", "10"] if fixture160 else ["5"],
        "targets": ["5", "10"] if fixture160 else ["5"],
        "time_windows": [
            {"start": "09:00", "end": "18:45", "start_inclusive": True, "end_inclusive": False}
        ],
        "constraints": "closed-template-constraints/v1",
    }


def small_search() -> MiningSearchSpaceV1:
    return MiningSearchSpaceV1.model_validate(search_record())


def evaluation() -> EvaluationConfigV1:
    return EvaluationConfigV1.model_validate(
        {
            "schema_version": "mining-evaluation/v1",
            "max_sessions": 19,
            "cost_model": {"type": "FIXED_PER_SIDE", "version": "1.0.0", "points_per_side": "1"},
            "slippage_model": {"type": "FIXED_POINTS", "version": "1.0.0", "points_per_side": "1"},
        }
    )
