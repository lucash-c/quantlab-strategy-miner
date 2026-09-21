"""Content-addressed operational cache keyed only by strategy_score_id."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from quantlab_core.canonical import canonical_json_bytes
from quantlab_core.errors import ContractError

from quantlab_scoring.engine import validate_score_record


class StrategyScoreCache:
    def __init__(self, root: Path):
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "strategy-scores.sqlite"
        self.db = sqlite3.connect(self.path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS scores (id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
        )
        self.db.commit()
        self.hits = 0
        self.misses = 0

    def close(self) -> None:
        self.db.close()

    def get(self, strategy_score_id: str) -> dict[str, Any] | None:
        row = self.db.execute(
            "SELECT payload FROM scores WHERE id=?", (strategy_score_id,)
        ).fetchone()
        if row is None:
            self.misses += 1
            return None
        try:
            record = json.loads(row[0])
            validate_score_record(record)
        except (ValueError, json.JSONDecodeError) as exc:
            raise ContractError("corrupt strategy score cache entry") from exc
        if record["strategy_score_id"] != strategy_score_id:
            raise ContractError("strategy score cache key mismatch")
        self.hits += 1
        return record

    def put(self, record: dict[str, Any]) -> None:
        try:
            validate_score_record(record)
        except ValueError as exc:
            raise ContractError("refusing invalid strategy score cache record") from exc
        strategy_score_id = record["strategy_score_id"]
        payload = canonical_json_bytes(record).decode()
        existing = self.db.execute(
            "SELECT payload FROM scores WHERE id=?", (strategy_score_id,)
        ).fetchone()
        if existing is not None and existing[0] != payload:
            raise ContractError("content-address collision in strategy score cache")
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO scores(id,payload) VALUES(?,?)",
                (strategy_score_id, payload),
            )
