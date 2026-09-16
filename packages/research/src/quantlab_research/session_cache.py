"""Transactional operational index + immutable aggregate packs; logical hashes ignore packing."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from collections.abc import Callable, Iterator
from contextlib import closing
from dataclasses import asdict
from pathlib import Path

from quantlab_backtest.engine_v3 import FeatureSessionInputV3, run_backtest_v3
from quantlab_core.canonical import canonical_json_bytes, sha256_file
from quantlab_core.errors import ContractError
from quantlab_core.feature_engine_v2 import mathematical_policy_record
from quantlab_core.price import common_decimal_scale
from quantlab_core.strategy_v3 import StrategyDefinitionV3
from quantlab_data.feature_cache_v2 import MaterializedFeatureSession, read_feature_observations
from quantlab_data.historical import read_session_candles, read_session_trades
from quantlab_mining.batch import ENGINE_VERSIONS
from quantlab_mining.checkpoint import single_writer
from quantlab_mining.contracts import identity

from quantlab_research.contracts import SESSION_POLICY

SESSION_EVALUATION_VERSION = "candidate-session-evaluation/v1"
PACK_CANDIDATES = 64


class SessionEvaluationCache:
    def __init__(self, root: Path):
        self.root = root / "session-evaluations"
        self.root.mkdir(parents=True, exist_ok=True)
        self.database = self.root / "index.sqlite"
        self._writer = single_writer(self.database)
        self._writer.__enter__()
        self.db = sqlite3.connect(self.database)
        if self.db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ContractError("corrupt session-evaluation index")
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS evaluations(
                id TEXT PRIMARY KEY, session_id TEXT NOT NULL, record TEXT NOT NULL,
                fingerprint TEXT NOT NULL, pack TEXT);
            CREATE TABLE IF NOT EXISTS session_audit(
                id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL,
                record TEXT NOT NULL,
                PRIMARY KEY(id,kind,ordinal));
        """)
        self.verified_packs: set[str] = set()

    def close(self) -> None:
        self.db.close()
        self._writer.__exit__(None, None, None)

    def audit(self, evaluation_id: str, kind: str) -> Iterator[dict]:
        for ordinal, payload in self.db.execute(
            "SELECT ordinal,record FROM session_audit WHERE id=? AND kind=? ORDER BY ordinal",
            (evaluation_id, kind),
        ):
            record = json.loads(payload)
            if record.get("trade_number" if kind == "ledger" else "signal_number") != ordinal + 1:
                raise ContractError("corrupt session audit ordinal")
            if canonical_json_bytes(record).decode() != payload:
                raise ContractError("non-canonical session audit")
            yield record

    def fingerprint(self, record: dict) -> str:
        content = {k: v for k, v in record.items() if k != "result_fingerprint"}
        digest = hashlib.sha256(
            canonical_json_bytes({"domain": "session-result/v1", "record": content})
        )
        eid = record["evaluation_id"]
        for kind in ("ledger", "journal"):
            count = 0
            for raw in self.audit(eid, kind):
                if raw["session_id"] != record["session"]["session_id"]:
                    raise ContractError("corrupt session audit identity")
                digest.update(canonical_json_bytes({"kind": kind, "record": raw}))
                count += 1
            if count != record[kind + "_count"]:
                raise ContractError("corrupt session audit count")
        return "sha256:" + digest.hexdigest()

    def load(self, evaluation_id: str) -> dict | None:
        row = self.db.execute(
            "SELECT record,fingerprint,pack FROM evaluations WHERE id=?", (evaluation_id,)
        ).fetchone()
        if row is None:
            if self.db.execute(
                "SELECT 1 FROM session_audit WHERE id=?", (evaluation_id,)
            ).fetchone():
                raise ContractError("orphan session audit")
            return None
        payload, fingerprint, pack = row
        record = json.loads(payload)
        if identity(SESSION_EVALUATION_VERSION, record["input"]) != evaluation_id:
            raise ContractError("session evaluation input identity mismatch")
        if (
            canonical_json_bytes(record).decode() != payload
            or record["evaluation_id"] != evaluation_id
            or record["result_fingerprint"] != fingerprint
            or self.fingerprint(record) != fingerprint
        ):
            raise ContractError("corrupt session evaluation fingerprint")
        if pack is None:
            self.publish_pending(record["session"]["session_id"])
            return self.load(evaluation_id)
        path = self.root / "packs" / (pack + ".jsonl")
        if pack not in self.verified_packs:
            if sha256_file(path) != pack:
                raise ContractError("corrupt immutable session pack")
            # Ensure the operational index has not diverged from its scientific pack.
            with path.open(encoding="utf-8") as stream:
                for line in stream:
                    item = json.loads(line)
                    if canonical_json_bytes(item).decode() != line:
                        raise ContractError("non-canonical session pack")
                    if item["kind"] == "evaluation":
                        stored = self.db.execute(
                            "SELECT record FROM evaluations WHERE id=?", (item["id"],)
                        ).fetchone()
                    else:
                        stored = self.db.execute(
                            "SELECT record FROM session_audit WHERE id=? AND kind=? AND ordinal=?",
                            (item["id"], item["kind"], item["ordinal"]),
                        ).fetchone()
                    if stored != (canonical_json_bytes(item["record"]).decode(),):
                        raise ContractError("session index/pack mismatch")
            self.verified_packs.add(pack)
        return record

    def publish_pending(self, session_id: str | None = None) -> None:
        """READY transactions survive interruption; publish packs before exposing cached results."""
        query = "SELECT id,record FROM evaluations WHERE pack IS NULL"
        params = ()
        if session_id is not None:
            query += " AND session_id=?"
            params = (session_id,)
        query += " ORDER BY session_id,id"
        with closing(self.db.cursor()) as cursor:
            cursor.execute(query, params)
            while rows := cursor.fetchmany(PACK_CANDIDATES):
                directory = self.root / "packs"
                directory.mkdir(exist_ok=True)
                fd, name = tempfile.mkstemp(prefix=".pack-", dir=directory)
                temporary = Path(name)
                try:
                    with os.fdopen(fd, "wb") as output:
                        for eid, payload in rows:
                            record = json.loads(payload)
                            if self.fingerprint(record) != record["result_fingerprint"]:
                                raise ContractError("cannot publish corrupt session result")
                            output.write(
                                canonical_json_bytes(
                                    {"id": eid, "kind": "evaluation", "record": record}
                                )
                            )
                            for kind in ("ledger", "journal"):
                                for ordinal, raw in enumerate(self.audit(eid, kind)):
                                    output.write(
                                        canonical_json_bytes(
                                            {
                                                "id": eid,
                                                "kind": kind,
                                                "ordinal": ordinal,
                                                "record": raw,
                                            }
                                        )
                                    )
                        output.flush()
                        os.fsync(output.fileno())
                    pack = sha256_file(temporary)
                    destination = directory / (pack + ".jsonl")
                    if destination.exists():
                        if sha256_file(destination) != pack:
                            raise ContractError("corrupt existing session pack")
                        temporary.unlink()
                    else:
                        os.replace(temporary, destination)
                    with self.db:
                        self.db.executemany(
                            "UPDATE evaluations SET pack=? WHERE id=?",
                            ((pack, eid) for eid, _ in rows),
                        )
                finally:
                    temporary.unlink(missing_ok=True)

    def evaluate(
        self,
        candidate_id: str,
        item: MaterializedFeatureSession,
        strategy: StrategyDefinitionV3,
        access: Callable[[str], None],
    ) -> tuple[dict, bool]:
        session = item.market.session
        scale = common_decimal_scale([session.price_scale], strategy.decimal_inputs())
        payload = {
            "candidate_id": candidate_id,
            "session_id": session.session_id,
            "physical_contract": session.physical_contract,
            "trades": item.market.trades.manifest["artifacts"]["trades"]["semantic_sha256"],
            "candles": item.market.candles[strategy.timeframe].manifest["artifacts"]["candles"][
                "semantic_sha256"
            ],
            "features": [
                {
                    "spec": spec.to_record(),
                    "fingerprint": item.features[spec.feature_id].manifest["artifacts"][
                        "feature_values"
                    ]["semantic_sha256"],
                }
                for spec in strategy.feature_specs
            ],
            "cost_model": strategy.cost_model.model_dump(mode="json"),
            "slippage_model": strategy.slippage_model.model_dump(mode="json"),
            "price_scale": scale,
            "engines": {
                k: ENGINE_VERSIONS[k] for k in ("backtest", "metrics", "conditions", "features")
            },
            "math": mathematical_policy_record(),
            "session_policy": SESSION_POLICY,
            "version": SESSION_EVALUATION_VERSION,
        }
        eid = identity(SESSION_EVALUATION_VERSION, payload)
        access("session_evaluation")
        existing = self.load(eid)
        if existing:
            return existing, True
        counts = {"ledger": 0, "journal": 0}

        def persist(kind: str, raw: object) -> None:
            record = raw.to_record()
            self.db.execute(
                "INSERT INTO session_audit VALUES(?,?,?,?)",
                (eid, kind, counts[kind], canonical_json_bytes(record).decode()),
            )
            counts[kind] += 1

        access("ticks")
        access("candles")
        access("features")
        with self.db:
            summary = run_backtest_v3(
                [
                    FeatureSessionInputV3(
                        session,
                        read_session_trades(item.market.trades.directory / "trades.parquet"),
                        read_session_candles(
                            item.market.candles[strategy.timeframe].directory / "candles.parquet"
                        ),
                        {
                            spec.feature_id: read_feature_observations(
                                item.features[spec.feature_id].directory / "feature-values.parquet"
                            )
                            for spec in strategy.feature_specs
                        },
                    )
                ],
                strategy,
                common_price_scale=scale,
                on_trade=lambda r: persist("ledger", r),
                on_signal=lambda r: persist("journal", r),
            )
            record = {
                "schema_version": SESSION_EVALUATION_VERSION,
                "evaluation_id": eid,
                "candidate_id": candidate_id,
                "input": payload,
                "session": asdict(session),
                "price_scale": scale,
                "backtest_metrics": summary.metrics,
                "ledger_count": counts["ledger"],
                "journal_count": counts["journal"],
            }
            record["result_fingerprint"] = self.fingerprint(record)
            self.db.execute(
                "INSERT INTO evaluations VALUES(?,?,?,?,NULL)",
                (
                    eid,
                    session.session_id,
                    canonical_json_bytes(record).decode(),
                    record["result_fingerprint"],
                ),
            )
        return record, False
