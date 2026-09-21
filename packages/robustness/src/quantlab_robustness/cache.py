"""Operational cache keyed by complete scientific inputs; SQLite bytes are never identities."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from quantlab_core.canonical import canonical_json_bytes
from quantlab_core.errors import ContractError
from quantlab_mining.checkpoint import single_writer


class RobustnessResultCache:
    def __init__(self, root: Path):
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "robustness-results.sqlite"
        self._writer = single_writer(self.path)
        self._writer.__enter__()
        self.db = sqlite3.connect(self.path)
        try:
            if self.db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ContractError("corrupt robustness cache")
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS results("
                "input_id TEXT PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL, "
                "fingerprint TEXT NOT NULL)"
            )
            self.db.commit()
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        self.db.close()
        self._writer.__exit__(None, None, None)

    @staticmethod
    def fingerprint(record: dict[str, Any]) -> str:
        return "sha256:" + hashlib.sha256(canonical_json_bytes(record)).hexdigest()

    def get(self, input_id: str, kind: str) -> dict[str, Any] | None:
        row = self.db.execute(
            "SELECT kind,payload,fingerprint FROM results WHERE input_id=?", (input_id,)
        ).fetchone()
        if row is None:
            return None
        stored_kind, payload, fingerprint = row
        try:
            record = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ContractError("corrupt robustness result cache JSON") from exc
        if (
            stored_kind != kind
            or canonical_json_bytes(record).decode() != payload
            or self.fingerprint(record) != fingerprint
        ):
            raise ContractError("corrupt robustness result cache entry")
        return record

    def put(self, input_id: str, kind: str, record: dict[str, Any]) -> None:
        payload = canonical_json_bytes(record).decode()
        fingerprint = self.fingerprint(record)
        with self.db:
            existing = self.db.execute(
                "SELECT kind,payload,fingerprint FROM results WHERE input_id=?", (input_id,)
            ).fetchone()
            expected = (kind, payload, fingerprint)
            if existing is not None and existing != expected:
                raise ContractError("robustness cache input collision")
            self.db.execute(
                "INSERT OR IGNORE INTO results VALUES(?,?,?,?)",
                (input_id, kind, payload, fingerprint),
            )


class RobustnessCheckpoint:
    def __init__(self, path: Path, protocol_id: str):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._writer = single_writer(path)
        self._writer.__enter__()
        self.db = sqlite3.connect(path)
        try:
            if self.db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ContractError("corrupt robustness checkpoint")
            self.db.executescript(
                "CREATE TABLE IF NOT EXISTS context(protocol_id TEXT PRIMARY KEY);"
                "CREATE TABLE IF NOT EXISTS completed("
                "unit_key TEXT PRIMARY KEY,input_id TEXT NOT NULL,"
                "result_fingerprint TEXT NOT NULL);"
            )
            row = self.db.execute("SELECT protocol_id FROM context").fetchone()
            if row is None:
                self.db.execute("INSERT INTO context VALUES(?)", (protocol_id,))
            elif row != (protocol_id,):
                raise ContractError("checkpoint belongs to a different robustness protocol")
            self.db.commit()
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        self.db.close()
        self._writer.__exit__(None, None, None)

    def completed(self, unit_key: str, input_id: str, record: dict[str, Any]) -> bool:
        row = self.db.execute(
            "SELECT input_id,result_fingerprint FROM completed WHERE unit_key=?", (unit_key,)
        ).fetchone()
        if row is None:
            return False
        expected = (input_id, RobustnessResultCache.fingerprint(record))
        if row != expected:
            raise ContractError("checkpoint/cache result mismatch")
        return True

    def mark(self, unit_key: str, input_id: str, record: dict[str, Any]) -> None:
        fingerprint = RobustnessResultCache.fingerprint(record)
        with self.db:
            existing = self.db.execute(
                "SELECT input_id,result_fingerprint FROM completed WHERE unit_key=?", (unit_key,)
            ).fetchone()
            if existing is not None and existing != (input_id, fingerprint):
                raise ContractError("checkpoint unit collision")
            self.db.execute(
                "INSERT OR IGNORE INTO completed VALUES(?,?,?)",
                (unit_key, input_id, fingerprint),
            )
