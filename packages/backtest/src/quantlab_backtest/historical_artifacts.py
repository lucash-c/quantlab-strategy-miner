"""Canonical v2 ledger and discarded-signal writers."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Protocol

from quantlab_core.canonical import canonical_json_bytes, sha256_file


class _Record(Protocol):
    def to_record(self) -> dict[str, object]: ...


class CanonicalJsonlWriter:
    def __init__(self, path: Path, schema_version: str) -> None:
        self.path = path
        self.schema_version = schema_version
        path.parent.mkdir(parents=True, exist_ok=True)
        self._stream = path.open("wb")
        self._digest = hashlib.sha256()
        self._digest.update(f"quantlab-semantic-hash\t{schema_version}\n".encode())
        self.count = 0

    def write(self, record: _Record) -> None:
        payload = canonical_json_bytes(record.to_record())
        self._stream.write(payload)
        self._digest.update(payload)
        self.count += 1

    def close(self) -> dict[str, object]:
        if not self._stream.closed:
            self._stream.close()
        return {
            "file": self.path.name,
            "row_count": self.count,
            "byte_sha256": sha256_file(self.path),
            "semantic_sha256": self._digest.hexdigest(),
            "schema_version": self.schema_version,
        }

    def __enter__(self) -> CanonicalJsonlWriter:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self._stream.close()

