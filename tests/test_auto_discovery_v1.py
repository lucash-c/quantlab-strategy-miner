from __future__ import annotations

import copy
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError
from quantlab_core.errors import ContractError
from quantlab_mining.auto_discovery import (
    RESOLUTION_PROTOCOL_ID,
    ProfileRegistry,
    build_composite_candidate_plan,
    preflight_candidate_universe,
    publish_candidate_plan_bundle,
    resolve_from_registry,
    resolve_request,
    verify_frozen_profile,
)
from quantlab_mining.contracts import (
    AutoDiscoveryProfileV1,
    CandidateUniverseManifestV1,
    GenerationPolicyV1,
    MiningConstraintsV1,
    MiningRequestV1,
    ResolvedSearchSpaceV1,
    load_contract,
)
from quantlab_mining.generator import CandidatePlan, preflight

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
                reordered = preflight_candidate_universe(
                    reversed_order, Path(tmp) / f"{filename}-reordered"
                )
                self.assertEqual(reordered.candidate_universe_id, universe_id)

    def test_profile_files_are_canonical_roundtrips(self) -> None:
        for filename in ("balanced-v1.json", "acceptance-v1.json"):
            profile = self.profile(filename)
            dumped = profile.model_dump(mode="json")
            self.assertEqual(
                profile.auto_discovery_profile_id,
                AutoDiscoveryProfileV1.model_validate(copy.deepcopy(dumped)).auto_discovery_profile_id,
            )

    def test_profile_registry_requires_full_hash_and_never_resolves_aliases(self) -> None:
        registry = ProfileRegistry.from_directory(PROFILE_ROOT)
        profile = self.profile("acceptance-v1.json")
        self.assertEqual(registry.resolve(profile.auto_discovery_profile_id), profile)
        with self.assertRaisesRegex(ContractError, "PROFILE_NOT_FOUND"):
            registry.resolve(profile.alias)
        with self.assertRaisesRegex(ContractError, "PROFILE_NOT_FOUND"):
            registry.resolve("sha256:" + "0" * 64)

    def test_profile_allowlist_can_only_narrow_timeframes_and_sides(self) -> None:
        profile = self.profile("acceptance-v1.json")
        narrowed = self.request(profile, sell=False).model_copy(update={"timeframes": ("5m",)})
        resolved = resolve_request(narrowed, profile)
        self.assertEqual([item.direction for item in resolved.subspaces], ["BUY"])
        self.assertEqual(resolved.subspaces[0].search_space.timeframes, ("5m",))

        outside = profile.model_copy(update={"timeframes": ("2m", "5m", "15m")})
        invalid = self.request(profile).model_dump(mode="json")
        invalid["timeframes"] = ["1m"]
        invalid["auto_discovery_profile_id"] = outside.auto_discovery_profile_id
        request = MiningRequestV1.model_validate(invalid)
        with self.assertRaisesRegex(ContractError, "TIMEFRAME_OUTSIDE_PROFILE"):
            resolve_request(request, outside)

    def test_buy_only_and_sell_only_composite_plans_remain_independent(self) -> None:
        profile = self.profile("acceptance-v1.json")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            buy = build_composite_candidate_plan(
                resolve_request(self.request(profile, sell=False), profile), root / "buy"
            )
            sell = build_composite_candidate_plan(
                resolve_request(self.request(profile, buy=False), profile), root / "sell"
            )
            self.assertEqual(buy.manifest.counts.U, 24)
            self.assertEqual(sell.manifest.counts.U, 24)
            self.assertEqual(buy.manifest.by_direction, {"BUY": 24})
            self.assertEqual(sell.manifest.by_direction, {"SELL": 24})
            self.assertFalse(set(buy.manifest.candidate_ids) & set(sell.manifest.candidate_ids))

    def test_bundle_interruption_resume_warm_and_checkpoint_context(self) -> None:
        profile = self.profile("acceptance-v1.json")
        request = self.request(profile)
        registry = ProfileRegistry.from_directory(PROFILE_ROOT)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            kwargs = {
                "registry": registry,
                "cache_root": root / "cache",
                "checkpoint": root / "checkpoint.json",
                "output_root": root / "output",
            }
            with self.assertRaisesRegex(ContractError, "CONTROLLED_INTERRUPTION"):
                publish_candidate_plan_bundle(
                    request, **kwargs, stop_after_resolution=True
                )
            self.assertTrue((root / "checkpoint.json").is_file())
            self.assertFalse((root / "output").exists())

            clean = publish_candidate_plan_bundle(request, **kwargs)
            expected_files = {
                "mining-request.json",
                "mining-constraints.json",
                "auto-discovery-profile.json",
                "resolved-search-space.json",
                "candidate-universe-manifest.json",
            }
            self.assertEqual(
                {path.name for path in clean.directory.iterdir() if path.is_file()},
                expected_files,
            )
            clean_bytes = {name: (clean.directory / name).read_bytes() for name in expected_files}
            warm = publish_candidate_plan_bundle(request, **kwargs)
            self.assertEqual(warm.bundle_id, clean.bundle_id)
            self.assertEqual(
                clean_bytes,
                {name: (warm.directory / name).read_bytes() for name in expected_files},
            )

            fresh = publish_candidate_plan_bundle(
                request,
                registry=registry,
                cache_root=root / "fresh-cache",
                checkpoint=root / "fresh-checkpoint.json",
                output_root=root / "fresh-output",
            )
            self.assertEqual(fresh.bundle_id, clean.bundle_id)
            self.assertEqual(
                clean_bytes,
                {name: (fresh.directory / name).read_bytes() for name in expected_files},
            )

            corrupt_artifact = clean.directory / "resolved-search-space.json"
            corrupt_artifact.write_bytes(b"corrupt")
            with self.assertRaisesRegex(ContractError, "CHECKPOINT_CONTEXT_MISMATCH"):
                publish_candidate_plan_bundle(request, **kwargs)
            self.assertEqual(corrupt_artifact.read_bytes(), b"corrupt")

            different = self.request(profile, sell=False)
            with self.assertRaisesRegex(ContractError, "CHECKPOINT_CONTEXT_MISMATCH"):
                publish_candidate_plan_bundle(different, **kwargs)

    def test_corrupt_cache_is_rejected_without_rebuild_or_overwrite(self) -> None:
        profile = self.profile("acceptance-v1.json")
        resolved = resolve_request(self.request(profile), profile)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan = build_composite_candidate_plan(resolved, root)
            plan.database.write_bytes(b"corrupt")
            with self.assertRaisesRegex(ContractError, "CANDIDATE_PLAN_CACHE_CORRUPT"):
                build_composite_candidate_plan(resolved, root)
            self.assertEqual(plan.database.read_bytes(), b"corrupt")

    def test_composite_consolidates_identical_candidates_with_all_provenance(self) -> None:
        from mining_helpers import small_search

        search = small_search()
        first = search.templates[0].model_dump(mode="json")
        second = copy.deepcopy(first)
        first["template_id"] = "DUPLICATE.A"
        second["template_id"] = "DUPLICATE.B"
        constraints = MiningConstraintsV1.model_validate(
            {
                "schema_version": "mining-constraints/v1",
                "logic": "AND_ONLY",
                "sampling": "NONE",
                "truncation": "PROHIBITED",
                "template_catalog": [first, second],
                "time_windows": [item.model_dump(mode="json") for item in search.time_windows],
                "sides": {
                    "BUY": {
                        "enabled": True,
                        "template_ids": ["DUPLICATE.A", "DUPLICATE.B"],
                        "stops": ["5"],
                        "targets": ["5"],
                    },
                    "SELL": {
                        "enabled": False,
                        "template_ids": [],
                        "stops": [],
                        "targets": [],
                    },
                },
                "generation_policy": GenerationPolicyV1().model_dump(mode="json"),
            }
        )
        request = MiningRequestV1.model_validate(
            {
                "schema_version": "mining-request/v1",
                "mode": "CONSTRAINED",
                "logical_asset": "WIN",
                "timeframes": ["1m"],
                "sides": {"BUY": {"enabled": True}, "SELL": {"enabled": False}},
                "auto_discovery_profile_id": None,
                "constraints": constraints.model_dump(mode="json"),
            }
        )
        resolved, _, _ = resolve_from_registry(request, None)
        with tempfile.TemporaryDirectory() as temporary:
            plan = build_composite_candidate_plan(resolved, Path(temporary))
            self.assertEqual(
                plan.manifest.counts.model_dump(),
                {"T": 2, "R": 0, "V": 2, "D": 1, "U": 1},
            )
            candidate_id = plan.manifest.candidate_ids[0]
            provenance = list(plan.provenance(candidate_id))
            self.assertEqual(len(provenance), 2)
            self.assertEqual(
                {item[0]["template_id"] for item in provenance},
                {"DUPLICATE.A", "DUPLICATE.B"},
            )
            legacy_search = search.model_copy(update={"directions": ("BUY",)})
            legacy = preflight(
                legacy_search, GenerationPolicyV1(), Path(temporary) / "legacy.sqlite"
            )
            self.assertEqual(candidate_id, next(legacy.candidates())[0])

    def test_composite_rejects_same_candidate_id_with_different_payload(self) -> None:
        profile = self.profile("acceptance-v1.json")
        resolved = resolve_request(self.request(profile), profile)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fake_plans = []
            for index in range(2):
                database_path = root / f"fake-{index}.sqlite"
                with closing(sqlite3.connect(database_path)) as database:
                    database.executescript(
                        """CREATE TABLE candidates(id TEXT PRIMARY KEY, strategy TEXT NOT NULL);
                        CREATE TABLE provenance(
                            id TEXT NOT NULL, origin TEXT NOT NULL, multiplicity INTEGER NOT NULL,
                            PRIMARY KEY(id, origin));"""
                    )
                    database.execute(
                        "INSERT INTO candidates VALUES (?,?)",
                        ("sha256:" + "0" * 64, json.dumps({"different": index})),
                    )
                    database.commit()
                fake_plans.append(
                    CandidatePlan(
                        database_path,
                        {
                            "search_space_id": "sha256:" + str(index) * 64,
                            "generation_policy_id": resolved.generation_policy.generation_policy_id,
                            "candidate_set_id": "sha256:" + "0" * 64,
                            "counts": {"T": 1, "R": 0, "V": 1, "D": 0, "U": 1},
                        },
                    )
                )
            with (
                patch("quantlab_mining.auto_discovery.preflight", side_effect=fake_plans),
                self.assertRaisesRegex(ContractError, "candidate hash collision"),
            ):
                build_composite_candidate_plan(resolved, root / "cache")

    def test_resolver_layer_has_no_market_or_result_layer_imports(self) -> None:
        source = (
            ROOT / "packages" / "mining" / "src" / "quantlab_mining" / "auto_discovery.py"
        ).read_text()
        for forbidden in (
            "quantlab_data",
            "quantlab_backtest",
            "quantlab_research",
            "quantlab_validation",
            "quantlab_scoring",
            "quantlab_robustness",
        ):
            self.assertNotIn(forbidden, source)
