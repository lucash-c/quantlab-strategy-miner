"""Immutable backtest outputs."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from quantlab_core.time import format_utc_ns


@dataclass(frozen=True, slots=True)
class EntrySignal:
    available_at_ns_utc: int
    candle_open_time_ns_utc: int


@dataclass(frozen=True, slots=True)
class ClosedTrade:
    trade_number: int
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
        record["schema_version"] = "trade-ledger-entry/v1"
        record["signal_available_at_utc"] = format_utc_ns(self.signal_available_at_ns_utc)
        record["entry_timestamp_utc"] = format_utc_ns(self.entry_timestamp_ns_utc)
        record["exit_timestamp_utc"] = format_utc_ns(self.exit_timestamp_ns_utc)
        return record


@dataclass(frozen=True, slots=True)
class OpenPosition:
    direction: str
    signal_available_at_ns_utc: int
    entry_timestamp_ns_utc: int
    entry_source_sequence: int
    entry_price_units: int
    target_price_units: int
    stop_price_units: int

    def to_record(self) -> dict[str, object]:
        record = asdict(self)
        record["signal_available_at_utc"] = format_utc_ns(self.signal_available_at_ns_utc)
        record["entry_timestamp_utc"] = format_utc_ns(self.entry_timestamp_ns_utc)
        return record


@dataclass(frozen=True, slots=True)
class BacktestSummary:
    metrics: dict[str, object]
    open_position: OpenPosition | None
