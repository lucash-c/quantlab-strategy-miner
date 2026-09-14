from __future__ import annotations

import json
from pathlib import Path


def strategy_record(
    *,
    direction: str = "BUY",
    target: str = "5",
    stop: str = "10",
    period: int = 2,
    end_of_data: str = "CLOSE_AT_LAST_TRADE",
) -> dict[str, object]:
    indicator_definition = {
        "name": "sma_close",
        "version": "1.0.0",
        "parameters": {"period": period},
    }
    return {
        "schema_version": "strategy-definition/v1",
        "strategy_id": "TEST.SMA",
        "strategy_version": 1,
        "name": "Technical deterministic fixture",
        "symbol": "TEST",
        "timeframe": "1m",
        "evaluation_mode": "ON_CLOSE",
        "direction": direction,
        "required_indicators": [indicator_definition],
        "entry_conditions": {
            "type": "logical",
            "operator": "AND",
            "children": [
                {
                    "type": "comparison",
                    "operator": "GT" if direction == "BUY" else "LT",
                    "left": {"type": "field", "name": "close"},
                    "right": {
                        "type": "indicator",
                        "name": "sma_close",
                        "version": "1.0.0",
                        "parameters": {"period": period},
                    },
                }
            ],
        },
        "take_profit": {"unit": "POINTS", "value": target},
        "stop_loss": {"unit": "POINTS", "value": stop},
        "execution": {
            "entry_fill": "NEXT_TRADE",
            "position_policy": "SINGLE_POSITION_NO_QUEUE",
            "same_tick_reentry": False,
            "end_of_data": end_of_data,
            "position_size": 1,
        },
        "cost_model": {"type": "NONE"},
        "slippage_model": {"type": "NONE"},
    }


def write_strategy(path: Path, **overrides: object) -> None:
    path.write_text(
        json.dumps(strategy_record(**overrides), ensure_ascii=False),
        encoding="utf-8",
    )
