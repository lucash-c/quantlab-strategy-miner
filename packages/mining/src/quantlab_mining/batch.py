"""Sequential evaluator with shared feature planning and transactional resume."""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable, Sequence
from contextlib import closing
from pathlib import Path
from typing import Any

from quantlab_backtest.engine_v3 import (
    BACKTEST_ENGINE_V3_VERSION,
    FeatureSessionInputV3,
    run_backtest_v3,
)
from quantlab_backtest.metrics_v3 import METRICS_ENGINE_V3_VERSION
from quantlab_core.canonical import canonical_json_bytes, write_canonical_json
from quantlab_core.condition_engine_v2 import CONDITION_ENGINE_V2_VERSION
from quantlab_core.errors import ContractError
from quantlab_core.feature_engine_v2 import FEATURE_ENGINE_V2_VERSION, mathematical_policy_record
from quantlab_core.feature_specs import FeatureSpec
from quantlab_core.price import common_decimal_scale
from quantlab_data.feature_cache_v2 import (
    FEATURE_CACHE_V2_VERSION,
    build_feature_set,
    read_feature_observations,
)
from quantlab_data.historical import (
    SessionSource,
    build_market_history,
    read_session_candles,
    read_session_trades,
)

from quantlab_mining.checkpoint import initialize, result_digest, single_writer, validate_completed
from quantlab_mining.contracts import (
    EvaluationConfigV1,
    GenerationPolicyV1,
    MiningSearchSpaceV1,
    identity,
)
from quantlab_mining.export import EXPORT_VERSION, export_batch
from quantlab_mining.generator import preflight

BATCH_ENGINE_VERSION = "1.0.0"
ENGINE_VERSIONS = {
    "backtest": BACKTEST_ENGINE_V3_VERSION,
    "metrics": METRICS_ENGINE_V3_VERSION,
    "conditions": CONDITION_ENGINE_V2_VERSION,
    "features": FEATURE_ENGINE_V2_VERSION,
    "feature_cache": FEATURE_CACHE_V2_VERSION,
    "batch": BATCH_ENGINE_VERSION,
    "export": EXPORT_VERSION,
}


class ControlledInterruption(ContractError):
    """Explicit acceptance hook after a committed candidate; resume is safe."""


def _ordered_specs(specs: dict[str, FeatureSpec]) -> tuple[FeatureSpec, ...]:
    emitted: set[str] = set()
    ordered = []
    while len(emitted) < len(specs):
        ready = sorted(
            fid for fid, spec in specs.items() if fid not in emitted and set(spec.inputs) <= emitted
        )
        if not ready:
            raise ContractError("inconsistent global feature dependencies")
        for fid in ready:
            ordered.append(specs[fid])
            emitted.add(fid)
    return tuple(ordered)


def directory_bytes(path: Path) -> int:
    return sum(file.stat().st_size for file in path.rglob("*") if file.is_file())


def run_batch(
    space: MiningSearchSpaceV1,
    policy: GenerationPolicyV1,
    evaluation: EvaluationConfigV1,
    sources: Sequence[SessionSource],
    *,
    cache_root: Path,
    checkpoint: Path,
    output: Path,
    stop_after: int | None = None,
    on_progress: Callable[[dict], None] | None = None,
) -> dict:
    started = time.perf_counter()
    if output.exists():
        raise ContractError("output directory already exists; refusing to overwrite")
    if stop_after is not None and stop_after < 1:
        raise ContractError("stop_after must be positive")
    with single_writer(checkpoint):
        plan = preflight(space, policy, checkpoint)  # ALWAYS before market/features/evaluation.
        if on_progress:
            on_progress({"phase": "PREFLIGHT", **plan.manifest["counts"]})
        materialization_start = time.perf_counter()
        market = build_market_history(
            sources,
            logical_asset=space.logical_asset,
            cache_root=cache_root,
            timeframes=tuple(sorted(set(space.timeframes))),
            max_sessions=evaluation.max_sessions,
        )
        union: dict[str, dict[str, FeatureSpec]] = {}
        for _, strategy in plan.candidates():
            target = union.setdefault(strategy.timeframe, {})
            for spec in strategy.feature_specs:
                if spec.feature_id in target and target[spec.feature_id] != spec:
                    raise ContractError("inconsistent global feature identity")
                target[spec.feature_id] = spec
        sets = {
            tf: build_feature_set(
                market, _ordered_specs(specs), timeframe=tf, cache_root=cache_root
            )
            for tf, specs in sorted(union.items())
        }
        feature_index = []
        for tf, feature_set in sorted(sets.items()):
            for item in feature_set.sessions:
                for fid, cache in sorted(item.features.items()):
                    feature_index.append(
                        {
                            "timeframe": tf,
                            "session_id": item.market.session.session_id,
                            "feature": union[tf][fid].to_record(),
                            "cache_key": cache.key,
                            "artifact": cache.manifest["artifacts"]["feature_values"],
                        }
                    )
        materialization_seconds = time.perf_counter() - materialization_start
        evaluations: dict[str, str] = {}
        scales: dict[str, int] = {}
        for cid, base in plan.candidates():
            strategy = base.model_copy(
                update={
                    "cost_model": evaluation.cost_model,
                    "slippage_model": evaluation.slippage_model,
                }
            )
            scale = common_decimal_scale(
                [item.session.price_scale for item in market.sessions], strategy.decimal_inputs()
            )
            scales[cid] = scale
            fids = {spec.feature_id for spec in strategy.feature_specs}
            evaluations[cid] = identity(
                "candidate-evaluation/v1",
                {
                    "candidate_id": cid,
                    "dataset_id": market.dataset.dataset_id,
                    "cost_model": strategy.cost_model.model_dump(mode="json"),
                    "slippage_model": strategy.slippage_model.model_dump(mode="json"),
                    "engines": ENGINE_VERSIONS,
                    "common_price_scale": scale,
                    "feature_artifacts": [
                        row
                        for row in feature_index
                        if row["timeframe"] == strategy.timeframe
                        and row["feature"]["feature_id"] in fids
                    ],
                },
            )
        context = {
            "generation": plan.manifest,
            "requested_search_space": space.model_dump(mode="json"),
            "evaluation_ids": evaluations,
            "engines": ENGINE_VERSIONS,
            "feature_index": feature_index,
        }
        records: dict[str, Any] = {
            "search-space.json": space.semantic_record(),
            "generation-policy.json": policy.model_dump(mode="json"),
            "evaluation-config.json": evaluation.model_dump(mode="json"),
            "market-dataset-manifest.json": market.manifest,
            "engine-versions.json": {
                **ENGINE_VERSIONS,
                "mathematical_policy": mathematical_policy_record(),
            },
        }
        durations = []
        with closing(sqlite3.connect(checkpoint)) as db:
            initialize(db, context)
            reused = validate_completed(
                db, evaluations
            )  # Validate ALL previous results before new work.
            executed = 0
            for cid, base in plan.candidates():
                if db.execute("SELECT 1 FROM results WHERE id=?", (cid,)).fetchone():
                    continue
                tick_start = time.perf_counter()
                strategy = base.model_copy(
                    update={
                        "cost_model": evaluation.cost_model,
                        "slippage_model": evaluation.slippage_model,
                    }
                )
                counts = {"ledger": 0, "journal": 0}

                def persist(kind: str, record: Any, cid: str = cid, counts: dict = counts) -> None:
                    db.execute(
                        "INSERT INTO audit VALUES (?,?,?,?)",
                        (
                            cid,
                            kind,
                            counts[kind],
                            canonical_json_bytes(record.to_record()).decode("utf-8"),
                        ),
                    )
                    counts[kind] += 1

                inputs = (
                    FeatureSessionInputV3(
                        item.market.session,
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
                    for item in sets[strategy.timeframe].sessions
                )
                with (
                    db
                ):  # Candidate + entire audit + metrics commit atomically, rollback on ANY error.
                    summary = run_backtest_v3(
                        inputs,
                        strategy,
                        common_price_scale=scales[cid],
                        on_trade=lambda record: persist("ledger", record),
                        on_signal=lambda record: persist("journal", record),
                    )
                    metrics = canonical_json_bytes(summary.metrics).decode("utf-8")
                    digest = result_digest(
                        db, cid, evaluations[cid], metrics, (counts["ledger"], counts["journal"])
                    )
                    db.execute(
                        "INSERT INTO results VALUES (?,?,?,?,?,?)",
                        (
                            cid,
                            evaluations[cid],
                            metrics,
                            digest,
                            counts["ledger"],
                            counts["journal"],
                        ),
                    )
                durations.append(time.perf_counter() - tick_start)
                executed += 1
                if on_progress:
                    on_progress(
                        {
                            "phase": "BACKTESTED",
                            "candidate_id": cid,
                            "trades": summary.metrics["trades"],
                            "completed": reused + executed,
                            "total": plan.manifest["counts"]["U"],
                        }
                    )
                if stop_after is not None and executed >= stop_after:
                    raise ControlledInterruption(
                        "CONTROLLED_INTERRUPTION: committed checkpoint ready for resume"
                    )
            validate_completed(db, evaluations)
            manifest = export_batch(
                db,
                plan,
                output,
                evaluation=evaluation,
                records=records,
                feature_index=feature_index,
                evaluation_ids=evaluations,
            )
        elapsed = time.perf_counter() - started
        report = {
            "schema_version": "candidate-batch-operational/v1",
            "run_id": manifest["run_id"],
            "total_seconds": str(elapsed),
            "materialization_seconds": str(materialization_seconds),
            "backtest_seconds": [str(value) for value in durations],
            "executed": executed,
            "reused": reused,
            "average_backtest_seconds": str(sum(durations) / executed) if executed else None,
            "cache_bytes": directory_bytes(cache_root),
            "artifact_bytes": directory_bytes(output),
            "peak_memory": {"value": None, "reason": "NOT_MEASURED_RELIABLY"},
            "market_operations": market.operational_report,
            "feature_operations": {tf: item.operational_report for tf, item in sets.items()},
        }
        write_canonical_json(output.parent / (output.name + ".operational.json"), report)
        return manifest
