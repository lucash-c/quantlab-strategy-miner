from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError
from quantlab_core.errors import ContractError
from quantlab_mining.auto_discovery import (
    RESOLUTION_PROTOCOL_ID,
    preflight_candidate_universe,
    resolve_request,
    verify_frozen_profile,
)
from quantlab_mining.contracts import (
    AutoDiscoveryProfileV1,
    CandidateUniverseManifestV1,
    MiningConstraintsV1,
    MiningRequestV1,
    ResolvedSearchSpaceV1,
    load_contract,
)

ROOT = Path(__file__).resolve().parents[1]
PROFILE_ROOT = ROOT / "examples" / "mining" / "auto-discovery"


class AutoDiscoveryV1Tests(unittest.TestCase):
    @staticmethod
    def profile(name: str) -> AutoDiscoveryProfileV1:
        return load_contract(PROFILE_ROOT / name, AutoDiscoveryProfileV1)

    @staticmethod
    def request(
        profile: AutoDiscoveryProfileV1, *, buy: bool = True, sell: bool = True
    ) -> MiningRequestV1:
        return MiningRequestV1.model_validate(
            {
                "schema_version": "mining-request/v1",
                "mode": "AUTO_DISCOVERY",
                "logical_asset": profile.logical_asset,
                "timeframes": list(profile.timeframes),
                "sides": {"BUY": {"enabled": buy}, "SELL": {"enabled": sell}},
                "auto_discovery_profile_id": profile.auto_discovery_profile_id,
                "constraints": None,
            }
        )

    def test_published_schemas_match_strict_contracts(self) -> None:
        models = {
            "mining-request-v1": MiningRequestV1,
            "mining-constraints-v1": MiningConstraintsV1,
            "auto-discovery-profile-v1": AutoDiscoveryProfileV1,
            "resolved-search-space-v1": ResolvedSearchSpaceV1,
            "candidate-universe-manifest-v1": CandidateUniverseManifestV1,
        }
        for name, model in models.items():
            published = json.loads((ROOT / "schemas" / f"{name}.schema.json").read_text())
            self.assertEqual(published, model.model_json_schema())

    def test_frozen_profile_identities_and_no_alias_semantics(self) -> None:
        balanced = self.profile("balanced-v1.json")
        acceptance = self.profile("acceptance-v1.json")
        self.assertEqual(
            balanced.auto_discovery_profile_id,
            "sha256:d199deb074d14ce6cb4f3b2a91f74979f7f4ce0d577291cb9c1262cec7ef93d2",
        )
        self.assertEqual(
            acceptance.auto_discovery_profile_id,
            "sha256:d948c66d986cf2834fa03fd9032205124f4191f329ecfe521585d3d139e6eb7b",
        )
        self.assertEqual(balanced.resolution_protocol_id, RESOLUTION_PROTOCOL_ID)
        renamed = balanced.model_copy(update={"alias": "IMMUTABLE.CONTENT.ALIAS"})
        self.assertEqual(renamed.auto_discovery_profile_id, balanced.auto_discovery_profile_id)

    def test_resolution_is_deterministic_and_does_not_run_preflight(self) -> None:
        profile = self.profile("balanced-v1.json")
        request = self.request(profile)
        reordered = MiningRequestV1.model_validate(
            {
                **request.model_dump(mode="json"),
                "timeframes": ["15m", "5m", "2m", "1m"],
                "sides": {"SELL": {"enabled": True}, "BUY": {"enabled": True}},
            }
        )
        with patch("quantlab_mining.auto_discovery.preflight") as structural_preflight:
            first = resolve_request(request, profile)
            second = resolve_request(reordered, profile)
        structural_preflight.assert_not_called()
        self.assertEqual(request.mining_request_id, reordered.mining_request_id)
        self.assertEqual(first.resolved_search_space_id, second.resolved_search_space_id)

    def test_buy_sell_independence_and_acceptance_asymmetry(self) -> None:
        profile = self.profile("acceptance-v1.json")
        buy = resolve_request(self.request(profile, sell=False), profile)
        sell = resolve_request(self.request(profile, buy=False), profile)
        self.assertEqual([item.direction for item in buy.subspaces], ["BUY"])
        self.assertEqual([item.direction for item in sell.subspaces], ["SELL"])
        self.assertEqual(buy.subspaces[0].search_space.stops, ("5",))
        self.assertEqual(buy.subspaces[0].search_space.targets, ("10",))
        self.assertEqual(sell.subspaces[0].search_space.stops, ("10",))
        self.assertEqual(sell.subspaces[0].search_space.targets, ("5",))
        self.assertNotEqual(buy.resolved_search_space_id, sell.resolved_search_space_id)

    def test_science_changes_ids_and_operational_fields_are_forbidden(self) -> None:
        profile = self.profile("balanced-v1.json")
        changed = profile.model_dump(mode="json")
        changed["constraints"]["sides"]["BUY"]["stops"][-1] = "45"
        changed_profile = AutoDiscoveryProfileV1.model_validate(changed)
        self.assertNotEqual(
            changed_profile.constraints.mining_constraints_id,
            profile.constraints.mining_constraints_id,
        )
        self.assertNotEqual(
            changed_profile.auto_discovery_profile_id, profile.auto_discovery_profile_id
        )
        invalid_request = self.request(profile).model_dump(mode="json")
        invalid_request["workers"] = 8
        with self.assertRaises(ValidationError):
            MiningRequestV1.model_validate(invalid_request)
        hidden_default = profile.model_dump(mode="json")
        hidden_default["constraints"]["generation_policy"].pop("max_conditions")
        with self.assertRaisesRegex(ValidationError, "explicitly declare"):
            AutoDiscoveryProfileV1.model_validate(hidden_default)

    def test_constrained_mode_and_legacy_search_space_remain_supported(self) -> None:
        profile = self.profile("acceptance-v1.json")
        record = self.request(profile).model_dump(mode="json")
        record.update(
            mode="CONSTRAINED",
            auto_discovery_profile_id=None,
            constraints=profile.constraints.model_dump(mode="json"),
        )
        request = MiningRequestV1.model_validate(record)
        resolved = resolve_request(request)
        self.assertIsNone(resolved.auto_discovery_profile_id)
        self.assertEqual(resolved.mining_constraints_id, profile.constraints.mining_constraints_id)

        from mining_helpers import small_search
        from quantlab_mining.contracts import GenerationPolicyV1
        from quantlab_mining.generator import preflight

        with tempfile.TemporaryDirectory() as tmp:
            legacy = preflight(
                small_search(), GenerationPolicyV1(), Path(tmp) / "legacy.sqlite"
            )
            self.assertEqual(legacy.manifest["counts"]["U"], 2)

    def test_wrong_profile_hash_and_global_budget_fail_closed(self) -> None:
        profile = self.profile("acceptance-v1.json")
        wrong = self.request(profile).model_dump(mode="json")
        wrong["auto_discovery_profile_id"] = "sha256:" + "0" * 64
        with self.assertRaisesRegex(ContractError, "PROFILE_ID_MISMATCH"):
            resolve_request(MiningRequestV1.model_validate(wrong), profile)

        resolved = resolve_request(self.request(profile), profile)
        constrained = resolved.model_dump(mode="json")
        constrained["generation_policy"]["candidate_budget"] = 30
        constrained["generation_policy"]["expansion_limit"] = 64
        constrained = ResolvedSearchSpaceV1.model_validate(constrained)
        with (
            tempfile.TemporaryDirectory() as tmp,
            self.assertRaisesRegex(ContractError, "SEARCH_SPACE_EXCEEDS_BUDGET"),
        ):
            preflight_candidate_universe(constrained, Path(tmp))

    def test_complete_frozen_universes_and_clean_warm_determinism(self) -> None:
        cases = (
            (
                "balanced-v1.json",
                "sha256:2c31cfad6554508ef50a518d2f95b5457ee196f22673915c1a7874c88e1f7610",
            ),
            (
                "acceptance-v1.json",
                "sha256:7d97661a7221f0de68597f65fd339e5406f3e248a9181308c53c71531f0e0cc1",
            ),
        )
        with tempfile.TemporaryDirectory() as tmp:
            for filename, universe_id in cases:
                profile = self.profile(filename)
                resolved = resolve_request(self.request(profile), profile)
                destination = Path(tmp) / filename
                clean = preflight_candidate_universe(resolved, destination)
                verify_frozen_profile(profile, clean)
                warm = preflight_candidate_universe(resolved, destination)
                verify_frozen_profile(profile, warm)
                self.assertEqual(clean, warm)
                self.assertEqual(clean.candidate_universe_id, universe_id)
                reversed_order = resolved.model_copy(
                    update={"subspaces": tuple(reversed(resolved.subspaces))}
                )
                reordered = preflight_candidate_universe(reversed_order, destination)
                self.assertEqual(reordered.candidate_universe_id, universe_id)

    def test_profile_files_are_canonical_roundtrips(self) -> None:
        for filename in ("balanced-v1.json", "acceptance-v1.json"):
            profile = self.profile(filename)
            dumped = profile.model_dump(mode="json")
            self.assertEqual(
                profile.auto_discovery_profile_id,
                AutoDiscoveryProfileV1.model_validate(copy.deepcopy(dumped)).auto_discovery_profile_id,
            )
