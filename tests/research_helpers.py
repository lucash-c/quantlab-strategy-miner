"""Small synthetic trade files with an analytically controlled morning/tail regime."""

from __future__ import annotations

import json
import zipfile
from datetime import date, timedelta
from pathlib import Path

from helpers import B3_HEADER
from quantlab_data.historical import SessionSource, build_market_history
from quantlab_data.session_catalog import SessionCatalog
from quantlab_mining.contracts import EvaluationConfigV1, MiningSearchSpaceV1
from quantlab_research.contracts import GatePolicyV1


def write_sources(
    root: Path,
    *,
    validation_variant=False,
    discovery_variant=False,
    mixed_scale=False,
    session_count=None,
):
    specification = json.loads(
        (Path(__file__).parent / "fixtures/research/twenty_sessions.json").read_text()
    )
    root.mkdir(parents=True, exist_ok=True)
    day = date.fromisoformat(specification["first_weekday"])
    sources = []
    for index in range(session_count or specification["sessions"]):
        while day.weekday() >= 5:
            day += timedelta(days=1)
        contract = specification["contracts"][
            index >= specification["contract_switch_after_sessions"]
        ]
        quantity = specification["first_session_quantity"] if index == 0 else 1
        rows = [B3_HEADER]
        slope = 1 if index < 13 or validation_variant else -1
        early_slope = -1 if discovery_variant and index < 13 else 1
        for minute in range(specification["minutes"]):
            price = 1000 + index * 100 + early_slope * min(minute, 60) + slope * max(minute - 60, 0)
            hour, civil_minute = divmod(9 * 60 + minute, 60)
            for second in (0, 30):
                value = price + (2 if second else 0)
                time = f"{hour:02d}{civil_minute:02d}{second:02d}000"
                sequence = minute * 2 + (2 if second else 1)
                price_text = f"{value},1234" if mixed_scale and index % 2 else f"{value},00"
                rows.append(
                    f"{day};{contract};0;{price_text};{quantity};{time};{sequence};1;{day};3;85;1\n"
                )
        stem = day.strftime("%d-%m-%Y") + "_NEGOCIOSAVISTA_DRV"
        info = zipfile.ZipInfo(f"{stem}.txt", date_time=(2026, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        path = root / f"{stem}.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(info, "".join(rows).encode())
        sources.append(SessionSource(path, contract))
        day += timedelta(days=1)
    return sources


def research_search(timeframes=("1m",)):
    templates = [
        {
            "template_id": "RANGE",
            "base": {
                "kind": "candle_context",
                "grids": {"mode": ["GEOMETRY"], "feature": ["candle_range"], "threshold": ["0"]},
            },
            "confirmations": [],
        },
        {
            "template_id": "ZERO",
            "base": {
                "kind": "candle_context",
                "grids": {
                    "mode": ["GEOMETRY"],
                    "feature": ["candle_range"],
                    "threshold": ["1000000"],
                },
            },
            "confirmations": [],
        },
        {
            "template_id": "CONCENTRATED",
            "base": {
                "kind": "candle_context",
                "grids": {"mode": ["GEOMETRY"], "feature": ["candle_range"], "threshold": ["0"]},
            },
            "confirmations": [{"kind": "volume", "grids": {"mode": ["RAW"], "threshold": ["50"]}}],
        },
    ]
    return MiningSearchSpaceV1.model_validate(
        {
            "schema_version": "mining-search-space/v1",
            "logical_asset": "WIN",
            "timeframes": list(timeframes),
            "directions": ["BUY", "SELL"],
            "templates": templates,
            "stops": ["5"],
            "targets": ["5"],
            "time_windows": [
                {"start": "09:00", "end": "10:00", "start_inclusive": True, "end_inclusive": False},
                {"start": "10:00", "end": "11:00", "start_inclusive": True, "end_inclusive": False},
            ],
            "constraints": "closed-template-constraints/v1",
        }
    )


def execution_config():
    return EvaluationConfigV1.model_validate(
        {
            "schema_version": "mining-evaluation/v1",
            "max_sessions": 19,
            "cost_model": {"type": "NONE", "version": "1.0.0"},
            "slippage_model": {"type": "NONE", "version": "1.0.0"},
        }
    )


def test_gate(*, validation=False, empty=False, criteria=None):
    return GatePolicyV1.model_validate(
        {
            "schema_version": "validation-gate-policy/v1"
            if validation
            else "discovery-gate-policy/v1",
            "criteria": []
            if empty
            else criteria
            if criteria is not None
            else [
                {"metric": "trades", "operator": "GTE", "threshold": 1},
                {"metric": "net_pnl", "operator": "GTE", "threshold": "0"},
            ],
        }
    )


def prepare(root: Path, *, count=19, timeframes=("1m",), **variants):
    sources = write_sources(root / "sources", session_count=max(count, 20), **variants)
    market = build_market_history(
        sources[:count],
        logical_asset="WIN",
        cache_root=root / "cache",
        timeframes=timeframes,
        max_sessions=count,
    )
    return SessionCatalog(market.manifest), sources
