from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from mining_helpers import search_record
from quantlab_core.errors import ContractError
from quantlab_mining.canonicalization import candidate_id
from quantlab_mining.contracts import GenerationPolicyV1, MiningSearchSpaceV1, load_contract
from quantlab_mining.generator import preflight


class TemplateTests(unittest.TestCase):
    def plan(self, base, confirmations=(), *, policy=None):
        record = search_record()
        record["templates"] = [
            {"template_id": "TEST", "base": base, "confirmations": list(confirmations)}
        ]
        space = MiningSearchSpaceV1.model_validate(record)
        with tempfile.TemporaryDirectory() as tmp:
            plan = preflight(space, policy or GenerationPolicyV1(), Path(tmp) / "plan.sqlite")
            return plan.manifest, list(plan.candidates())

    @staticmethod
    def atom(kind, grids, direction_grids=None):
        return {"kind": kind, "grids": grids, "direction_grids": direction_grids or {}}

    def trend(self):
        return self.atom(
            "trend_close", {"average": ["ema_close"], "period": [2], "operator": ["COMPARE"]}
        )

    def test_six_families_all_schemas_buy_sell(self):
        atoms = [
            self.trend(),
            self.atom(
                "trend_pair",
                {
                    "average": ["ema_close"],
                    "short_period": [2],
                    "long_period": [3],
                    "operator": ["CROSS"],
                },
            ),
            self.atom("vwap_context", {"mode": ["CLOSE"], "operator": ["CROSS"]}),
            self.atom("breakout_previous", {"period": [2], "include_current": [False]}),
            self.atom(
                "momentum",
                {"feature": ["point_change"], "period": [2]},
                {"BUY": {"threshold": ["5"]}, "SELL": {"threshold": ["-7"]}},
            ),
            self.atom("candle_context", {"mode": ["DIRECTION"]}),
        ]
        for atom in atoms:
            with self.subTest(kind=atom["kind"]):
                manifest, candidates = self.plan(atom)
                self.assertEqual(manifest["counts"]["U"], 2)
                self.assertEqual({s.direction for _, s in candidates}, {"BUY", "SELL"})
                self.assertTrue(all(cid == candidate_id(s) for cid, s in candidates))
                self.assertTrue(
                    all(s.schema_version == "strategy-definition/v3" for _, s in candidates)
                )
                self.assertEqual(
                    [cid for cid, _ in candidates], sorted(cid for cid, _ in candidates)
                )

    def test_vwap_distance_explicit_bindings_dependency_closure(self):
        atom = self.atom(
            "vwap_context",
            {"mode": ["DISTANCE"], "lower_inclusive": [True], "upper_inclusive": [False]},
            {"BUY": {"lower": ["-5"], "upper": ["10"]}, "SELL": {"lower": ["-10"], "upper": ["5"]}},
        )
        _, candidates = self.plan(atom)
        for _, strategy in candidates:
            self.assertEqual(
                [f.name for f in strategy.features], ["session_trade_vwap", "distance_to_vwap"]
            )
        del atom["direction_grids"]["SELL"]
        with self.assertRaises(ContractError):
            self.plan(atom)

    def test_candle_geometry_close_position_and_n_return(self):
        atoms = [
            self.atom(
                "candle_context",
                {
                    "mode": ["GEOMETRY"],
                    "feature": ["absolute_body", "candle_range"],
                    "threshold": ["5"],
                },
            ),
            self.atom(
                "candle_context",
                {"mode": ["CLOSE_POSITION"], "lower_inclusive": [True], "upper_inclusive": [True]},
                {
                    "BUY": {"lower": ["0.6"], "upper": ["1"]},
                    "SELL": {"lower": ["0"], "upper": ["0.4"]},
                },
            ),
            self.atom(
                "momentum",
                {"feature": ["n_candle_return"], "period": [3]},
                {"BUY": {"threshold": ["0.01"]}, "SELL": {"threshold": ["-0.02"]}},
            ),
        ]
        for atom in atoms:
            self.assertGreater(self.plan(atom)[0]["counts"]["U"], 0)

    def test_all_confirmation_modes_and_three_atoms(self):
        volumes = [
            self.atom("volume", {"mode": ["RAW"], "threshold": ["1"]}),
            self.atom("volume", {"mode": ["ROLLING"], "period": [2], "include_current": [False]}),
            self.atom(
                "volume",
                {
                    "mode": ["RELATIVE"],
                    "period": [2],
                    "include_current": [False],
                    "threshold": ["1.5"],
                },
            ),
        ]
        volatilities = [
            self.atom("volatility", {"mode": ["ATR_THRESHOLD"], "period": [2], "threshold": ["0"]}),
            self.atom(
                "volatility", {"mode": ["ROLLING_RANGE"], "period": [2], "include_current": [False]}
            ),
            self.atom(
                "volatility",
                {
                    "mode": ["ATR_RANGE"],
                    "period": [2],
                    "lower": ["0"],
                    "upper": ["20"],
                    "lower_inclusive": [True],
                    "upper_inclusive": [False],
                },
            ),
        ]
        for volume in volumes:
            for volatility in volatilities:
                manifest, candidates = self.plan(self.trend(), (volume, volatility))
                self.assertEqual(manifest["counts"]["U"], 2)
                self.assertTrue(all(len(s.entry_conditions.children) == 3 for _, s in candidates))

    def test_reordered_grids_templates_conditions_have_same_ids(self):
        volume = self.atom("volume", {"mode": ["RAW"], "threshold": ["1", "2"]})
        volatility = self.atom(
            "volatility", {"mode": ["ATR_THRESHOLD"], "period": [2], "threshold": ["0"]}
        )
        x = self.plan(self.trend(), (volume, volatility))[1]
        volume["grids"]["threshold"].reverse()
        y = self.plan(self.trend(), (volatility, volume))[1]
        self.assertEqual(
            [(cid, s.canonical_bytes()) for cid, s in x],
            [(cid, s.canonical_bytes()) for cid, s in y],
        )

    def test_invalid_policy_or_template_is_not_silently_truncated(self):
        invalids = [
            self.atom("breakout_previous", {"period": [2], "include_current": [True]}),
            self.atom("trend_close", {"average": ["rsi"], "period": [2], "operator": ["COMPARE"]}),
            self.atom(
                "trend_close", {"average": ["ema_close"], "period": [1001], "operator": ["COMPARE"]}
            ),
            self.atom(
                "trend_close", {"average": ["ema_close"], "period": [True], "operator": ["COMPARE"]}
            ),
            self.atom("trend_close", {"average": ["ema_close"], "period": [2], "operator": ["OR"]}),
        ]
        for atom in invalids:
            with self.assertRaises(ContractError):
                self.plan(atom)
        with self.assertRaisesRegex(ContractError, "CROSS"):
            cross = self.trend()
            cross["grids"]["operator"] = ["CROSS"]
            self.plan(
                cross, (self.atom("vwap_context", {"mode": ["CLOSE"], "operator": ["CROSS"]}),)
            )
        with self.assertRaisesRegex(ContractError, "whitelisted"):
            self.plan(self.trend(), (self.atom("candle_context", {"mode": ["DIRECTION"]}),))
        with self.assertRaisesRegex(ContractError, "grid"):
            oversized = self.trend()
            oversized["grids"]["period"] = list(range(1, 34))
            self.plan(oversized)

    def test_empty_range_mechanically_rejected_not_failed(self):
        atom = self.atom(
            "vwap_context",
            {"mode": ["DISTANCE"], "lower_inclusive": [True], "upper_inclusive": [False]},
            {d: {"lower": ["5"], "upper": ["1"]} for d in ("BUY", "SELL")},
        )
        manifest, candidates = self.plan(atom)
        self.assertEqual(manifest["counts"], {"T": 2, "R": 2, "V": 0, "D": 0, "U": 0})
        self.assertEqual(candidates, [])

    def test_feature_declaration_aliases_and_duplicates_canonical(self):
        _, candidates = self.plan(self.trend())
        cid, strategy = candidates[0]
        record = strategy.model_dump(mode="json")
        original = copy.deepcopy(record["features"][0])
        original["feature_id"] = "alias"
        record["features"].append(original)
        condition = copy.deepcopy(record["entry_conditions"])
        condition["right"]["feature_id"] = "alias"
        record["entry_conditions"] = {
            "type": "logical",
            "operator": "AND",
            "children": [record["entry_conditions"], condition],
        }
        self.assertEqual(cid, candidate_id(type(strategy).model_validate(record)))

    def test_published_examples_fixture_and_real_six(self):
        root = Path(__file__).resolve().parents[1]
        for filename, expected in (("fixture-160.json", 160), ("b3-six.json", 6)):
            space = load_contract(root / "examples/mining" / filename, MiningSearchSpaceV1)
            with tempfile.TemporaryDirectory() as tmp:
                self.assertEqual(
                    preflight(space, GenerationPolicyV1(), Path(tmp) / "plan.sqlite").manifest[
                        "counts"
                    ]["U"],
                    expected,
                )

    def test_published_schemas_match_models(self):
        from quantlab_mining.contracts import EvaluationConfigV1

        root = Path(__file__).resolve().parents[1]
        for model, name in (
            (MiningSearchSpaceV1, "mining-search-space"),
            (GenerationPolicyV1, "generation-policy"),
            (EvaluationConfigV1, "mining-evaluation"),
        ):
            schema = json.loads((root / f"schemas/{name}-v1.schema.json").read_text())
            self.assertEqual(schema, model.model_json_schema())
