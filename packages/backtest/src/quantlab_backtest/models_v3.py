"""Audit records for the friction-aware chronological backtest v3."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from quantlab_core.errors import ContractError
from quantlab_core.time import format_utc_ns


@dataclass(frozen=True, slots=True)
class EntrySignalV3:
    signal_id: str
    session_id: str
    trading_date: str
    logical_asset: str
    physical_contract: str
    timeframe: str
    available_at_ns_utc: int
    candle_open_time_ns_utc: int
    executable_in_session: bool
    non_executable_reason: str | None
    condition_snapshot: dict[str, object]


@dataclass(frozen=True, slots=True)
class OpenPositionV3:
    signal: EntrySignalV3
    direction: str
    entry_timestamp_ns_utc: int
    entry_source_sequence: int
    entry_market_price_units: int
    entry_execution_price_units: int
    target_execution_price_units: int
    stop_execution_price_units: int


@dataclass(frozen=True, slots=True)
class ClosedTradeV3:
    trade_number: int
    signal_id: str
    session_id: str
    trading_date: str
    logical_asset: str
    physical_contract: str
    timeframe: str
    direction: str
    signal_available_at_ns_utc: int
    entry_timestamp_ns_utc: int
    entry_source_sequence: int
    entry_market_price_units: int
    entry_execution_price_units: int
    target_execution_price_units: int
    stop_execution_price_units: int
    exit_timestamp_ns_utc: int
    exit_source_sequence: int
    exit_market_price_units: int
    exit_execution_price_units: int
    exit_reason: str
    gross_pnl_units: int
    slippage_impact_units: int
    costs_units: int
    net_pnl_units: int

    def __post_init__(self) -> None:
        if self.slippage_impact_units < 0:
            raise ContractError("slippage impact cannot be negative")
        if self.costs_units < 0:
            raise ContractError("costs cannot be negative")
        if (
            self.gross_pnl_units - self.slippage_impact_units - self.costs_units
            != self.net_pnl_units
        ):
            raise ContractError("gross - slippage - costs must equal net")

    def to_record(self) -> dict[str, object]:
        record = asdict(self)
        record["schema_version"] = "trade-ledger-entry/v3"
        record["signal_available_at_utc"] = format_utc_ns(
            self.signal_available_at_ns_utc
        )
        record["entry_timestamp_utc"] = format_utc_ns(self.entry_timestamp_ns_utc)
        record["exit_timestamp_utc"] = format_utc_ns(self.exit_timestamp_ns_utc)
        return record


@dataclass(frozen=True, slots=True)
class SignalJournalRecordV3:
    signal_number: int
    signal_id: str
    session_id: str
    trading_date: str
    logical_asset: str
    physical_contract: str
    timeframe: str
    available_at_ns_utc: int
    candle_open_time_ns_utc: int
    outcome: str
    reason: str | None
    condition_snapshot: dict[str, object]

    def to_record(self) -> dict[str, object]:
        record = asdict(self)
        record["schema_version"] = "signal-journal-entry/v3"
        record["available_at_utc"] = format_utc_ns(self.available_at_ns_utc)
        return record


@dataclass(frozen=True, slots=True)
class BacktestSummaryV3:
    metrics: dict[str, object]
    journal_records: int
