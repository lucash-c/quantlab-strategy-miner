from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from mining_helpers import search_record, small_search
from pydantic import ValidationError
from quantlab_core.errors import ContractError
from quantlab_mining.canonicalization import candidate_id, candidate_payload, normalize_condition
from quantlab_mining.contracts import GenerationPolicyV1, MiningSearchSpaceV1, load_contract
from quantlab_mining.contradictions import contradiction
from quantlab_mining.generator import preflight


class GenerationTests(unittest.TestCase):
    def plan(self, root: Path, record=None, budget=160):
        return preflight(
            MiningSearchSpaceV1.model_validate(record or search_record(fixture160=True)),
            GenerationPolicyV1(candidate_budget=budget),
            root / "plan.sqlite",
        )

    def test_known_fixture_160_all_timeframes(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = self.plan(Path(tmp))
            self.assertEqual(
                plan.manifest["counts"], {"T": 192, "R": 32, "V": 160, "D": 0, "U": 160}
            )
            self.assertEqual({s.timeframe for _, s in plan.candidates()}, {"1m", "2m", "5m", "15m"})
            self.assertEqual(len(list(plan.candidates())), 160)

    def test_budget_and_expansion_abort(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ContractError, "SEARCH_SPACE_EXCEEDS_BUDGET"):
                self.plan(Path(tmp), budget=159)
            with self.assertRaisesRegex(ContractError, "SEARCH_SPACE_EXPANSION_LIMIT_EXCEEDED"):
                preflight(
                    small_search(),
                    GenerationPolicyV1(expansion_limit=1),
                    Path(tmp) / "untouched.sqlite",
                )
            self.assertFalse((Path(tmp) / "untouched.sqlite").exists())

    def test_separate_policy_and_search_identities(self):
        a, b = GenerationPolicyV1(candidate_budget=160), GenerationPolicyV1(candidate_budget=200)
        self.assertNotEqual(a.generation_policy_id, b.generation_policy_id)
        with tempfile.TemporaryDirectory() as tmp:
            x = self.plan(Path(tmp), budget=160).manifest
            y = self.plan(Path(tmp), budget=200).manifest
            self.assertEqual(x["search_space_id"], y["search_space_id"])
            self.assertEqual(x["candidate_set_id"], y["candidate_set_id"])

    def test_metadata_and_friction_not_candidate_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, strategy = next(self.plan(Path(tmp)).candidates())
            changed = strategy.model_dump(mode="json")
            changed.update(strategy_id="OTHER", name="Other", strategy_version=9)
            changed["cost_model"] = {
                "type": "FIXED_PER_SIDE",
                "version": "1.0.0",
                "points_per_side": "10",
            }
            changed["slippage_model"] = {
                "type": "FIXED_POINTS",
                "version": "1.0.0",
                "points_per_side": "2",
            }
            self.assertEqual(
                candidate_id(strategy), candidate_id(type(strategy).model_validate(changed))
            )
            self.assertNotIn("canonicalization_version", candidate_payload(strategy))

    def test_proven_duplicates_not_empirical(self):
        record = search_record()
        duplicate = copy.deepcopy(record["templates"][0])
        duplicate["template_id"] = "ANOTHER.LABEL"
        record["templates"].append(duplicate)
        with tempfile.TemporaryDirectory() as tmp:
            plan = self.plan(Path(tmp), record)
            self.assertEqual(plan.manifest["counts"], {"T": 4, "R": 0, "V": 4, "D": 2, "U": 2})
            self.assertEqual(plan.manifest["search_space_id"], small_search().search_space_id)

    def test_strict_no_float_unknown_fields_no_ranges(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "search.json"
            path.write_text(
                json.dumps({**search_record(), "candidate_budget": 2}), encoding="utf-8"
            )
            with self.assertRaises(ContractError):
                load_contract(path, MiningSearchSpaceV1)
            record = search_record()
            record["templates"][0]["base"]["grids"]["period"] = [2.0]
            path.write_text(json.dumps(record), encoding="utf-8")
            with self.assertRaisesRegex(ContractError, "floating-point"):
                load_contract(path, MiningSearchSpaceV1)
            with self.assertRaises(ValidationError):
                GenerationPolicyV1(candidate_budget=50_001)

    def test_decimal_grid_spelling_does_not_change_semantic_universe(self):
        record = search_record()
        record["templates"][0]["confirmations"] = [
            {"kind": "volume", "grids": {"mode": ["RAW"], "threshold": ["1.500"]}}
        ]
        a = MiningSearchSpaceV1.model_validate(record)
        record["templates"][0]["confirmations"][0]["grids"]["threshold"] = ["1.5"]
        b = MiningSearchSpaceV1.model_validate(record)
        self.assertEqual(a.search_space_id, b.search_space_id)


class EquivalenceTests(unittest.TestCase):
    a = {"type": "candle_field", "name": "close"}

    def const(self, value):
        return {"type": "constant", "dimension": "PRICE", "value": value}

    def cmp(self, op, right, left=None):
        return {"type": "comparison", "operator": op, "left": left or self.a, "right": right}

    def normalize(self, node):
        return normalize_condition(node, {})

    def test_flatten_order_dedup_decimal_and_comparison(self):
        atom = self.cmp("LT", self.const("1.500"))
        normalized = self.cmp("GT", self.a, self.const("1.5"))
        nested = {
            "type": "logical",
            "operator": "AND",
            "children": [atom, {"type": "logical", "operator": "AND", "children": [atom]}],
        }
        self.assertEqual(self.normalize(nested), normalized)
        self.assertEqual(
            self.normalize(self.cmp("EQ", self.const("1.0"))),
            self.normalize(self.cmp("EQ", self.a, self.const("1"))),
        )

    def test_cross_swap_and_type_mismatch(self):
        node = {
            "type": "cross",
            "operator": "CROSS_BELOW",
            "left": self.a,
            "right": self.const("1"),
        }
        self.assertEqual(
            self.normalize(node),
            {**node, "operator": "CROSS_ABOVE", "left": node["right"], "right": node["left"]},
        )
        with self.assertRaisesRegex(ContractError, "dimensions"):
            self.normalize(self.cmp("GT", {"type": "constant", "dimension": "RATIO", "value": "1"}))

    def test_local_interval_relation_eq_and_enum_proofs(self):
        pairs = [
            [self.cmp("GT", self.const("10")), self.cmp("LT", self.const("5"))],
            [self.cmp("GT", self.const("10")), self.cmp("LTE", self.const("10"))],
            [self.cmp("EQ", self.const("10")), self.cmp("NE", self.const("10.0"))],
        ]
        for atoms in pairs:
            node = self.normalize({"type": "logical", "operator": "AND", "children": atoms})
            self.assertIsNotNone(contradiction(node))
        self.assertIsNone(contradiction(self.normalize(self.cmp("GT", self.const("10")))))
        enum = {"type": "feature", "feature_id": "e"}
        atoms = [
            self.cmp("EQ", {"type": "constant", "dimension": "ENUM", "value": v}, enum)
            for v in ("UP", "DOWN")
        ]
        self.assertIsNotNone(
            contradiction(
                normalize_condition(
                    {"type": "logical", "operator": "AND", "children": atoms}, {"e": "ENUM"}
                )
            )
        )
