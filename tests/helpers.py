from __future__ import annotations

import json
import zipfile
from datetime import date
from pathlib import Path


def strategy_record(
    *,
    symbol: str = "TEST",
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
        "symbol": symbol,
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


def historical_strategy_record(
    *,
    logical_asset: str = "WIN",
    timeframe: str = "1m",
    target: str = "100.125",
    stop: str = "100.125",
    period: int = 2,
) -> dict[str, object]:
    record = strategy_record(
        symbol="IGNORED",
        target=target,
        stop=stop,
        period=period,
    )
    record["schema_version"] = "strategy-definition/v2"
    record["logical_asset"] = logical_asset
    record.pop("symbol")
    record["timeframe"] = timeframe
    record["execution"] = {
        "entry_fill": "NEXT_TRADE",
        "position_policy": "SINGLE_POSITION_NO_QUEUE",
        "same_tick_reentry": False,
        "session_end": "CLOSE_AT_LAST_TRADE",
        "require_post_fill_event": True,
        "position_size": 1,
    }
    return record


def write_historical_strategy(path: Path, **overrides: object) -> None:
    path.write_text(
        json.dumps(historical_strategy_record(**overrides), ensure_ascii=False),
        encoding="utf-8",
    )


B3_HEADER = (
    "DataReferencia;CodigoInstrumento;AcaoAtualizacao;PrecoNegocio;"
    "QuantidadeNegociada;HoraFechamento;CodigoIdentificadorNegocio;"
    "TipoSessaoPregao;DataNegocio;CodigoParticipanteComprador;"
    "CodigoParticipanteVendedor;TipoDoCanal\n"
)


def write_historical_fixture_sources(root: Path) -> list[dict[str, str]]:
    fixture_path = Path(__file__).parent / "fixtures" / "historical" / "twenty_sessions.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    rows: list[dict[str, str]] = []
    for session_index, session in enumerate(fixture["sessions"]):
        trading_date = session["trading_date"]
        contract = session["physical_contract"]
        integer, fraction = session["base_price"].split(",")
        base_hundredths = int(integer) * 100 + int(fraction.ljust(2, "0")[:2])
        prices = [
            base_hundredths,
            base_hundredths + 1000,
            base_hundredths + 1100,
            base_hundredths + 1200,
            base_hundredths + 1200,
        ]
        date_value = date.fromisoformat(trading_date)
        stem = date_value.strftime("%d-%m-%Y") + "_NEGOCIOSAVISTA_DRV"
        zip_path = root / f"{stem}.zip"
        lines = [B3_HEADER]
        for tick_index, price in enumerate(prices):
            whole, cents = divmod(price, 100)
            price_text = f"{whole},{cents:02d}"
            lines.append(
                f"{trading_date};{contract};0;{price_text};1;"
                f"09{tick_index:02d}00000;{session_index * 10 + tick_index + 1};1;"
                f"{trading_date};3;85;1\n"
            )
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(f"{stem}.txt", "".join(lines).encode("utf-8"))
        rows.append({"source": str(zip_path), "physical_contract": contract})
    return rows


def write_historical_catalog(path: Path, sessions: list[dict[str, str]]) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": "historical-sources/v1",
                "logical_asset": "WIN",
                "sessions": sessions,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
