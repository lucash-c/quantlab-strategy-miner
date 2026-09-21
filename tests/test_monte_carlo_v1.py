from __future__ import annotations

import unittest

from quantlab_core.canonical import canonical_json_bytes
from quantlab_core.numeric import CanonicalRational
from quantlab_robustness.exact import nearest_rank
from quantlab_robustness.monte_carlo import evaluate_candidate_paths
from quantlab_robustness.sampling import build_path_set, draw_trace, source_pool_record
from robustness_helpers import candidate_strategy, mc_policy, session_record, trade


def pool():
    return source_pool_record(
        source={"source": "EXPLICIT_SESSION_IDS", "session_ids": ["A", "B", "C"]},
        dataset_id="dataset",
        sessions=[
            {"session_id": "A", "trading_date": "2026-01-01"},
            {"session_id": "B", "trading_date": "2026-01-02"},
            {"session_id": "C", "trading_date": "2026-01-03"},
        ],
    )


def records():
    return {
        "A": session_record("A", "2026-01-01", 10),
        "B": session_record("B", "2026-01-02", -5),
        "C": session_record("C", "2026-01-03", 2),
    }


class MonteCarloTests(unittest.TestCase):
    def test_sampler_vector_is_fixed_big_endian_and_repeatable(self):
        args = dict(
            source_pool_id="pool",
            sampling_method="SESSION_BOOTSTRAP_WITH_REPLACEMENT",
            seed="0" * 63 + "1",
            path_index=0,
            draw_index=0,
            upper_bound=3,
        )
        trace = draw_trace(**args)
        self.assertEqual(trace, draw_trace(**args))
        encoded = canonical_json_bytes(
            {
                "domain": "quantlab-monte-carlo-sampler/v1",
                "sampler_version": "SHA256_COUNTER_REJECTION_V1",
                "source_pool_id": "pool",
                "sampling_method": "SESSION_BOOTSTRAP_WITH_REPLACEMENT",
                "seed": "0" * 63 + "1",
                "path_index": 0,
                "draw_index": 0,
                "retry_index": 0,
            }
        )
        self.assertEqual(
            encoded,
            b'{"domain":"quantlab-monte-carlo-sampler/v1","draw_index":0,'
            b'"path_index":0,"retry_index":0,'
            b'"sampler_version":"SHA256_COUNTER_REJECTION_V1",'
            b'"sampling_method":"SESSION_BOOTSTRAP_WITH_REPLACEMENT",'
            b'"seed":"0000000000000000000000000000000000000000000000000000000000000001",'
            b'"source_pool_id":"pool"}\n',
        )
        self.assertEqual(trace["integer_encoding"], "UNSIGNED_BIG_ENDIAN_256")
        self.assertEqual(
            trace["attempts"],
            [
                {
                    "retry_index": 0,
                    "digest_hex": (
                        "656f19858471e7c05cd75b45fa0f8a97"
                        "cf109c1ec6443c2f1f438f1e1dde6cd8"
                    ),
                    "integer_decimal": (
                        "45879893874379962795581354991611667277727738711917935092043164423611020635352"
                    ),
                    "accepted": True,
                }
            ],
        )
        self.assertEqual(trace["selected_index"], 0)

        rejected = draw_trace(**{**args, "path_index": 1, "upper_bound": (1 << 255) + 1})
        self.assertEqual([item["accepted"] for item in rejected["attempts"]], [False, True])
        self.assertEqual(
            [item["digest_hex"] for item in rejected["attempts"]],
            [
                "872367e9d9623e666a5792423b9958ca781e8c22c1144aabafb6c8e3a5f8bbf2",
                "2a15da9c034f14bc0ca02776def0a36d342b7ccce2912ce10d3cb5450520062d",
            ],
        )
        self.assertEqual(
            rejected["selected_index"],
            19035752215661283710184688396880812535000216850056889819830240940955426358829,
        )

    def test_path_set_is_candidate_independent_and_seeded(self):
        first = build_path_set(mc_policy(), pool())
        second = build_path_set(mc_policy(), pool())
        changed = build_path_set(mc_policy(seed="0" * 63 + "2"), pool())
        self.assertEqual(first, second)
        self.assertNotIn("candidate_id", str(first))
        self.assertNotEqual(first["monte_carlo_path_set_id"], changed["monte_carlo_path_set_id"])

    def test_full_permutation_preserves_total_net_but_changes_trajectory(self):
        policy = mc_policy(number_of_paths=12)
        path_set = build_path_set(policy, pool())
        candidate_id, strategy = candidate_strategy()
        source = records()
        source["A"]["ledger"] = [trade("A", 1, 20), trade("A", 2, -10)]
        source["A"]["ledger_count"] = 2
        result = evaluate_candidate_paths(
            candidate_id=candidate_id,
            strategy=strategy,
            policy=policy,
            path_set=path_set,
            session_records=source,
            ledger_loader=lambda eid: source[eid.removeprefix("evaluation-")]["ledger"],
        )
        self.assertEqual({row["total_net"]["numerator"] for row in result["paths"]}, {"7"})
        self.assertGreater(
            len(
                {
                    tuple(block["source_session_id"] for block in row["blocks"])
                    for row in result["paths"]
                }
            ),
            1,
        )
        self.assertGreater(
            len({row["max_drawdown"]["numerator"] for row in result["paths"]}), 1
        )
        self.assertEqual(
            result["summary"]["probability_net_le_zero"]["value"],
            {"numerator": "0", "denominator": "1"},
        )

    def test_bootstrap_repeated_session_preserves_order_and_occurrences(self):
        policy = mc_policy(
            "SESSION_BOOTSTRAP_WITH_REPLACEMENT", number_of_paths=32, path_length_sessions=3
        )
        path_set = build_path_set(policy, pool())
        repeated = path_set["paths"][31]
        self.assertEqual(
            [block["source_session_id"] for block in repeated["blocks"]], ["A", "B", "A"]
        )
        candidate_id, strategy = candidate_strategy()
        source = records()
        one_path_set = {**path_set, "paths": [repeated], "path_count": 1}
        one_path_set["monte_carlo_path_set_id"] = "one-path-for-oracle"
        result = evaluate_candidate_paths(
            candidate_id=candidate_id,
            strategy=strategy,
            policy=policy,
            path_set=one_path_set,
            session_records=source,
            ledger_loader=lambda eid: source[eid.removeprefix("evaluation-")]["ledger"],
        )
        blocks = result["paths"][0]["blocks"]
        self.assertEqual([block["synthetic_block_ordinal"] for block in blocks], [1, 2, 3])
        self.assertEqual(len({block["synthetic_occurrence_id"] for block in blocks}), 3)
        self.assertEqual(result["paths"][0]["total_trades"], 3)
        self.assertEqual(result["paths"][0]["total_net"], {"numerator": "15", "denominator": "1"})
        self.assertEqual(
            [block["source_trading_date"] for block in blocks],
            ["2026-01-01", "2026-01-02", "2026-01-01"],
        )

    def test_nearest_rank_has_no_interpolation_and_insufficient_set_is_identified(self):
        values = [CanonicalRational(value) for value in (1, 2, 3, 4)]
        self.assertEqual(nearest_rank(values, CanonicalRational(1, 2)), CanonicalRational(2))
        self.assertEqual(nearest_rank(values, CanonicalRational(95, 100)), CanonicalRational(4))
        insufficient = build_path_set(
            mc_policy(minimum_source_sessions=4),
            pool(),
        )
        self.assertEqual(insufficient["status"], "INSUFFICIENT_MONTE_CARLO_SOURCE")
        self.assertTrue(insufficient["monte_carlo_path_set_id"].startswith("sha256:"))
