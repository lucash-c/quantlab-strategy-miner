"""Exact disk-backed preflight: no market access and no backtest side effects."""

from __future__ import annotations

import itertools
import json
import sqlite3
from collections import Counter
from collections.abc import Iterator
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from quantlab_core.canonical import canonical_json_bytes
from quantlab_core.errors import ContractError
from quantlab_core.strategy_v3 import StrategyDefinitionV3

from quantlab_mining.canonicalization import named_candidate
from quantlab_mining.contracts import (
    CANONICALIZATION_VERSION,
    GENERATOR_VERSION,
    TEMPLATE_REGISTRY_VERSION,
    GenerationPolicyV1,
    MiningSearchSpaceV1,
    identity,
)
from quantlab_mining.contradictions import contradiction
from quantlab_mining.templates import StrategyBuilder, bindings, validate_search


@dataclass(frozen=True)
class CandidatePlan:
    database: Path
    manifest: dict

    def candidates(self) -> Iterator[tuple[str, StrategyDefinitionV3]]:
        with closing(sqlite3.connect(self.database)) as db:
            for cid, payload in db.execute("SELECT id, strategy FROM candidates ORDER BY id"):
                yield cid, StrategyDefinitionV3.model_validate(json.loads(payload))


def preflight(
    space: MiningSearchSpaceV1, policy: GenerationPolicyV1, database: Path
) -> CandidatePlan:
    theoretical = validate_search(space, policy)  # Count analytically BEFORE any expansion.
    database.parent.mkdir(parents=True, exist_ok=True)
    rejected: Counter[str] = Counter()
    duplicate_count = 0
    unique_count = 0
    with closing(sqlite3.connect(database)) as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS candidates(id TEXT PRIMARY KEY, strategy TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS provenance(
                id TEXT NOT NULL, origin TEXT NOT NULL, multiplicity INTEGER NOT NULL,
                PRIMARY KEY(id, origin));
            DELETE FROM candidates;
            DELETE FROM provenance;
        """)
        for entry in space.templates:
            for direction in space.directions:
                atom_grids = [entry.base, *entry.confirmations]
                for parameters in itertools.product(*(bindings(a, direction) for a in atom_grids)):
                    if (
                        entry.base.kind == "trend_pair"
                        and parameters[0]["short_period"] >= parameters[0]["long_period"]
                    ):
                        rejected["SHORT_PERIOD_NOT_LESS_THAN_LONG"] += (
                            len(space.timeframes)
                            * len(space.stops)
                            * len(space.targets)
                            * len(space.time_windows)
                        )
                        continue
                    for tf, stop, target, window in itertools.product(
                        space.timeframes, space.stops, space.targets, space.time_windows
                    ):
                        builder = StrategyBuilder(direction)
                        atoms = [
                            builder.atom(a.kind, p)
                            for a, p in zip(atom_grids, parameters, strict=True)
                        ]
                        if len(builder.features) > policy.max_features:
                            rejected["MAX_FEATURES"] += 1
                            continue
                        strategy = builder.strategy(space, tf, stop, target, window, atoms)
                        cid, strategy = named_candidate(strategy)
                        reason = contradiction(strategy.model_dump(mode="json")["entry_conditions"])
                        if reason:
                            rejected[reason] += 1
                            continue
                        payload = strategy.canonical_bytes().decode("utf-8")
                        existing = db.execute(
                            "SELECT strategy FROM candidates WHERE id=?", (cid,)
                        ).fetchone()
                        if existing is None:
                            db.execute("INSERT INTO candidates VALUES (?,?)", (cid, payload))
                            unique_count += 1
                        else:
                            if existing[0] != payload:
                                raise ContractError(
                                    "candidate hash collision or inconsistent canonical payload"
                                )
                            duplicate_count += 1
                        origin = canonical_json_bytes(
                            {
                                "template_id": entry.template_id,
                                "family": entry.base.kind,
                                "parameters": list(parameters),
                                "timeframe": tf,
                                "direction": direction,
                                "stop": stop,
                                "target": target,
                                "time_window": window.model_dump(mode="json"),
                            }
                        ).decode("utf-8")
                        db.execute(
                            """INSERT INTO provenance VALUES (?,?,1)
                            ON CONFLICT(id,origin) DO UPDATE SET multiplicity=multiplicity+1""",
                            (cid, origin),
                        )
        rejected_count = sum(rejected.values())
        if theoretical != rejected_count + duplicate_count + unique_count:
            raise ContractError("generation count invariant violated")
        ids = [row[0] for row in db.execute("SELECT id FROM candidates ORDER BY id")]
        db.commit()
    manifest = {
        "schema_version": "candidate-generation-manifest/v1",
        "search_space_id": space.search_space_id,
        "generation_policy_id": policy.generation_policy_id,
        "candidate_set_id": identity("candidate-set/v1", ids),
        "generator_version": GENERATOR_VERSION,
        "canonicalization_version": CANONICALIZATION_VERSION,
        "template_registry_version": TEMPLATE_REGISTRY_VERSION,
        "counts": {
            "T": theoretical,
            "R": rejected_count,
            "V": theoretical - rejected_count,
            "D": duplicate_count,
            "U": unique_count,
        },
        "rejection_reasons": dict(sorted(rejected.items())),
    }
    if unique_count > policy.candidate_budget:
        raise ContractError(
            f"SEARCH_SPACE_EXCEEDS_BUDGET: U={unique_count}, budget={policy.candidate_budget}"
        )
    return CandidatePlan(database, manifest)
