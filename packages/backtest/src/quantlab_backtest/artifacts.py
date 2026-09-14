"""Canonical backtest artifact writers."""

from __future__ import annotations

import hashlib
from pathlib import Path

from quantlab_core.canonical import canonical_json_bytes, sha256_file, write_canonical_json

from quantlab_backtest.models import ClosedTrade


class LedgerWriter:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._stream = path.open("wb")
        self._semantic_digest = hashlib.sha256()
        self._semantic_digest.update(b"quantlab-semantic-hash\ttrade-ledger/v1\n")
        self.count = 0

    def write(self, trade: ClosedTrade) -> None:
        payload = canonical_json_bytes(trade.to_record())
        self._stream.write(payload)
        self._semantic_digest.update(payload)
        self.count += 1

    def close(self) -> dict[str, object]:
        if not self._stream.closed:
            self._stream.close()
        return {
            "file": self.path.name,
            "row_count": self.count,
            "byte_sha256": sha256_file(self.path),
            "semantic_sha256": self._semantic_digest.hexdigest(),
            "schema_version": "trade-ledger/v1",
        }

    def __enter__(self) -> LedgerWriter:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self._stream.close()


def write_metrics(path: Path, metrics: dict[str, object]) -> dict[str, object]:
    write_canonical_json(path, metrics)
    digest = sha256_file(path)
    return {
        "file": path.name,
        "byte_sha256": digest,
        "semantic_sha256": digest,
        "schema_version": "backtest-metrics/v1",
    }
