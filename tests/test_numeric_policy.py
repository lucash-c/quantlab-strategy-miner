from __future__ import annotations

import unittest

from quantlab_core.errors import ContractError
from quantlab_core.numeric import CanonicalRational, round_half_even
from quantlab_core.price import common_decimal_scale


class CanonicalRationalTests(unittest.TestCase):
    def test_equivalent_fractions_have_one_representation(self) -> None:
        expected = CanonicalRational(-1, 2)
        for value in (
            CanonicalRational(-2, 4),
            CanonicalRational(2, -4),
            CanonicalRational(-50, 100),
        ):
            self.assertEqual(value, expected)
            self.assertEqual(
                value.to_record(), {"numerator": "-1", "denominator": "2"}
            )
        self.assertEqual(
            CanonicalRational(0, 500).to_record(),
            {"numerator": "0", "denominator": "1"},
        )

    def test_zero_denominator_is_rejected(self) -> None:
        with self.assertRaises(ContractError):
            CanonicalRational(1, 0)

    def test_round_half_even_handles_sign_and_ties(self) -> None:
        self.assertEqual(round_half_even(5, 2), 2)
        self.assertEqual(round_half_even(7, 2), 4)
        self.assertEqual(round_half_even(-5, 2), -2)
        self.assertEqual(round_half_even(-7, 2), -4)

    def test_common_scale_accepts_exact_signed_strategy_constants(self) -> None:
        self.assertEqual(common_decimal_scale([2], ["-1.250", "0.125"]), 3)


if __name__ == "__main__":
    unittest.main()
