"""Formal daily-session and historical-dataset contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from quantlab_core.errors import ContractError

TRADING_SESSION_SCHEMA_VERSION = "trading-session/v1"
HISTORICAL_DATASET_SCHEMA_VERSION = "historical-dataset/v1"


@dataclass(frozen=True, slots=True)
class TradingSession:
    session_id: str
    trading_date: str
    logical_asset: str
    physical_contract: str
    first_event_ns_utc: int
    last_event_ns_utc: int
    event_count: int
    price_scale: int
    source_sha256: str
    import_id: str
    normalized_semantic_sha256: str

    def __post_init__(self) -> None:
        try:
            parsed_date = date.fromisoformat(self.trading_date)
        except ValueError as exc:
            raise ContractError(f"invalid trading_date: {exc}") from exc
        if parsed_date.isoformat() != self.trading_date:
            raise ContractError("trading_date must use YYYY-MM-DD")
        for field_name, value in (
            ("session_id", self.session_id),
            ("logical_asset", self.logical_asset),
            ("physical_contract", self.physical_contract),
            ("source_sha256", self.source_sha256),
            ("import_id", self.import_id),
            ("normalized_semantic_sha256", self.normalized_semantic_sha256),
        ):
            if not value or value.strip() != value:
                raise ContractError(
                    f"{field_name} must be non-empty without surrounding whitespace"
                )
        if self.event_count <= 0:
            raise ContractError("a TradingSession must contain at least one event")
        if self.first_event_ns_utc > self.last_event_ns_utc:
            raise ContractError("session event bounds are inverted")
        if not 0 <= self.price_scale <= 9:
            raise ContractError("session price_scale must be between 0 and 9")

    @property
    def conflict_key(self) -> tuple[str, str]:
        return self.logical_asset, self.trading_date


@dataclass(frozen=True, slots=True)
class HistoricalDataset:
    dataset_id: str
    window_fingerprint: str
    logical_asset: str
    max_sessions: int
    timeframes: tuple[str, ...]
    sessions: tuple[TradingSession, ...]

    def __post_init__(self) -> None:
        if self.max_sessions <= 0:
            raise ContractError("max_sessions must be positive")
        if not self.sessions:
            raise ContractError("HistoricalDataset must contain at least one session")
        if len(self.sessions) > self.max_sessions:
            raise ContractError("HistoricalDataset exceeds max_sessions")
        if not self.timeframes or len(set(self.timeframes)) != len(self.timeframes):
            raise ContractError("timeframes must be non-empty and unique")
        previous_date: str | None = None
        conflict_keys: set[tuple[str, str]] = set()
        for session in self.sessions:
            if session.logical_asset != self.logical_asset:
                raise ContractError("all sessions must use the dataset logical_asset")
            if previous_date is not None and session.trading_date <= previous_date:
                raise ContractError("sessions must be strictly ordered by trading_date")
            previous_date = session.trading_date
            if session.conflict_key in conflict_keys:
                raise ContractError(
                    "one physical contract is allowed per logical_asset/trading_date"
                )
            conflict_keys.add(session.conflict_key)
