from __future__ import annotations

import unittest

from quantlab_core.errors import ContractError
from quantlab_core.numeric import CanonicalRational
from quantlab_core.sessions import TradingSession
from quantlab_data.session_catalog import SessionCatalog
from quantlab_research.access import ResearchAccess
from quantlab_research.comparison import compare
from quantlab_research.contracts import GatePolicyV1, SplitPolicyV1
from quantlab_research.gates import apply_gate, defined, undefined
from quantlab_research.split import create_split


def catalog(n=19):
    from dataclasses import asdict

    sessions = [
        TradingSession(
            f"s{i}",
            f"2026-01-{i:02d}",
            "WIN",
            "WINV26",
            1,
            2,
            2,
            0,
            "source",
            "import",
            "normalized",
        )
        for i in range(1, n + 1)
    ]
    return SessionCatalog(
        {"dataset_id": "parent", "logical_asset": "WIN", "sessions": [asdict(s) for s in sessions]}
    )


def policy(criteria=(), *, validation=False):
    return GatePolicyV1.model_validate(
        {
            "schema_version": "validation-gate-policy/v1"
            if validation
            else "discovery-gate-policy/v1",
            "criteria": list(criteria),
        }
    )


class ResearchContractTests(unittest.TestCase):
    def test_split_13_6_generic_and_insufficient(self):
        split, d, v = create_split(catalog(), SplitPolicyV1())
        self.assertEqual((split["discovery"]["count"], split["validation"]["count"]), (13, 6))
        self.assertEqual(d["session_ids"], [f"s{i}" for i in range(1, 14)])
        self.assertEqual(v["session_ids"], [f"s{i}" for i in range(14, 20)])
        for n in (1, 6, 18):
            with self.assertRaisesRegex(ContractError, "INSUFFICIENT_SESSIONS"):
                create_split(catalog(n), SplitPolicyV1())
        bigger, _, _ = create_split(catalog(30), SplitPolicyV1())
        self.assertEqual(bigger["discovery"]["count"], 24)
        smaller, _, _ = create_split(
            catalog(5),
            SplitPolicyV1(
                validation_sessions=2, min_validation_sessions=1, min_discovery_sessions=1
            ),
        )
        self.assertEqual(smaller["discovery"]["count"], 3)

    def test_gates_empty_and_canonical_threshold_ids(self):
        empty = apply_gate("c", "e", {}, policy())
        self.assertEqual(empty["result"], "PASS")
        self.assertEqual(empty["information"], ["NO_CRITERIA_CONFIGURED"])
        a = {"metric": "win_rate", "operator": "GTE", "threshold": "0.500"}
        b = {**a, "threshold": {"numerator": "2", "denominator": "4"}}
        self.assertEqual(policy([a]).policy_id, policy([b, b]).policy_id)
        passed = apply_gate("c", "e", {"win_rate": defined(CanonicalRational(1, 2))}, policy([a]))
        self.assertEqual(passed["result"], "PASS")

    def test_all_failures_sorted_and_original_undefined_reason(self):
        criteria = [
            {"metric": "trades", "operator": "GTE", "threshold": 5},
            {"metric": "profit_factor", "operator": "NE", "threshold": "1"},
        ]
        result = apply_gate(
            "c",
            "e",
            {"trades": defined(0), "profit_factor": undefined("NO_LOSSES")},
            policy(criteria),
        )
        self.assertEqual(len(result["failures"]), 2)
        ids = [r["criterion_id"] for r in result["failures"]]
        self.assertEqual(ids, sorted(ids))
        u = next(r for r in result["failures"] if r["metric"] == "profit_factor")
        self.assertEqual((u["reason"], u["undefined_reason"]), ("UNDEFINED_METRIC", "NO_LOSSES"))

    def test_all_six_comparison_operators(self):
        for operator in ("GT", "GTE", "LT", "LTE", "EQ", "NE"):
            result = apply_gate(
                "c",
                "e",
                {"trades": defined(2)},
                policy([{"metric": "trades", "operator": operator, "threshold": 2}]),
            )
            self.assertEqual(result["result"] == "PASS", operator in {"GTE", "LTE", "EQ"})

    def test_registry_no_code_float_or_comparative_discovery(self):
        for metric, threshold in (
            ("eval(x)", 1),
            ("average_trade_delta", "-10"),
            ("trades", 1.5),
            ("trades", True),
            ("trades", "0.5"),
        ):
            with self.assertRaises((ValueError, ContractError)):
                policy([{"metric": metric, "operator": "GTE", "threshold": threshold}])
        policy(
            [{"metric": "average_trade_delta", "operator": "GTE", "threshold": "-10"}],
            validation=True,
        )

    def test_comparison_deltas_positive_denominator_and_undefined(self):
        from quantlab_research.contracts import COMPARISON_BASES

        d = {name: defined(2) for name in COMPARISON_BASES}
        v = {name: defined(1) for name in COMPARISON_BASES}
        result = compare(
            "c",
            {"partition_evaluation_id": "d", "metrics": d},
            {"partition_evaluation_id": "v", "metrics": v},
        )
        self.assertEqual(
            result["metrics"]["average_trade_delta"]["value"], CanonicalRational(-1).to_record()
        )
        self.assertEqual(
            result["metrics"]["profit_factor_ratio"]["value"], CanonicalRational(1, 2).to_record()
        )
        for numerator in (0, -1):
            d["trades_per_session"] = defined(numerator)
            result = compare(
                "c",
                {"partition_evaluation_id": "d", "metrics": d},
                {"partition_evaluation_id": "v", "metrics": v},
            )
            self.assertEqual(
                result["metrics"]["trades_per_session_ratio"]["reason"],
                "DISCOVERY_DENOMINATOR_NOT_POSITIVE",
            )
        d["profit_factor"] = undefined("NO_LOSSES")
        result = compare(
            "c",
            {"partition_evaluation_id": "d", "metrics": d},
            {"partition_evaluation_id": "v", "metrics": v},
        )
        self.assertEqual(
            result["metrics"]["profit_factor_delta"]["sources"],
            [{"partition": "DISCOVERY", "reason": "NO_LOSSES"}],
        )

    def test_authorization_zero_holdout_before_release(self):
        _, d, v = create_split(catalog(), SplitPolicyV1())
        observed = []
        access = ResearchAccess(d, v, observed.append)
        for kind in ("ticks", "candles", "features", "session_evaluation"):
            with self.assertRaisesRegex(ContractError, "RESEARCH_ACCESS_DENIED"):
                access.check("VALIDATION", "s14", kind, "c")
        self.assertEqual(observed, [])
        access.released, access.approved = True, {"c"}
        access.check("VALIDATION", "s14", "ticks", "c")
        with self.assertRaises(ContractError):
            access.check("VALIDATION", "s14", "ticks", "other")

    def test_published_contract_roundtrip_and_tampered_ids_rejected(self):
        from quantlab_research.records import ResearchPartitionV1, ResearchSplitPlanV1

        split, d, _ = create_split(catalog(), SplitPolicyV1())
        self.assertEqual(ResearchSplitPlanV1.model_validate(split).model_dump(mode="json"), split)
        self.assertEqual(ResearchPartitionV1.model_validate(d).model_dump(mode="json"), d)
        with self.assertRaisesRegex(ValueError, "split_plan_id mismatch"):
            ResearchSplitPlanV1.model_validate({**split, "split_plan_id": "tampered"})
        p = policy([{"metric": "net_pnl", "operator": "GTE", "threshold": "0.500"}])
        self.assertEqual(GatePolicyV1.model_validate(p.canonical_record()).policy_id, p.policy_id)
        record = p.canonical_record()
        record["criteria"][0]["criterion_id"] = "tampered"
        with self.assertRaisesRegex(ValueError, "criterion_id"):
            GatePolicyV1.model_validate(record)

    def test_published_schemas_match_strict_models(self):
        import json
        from pathlib import Path

        from quantlab_research.records import ResearchPartitionV1, ResearchSplitPlanV1

        root = Path(__file__).resolve().parents[1]
        for name, cls in (
            ("research-split-policy-v1", SplitPolicyV1),
            ("research-gate-policy-v1", GatePolicyV1),
            ("research-split-plan-v1", ResearchSplitPlanV1),
            ("research-partition-v1", ResearchPartitionV1),
        ):
            published = json.loads((root / "schemas" / (name + ".schema.json")).read_text())
            self.assertEqual(
                published.pop("$schema"), "https://json-schema.org/draft/2020-12/schema"
            )
            self.assertEqual(published, cls.model_json_schema())


if __name__ == "__main__":
    unittest.main()
