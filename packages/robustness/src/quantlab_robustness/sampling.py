"""Library-independent SHA-256 counter sampler with explicit big-endian semantics."""

from __future__ import annotations

import hashlib
from typing import Any

from quantlab_core.canonical import canonical_json_bytes
from quantlab_core.errors import ContractError
from quantlab_mining.contracts import identity

from quantlab_robustness.contracts import MonteCarloPolicyV1

SAMPLER_DOMAIN = "quantlab-monte-carlo-sampler/v1"
SAMPLER_VERSION = "SHA256_COUNTER_REJECTION_V1"


def draw_trace(
    *,
    source_pool_id: str,
    sampling_method: str,
    seed: str,
    path_index: int,
    draw_index: int,
    upper_bound: int,
) -> dict[str, Any]:
    if upper_bound < 1:
        raise ContractError("sampler upper bound must be positive")
    limit = ((1 << 256) // upper_bound) * upper_bound
    attempts = []
    retry_index = 0
    while True:
        payload = {
            "domain": SAMPLER_DOMAIN,
            "sampler_version": SAMPLER_VERSION,
            "source_pool_id": source_pool_id,
            "sampling_method": sampling_method,
            "seed": seed,
            "path_index": path_index,
            "draw_index": draw_index,
            "retry_index": retry_index,
        }
        digest = hashlib.sha256(canonical_json_bytes(payload)).digest()
        integer = int.from_bytes(digest, byteorder="big", signed=False)
        accepted = integer < limit
        attempts.append(
            {
                "retry_index": retry_index,
                "digest_hex": digest.hex(),
                "integer_decimal": str(integer),
                "accepted": accepted,
            }
        )
        if accepted:
            return {
                "encoding": "CANONICAL_JSON_UTF8_WITH_TRAILING_LF",
                "integer_encoding": "UNSIGNED_BIG_ENDIAN_256",
                "upper_bound": upper_bound,
                "rejection_limit_decimal": str(limit),
                "attempts": attempts,
                "selected_index": integer % upper_bound,
            }
        retry_index += 1


def source_pool_record(
    *, source: dict[str, Any], dataset_id: str, sessions: list[dict[str, str]]
) -> dict[str, Any]:
    session_ids = [item["session_id"] for item in sessions]
    dates = [item["trading_date"] for item in sessions]
    if not session_ids or len(session_ids) != len(set(session_ids)) or dates != sorted(set(dates)):
        raise ContractError("Monte Carlo source pool must be nonempty, unique and chronological")
    record = {
        "schema_version": "monte-carlo-source-pool/v1",
        "source": source,
        "dataset_id": dataset_id,
        "sessions": sessions,
    }
    record["monte_carlo_source_pool_id"] = identity("monte-carlo-source-pool/v1", record)
    return record


def build_path_set(policy: MonteCarloPolicyV1, pool: dict[str, Any]) -> dict[str, Any]:
    sessions = pool["sessions"]
    if len(sessions) < policy.minimum_source_sessions:
        record = {
            "schema_version": "monte-carlo-path-set/v1",
            "status": "INSUFFICIENT_MONTE_CARLO_SOURCE",
            "monte_carlo_policy_id": policy.policy_id,
            "monte_carlo_source_pool_id": pool["monte_carlo_source_pool_id"],
            "sampler_domain": SAMPLER_DOMAIN,
            "sampler_version": SAMPLER_VERSION,
            "seed": policy.seed,
            "sampling_method": policy.sampling_method,
            "paths": [],
            "path_count": 0,
        }
        record["monte_carlo_path_set_id"] = identity("monte-carlo-path-set/v1", record)
        return record
    if (
        policy.sampling_method == "SESSION_PERMUTATION_WITHOUT_REPLACEMENT"
        and policy.path_length_sessions > len(sessions)
    ):
        raise ContractError("permutation path length exceeds source pool")
    paths = []
    for path_index in range(policy.number_of_paths):
        available = list(range(len(sessions)))
        blocks = []
        for draw_index in range(policy.path_length_sessions):
            upper = (
                len(available)
                if policy.sampling_method == "SESSION_PERMUTATION_WITHOUT_REPLACEMENT"
                else len(sessions)
            )
            trace = draw_trace(
                source_pool_id=pool["monte_carlo_source_pool_id"],
                sampling_method=policy.sampling_method,
                seed=policy.seed,
                path_index=path_index,
                draw_index=draw_index,
                upper_bound=upper,
            )
            selected = trace["selected_index"]
            source_position = (
                available.pop(selected)
                if policy.sampling_method == "SESSION_PERMUTATION_WITHOUT_REPLACEMENT"
                else selected
            )
            source_session = sessions[source_position]
            block = {
                "path_index": path_index,
                "synthetic_block_ordinal": draw_index + 1,
                "source_session_id": source_session["session_id"],
                "source_pool_position": source_position,
                "sampling": {
                    "draw_index": draw_index,
                    "accepted_retry_index": trace["attempts"][-1]["retry_index"],
                    "accepted_digest_hex": trace["attempts"][-1]["digest_hex"],
                },
            }
            blocks.append(block)
        path = {
            "schema_version": "monte-carlo-path-definition/v1",
            "monte_carlo_policy_id": policy.policy_id,
            "monte_carlo_source_pool_id": pool["monte_carlo_source_pool_id"],
            "path_index": path_index,
            "blocks": blocks,
        }
        path["path_id"] = identity("monte-carlo-path-definition/v1", path)
        for block in blocks:
            block["synthetic_occurrence_id"] = identity(
                "monte-carlo-session-occurrence/v1",
                {
                    "path_id": path["path_id"],
                    "synthetic_block_ordinal": block["synthetic_block_ordinal"],
                },
            )
        paths.append(path)
    record = {
        "schema_version": "monte-carlo-path-set/v1",
        "status": "READY",
        "monte_carlo_policy_id": policy.policy_id,
        "monte_carlo_source_pool_id": pool["monte_carlo_source_pool_id"],
        "sampler_domain": SAMPLER_DOMAIN,
        "sampler_version": SAMPLER_VERSION,
        "seed": policy.seed,
        "sampling_method": policy.sampling_method,
        "path_count": len(paths),
        "paths": paths,
    }
    record["monte_carlo_path_set_id"] = identity("monte-carlo-path-set/v1", record)
    return record
