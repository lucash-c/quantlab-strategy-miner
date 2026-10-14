"""Publish exact JSON Schemas for the versioned mining contracts."""

from __future__ import annotations

import json
from pathlib import Path

from quantlab_mining.contracts import (
    AutoDiscoveryProfileV1,
    CandidateUniverseManifestV1,
    MiningConstraintsV1,
    MiningRequestV1,
    ResolvedSearchSpaceV1,
)

ROOT = Path(__file__).resolve().parents[1]
MODELS = {
    "mining-request-v1": MiningRequestV1,
    "mining-constraints-v1": MiningConstraintsV1,
    "auto-discovery-profile-v1": AutoDiscoveryProfileV1,
    "resolved-search-space-v1": ResolvedSearchSpaceV1,
    "candidate-universe-manifest-v1": CandidateUniverseManifestV1,
}


def main() -> None:
    for name, model in MODELS.items():
        destination = ROOT / "schemas" / f"{name}.schema.json"
        destination.write_text(
            json.dumps(model.model_json_schema(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
