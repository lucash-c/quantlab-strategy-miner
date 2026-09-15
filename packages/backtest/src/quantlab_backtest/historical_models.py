"""Immutable session-aware backtest records."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from quantlab_core.time import format_utc_ns


@dataclass(frozen=True, slots=True)
class HistoricalEntrySignal:
    session_id: str
    trading_date: str
    logical_asset: str
    physical_contract: str
    timeframe: str
    available_at_ns_utc: int
    candle_open_time_ns_utc: int
    executable_in_session: bool
    non_executable_reason: str | None


@dataclass(frozen=True, slots=True)
class HistoricalOpenPosition:
    signal: HistoricalEntrySignal
    direction: str
    entry_timestamp_ns_utc: int
    entry_source_sequence: int
    entry_price_units: int
    target_price_units: int
    stop_price_units: int


@dataclass(frozen=True, slots=True)
class HistoricalClosedTrade:
    trade_number: int
    session_id: str
    trading_date: str
    logical_asset: str
    physical_contract: str
    timeframe: str
    direction: str
    signal_available_at_ns_utc: int
    entry_timestamp_ns_utc: int
    entry_source_sequence: int
    entry_price_units: int
    exit_timestamp_ns_utc: int
    exit_source_sequence: int
    exit_price_units: int
    exit_reason: str
    pnl_units: int

    def to_record(self) -> dict[str, object]:
        record = asdict(self)
        record["schema_version"] = "trade-ledger-entry/v2"
        record["signal_available_at_utc"] = format_utc_ns(self.signal_available_at_ns_utc)
        record["entry_timestamp_utc"] = format_utc_ns(self.entry_timestamp_ns_utc)
        record["exit_timestamp_utc"] = format_utc_ns(self.exit_timestamp_ns_utc)
        return record


@dataclass(frozen=True, slots=True)
class DiscardedSignal:
    discard_number: int
    session_id: str
    trading_date: str
    logical_asset: str
    physical_contract: str
    timeframe: str
    available_at_ns_utc: int
    candle_open_time_ns_utc: int
    reason: str

    def to_record(self) -> dict[str, object]:
        record = asdict(self)
        record["schema_version"] = "discarded-signal/v1"
        record["available_at_utc"] = format_utc_ns(self.available_at_ns_utc)
        return record


@dataclass(frozen=True, slots=True)
class HistoricalBacktestSummary:
    metrics: dict[str, object]
    discarded_signals: int

