"""Metadata-only historical selection and explicitly authorized lazy payload resolution."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from quantlab_core.canonical import sha256_file
from quantlab_core.errors import ContractError
from quantlab_core.sessions import HistoricalDataset, TradingSession
from quantlab_core.timeframes import TIMEFRAME_ENGINE_VERSION

from quantlab_data.historical import (
    HISTORICAL_MATERIALIZER_VERSION,
    CachedArtifact,
    MaterializedSession,
    _ensure_candles,
    _identity_key,
)


def _reject(value: str) -> None:
    raise ContractError(f"non-exact JSON number forbidden: {value}")


def load_metadata(path: Path) -> dict:
    try:
        return json.loads(
            path.read_text(encoding="utf-8"), parse_float=_reject, parse_constant=_reject
        )
    except (OSError, ValueError) as exc:
        raise ContractError(f"invalid metadata: {exc}") from exc


def dataset_identity(manifest: dict) -> tuple[str, str]:
    window = {
        "logical_asset": manifest["logical_asset"],
        "max_sessions": manifest["window"]["max_sessions"],
        "selection_policy": "LATEST_AVAILABLE_TRADING_DATES",
        "session_ids": [s["session_id"] for s in manifest["sessions"]],
    }
    window_id = "sha256:" + _identity_key(window)
    payload = {
        "window_fingerprint": window_id,
        "timeframes": manifest["timeframes"],
        "session_reset": True,
        "candle_engine_version": TIMEFRAME_ENGINE_VERSION,
        "materializer_version": HISTORICAL_MATERIALIZER_VERSION,
        "feature_policy": "NOT_MATERIALIZED",
    }
    return "sha256:" + _identity_key(payload), window_id


@dataclass(frozen=True)
class SessionCatalog:
    manifest: dict

    @property
    def sessions(self) -> tuple[TradingSession, ...]:
        fields = TradingSession.__dataclass_fields__
        return tuple(
            TradingSession(**{k: row[k] for k in fields}) for row in self.manifest["sessions"]
        )

    def select(self, max_sessions: int) -> SessionCatalog:
        if type(max_sessions) is not int or max_sessions < 1:
            raise ContractError("max_sessions must be a positive exact integer")
        manifest = {
            **self.manifest,
            "sessions": self.manifest["sessions"][-max_sessions:],
            "window": {
                **self.manifest["window"],
                "max_sessions": max_sessions,
                "selected_sessions": min(len(self.sessions), max_sessions),
            },
        }
        manifest["dataset_id"], manifest["window_fingerprint"] = dataset_identity(manifest)
        return SessionCatalog(manifest)

    def entry(self, session_id: str) -> dict:
        return next(s for s in self.manifest["sessions"] if s["session_id"] == session_id)


def load_session_catalog(path: Path) -> SessionCatalog:
    manifest = load_metadata(path)
    try:
        if manifest["schema_version"] != "market-historical-dataset-manifest/v1":
            raise ContractError("research requires a prepared market historical manifest v1")
        catalog = SessionCatalog(manifest)
        dataset_id, window_id = dataset_identity(manifest)
        if (manifest["dataset_id"], manifest["window_fingerprint"]) != (dataset_id, window_id):
            raise ContractError("historical metadata identity mismatch")
        HistoricalDataset(
            dataset_id,
            window_id,
            manifest["logical_asset"],
            manifest["window"]["max_sessions"],
            tuple(manifest["timeframes"]),
            catalog.sessions,
        )
        return catalog
    except (KeyError, TypeError, ValueError) as exc:
        raise ContractError(f"invalid historical metadata: {exc}") from exc


def verify_artifacts(directory: Path, manifest: dict, verified: set[Path]) -> None:
    for artifact in manifest["artifacts"].values():
        path = (directory / artifact["file"]).resolve()
        if not path.is_relative_to(directory.resolve()):
            raise ContractError("cache artifact escapes its directory")
        if path not in verified:
            if sha256_file(path) != artifact["byte_sha256"]:
                raise ContractError(f"corrupt cache artifact: {path}")
            verified.add(path)


def resolve_session(
    catalog: SessionCatalog,
    session: TradingSession,
    timeframes: tuple[str, ...],
    cache_root: Path,
    verified: set[Path],
) -> MaterializedSession:
    """Caller MUST authorize this session before invocation; metadata selection never calls it."""
    entry = catalog.entry(session.session_id)
    key = entry["cache"]["session"]
    if key != session.session_id.removeprefix("sha256:"):
        raise ContractError("session metadata/cache key mismatch")
    directory = cache_root / "sessions" / key
    manifest = load_metadata(directory / "session-manifest.json")
    if (
        manifest["cache_key"] != key
        or _identity_key(manifest["identity"]) != key
        or manifest["session"] != asdict(session)
    ):
        raise ContractError("session manifest identity mismatch")
    if manifest["artifacts"]["trades"] != entry["artifacts"]["trades"]:
        raise ContractError("session trade fingerprint changed")
    verify_artifacts(directory, manifest, verified)
    trades = CachedArtifact(key, directory, manifest, True)
    candles = {tf: _ensure_candles(trades, session, tf, cache_root) for tf in timeframes}
    for tf, cache in candles.items():
        if (
            tf in entry["artifacts"]["candles"]
            and cache.manifest["artifacts"]["candles"] != entry["artifacts"]["candles"][tf]
        ):
            raise ContractError("candle fingerprint changed")
    return MaterializedSession(session, trades, candles, {})
