"""Transactional operational state, verified before reuse; never hashed as a file."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from quantlab_core.canonical import canonical_json_bytes
from quantlab_core.errors import ContractError


@contextmanager
def single_writer(database: Path) -> Iterator[None]:
    database.parent.mkdir(parents=True, exist_ok=True)
    lock = database.with_suffix(database.suffix + ".lock")
    with lock.open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ContractError("checkpoint already has an active writer") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def initialize(db: sqlite3.Connection, context: dict[str, Any]) -> None:
    if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
        raise ContractError("corrupt checkpoint database")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS checkpoint_meta(context TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS results(
            id TEXT PRIMARY KEY, evaluation_id TEXT NOT NULL, metrics TEXT NOT NULL,
            digest TEXT NOT NULL, ledger_count INTEGER NOT NULL, journal_count INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS audit(
            id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, payload TEXT NOT NULL,
            PRIMARY KEY(id,kind,ordinal));
    """)
    payload = canonical_json_bytes(context).decode("utf-8")
    existing = db.execute("SELECT context FROM checkpoint_meta").fetchall()
    if existing:
        if existing != [(payload,)]:
            raise ContractError(
                "CHECKPOINT_CONTEXT_MISMATCH: identities, versions or hashes changed"
            )
    else:
        db.execute("INSERT INTO checkpoint_meta VALUES (?)", (payload,))
        db.commit()


def result_digest(
    db: sqlite3.Connection, cid: str, eid: str, metrics: str, counts: tuple[int, int]
) -> str:
    digest = hashlib.sha256(
        canonical_json_bytes(
            {
                "domain": "batch-result-integrity/v1",
                "candidate_id": cid,
                "evaluation_id": eid,
                "metrics": json.loads(metrics),
            }
        )
    )
    for kind, expected in zip(("ledger", "journal"), counts, strict=True):
        count = 0
        for ordinal, payload in db.execute(
            "SELECT ordinal,payload FROM audit WHERE id=? AND kind=? ORDER BY ordinal", (cid, kind)
        ):
            if (
                ordinal != count
                or canonical_json_bytes(json.loads(payload)).decode("utf-8") != payload
            ):
                raise ContractError("corrupt checkpoint audit sequence/serialization")
            digest.update(
                canonical_json_bytes(
                    {"kind": kind, "ordinal": ordinal, "record": json.loads(payload)}
                )
            )
            count += 1
        if count != expected:
            raise ContractError("corrupt checkpoint audit count")
    return digest.hexdigest()


def validate_completed(db: sqlite3.Connection, evaluations: dict[str, str]) -> int:
    count = 0
    for cid, eid, metrics, digest, ledgers, journals in db.execute(
        "SELECT * FROM results ORDER BY id"
    ):
        if evaluations.get(cid) != eid:
            raise ContractError("checkpoint evaluation identity mismatch")
        if result_digest(db, cid, eid, metrics, (ledgers, journals)) != digest:
            raise ContractError("corrupt checkpoint result fingerprint")
        count += 1
    if db.execute("SELECT count(*) FROM audit WHERE id NOT IN (SELECT id FROM results)").fetchone()[
        0
    ]:
        raise ContractError("checkpoint contains uncommitted/orphan audit rows")
    return count
