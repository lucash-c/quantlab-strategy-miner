"""Deterministic mining-intent resolution over Candidate Generator v1."""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from contextlib import closing
from pathlib import Path

from quantlab_core.errors import ContractError

from quantlab_mining.contracts import (
    CANONICALIZATION_VERSION,
    GENERATOR_VERSION,
    TEMPLATE_REGISTRY_VERSION,
    AutoDiscoveryProfileV1,
    CandidateSubplanManifestV1,
    CandidateUniverseManifestV1,
    GenerationCountsV1,
    MiningRequestV1,
    ResolvedSearchSpaceV1,
    ResolvedSideSearchSpaceV1,
    identity,
)
from quantlab_mining.generator import preflight

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
        if request.constraints is None:  # Guaranteed by the strict request contract.
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


def preflight_candidate_universe(
    resolved: ResolvedSearchSpaceV1, database_directory: Path
) -> CandidateUniverseManifestV1:
    """Materialize the complete structural universe, never a sampled/truncated subset."""

    if resolved.resolution_protocol_id != RESOLUTION_PROTOCOL_ID:
        raise ContractError("AUTO_DISCOVERY_RESOLUTION_PROTOCOL_MISMATCH")
    database_directory.mkdir(parents=True, exist_ok=True)
    candidate_ids: set[str] = set()
    distributions: dict[str, dict[str, set[str]]] = {
        "direction": defaultdict(set),
        "timeframe": defaultdict(set),
        "family": defaultdict(set),
    }
    subplans: list[CandidateSubplanManifestV1] = []
    totals = {key: 0 for key in ("T", "R", "V")}
    for subspace in sorted(resolved.subspaces, key=lambda item: item.direction):
        database = database_directory / f"candidate-plan-{subspace.direction.lower()}.sqlite"
        plan = preflight(subspace.search_space, resolved.generation_policy, database)
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
        with closing(sqlite3.connect(database)) as db:
            for candidate_id, origin_json in db.execute("SELECT id, origin FROM provenance"):
                candidate_ids.add(candidate_id)
                origin = json.loads(origin_json)
                distributions["direction"][origin["direction"]].add(candidate_id)
                distributions["timeframe"][origin["timeframe"]].add(candidate_id)
                distributions["family"][origin["family"]].add(candidate_id)

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
