"""Single chronological holdout with capability checks, immutable freeze and session reuse."""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

from quantlab_backtest.partition_metrics import aggregate_partition
from quantlab_core.canonical import canonical_json_bytes, write_canonical_json
from quantlab_core.errors import ContractError
from quantlab_core.feature_engine_v2 import mathematical_policy_record
from quantlab_core.feature_specs import feature_registry_record
from quantlab_core.price import common_decimal_scale
from quantlab_core.sessions import HistoricalDataset
from quantlab_data.feature_cache_v2 import build_feature_set
from quantlab_data.historical import MarketHistoricalBuild
from quantlab_data.session_catalog import SessionCatalog, load_metadata, resolve_session
from quantlab_mining.batch import (
    ENGINE_VERSIONS,
    ControlledInterruption,
    _ordered_specs,
    directory_bytes,
)
from quantlab_mining.checkpoint import single_writer
from quantlab_mining.contracts import (
    EvaluationConfigV1,
    GenerationPolicyV1,
    MiningSearchSpaceV1,
    identity,
)
from quantlab_mining.generator import CandidatePlan, preflight

from quantlab_research.access import ResearchAccess
from quantlab_research.comparison import compare
from quantlab_research.contracts import (
    AGGREGATION_POLICY,
    COMPARISON_POLICY,
    RESEARCH_VERSION,
    GatePolicyV1,
    SplitPolicyV1,
    metric_registry_record,
)
from quantlab_research.export import export_research
from quantlab_research.freeze import pass_set, publish_freeze, validate_freeze
from quantlab_research.gates import apply_gate
from quantlab_research.session_cache import SessionEvaluationCache
from quantlab_research.split import create_split


def protocol_record(
    plan: CandidatePlan,
    split: dict,
    dgate: GatePolicyV1,
    vgate: GatePolicyV1,
    evaluation: EvaluationConfigV1,
) -> dict:
    record = {
        "schema_version": "research-protocol/v1",
        "search_space_id": plan.manifest["search_space_id"],
        "generation_policy_id": plan.manifest["generation_policy_id"],
        "candidate_set_id": plan.manifest["candidate_set_id"],
        "split_plan_id": split["split_plan_id"],
        "discovery_gate_policy": dgate.canonical_record(),
        "validation_gate_policy": vgate.canonical_record(),
        "cost_model": evaluation.cost_model.model_dump(mode="json"),
        "slippage_model": evaluation.slippage_model.model_dump(mode="json"),
        "engines": {**ENGINE_VERSIONS, "research": RESEARCH_VERSION},
        "mathematical_policy": mathematical_policy_record(),
        "aggregation_policy": AGGREGATION_POLICY,
        "comparison_policy": COMPARISON_POLICY,
    }
    record["research_protocol_id"] = identity("research-protocol/v1", record)
    return record


def initialize(db: sqlite3.Connection, protocol: dict) -> None:
    if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
        raise ContractError("corrupt research checkpoint")
    db.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS research_meta(protocol TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS research_refs(
            stage TEXT, cid TEXT, sid TEXT, eid TEXT, fingerprint TEXT,
            PRIMARY KEY(stage,cid,sid));
        CREATE TABLE IF NOT EXISTS research_results(
            stage TEXT, cid TEXT, payload TEXT, PRIMARY KEY(stage,cid));
        CREATE TABLE IF NOT EXISTS research_gates(
            stage TEXT, cid TEXT, payload TEXT, PRIMARY KEY(stage,cid));
        CREATE TABLE IF NOT EXISTS research_comparisons(cid TEXT PRIMARY KEY, payload TEXT);
        CREATE TABLE IF NOT EXISTS research_session_records(
            cid TEXT, trading_date TEXT, payload TEXT, PRIMARY KEY(cid,trading_date));
        CREATE TABLE IF NOT EXISTS research_audit(
            stage TEXT, cid TEXT, kind TEXT, ordinal INTEGER, payload TEXT,
            PRIMARY KEY(stage,cid,kind,ordinal));
    """)
    payload = canonical_json_bytes(protocol).decode()
    rows = db.execute("SELECT protocol FROM research_meta").fetchall()
    if rows and rows != [(payload,)]:
        raise ContractError("CHECKPOINT_CONTEXT_MISMATCH: research protocol changed")
    if not rows:
        db.execute("INSERT INTO research_meta VALUES(?)", (payload,))
        db.commit()
    validate_checkpoint_results(db, "DISCOVERY")


def validate_checkpoint_results(db: sqlite3.Connection, stage: str) -> None:
    for cid, stored in db.execute(
        "SELECT cid,payload FROM research_results WHERE stage=?", (stage,)
    ):
        record = json.loads(stored)
        content = {k: v for k, v in record.items() if k != "result_fingerprint"}
        if (
            canonical_json_bytes(record).decode() != stored
            or record["candidate_id"] != cid
            or identity("research-partition-result/v1", content) != record["result_fingerprint"]
        ):
            raise ContractError("corrupt research aggregate checkpoint")
        for kind, name in (("ledger", "ledger_fingerprint"), ("journal", "journal_fingerprint")):
            import hashlib

            digest = hashlib.sha256()
            for index, (ordinal, raw) in enumerate(
                db.execute(
                    "SELECT ordinal,payload FROM research_audit WHERE stage=? AND cid=? AND kind=? "
                    "ORDER BY ordinal",
                    (stage, cid, kind),
                )
            ):
                if ordinal != index or canonical_json_bytes(json.loads(raw)).decode() != raw:
                    raise ContractError("corrupt research audit checkpoint")
                digest.update(raw.encode())
            if digest.hexdigest() != record[name]:
                raise ContractError("corrupt research audit digest")


def _results(db: sqlite3.Connection, table: str, stage: str) -> list[dict]:
    return [
        json.loads(row[0])
        for row in db.execute(f"SELECT payload FROM {table} WHERE stage=? ORDER BY cid", (stage,))
    ]


def _stage(
    stage: str,
    view: dict,
    ids: set[str],
    plan: CandidatePlan,
    catalog: SessionCatalog,
    evaluation: EvaluationConfigV1,
    cache: SessionEvaluationCache,
    cache_root: Path,
    db: sqlite3.Connection,
    access: ResearchAccess,
    report: dict,
    gate: GatePolicyV1,
    stop_after: int | None,
    observer: Callable[[dict], None] | None,
) -> None:
    if not ids:
        return
    union: dict[str, dict] = {}
    for cid, strategy in plan.candidates():
        if cid in ids:
            union.setdefault(strategy.timeframe, {}).update(
                {s.feature_id: s for s in strategy.feature_specs}
            )
    verified: set[Path] = set()
    stage_report = report["stages"][stage]
    completed = 0
    for session in catalog.sessions:
        if session.session_id not in view["session_ids"]:
            continue
        access.check(stage, session.session_id, "ticks")
        access.check(stage, session.session_id, "candles")
        market = resolve_session(catalog, session, tuple(sorted(union)), cache_root, verified)
        build = MarketHistoricalBuild(
            HistoricalDataset(
                view["partition_id"],
                view["partition_id"],
                session.logical_asset,
                1,
                tuple(sorted(union)),
                (session,),
            ),
            (market,),
            {},
            {},
        )
        sets = {}
        for tf, specs in sorted(union.items()):
            access.check(stage, session.session_id, "features")
            sets[tf] = build_feature_set(
                build, _ordered_specs(specs), timeframe=tf, cache_root=cache_root
            )
        for cid, base in plan.candidates():
            if cid not in ids:
                continue
            strategy = base.model_copy(
                update={
                    "cost_model": evaluation.cost_model,
                    "slippage_model": evaluation.slippage_model,
                }
            )
            started = time.perf_counter()
            record, reused = cache.evaluate(
                cid,
                sets[strategy.timeframe].sessions[0],
                strategy,
                lambda kind, cid=cid, sid=session.session_id: access.check(stage, sid, kind, cid),
            )
            stage_report["evaluation_seconds"] += time.perf_counter() - started
            stage_report[
                "session_evaluations_reused" if reused else "session_evaluations_built"
            ] += 1
            with db:
                old = db.execute(
                    "SELECT eid,fingerprint FROM research_refs WHERE stage=? AND cid=? AND sid=?",
                    (stage, cid, session.session_id),
                ).fetchone()
                if old and old != (record["evaluation_id"], record["result_fingerprint"]):
                    raise ContractError("research session reference mismatch")
                db.execute(
                    "INSERT OR REPLACE INTO research_refs VALUES(?,?,?,?,?)",
                    (
                        stage,
                        cid,
                        session.session_id,
                        record["evaluation_id"],
                        record["result_fingerprint"],
                    ),
                )
                payload = canonical_json_bytes(record).decode()
                old_record = db.execute(
                    "SELECT payload FROM research_session_records WHERE cid=? AND trading_date=?",
                    (cid, session.trading_date),
                ).fetchone()
                if old_record and old_record != (payload,):
                    raise ContractError("corrupt research session record")
                db.execute(
                    "INSERT OR REPLACE INTO research_session_records VALUES(?,?,?)",
                    (cid, session.trading_date, payload),
                )
            completed += 1
            if observer:
                observer(
                    {
                        "phase": "SESSION_BACKTESTED",
                        "stage": stage,
                        "candidate_id": cid,
                        "session_id": session.session_id,
                        "reused": reused,
                    }
                )
            if stop_after and completed >= stop_after:
                cache.publish_pending(session.session_id)
                raise ControlledInterruption("CONTROLLED_INTERRUPTION: research session committed")
        cache.publish_pending(session.session_id)
    for cid, base in plan.candidates():
        if cid not in ids:
            continue
        strategy = base.model_copy(
            update={
                "cost_model": evaluation.cost_model,
                "slippage_model": evaluation.slippage_model,
            }
        )
        selected = [s for s in catalog.sessions if s.session_id in view["session_ids"]]
        scale = common_decimal_scale([s.price_scale for s in selected], strategy.decimal_inputs())

        def inputs(selected=selected, cid=cid):
            for session in selected:
                access.check(stage, session.session_id, "session_evaluation", cid)
                row = db.execute(
                    "SELECT eid,fingerprint FROM research_refs WHERE stage=? AND cid=? AND sid=?",
                    (stage, cid, session.session_id),
                ).fetchone()
                if row is None:
                    raise ContractError("missing candidate/session reference")
                record = cache.load(row[0])
                if record is None or record["result_fingerprint"] != row[1]:
                    raise ContractError("candidate/session reference integrity mismatch")
                yield record, cache.audit(row[0], "ledger"), cache.audit(row[0], "journal")

        counts = {"ledger": 0, "journal": 0}

        def persist(kind: str, record: dict, cid=cid, counts=counts) -> None:
            db.execute(
                "INSERT INTO research_audit VALUES(?,?,?,?,?)",
                (stage, cid, kind, counts[kind], canonical_json_bytes(record).decode()),
            )
            counts[kind] += 1

        started = time.perf_counter()
        with db:
            db.execute("DELETE FROM research_audit WHERE stage=? AND cid=?", (stage, cid))
            result = aggregate_partition(
                cid,
                view["partition_id"],
                inputs(),
                strategy,
                scale,
                lambda r: persist("ledger", r),
                lambda r: persist("journal", r),
            )
            previous = db.execute(
                "SELECT payload FROM research_results WHERE stage=? AND cid=?", (stage, cid)
            ).fetchone()
            payload = canonical_json_bytes(result).decode()
            if previous and previous != (payload,):
                raise ContractError("DETERMINISM_VIOLATION: partition result changed")
            db.execute(
                "INSERT OR REPLACE INTO research_results VALUES(?,?,?)", (stage, cid, payload)
            )
        stage_report["aggregation_seconds"] += time.perf_counter() - started
        if stage == "DISCOVERY":
            started = time.perf_counter()
            gate_result = apply_gate(
                cid, result["partition_evaluation_id"], result["metrics"], gate
            )
            with db:
                db.execute(
                    "INSERT OR REPLACE INTO research_gates VALUES(?,?,?)",
                    (stage, cid, canonical_json_bytes(gate_result).decode()),
                )
            stage_report["gates_seconds"] += time.perf_counter() - started


def run_research(
    space: MiningSearchSpaceV1,
    generation: GenerationPolicyV1,
    evaluation: EvaluationConfigV1,
    catalog: SessionCatalog,
    split_policy: SplitPolicyV1,
    dgate: GatePolicyV1,
    vgate: GatePolicyV1,
    *,
    cache_root: Path,
    checkpoint: Path,
    output: Path,
    stop_after: int | None = None,
    stop_stage: str = "VALIDATION",
    observer: Callable[[dict], None] | None = None,
    previous_experiment: Path | None = None,
) -> dict:
    if output.exists():
        raise ContractError("refusing to overwrite research output")
    if (
        dgate.schema_version != "discovery-gate-policy/v1"
        or vgate.schema_version != "validation-gate-policy/v1"
    ):
        raise ContractError("gate policies assigned to wrong stages")
    if stop_stage not in {"DISCOVERY", "VALIDATION"} or (stop_after is not None and stop_after < 1):
        raise ContractError("invalid controlled interruption configuration")
    evaluation = evaluation.canonicalized()
    catalog = catalog.select(evaluation.max_sessions)
    if catalog.manifest["logical_asset"] != space.logical_asset:
        raise ContractError("historical logical asset differs from search space")
    started = time.perf_counter()
    report = {
        "schema_version": "research-operational/v1",
        "stages": {
            s: {
                "session_evaluations_built": 0,
                "session_evaluations_reused": 0,
                "evaluation_seconds": 0.0,
                "aggregation_seconds": 0.0,
                "gates_seconds": 0.0,
            }
            for s in ("DISCOVERY", "VALIDATION")
        },
    }
    with single_writer(checkpoint):
        plan = preflight(space, generation, checkpoint)
        split, discovery, validation = create_split(catalog, split_policy)
        protocol = protocol_record(plan, split, dgate, vgate, evaluation)
        cache = SessionEvaluationCache(cache_root)
        try:
            with closing(sqlite3.connect(checkpoint)) as db:
                initialize(db, protocol)
                state = checkpoint.with_suffix(".research")
                state.mkdir(exist_ok=True)
                protocol_path = state / "research-protocol.json"
                if protocol_path.exists():
                    if load_metadata(protocol_path) != protocol:
                        raise ContractError("frozen research protocol mismatch")
                else:
                    temporary = state / ".protocol.json"
                    write_canonical_json(temporary, protocol)
                    temporary.replace(protocol_path)
                access = ResearchAccess(discovery, validation, observer)
                ids = {cid for cid, _ in plan.candidates()}
                _stage(
                    "DISCOVERY",
                    discovery,
                    ids,
                    plan,
                    catalog,
                    evaluation,
                    cache,
                    cache_root,
                    db,
                    access,
                    report,
                    dgate,
                    stop_after if stop_stage == "DISCOVERY" else None,
                    observer,
                )
                devals, dgates = (
                    _results(db, "research_results", "DISCOVERY"),
                    _results(db, "research_gates", "DISCOVERY"),
                )
                approved = pass_set(discovery["partition_id"], dgate.policy_id, devals, dgates)
                frozen = publish_freeze(
                    state / "discovery-freeze", protocol, split, approved, devals, dgates
                )
                validate_freeze(state / "discovery-freeze", frozen)
                access.approved = set(approved["candidate_ids"])
                access.released = True
                validate_checkpoint_results(db, "VALIDATION")
                if observer:
                    observer(
                        {
                            "phase": "DISCOVERY_FREEZE_VALIDATED",
                            "discovery_freeze_id": frozen["discovery_freeze_id"],
                        }
                    )
                _stage(
                    "VALIDATION",
                    validation,
                    access.approved,
                    plan,
                    catalog,
                    evaluation,
                    cache,
                    cache_root,
                    db,
                    access,
                    report,
                    vgate,
                    stop_after if stop_stage == "VALIDATION" else None,
                    observer,
                )
                by_id = {r["candidate_id"]: r for r in devals}
                for vresult in _results(db, "research_results", "VALIDATION"):
                    gate_start = time.perf_counter()
                    cid = vresult["candidate_id"]
                    comparison = compare(cid, by_id[cid], vresult)
                    vresult_gate = apply_gate(
                        cid,
                        vresult["partition_evaluation_id"],
                        {**vresult["metrics"], **comparison["metrics"]},
                        vgate,
                    )
                    with db:
                        db.execute(
                            "INSERT OR REPLACE INTO research_comparisons VALUES(?,?)",
                            (cid, canonical_json_bytes(comparison).decode()),
                        )
                        db.execute(
                            "INSERT OR REPLACE INTO research_gates VALUES(?,?,?)",
                            ("VALIDATION", cid, canonical_json_bytes(vresult_gate).decode()),
                        )
                    report["stages"]["VALIDATION"]["gates_seconds"] += (
                        time.perf_counter() - gate_start
                    )
                vgates = _results(db, "research_gates", "VALIDATION")
                passed_v = {r["candidate_id"] for r in vgates if r["result"] == "PASS"}
                statuses = [
                    {
                        "candidate_id": cid,
                        "evaluation_status": "BACKTESTED",
                        "discovery_status": "DISCOVERY_PASSED"
                        if cid in access.approved
                        else "DISCOVERY_FAILED_GATE",
                        "validation_status": "VALIDATION_PASSED"
                        if cid in passed_v
                        else "VALIDATION_FAILED_GATE"
                        if cid in access.approved
                        else "VALIDATION_NOT_RUN",
                        "reason": None
                        if cid in access.approved
                        else "NOT_IN_DISCOVERY_PASS_SET"
                        if access.approved
                        else "EMPTY_DISCOVERY_PASS_SET",
                    }
                    for cid in sorted(ids)
                ]
                experiment = {
                    "schema_version": "validation-experiment/v1",
                    "research_protocol_id": protocol["research_protocol_id"],
                    "discovery_freeze_id": frozen["discovery_freeze_id"],
                    "validation_partition_id": validation["partition_id"],
                    "validation_results": [
                        {
                            "evaluation_id": r["partition_evaluation_id"],
                            "result_fingerprint": r["result_fingerprint"],
                        }
                        for r in _results(db, "research_results", "VALIDATION")
                    ],
                    "comparisons": [
                        json.loads(row[0])["result_fingerprint"]
                        for row in db.execute(
                            "SELECT payload FROM research_comparisons ORDER BY cid"
                        )
                    ],
                    "validation_gate_results": [r["result_fingerprint"] for r in vgates],
                    "statuses": statuses,
                    "final_status": "COMPLETE"
                    if access.approved
                    else "COMPLETE_WITHOUT_VALIDATION",
                }
                experiment["validation_experiment_id"] = identity(
                    "validation-experiment/v1", experiment
                )
                counts = {
                    "candidate_set": len(ids),
                    "discovery_backtested": len(devals),
                    "discovery_passed": len(access.approved),
                    "validation_backtested": len(vgates),
                    "validation_passed": len(passed_v),
                    "discovery_failed_gate": len(ids) - len(access.approved),
                    "validation_failed_gate": len(vgates) - len(passed_v),
                    "validation_not_run": len(ids) - len(vgates),
                }
                groups: dict[tuple[str, str], set[str]] = {}
                for cid, strategy in plan.candidates():
                    groups.setdefault((strategy.timeframe, strategy.direction), set()).add(cid)
                families: dict[str, set[str]] = {}
                kinds = {entry.template_id: entry.base.kind for entry in space.templates}
                for cid, origin in db.execute(
                    "SELECT id,origin FROM provenance ORDER BY id,origin"
                ):
                    families.setdefault(kinds[json.loads(origin)["template_id"]], set()).add(cid)

                def group_counts(members: set[str]) -> dict:
                    return {
                        "candidates": len(members),
                        "discovery_backtested": len(members),
                        "discovery_passed": len(members & access.approved),
                        "validation_backtested": len(members & access.approved),
                        "validation_passed": len(members & passed_v),
                    }

                pressure = {
                    "counts": counts,
                    "candidate_groups": [
                        {"timeframe": tf, "direction": dr, **group_counts(members)}
                        for (tf, dr), members in sorted(groups.items())
                    ],
                    "family_groups": [
                        {"family": family, **group_counts(members)}
                        for family, members in sorted(families.items())
                    ],
                    "family_counts_may_overlap": True,
                    "order": "CANDIDATE_ID_NOT_PERFORMANCE",
                }
                provenance = {
                    "schema_version": "research-provenance/v1",
                    "exposure_assurance": "NOT_GLOBALLY_TRACKED",
                    "validation_partition_id": validation["partition_id"],
                    "previous_experiment_id": None,
                    "known_prior_holdout_overlap": False,
                    "overlapping_validation_sessions": [],
                    "warning": "Single holdout does not prove robustness or approve live trading",
                }
                if previous_experiment:
                    previous = load_metadata(previous_experiment / "validation-experiment.json")
                    previous_split = load_metadata(previous_experiment / "split-plan.json")
                    overlap = sorted(
                        set(previous_split["validation"]["session_ids"])
                        & set(validation["session_ids"])
                    )
                    provenance.update(
                        previous_experiment_id=previous["validation_experiment_id"],
                        previous_validation_partition_id=previous_split["validation"][
                            "partition_id"
                        ],
                        known_prior_holdout_overlap=bool(overlap),
                        overlapping_validation_sessions=overlap,
                    )
                records = {
                    "research-protocol.json": protocol,
                    "split-plan.json": split,
                    "discovery-partition.json": discovery,
                    "validation-partition.json": validation,
                    "discovery-gate-policy.json": dgate.canonical_record(),
                    "validation-gate-policy.json": vgate.canonical_record(),
                    "discovery-pass-set.json": approved,
                    "discovery-freeze.json": frozen,
                    "selection-pressure.json": pressure,
                    "provenance.json": provenance,
                    "metric-registry.json": metric_registry_record(),
                    "feature-registry.json": feature_registry_record(),
                    "generation-manifest.json": plan.manifest,
                }
                export_start = time.perf_counter()
                manifest = export_research(db, output, records, experiment)
                report["export_seconds"] = str(time.perf_counter() - export_start)
                report["validation_experiment_id"] = experiment["validation_experiment_id"]
                report["artifact_bytes"] = directory_bytes(output)
                return manifest
        finally:
            cache.close()
            report["total_seconds"] = str(time.perf_counter() - started)
            report["cache_bytes"] = directory_bytes(cache_root)
            for values in report["stages"].values():
                for name in ("evaluation_seconds", "aggregation_seconds", "gates_seconds"):
                    values[name] = str(values[name])
            write_canonical_json(output.parent / (output.name + ".operational.json"), report)
