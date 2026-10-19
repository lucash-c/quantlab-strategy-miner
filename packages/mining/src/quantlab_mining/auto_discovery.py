"""Deterministic mining-intent resolution over Candidate Generator v1."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from collections import defaultdict
from collections.abc import Iterator, Mapping
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from quantlab_core.canonical import canonical_json_bytes, sha256_file, write_canonical_json
from quantlab_core.errors import ContractError
from quantlab_core.strategy_v3 import StrategyDefinitionV3

from quantlab_mining.canonicalization import named_candidate
from quantlab_mining.contracts import (
    CANONICALIZATION_VERSION,
    GENERATOR_VERSION,
    TEMPLATE_REGISTRY_VERSION,
    AutoDiscoveryProfileV1,
    CandidateSubplanManifestV1,
    CandidateUniverseManifestV1,
    GenerationCountsV1,
    MiningConstraintsV1,
    MiningRequestV1,
    ResolvedSearchSpaceV1,
    ResolvedSideSearchSpaceV1,
    identity,
    load_contract,
)
from quantlab_mining.generator import CandidatePlan, preflight

RESOLUTION_PROTOCOL_RECORD = {
    "schema_version": "auto-discovery-resolution-protocol/v1",
    "resolver_version": "1.0.0",
    "generator_version": GENERATOR_VERSION,
    "canonicalization_version": CANONICALIZATION_VERSION,
    "template_registry_version": TEMPLATE_REGISTRY_VERSION,
    "side_semantics": "independent-unidirectional-canonical-union/v1",
    "sampling": "NONE",
    "truncation": "PROHIBITED",
}
RESOLUTION_PROTOCOL_ID = identity("resolution-protocol/v1", RESOLUTION_PROTOCOL_RECORD)
_TIMEFRAME_ORDER = ("1m", "2m", "5m", "15m")
_CACHE_RECORD = "candidate-plan-cache.json"
_COMPOSITE_DATABASE = "composite-plan.sqlite"
_CANDIDATE_MANIFEST = "candidate-universe-manifest.json"
_BUNDLE_FILES = (
    "mining-request.json",
    "mining-constraints.json",
    "auto-discovery-profile.json",
    "resolved-search-space.json",
    "candidate-universe-manifest.json",
)


@dataclass(frozen=True)
class ProfileRegistry:
    """Immutable profile lookup by full content hash; aliases are never selectors."""

    profiles: Mapping[str, AutoDiscoveryProfileV1]

    @classmethod
    def from_directory(cls, directory: Path) -> ProfileRegistry:
        if not directory.is_dir():
            raise ContractError(f"AUTO_DISCOVERY_PROFILE_REGISTRY_NOT_FOUND: {directory}")
        profiles: dict[str, AutoDiscoveryProfileV1] = {}
        aliases: dict[str, str] = {}
        for path in sorted(directory.glob("*.json")):
            profile = load_contract(path, AutoDiscoveryProfileV1)
            profile_id = profile.auto_discovery_profile_id
            if profile.alias in aliases and aliases[profile.alias] != profile_id:
                raise ContractError(f"AUTO_DISCOVERY_PROFILE_ALIAS_COLLISION: {profile.alias}")
            if profile_id in profiles and profiles[profile_id] != profile:
                raise ContractError(f"AUTO_DISCOVERY_PROFILE_ID_COLLISION: {profile_id}")
            profiles[profile_id] = profile
            aliases[profile.alias] = profile_id
        if not profiles:
            raise ContractError("AUTO_DISCOVERY_PROFILE_REGISTRY_EMPTY")
        return cls(profiles=profiles)

    def resolve(self, profile_id: str) -> AutoDiscoveryProfileV1:
        try:
            return self.profiles[profile_id]
        except KeyError as exc:
            raise ContractError(f"AUTO_DISCOVERY_PROFILE_NOT_FOUND: {profile_id}") from exc


@dataclass(frozen=True)
class CompositeCandidatePlan:
    """Disk-backed canonical union of unidirectional legacy Candidate Plans."""

    directory: Path
    manifest: CandidateUniverseManifestV1

    @property
    def database(self) -> Path:
        return self.directory / _COMPOSITE_DATABASE

    def candidates(self) -> Iterator[tuple[str, StrategyDefinitionV3]]:
        with closing(sqlite3.connect(self.database)) as database:
            for candidate_id, payload in database.execute(
                "SELECT id, strategy FROM candidates ORDER BY id"
            ):
                yield candidate_id, StrategyDefinitionV3.model_validate(json.loads(payload))

    def provenance(self, candidate_id: str) -> Iterator[tuple[dict[str, Any], int]]:
        with closing(sqlite3.connect(self.database)) as database:
            for origin, multiplicity in database.execute(
                "SELECT origin, multiplicity FROM provenance WHERE id=? ORDER BY origin",
                (candidate_id,),
            ):
                yield json.loads(origin), multiplicity


@dataclass(frozen=True)
class CandidatePlanBundle:
    bundle_id: str
    directory: Path
    plan: CompositeCandidatePlan
    resolved: ResolvedSearchSpaceV1


def resolve_request(
    request: MiningRequestV1, profile: AutoDiscoveryProfileV1 | None = None
) -> ResolvedSearchSpaceV1:
    """Resolve all scientific axes without touching market data or result layers."""

    if request.mode == "AUTO_DISCOVERY":
        if profile is None:
            raise ContractError("AUTO_DISCOVERY_PROFILE_REQUIRED")
        if request.auto_discovery_profile_id != profile.auto_discovery_profile_id:
            raise ContractError("AUTO_DISCOVERY_PROFILE_ID_MISMATCH")
        if profile.resolution_protocol_id != RESOLUTION_PROTOCOL_ID:
            raise ContractError("AUTO_DISCOVERY_RESOLUTION_PROTOCOL_MISMATCH")
        if request.logical_asset != profile.logical_asset:
            raise ContractError("REQUEST_ASSET_OUTSIDE_PROFILE")
        if not set(request.timeframes) <= set(profile.timeframes):
            raise ContractError("REQUEST_TIMEFRAME_OUTSIDE_PROFILE")
        constraints = profile.constraints
        profile_id: str | None = profile.auto_discovery_profile_id
    else:
        if profile is not None:
            raise ContractError("CONSTRAINED_REQUEST_FORBIDS_PROFILE")
        if request.constraints is None:
            raise AssertionError("validated constrained request lost its constraints")
        constraints = request.constraints
        profile_id = None

    directions = []
    for direction in ("BUY", "SELL"):
        if request.sides[direction].enabled:
            if not constraints.sides[direction].enabled:
                raise ContractError(f"REQUEST_SIDE_OUTSIDE_CONSTRAINTS: {direction}")
            directions.append(direction)
    timeframes = tuple(tf for tf in _TIMEFRAME_ORDER if tf in request.timeframes)
    subspaces = tuple(
        ResolvedSideSearchSpaceV1(
            direction=direction,
            search_space=constraints.side_search_space(
                request.logical_asset, timeframes, direction
            ),
        )
        for direction in directions
    )
    return ResolvedSearchSpaceV1(
        schema_version="resolved-search-space/v1",
        mining_request_id=request.mining_request_id,
        mining_constraints_id=constraints.mining_constraints_id,
        auto_discovery_profile_id=profile_id,
        resolution_protocol_id=RESOLUTION_PROTOCOL_ID,
        generation_policy=constraints.generation_policy,
        subspaces=subspaces,
    )


def resolve_from_registry(
    request: MiningRequestV1, registry: ProfileRegistry | None
) -> tuple[ResolvedSearchSpaceV1, MiningConstraintsV1, AutoDiscoveryProfileV1 | None]:
    """Resolve a request with no alias/default fallback."""

    if request.mode == "AUTO_DISCOVERY":
        if registry is None or request.auto_discovery_profile_id is None:
            raise ContractError("AUTO_DISCOVERY_PROFILE_REGISTRY_REQUIRED")
        profile = registry.resolve(request.auto_discovery_profile_id)
        return resolve_request(request, profile), profile.constraints, profile
    if request.constraints is None:
        raise AssertionError("validated constrained request lost its constraints")
    return resolve_request(request), request.constraints, None


def _load_json(path: Path) -> Any:
    def reject(value: str) -> None:
        raise ValueError(f"non-canonical numeric value {value}")

    return json.loads(path.read_text(encoding="utf-8"), parse_float=reject, parse_constant=reject)


def _cache_error(message: str) -> ContractError:
    return ContractError(f"CANDIDATE_PLAN_CACHE_CORRUPT: {message}")


def _validate_composite_database(
    database_path: Path, manifest: CandidateUniverseManifestV1
) -> None:
    try:
        with closing(sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)) as database:
            rows = list(database.execute("SELECT id, strategy FROM candidates ORDER BY id"))
            if tuple(row[0] for row in rows) != manifest.candidate_ids:
                raise _cache_error("candidate IDs differ from manifest")
            for candidate_id, payload in rows:
                strategy = StrategyDefinitionV3.model_validate(json.loads(payload))
                observed_id, canonical = named_candidate(strategy)
                if observed_id != candidate_id or canonical.canonical_bytes().decode() != payload:
                    raise _cache_error(f"candidate payload mismatch: {candidate_id}")
            orphan = database.execute(
                """SELECT p.id FROM provenance p
                LEFT JOIN candidates c ON c.id=p.id WHERE c.id IS NULL LIMIT 1"""
            ).fetchone()
            if orphan is not None:
                raise _cache_error(f"orphan provenance: {orphan[0]}")
            for (origin,) in database.execute("SELECT origin FROM provenance"):
                if not isinstance(json.loads(origin), dict):
                    raise _cache_error("invalid provenance payload")
    except ContractError:
        raise
    except (json.JSONDecodeError, OSError, sqlite3.DatabaseError, ValueError) as exc:
        raise _cache_error(str(exc)) from exc


def validate_composite_candidate_plan(plan: CompositeCandidatePlan) -> None:
    """Validate manifest/payload agreement without generating or changing candidates."""

    try:
        persisted = CandidateUniverseManifestV1.model_validate(
            _load_json(plan.directory / _CANDIDATE_MANIFEST)
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise _cache_error(f"invalid candidate manifest: {exc}") from exc
    if persisted != plan.manifest:
        raise _cache_error("in-memory and persisted candidate manifests differ")
    _validate_composite_database(plan.database, plan.manifest)


def _load_cached_plan(
    directory: Path, resolved: ResolvedSearchSpaceV1
) -> CompositeCandidatePlan:
    record_path = directory / _CACHE_RECORD
    try:
        record = _load_json(record_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise _cache_error(f"invalid cache record: {exc}") from exc
    if record.get("schema_version") != "composite-candidate-plan-cache/v1":
        raise _cache_error("unsupported cache record")
    if record.get("resolved_search_space_id") != resolved.resolved_search_space_id:
        raise ContractError("CHECKPOINT_CONTEXT_MISMATCH: resolved search space")
    expected_files = {
        _CANDIDATE_MANIFEST,
        _COMPOSITE_DATABASE,
        *(f"subplans/{item.direction.lower()}.sqlite" for item in resolved.subspaces),
    }
    files = record.get("files")
    if not isinstance(files, dict) or set(files) != expected_files:
        raise _cache_error("cache file inventory mismatch")
    for relative, expected_hash in files.items():
        path = directory / relative
        if not path.is_file() or sha256_file(path) != expected_hash:
            raise _cache_error(f"file hash mismatch: {relative}")
    try:
        manifest = CandidateUniverseManifestV1.model_validate(
            _load_json(directory / _CANDIDATE_MANIFEST)
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise _cache_error(f"invalid candidate manifest: {exc}") from exc
    if manifest.resolved_search_space_id != resolved.resolved_search_space_id:
        raise ContractError("CHECKPOINT_CONTEXT_MISMATCH: candidate manifest")
    _validate_composite_database(directory / _COMPOSITE_DATABASE, manifest)
    return CompositeCandidatePlan(directory=directory, manifest=manifest)


def _merge_subplans(
    resolved: ResolvedSearchSpaceV1,
    plans: list[tuple[ResolvedSideSearchSpaceV1, CandidatePlan]],
    database_path: Path,
) -> CandidateUniverseManifestV1:
    candidate_ids: set[str] = set()
    distributions: dict[str, dict[str, set[str]]] = {
        "direction": defaultdict(set),
        "timeframe": defaultdict(set),
        "family": defaultdict(set),
    }
    subplans: list[CandidateSubplanManifestV1] = []
    totals = {key: 0 for key in ("T", "R", "V")}
    with closing(sqlite3.connect(database_path)) as composite:
        composite.executescript(
            """
            PRAGMA journal_mode=DELETE;
            CREATE TABLE candidates(id TEXT PRIMARY KEY, strategy TEXT NOT NULL);
            CREATE TABLE provenance(
                id TEXT NOT NULL, origin TEXT NOT NULL, multiplicity INTEGER NOT NULL,
                PRIMARY KEY(id, origin));
            """
        )
        for subspace, plan in plans:
            counts = GenerationCountsV1.model_validate(plan.manifest["counts"])
            subplans.append(
                CandidateSubplanManifestV1(
                    direction=subspace.direction,
                    search_space_id=plan.manifest["search_space_id"],
                    generation_policy_id=plan.manifest["generation_policy_id"],
                    candidate_set_id=plan.manifest["candidate_set_id"],
                    counts=counts,
                )
            )
            for key in totals:
                totals[key] += getattr(counts, key)
            with closing(sqlite3.connect(plan.database)) as source:
                for candidate_id, payload in source.execute(
                    "SELECT id, strategy FROM candidates ORDER BY id"
                ):
                    existing = composite.execute(
                        "SELECT strategy FROM candidates WHERE id=?", (candidate_id,)
                    ).fetchone()
                    if existing is None:
                        composite.execute(
                            "INSERT INTO candidates VALUES (?,?)", (candidate_id, payload)
                        )
                    elif existing[0] != payload:
                        raise ContractError(
                            "candidate hash collision or inconsistent canonical payload"
                        )
                    candidate_ids.add(candidate_id)
                for candidate_id, origin_json, multiplicity in source.execute(
                    "SELECT id, origin, multiplicity FROM provenance ORDER BY id, origin"
                ):
                    origin = json.loads(origin_json)
                    for name in ("direction", "timeframe", "family"):
                        distributions[name][origin[name]].add(candidate_id)
                    composite.execute(
                        """INSERT INTO provenance VALUES (?,?,?)
                        ON CONFLICT(id,origin) DO UPDATE SET
                        multiplicity=multiplicity+excluded.multiplicity""",
                        (candidate_id, origin_json, multiplicity),
                    )
        composite.commit()

    ordered_ids = tuple(sorted(candidate_ids))
    unique_count = len(ordered_ids)
    duplicate_count = totals["T"] - totals["R"] - unique_count
    counts = GenerationCountsV1(
        T=totals["T"], R=totals["R"], V=totals["V"], D=duplicate_count, U=unique_count
    )
    policy = resolved.generation_policy
    if policy.expansion_limit < counts.T:
        raise ContractError(f"SEARCH_SPACE_EXPANSION_LIMIT_EXCEEDED: T={counts.T}")
    if policy.candidate_budget < counts.U:
        raise ContractError(
            f"SEARCH_SPACE_EXCEEDS_BUDGET: U={counts.U}, budget={policy.candidate_budget}"
        )
    return CandidateUniverseManifestV1(
        schema_version="candidate-universe-manifest/v1",
        resolved_search_space_id=resolved.resolved_search_space_id,
        resolution_protocol_id=resolved.resolution_protocol_id,
        subplans=tuple(subplans),
        counts=counts,
        by_direction={key: len(value) for key, value in sorted(distributions["direction"].items())},
        by_timeframe={key: len(value) for key, value in sorted(distributions["timeframe"].items())},
        by_family={key: len(value) for key, value in sorted(distributions["family"].items())},
        candidate_ids=ordered_ids,
        candidate_universe_id=identity("candidate-set/v1", list(ordered_ids)),
    )


def build_composite_candidate_plan(
    resolved: ResolvedSearchSpaceV1, cache_root: Path
) -> CompositeCandidatePlan:
    """Build or validate one immutable content-addressed composite plan cache."""

    if resolved.resolution_protocol_id != RESOLUTION_PROTOCOL_ID:
        raise ContractError("AUTO_DISCOVERY_RESOLUTION_PROTOCOL_MISMATCH")
    cache_root.mkdir(parents=True, exist_ok=True)
    directory = cache_root / resolved.resolved_search_space_id.removeprefix("sha256:")
    if directory.exists():
        return _load_cached_plan(directory, resolved)

    staging = Path(tempfile.mkdtemp(prefix=".cp-", dir=cache_root))
    try:
        subplan_directory = staging / "subplans"
        plans: list[tuple[ResolvedSideSearchSpaceV1, CandidatePlan]] = []
        for subspace in sorted(resolved.subspaces, key=lambda item: item.direction):
            database = subplan_directory / f"{subspace.direction.lower()}.sqlite"
            plans.append(
                (
                    subspace,
                    preflight(subspace.search_space, resolved.generation_policy, database),
                )
            )
        manifest = _merge_subplans(resolved, plans, staging / _COMPOSITE_DATABASE)
        write_canonical_json(staging / _CANDIDATE_MANIFEST, manifest.model_dump(mode="json"))
        relative_files = (
            _CANDIDATE_MANIFEST,
            _COMPOSITE_DATABASE,
            *(f"subplans/{item.direction.lower()}.sqlite" for item in resolved.subspaces),
        )
        record = {
            "schema_version": "composite-candidate-plan-cache/v1",
            "resolved_search_space_id": resolved.resolved_search_space_id,
            "candidate_universe_id": manifest.candidate_universe_id,
            "files": {
                relative: sha256_file(staging / relative) for relative in sorted(relative_files)
            },
        }
        write_canonical_json(staging / _CACHE_RECORD, record)
        try:
            os.replace(staging, directory)
        except OSError:
            if not directory.exists():
                raise
            shutil.rmtree(staging, ignore_errors=True)
            return _load_cached_plan(directory, resolved)
        return CompositeCandidatePlan(directory=directory, manifest=manifest)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def preflight_candidate_universe(
    resolved: ResolvedSearchSpaceV1, database_directory: Path
) -> CandidateUniverseManifestV1:
    """Compatibility wrapper returning the immutable composite plan manifest."""

    return build_composite_candidate_plan(resolved, database_directory).manifest


def verify_frozen_profile(
    profile: AutoDiscoveryProfileV1, manifest: CandidateUniverseManifestV1
) -> None:
    """Fail closed if a complete frozen profile resolves to an unexpected universe."""

    expected = profile.expected_candidate_universe
    observed = {
        "counts": manifest.counts.model_dump(mode="json"),
        "by_direction": manifest.by_direction,
        "by_timeframe": manifest.by_timeframe,
        "by_family": manifest.by_family,
    }
    if observed != expected.model_dump(mode="json"):
        raise ContractError(
            "AUTO_DISCOVERY_PROFILE_CARDINALITY_MISMATCH: "
            f"expected={expected.model_dump(mode='json')}, observed={observed}"
        )


def _complete_profile_request(
    request: MiningRequestV1, profile: AutoDiscoveryProfileV1
) -> bool:
    return set(request.timeframes) == set(profile.timeframes) and all(
        request.sides[direction].enabled == profile.constraints.sides[direction].enabled
        for direction in ("BUY", "SELL")
    )


def _checkpoint_context(
    request: MiningRequestV1,
    constraints: MiningConstraintsV1,
    profile: AutoDiscoveryProfileV1 | None,
    resolved: ResolvedSearchSpaceV1,
) -> dict[str, Any]:
    return {
        "mining_request_id": request.mining_request_id,
        "mining_constraints_id": constraints.mining_constraints_id,
        "auto_discovery_profile_id": profile.auto_discovery_profile_id if profile else None,
        "resolved_search_space_id": resolved.resolved_search_space_id,
        "resolution_protocol_id": resolved.resolution_protocol_id,
    }


def _read_checkpoint(path: Path, context: dict[str, Any]) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        checkpoint = _load_json(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ContractError(f"CHECKPOINT_CONTEXT_MISMATCH: invalid checkpoint: {exc}") from exc
    if (
        checkpoint.get("schema_version") != "auto-discovery-checkpoint/v1"
        or checkpoint.get("context") != context
    ):
        raise ContractError("CHECKPOINT_CONTEXT_MISMATCH")
    return checkpoint


def _write_checkpoint(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=".checkpoint-", dir=path.parent)
    os.close(handle)
    temporary = Path(temporary_name)
    try:
        write_canonical_json(temporary, record)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _bundle_payloads(
    request: MiningRequestV1,
    constraints: MiningConstraintsV1,
    profile: AutoDiscoveryProfileV1 | None,
    resolved: ResolvedSearchSpaceV1,
    manifest: CandidateUniverseManifestV1,
) -> dict[str, Any]:
    return {
        "mining-request.json": request.model_dump(mode="json"),
        "mining-constraints.json": constraints.model_dump(mode="json"),
        "auto-discovery-profile.json": profile.model_dump(mode="json") if profile else None,
        "resolved-search-space.json": resolved.model_dump(mode="json"),
        "candidate-universe-manifest.json": manifest.model_dump(mode="json"),
    }


def _bundle_id(payloads: dict[str, Any]) -> str:
    hashes = {
        name: "sha256:" + hashlib.sha256(canonical_json_bytes(value)).hexdigest()
        for name, value in sorted(payloads.items())
    }
    return identity("candidate-plan-bundle/v1", hashes)


def _validate_bundle(directory: Path, payloads: dict[str, Any]) -> None:
    if not directory.is_dir():
        raise ContractError("CHECKPOINT_CONTEXT_MISMATCH: bundle path is not a directory")
    observed = {path.name for path in directory.iterdir() if path.is_file()}
    if observed != set(_BUNDLE_FILES):
        raise ContractError("CHECKPOINT_CONTEXT_MISMATCH: bundle inventory")
    for name, payload in payloads.items():
        if (directory / name).read_bytes() != canonical_json_bytes(payload):
            raise ContractError(f"CHECKPOINT_CONTEXT_MISMATCH: bundle artifact {name}")


def load_candidate_plan_bundle(
    directory: Path, *, cache_root: Path
) -> CandidatePlanBundle:
    """Validate a published bundle and its immutable Composite Candidate Plan.

    This is deliberately a read-only operation.  Research consumes the candidates that
    were already materialized during planning; it never expands the grids again.
    """

    try:
        request = MiningRequestV1.model_validate(_load_json(directory / "mining-request.json"))
        constraints = MiningConstraintsV1.model_validate(
            _load_json(directory / "mining-constraints.json")
        )
        profile_payload = _load_json(directory / "auto-discovery-profile.json")
        profile = (
            AutoDiscoveryProfileV1.model_validate(profile_payload)
            if profile_payload is not None
            else None
        )
        resolved = ResolvedSearchSpaceV1.model_validate(
            _load_json(directory / "resolved-search-space.json")
        )
        manifest = CandidateUniverseManifestV1.model_validate(
            _load_json(directory / "candidate-universe-manifest.json")
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ContractError(
            f"CHECKPOINT_CONTEXT_MISMATCH: invalid candidate bundle: {exc}"
        ) from exc

    expected_resolved = resolve_request(request, profile)
    expected_constraints = profile.constraints if profile is not None else request.constraints
    if expected_constraints is None or constraints != expected_constraints:
        raise ContractError("CHECKPOINT_CONTEXT_MISMATCH: bundle constraints")
    if resolved != expected_resolved:
        raise ContractError("CHECKPOINT_CONTEXT_MISMATCH: bundle resolution")
    payloads = _bundle_payloads(request, constraints, profile, resolved, manifest)
    _validate_bundle(directory, payloads)

    plan_directory = cache_root / resolved.resolved_search_space_id.removeprefix("sha256:")
    plan = _load_cached_plan(plan_directory, resolved)
    if plan.manifest != manifest:
        raise ContractError("CHECKPOINT_CONTEXT_MISMATCH: bundle candidate universe")
    if profile is not None and _complete_profile_request(request, profile):
        verify_frozen_profile(profile, manifest)
    return CandidatePlanBundle(
        bundle_id=_bundle_id(payloads), directory=directory, plan=plan, resolved=resolved
    )


def publish_candidate_plan_bundle(
    request: MiningRequestV1,
    *,
    registry: ProfileRegistry | None,
    cache_root: Path,
    checkpoint: Path,
    output_root: Path,
    stop_after_resolution: bool = False,
) -> CandidatePlanBundle:
    """Resolve, checkpoint, plan and atomically publish five immutable artifacts."""

    resolved, constraints, profile = resolve_from_registry(request, registry)
    context = _checkpoint_context(request, constraints, profile, resolved)
    existing_checkpoint = _read_checkpoint(checkpoint, context)
    if existing_checkpoint is None:
        _write_checkpoint(
            checkpoint,
            {
                "schema_version": "auto-discovery-checkpoint/v1",
                "phase": "RESOLVED",
                "context": context,
            },
        )
    if stop_after_resolution:
        raise ContractError("CONTROLLED_INTERRUPTION_AFTER_RESOLUTION")

    plan = build_composite_candidate_plan(resolved, cache_root)
    if profile is not None and _complete_profile_request(request, profile):
        verify_frozen_profile(profile, plan.manifest)
    if existing_checkpoint is not None:
        recorded_universe = existing_checkpoint.get("candidate_universe_id")
        if recorded_universe not in (None, plan.manifest.candidate_universe_id):
            raise ContractError("CHECKPOINT_CONTEXT_MISMATCH: candidate universe")

    payloads = _bundle_payloads(request, constraints, profile, resolved, plan.manifest)
    bundle_id = _bundle_id(payloads)
    output_root.mkdir(parents=True, exist_ok=True)
    destination = output_root / bundle_id.removeprefix("sha256:")
    if destination.exists():
        _validate_bundle(destination, payloads)
    else:
        staging = Path(tempfile.mkdtemp(prefix=".bundle-", dir=output_root))
        try:
            for name in _BUNDLE_FILES:
                write_canonical_json(staging / name, payloads[name])
            try:
                os.replace(staging, destination)
            except OSError:
                if not destination.exists():
                    raise
                shutil.rmtree(staging, ignore_errors=True)
                _validate_bundle(destination, payloads)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
    _write_checkpoint(
        checkpoint,
        {
            "schema_version": "auto-discovery-checkpoint/v1",
            "phase": "PUBLISHED",
            "context": context,
            "candidate_universe_id": plan.manifest.candidate_universe_id,
            "bundle_id": bundle_id,
        },
    )
    return CandidatePlanBundle(
        bundle_id=bundle_id, directory=destination, plan=plan, resolved=resolved
    )
