"""Immutable market-data domain models."""

from __future__ import annotations

from dataclasses import dataclass

from quantlab_core.errors import ContractError


@dataclass(frozen=True, slots=True)
class MarketTrade:
    symbol: str
    timestamp_ns_utc: int
    source_sequence: int
    price_units: int
    quantity: int

    def __post_init__(self) -> None:
        if not self.symbol:
            raise ContractError("symbol cannot be empty")
        if self.source_sequence < 0:
            raise ContractError("source_sequence cannot be negative")
        if self.price_units <= 0:
            raise ContractError("price must be positive")
        if self.quantity <= 0:
            raise ContractError("quantity must be positive")

    @property
    def order_key(self) -> tuple[int, int]:
        return self.timestamp_ns_utc, self.source_sequence


@dataclass(frozen=True, slots=True)
class Candle:
    symbol: str
    timeframe: str
    open_time_ns_utc: int
    close_time_ns_utc: int
    open_units: int
    high_units: int
    low_units: int
    close_units: int
    volume: int
    trade_count: int

    def __post_init__(self) -> None:
        if self.close_time_ns_utc <= self.open_time_ns_utc:
            raise ContractError("candle close must be after candle open")
        if not (self.low_units <= self.open_units <= self.high_units):
            raise ContractError("candle open is outside low/high")
        if not (self.low_units <= self.close_units <= self.high_units):
            raise ContractError("candle close is outside low/high")
        if self.volume <= 0 or self.trade_count <= 0:
            raise ContractError("candle must contain at least one trade")


@dataclass(frozen=True, slots=True)
class FeatureRow:
    candle: Candle
    sma_close_sum_units: int | None
    sma_close_period: int | None
    available_at_ns_utc: int
